import asyncio
from decimal import Decimal
from domain import LocalOrderBook, TradeEvent, OrderBookUpdate, GapDetectedError
from infrastructure import IMarketDataSource, ReorderingBuffer, LatencyMonitor
from analyzers import IcebergAnalyzer, WhaleAnalyzer, AccumulationDetector, SpoofingAnalyzer, FlowToxicityAnalyzer, GammaProvider
from analyzers_features import FeatureCollector  # WHY: Для ML feature collection
from analyzers_derivatives import DerivativesAnalyzer  # WHY: Clean Architecture - математика derivatives
from datetime import datetime
# WHY: Импорт функции загрузки config для мульти-токен поддержки
from config import get_config


class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    RESET = '\033[0m'

class TradingEngine:
    """
    Главный движок системы.
    Управляет потоками данных и детекцией айсбергов.
    
    === ОБНОВЛЕНИЕ: Мульти-токен поддержка (Task: Multi-Asset Support) ===
    Теперь создает экземпляры анализаторов с конфигурацией токена.
    """
    
    def __init__(self, symbol: str, infra: IMarketDataSource, deribit_infra=None, repository=None):
        self.symbol = symbol
        self.infra = infra
        self.repository = repository
        self.book = LocalOrderBook(symbol=symbol)  # Book автоматически загружает config
        self.deribit = deribit_infra
        
        # === НОВОЕ: Создаем экземпляры анализаторов с config ===
        # WHY: Анализаторы теперь становятся stateful и используют config токена
        config = get_config(symbol)
        self.iceberg_analyzer = IcebergAnalyzer(config)
        self.whale_analyzer = WhaleAnalyzer(config)
        self.spoofing_analyzer = SpoofingAnalyzer()  # WHY: Anti-spoofing для фильтрации fake walls
        
        # === НОВОЕ: FlowToxicityAnalyzer для VPIN (Task: VPIN Implementation) ===
        # WHY: Рассчитывает токсичность потока для корректировки confidence айсбергов
        bucket_size = config.vpin_bucket_size  # Из AssetConfig (10 BTC, 100 ETH и т.д.)
        self.flow_toxicity_analyzer = FlowToxicityAnalyzer(self.book, bucket_size)
        
        # === НОВОЕ: AccumulationDetector для свинг-трейдинга (Phase 3.2) ===
        # WHY: Автоматическая детекция накопления/дистрибуции на мульти-таймфреймах
        # FIX: Gemini Validation - передаём config для мульти-ассет поддержки
        self.accumulation_detector = AccumulationDetector(self.book, config)
        
        # === НОВОЕ: DerivativesAnalyzer для Clean Architecture (Refactor 2025-12-25) ===
        # WHY: Разделение IO (infrastructure) и математики (analyzer)
        self.derivatives_analyzer = DerivativesAnalyzer()
        
        # === НОВОЕ: GammaProvider для GEX метрик (Fix: Lobotomy Issue) ===
        # WHY: Читает gamma_profile из LocalOrderBook для FeatureCollector
        self.gamma_provider = GammaProvider(self.book)
        
        # === НОВОЕ: FeatureCollector для ML (Шаг 5: Интеграция) ===
        # WHY: Собирает снимки всех метрик при обнаружении айсбергов
        self.feature_collector = FeatureCollector(
            order_book=self.book,
            flow_analyzer=None,  # Не используем - данные читаются напрямую из book
            derivatives_analyzer=self.derivatives_analyzer,  # FIX: Clean Architecture - передаём analyzer!
            spoofing_detector=self.spoofing_analyzer,  # WHY: Anti-spoofing для ML features
            gamma_provider=self.gamma_provider,  # FIX: Lobotomy Issue - GEX метрики!
            flow_toxicity_analyzer=self.flow_toxicity_analyzer  # WHY: VPIN для ML features
        )
        
        # Очереди для событий (Producer-Consumer pattern)
        self.depth_queue = asyncio.Queue()
        self.trade_queue = asyncio.Queue()

        # === НОВОЕ: Adaptive Delay (Task: Gemini Phase 2.1) ===
        # WHY: Мониторинг задержек для адаптивной синхронизации потоков
        self.latency_monitor = LatencyMonitor(
            window_size=100,  # 100 последних событий
            k=3.0,            # 99.7% покрытие (правило 3 сигм)
            base_processing_ms=10.0  # Binance processing time
        )
        
        self.buffer = ReorderingBuffer(delay_ms=50)  # Начальное значение
        
        # Флаг инициализации
        self.is_initialized = False
        
        # === FUSION LOGIC: Price tracking for Absorption detection ===
        self._last_mid_price = None  # Используется для расчёта price_change
        
        # === FIX: Time-based accumulation check (Gemini Validation) ===
        # WHY: Iteration-based проверка нестабильна из-за Adaptive Delay
        self.last_accumulation_check_time = 0.0  # Timestamp последней проверки
        
        # === FIX VULNERABILITY A: DB write throttling ===
        # WHY: Throttling перенесён из FeatureCollector в services layer
        self.last_db_write_time = 0.0  # Timestamp последней записи в DB

    async def run(self):
        """
        Точка входа: Запуск всего механизма.
        
        КРИТИЧЕСКАЯ ПОСЛЕДОВАТЕЛЬНОСТЬ:
        1. Запускаем WebSocket стримы (они буферизуют данные)
        2. Скачиваем снапшот
        3. Применяем снапшот
        4. Применяем буферизованные updates (только те, что ПОСЛЕ снапшота)
        5. Переходим в нормальный режим real-time обработки
        """
        
        print(f"🚀 Starting Engine for {self.symbol}...")
            
            # Шаг 1: Запускаем сборщиков данных (Producers) - Это те, что читают данные из WebSocket
        tasks_to_gather = [
            asyncio.create_task(self._produce_depth()),
            asyncio.create_task(self._produce_trades()),
            
        
    
        ]

        # --- НОВАЯ ЗАДАЧА: GEX MONITOR ---
        if self.deribit:
            tasks_to_gather.append(asyncio.create_task(self._produce_gex()))
            
            # --- НОВАЯ ЗАДАЧА: DERIVATIVES CACHE (ШАГ 6.3) ---
            # WHY: Обновляет basis/skew каждые 5 минут для FeatureCollector
            tasks_to_gather.append(asyncio.create_task(self._feed_derivatives_cache()))
        
        # --- НОВАЯ ЗАДАЧА: PERIODIC CLEANUP (Memory Management) ---
        # WHY: Удаляет старые айсберги каждые 5 минут (вместо счётчика сделок)
        tasks_to_gather.append(asyncio.create_task(self._periodic_cleanup_task()))
        
        await self._initialize_book()
        
        
        # Шаг 2: Даем время буферизовать данные перед снапшотом
        print("⏳ Buffering WebSocket streams (2s)...")
        await asyncio.sleep(2)
        
        # Шаг 3: Скачиваем снапшот
        print("📸 Downloading snapshot...")
        snapshot = await self.infra.get_snapshot(self.symbol)
        
        # Шаг 4: Применяем снапшот к книге
        self.book.apply_snapshot(
            bids=snapshot['bids'],
            asks=snapshot['asks'],
            last_update_id=snapshot['lastUpdateId']
        )
        
        # Шаг 5: Применяем буферизованные updates (только актуальные)
        await self._apply_buffered_updates()
        
        # Шаг 6: Помечаем систему как инициализированную
        self.is_initialized = True
        print("✅ System initialized. Real-time processing started.\n")
        
        # Шаг 7: Запускаем основной цикл обработки
        consumer_task = asyncio.create_task(self._consume_and_analyze())
        tasks_to_gather.append(consumer_task)
        
        # Держим все задачи активными
        await asyncio.gather(*tasks_to_gather)

    async def _initialize_book(self):
        """
        WHY: Восстанавливает исторический контекст (ЗАДАЧА 3 - Cold Start).
        
        Загружает последние 7 дней SmartCandles из БД:
        - 1H: 168 свечей (7 дней * 24ч)
        - 4H: 42 свечи (7 дней * 6 свечей/день)
        - 1D: 30 свечей
        - 1W: 12 свечей
        
        Теория: Детекция накопления требует истории CVD.
        """
        if not self.repository:
            print("⚠️  No repository - skipping historical context restore")
            return
        
        print("📚 Restoring historical context (7 days)...")
        
        # Загружаем для каждого таймфрейма
        timeframes = {
            '1h': 168,  # 7 дней
            '4h': 42,   # 7 дней
            '1d': 30,   # 30 дней
            '1w': 12    # 12 недель
        }
        
        for tf, limit in timeframes.items():
            try:
                # Вызываем repository.get_aggregated_smart_candles()
                candles = await self.repository.get_aggregated_smart_candles(
                    symbol=self.symbol,
                    timeframe=tf,
                    limit=limit
                )
                
                # Загружаем в HistoricalMemory
                self.book.historical_memory.load_from_aggregated_candles(candles, tf)
                
                print(f"   ✅ Loaded {len(candles)} {tf} candles")
                
            except Exception as e:
                print(f"   ⚠️  Failed to load {tf} candles: {e}")
        
        # Показываем статистику
        stats = self.book.historical_memory.get_stats()
        print(f"📊 Historical memory stats: {stats}")

    async def _apply_buffered_updates(self):
        """
        КРИТИЧЕСКАЯ ФУНКЦИЯ:
        Применяет только те updates из буфера, которые НЕ включены в снапшот.
        """
        applied_count = 0
        skipped_count = 0
        
        temp_buffer = []
        
        # Выгребаем все из очереди
        while not self.depth_queue.empty():
            temp_buffer.append(await self.depth_queue.get())
        
        # Применяем только актуальные
        for update in temp_buffer:
            if update.final_update_id > self.book.last_update_id:
                if self.book.apply_update(update):
                    applied_count += 1
            else:
                skipped_count += 1
        
        print(f"📦 Buffer processed: {applied_count} applied, {skipped_count} skipped (old)")

    async def _produce_depth(self):
        """Producer: Читает сокет стакана и кладет в очередь"""
        async for update in self.infra.listen_updates(self.symbol):
            # === НОВОЕ: Записываем задержку ===
            import time
            arrival_time_ms = time.time() * 1000
            event_time_ms = int(update.event_time.timestamp() * 1000)
            self.latency_monitor.record_latency(event_time_ms, arrival_time_ms)
            
            await self.depth_queue.put(update)

    async def _produce_trades(self):
        """Producer: Читает сокет сделок и кладет в очередь"""
        async for trade in self.infra.listen_trades(self.symbol):
            # === НОВОЕ: Записываем задержку ===
            import time
            arrival_time_ms = time.time() * 1000
            event_time_ms = trade.event_time
            self.latency_monitor.record_latency(event_time_ms, arrival_time_ms)
            
            await self.trade_queue.put(trade)

    async def _consume_and_analyze(self):
        """
        ФИНАЛЬНАЯ ВЕРСИЯ:
        Гибрид: "Новый движок" (Buffer/Race Protection) + "Старый мозг" (Logic/Analytics).
        """
        print("🛡️ Reordering Buffer activated. Starting analysis...")
        
        iteration_count = 0  # Для периодического обновления delay
        
        # === FIX: Time-based accumulation check (Gemini Validation) ===
        # WHY: Инициализируем таймер (если еще не инициализирован)
        import time
        if self.last_accumulation_check_time == 0.0:
            self.last_accumulation_check_time = time.time()
        
        while True:
            # === НОВОЕ: Adaptive Delay ===
            # WHY: Динамически обновляем задержку каждые 100 итераций
            iteration_count += 1
            if iteration_count % 100 == 0:
                adaptive_delay_ms = self.latency_monitor.get_adaptive_delay()
                self.buffer.delay_sec = adaptive_delay_ms / 1000.0
                
                # Отладочный вывод (каждые 1000 итераций)
                if iteration_count % 1000 == 0:
                    stats = self.latency_monitor.get_stats()
                    print(f"📊 Latency Stats: RTT={stats['mean_rtt']:.1f}ms, "
                          f"Jitter={stats['stdev_jitter']:.1f}ms, "
                          f"Adaptive Delay={stats['adaptive_delay']:.1f}ms")
            
            # === FIX: Accumulation Detection (Wyckoff) - TIME-BASED ===
            # WHY: Проверяем каждые 30 секунд (вместо 500 итераций)
            # НЕ привязано к сделкам - работает даже в периоды низкой активности
            # Gemini Fix: Time-based вместо iteration-based (стабильный интервал)
            current_time = time.time()
            if current_time - self.last_accumulation_check_time > 30.0:
                self.last_accumulation_check_time = current_time  # Reset timer
                
                try:
                    accumulation_results = self.accumulation_detector.detect_accumulation_multi_timeframe()
                    
                    # Если обнаружена дивергенция на любом таймфрейме
                    if accumulation_results:
                        for timeframe, result in accumulation_results.items():
                            self._print_accumulation_alert(timeframe, result)
                except Exception as e:
                    # Не ломаем главный цикл при ошибках в детекции
                    print(f"⚠️ Accumulation detection error: {e}")
            
            # 1. Ждем с адаптивной задержкой (Micro-Batching)
            current_delay_sec = self.buffer.delay_sec
            await asyncio.sleep(current_delay_sec) 
            
            # 2. Забираем Сделки (Priority 0 - Высший, так как они имеют точный timestamp)
            while not self.trade_queue.empty():
                trade = self.trade_queue.get_nowait()
                self.buffer.add(trade, event_time=trade.event_time, priority=0)
                
            # 3. Забираем Обновления стакана (Priority 1 - Низший)
            while not self.depth_queue.empty():
                update = self.depth_queue.get_nowait()
                # Приводим время к timestamp (мс) для корректной сортировки с трейдами
                ts = update.event_time.timestamp() * 1000 
                self.buffer.add(update, event_time=ts, priority=1)

            # 4. Получаем отсортированный список всех событий
            sorted_events = self.buffer.get_all_sorted()
            
            if not sorted_events:
                continue

            # 5. Обрабатываем события строго по порядку времени
            for event in sorted_events:
                
                # --- ВАРИАНТ А: Обновление Стакана (OrderBookUpdate) ---
                if isinstance(event, OrderBookUpdate):
                    update = event
                    try:
                        if self.book.apply_update(update):
                            
                            # === NEW: Delta-t Iceberg Detection ===
                            update_time_ms = int(update.event_time.timestamp() * 1000)
                            
                            for pending in list(self.book.pending_refill_checks):
                                trade = pending['trade']
                                
                                if pending['price'] != trade.price:
                                    continue
                                
                                delta_t = update_time_ms - pending['trade_time_ms']
                                
                                if delta_t < 0:  # Race condition - reject negative
                                    continue
                                
                                if delta_t > 100:
                                    self.book.pending_refill_checks.remove(pending)
                                    continue
                                
                                current_vol = self._get_volume_at_price(trade.price, pending['is_ask'])
                                
                                if current_vol >= pending['visible_before']:
                                    
                                    # === GEMINI FIX: Извлекаем VPIN и CVD Divergence (Data Fusion) ===
                                    stored_vpin = pending.get('vpin_score')
                                    stored_divergence = pending.get('cvd_divergence')
                                    
                                    iceberg_event = self.iceberg_analyzer.analyze_with_timing(
                                        book=self.book,
                                        trade=trade,
                                        visible_before=pending['visible_before'],
                                        delta_t_ms=delta_t,
                                        update_time_ms=update_time_ms,
                                        vpin_score=stored_vpin,        # ✅ GEMINI: Pass VPIN
                                        cvd_divergence=stored_divergence # ✅ GEMINI: Pass CVD
                                    )
                                    
                                    if iceberg_event:
                                        lvl = self.book.active_icebergs.get(trade.price)
                                        total_hidden = lvl.total_hidden_volume if lvl else iceberg_event.detected_hidden_volume
                                        obi = self.book.get_weighted_obi(depth=20)
                                        
                                        # === НОВОЕ: Anti-Spoofing Integration ===
                                        # WHY: Рассчитываем вероятность спуфинга для корректировки confidence
                                        if lvl:
                                            # Получаем текущую mid_price и историю
                                            current_mid = self.book.get_mid_price()
                                            price_history = list(self.book.historical_memory.history['1h']['price'])
                                            
                                            # Рассчитываем spoofing probability
                                            spoofing_prob = self.spoofing_analyzer.calculate_spoofing_probability(
                                                iceberg_level=lvl,
                                                current_mid_price=current_mid,
                                                price_history=price_history
                                            )
                                            
                                            # Сохраняем в IcebergLevel
                                            lvl.spoofing_probability = spoofing_prob
                                            
                                            # Корректируем confidence на основе spoofing
                                            # WHY: Формула adjusted = base * (1 - spoofing_prob)
                                            base_confidence = lvl.confidence_score
                                            lvl.confidence_score = base_confidence * (1.0 - spoofing_prob)
                                        
                                        self._print_iceberg_update(iceberg_event, total_hidden, obi, lvl)
                                        
                                        # === НОВОЕ: Anti-Spoofing Integration ===
                                        # WHY: Рассчитываем вероятность спуфинга и корректируем confidence
                                        if lvl:
                                            current_mid = self.book.get_mid_price()
                                            price_history = self.book.historical_memory.get_price_history(limit=100)
                                            
                                            spoofing_prob = self.spoofing_analyzer.calculate_spoofing_probability(
                                                iceberg_level=lvl,
                                                current_mid_price=current_mid,
                                                price_history=price_history
                                            )
                                            
                                            # Обновляем поле в айсберге
                                            lvl.spoofing_probability = spoofing_prob
                                            
                                            # Корректируем confidence: adjusted = base * (1 - spoofing_prob)
                                            if spoofing_prob > 0.5:  # Только если вероятность спуфинга высокая
                                                original_conf = lvl.confidence_score
                                                lvl.confidence_score = original_conf * (1.0 - spoofing_prob)
                                                
                                                # Debug вывод для высокого spoofing
                                                if spoofing_prob > 0.7:
                                                    print(f"   ⚠️  SPOOFING ALERT: {spoofing_prob*100:.0f}% probability (confidence adjusted {original_conf:.2f} → {lvl.confidence_score:.2f})")
                                        
                                        # === НОВОЕ: ML Feature Collection (ШАГ 5.2) ===
                                        # WHY: Сохраняем снимок метрик при обнаружении айсберга
                                        if lvl:
                                            # 1. ВСЕГДА собираем snapshot (обновляет CVD state!)
                                            snapshot = self.feature_collector.capture_snapshot()
                                            
                                            # 2. THROTTLE ТОЛЬКО DB writes (100ms)
                                            import time
                                            current_time = time.time()
                                            time_since_last_write = current_time - self.last_db_write_time
                                            
                                            if self.repository and time_since_last_write >= 0.1:  # 100ms throttle
                                                self.last_db_write_time = current_time
                                                
                                                # 3. Классифицируем намерение (SCALPER/INTRADAY/POSITIONAL)
                                                # TODO: Replace estimated_adv with actual ADV from historical_memory
                                                estimated_adv = Decimal("10000")  # ~10k BTC average for BTCUSDT
                                                intention_type = self.iceberg_analyzer.classify_intention(
                                                    hidden_volume=lvl.total_hidden_volume,
                                                    adv_20d=estimated_adv
                                                )
                                                
                                                # 4. Вычисляем IIR (Iceberg Impact Ratio)
                                                iir_value = float(lvl.total_hidden_volume / estimated_adv) if estimated_adv > 0 else 0.0
                                                
                                                # 5. Создаем lifecycle event с классификацией
                                                lifecycle_id = await self.repository.save_lifecycle_event(
                                                    symbol=self.symbol,
                                                    price=trade.price,
                                                    is_ask=lvl.is_ask,
                                                    event_type='REFILLED',
                                                    total_volume_absorbed=lvl.total_hidden_volume,
                                                    refill_count=lvl.refill_count,
                                                    intention_type=intention_type,
                                                    iir_value=iir_value
                                                )
                                                
                                                # 6. Сохраняем feature snapshot
                                                if lifecycle_id:
                                                    await self.repository.save_feature_snapshot(lifecycle_id, snapshot)
                                                
                                                # 7. Сохраняем уровень
                                                asyncio.create_task(self.repository.save_level(lvl, self.symbol))
                                    
                                    self.book.pending_refill_checks.remove(pending)
                            
                            if not self.book.validate_integrity():
                                print("❌ Book integrity failed! Resyncing...")
                                await self._resync()
                                break
                    except GapDetectedError:
                        print("⚠️ Gap detected in order book. Resyncing...")
                        await self._resync()
                        break

                # --- ВАРИАНТ Б: Сделка (TradeEvent) ---
                elif isinstance(event, TradeEvent):
                    trade = event
                    
                    # === НОВОЕ: VPIN Update (Обновление токсичности потока) ===
                    # WHY: Рассчитываем VPIN при каждой сделке
                    vpin_score = self.flow_toxicity_analyzer.update_vpin(trade)
                    
                    # === GEMINI FIX: Захват CVD Divergence (Data Fusion) ===
                    # WHY: Получаем cached divergence для передачи в analyze_with_timing()
                    current_divergence = self.accumulation_detector.get_current_divergence_state()
                    
                    # === ЛОГИКА АНАЛИЗА (ИЗ ТВОЕЙ СТАРОЙ ВЕРСИИ) ===

                    # 1. Пробой уровней (Check Breaches)
                    # Проверяем, пробила ли цена существующие айсберги
                    breached_levels = self.book.check_breaches(trade.price)
                    for lvl in breached_levels:
                        self._print_breakout_alert(lvl, trade.price)

                    # 2. Whale Analyzer & Algo Detection
                    # Возвращает 3 значения: категория, объем в $, и флаг алгоритма
                    # WHY: Используем экземпляр анализатора с config (адаптирован под токен)
                    category, vol_usd, algo_alert = self.whale_analyzer.update_stats(self.book, trade)
                    
                    # === FIX: Обновляем историческую память для аккумуляции (Task: Full Wyckoff) ===
                    # WHY: Сохраняем Whale/Minnow CVD и цену для детекции дивергенции
                    current_ts = datetime.fromtimestamp(trade.event_time / 1000.0)
                    
                    self.book.historical_memory.update_history(
                        timestamp=current_ts,
                        whale_cvd=self.book.whale_cvd['whale'],   # Данные уже обновлены в update_stats
                        minnow_cvd=self.book.whale_cvd['minnow'], # Данные уже обновлены в update_stats
                        price=trade.price
                    )
                    # ========================================================
                    
                    # Если обнаружен алгоритмический бот
                    if algo_alert:
                        side_str = "SELL 🔴" if algo_alert == "SELL_ALGO" else "BUY 🟢"
                        print(f"\n🤖 {Colors.YELLOW}ALGO DETECTED!{Colors.RESET} {side_str}")
                    
                    # Если обнаружен Кит (Whale)
                    if category == 'whale':
                        self._print_whale_alert(trade, vol_usd)
                    
                    # 4. CVD Status (Баланс сил)
                    # Раз в 50 сделок печатаем таблицу покупок/продаж
                    if self.book.trade_count % 50 == 0:
                        self._print_cvd_status(trade.price)

                    # 5. Фильтр шума
                    # Мелкие сделки (< 0.01 BTC) пропускаем для айсберг-анализа, но учитываем в статистике выше
                    if trade.quantity < Decimal("0.01"):
                        continue
                    
                    # === NEW DELTA-T LOGIC (REPLACE OLD ICEBERG DETECTION) ===
    
                    # 1. Calculate visible volume BEFORE trade
                    target_vol = Decimal("0")
                    if trade.is_buyer_maker:
                        target_vol = self.book.bids.get(trade.price, Decimal("0"))
                    else:
                        target_vol = self.book.asks.get(trade.price, Decimal("0"))
    
                    # 2. DO NOT analyze immediately - add to pending queue
                    # === GEMINI FIX: Сохраняем VPIN и CVD Divergence (Data Fusion) ===
                    self.book.pending_refill_checks.append({
                        'trade': trade,
                        'visible_before': target_vol,
                        'trade_time_ms': trade.event_time,
                        'price': trade.price,
                        'is_ask': not trade.is_buyer_maker,
                        'vpin_score': vpin_score,           # ✅ GEMINI: VPIN context
                        'cvd_divergence': current_divergence # ✅ GEMINI: CVD context
                    })
    
                    # 3. Cleanup old entries (> 100ms ago)
                    self._cleanup_pending_checks(current_time_ms=trade.event_time)
                    
                    # === FUSION LOGIC (OFI + Absorption) ===
                    # WHY: Вычисляем OFI и проверяем сценарий Absorption (Gemini Phase 3.1)
                    ofi_value = self.book.calculate_ofi()  # НОВОЕ: Вызов OFI
                    
                    # Сценарий Absorption: OFI > 0 но цена не растёт → Sell Iceberg
                    # (Будет логироваться в ML для обучения моделей)
                    current_mid = self.book.get_mid_price()
                    absorption_detected = False
                    
                    # Проверяем только если есть история цены
                    if hasattr(self, '_last_mid_price') and self._last_mid_price:
                        price_change_pct = abs(float(current_mid - self._last_mid_price) / float(self._last_mid_price)) * 100.0
                        
                        # Absorption: OFI положительный + цена стабильна (< 0.01%)
                        if ofi_value > 0 and price_change_pct < 0.01:
                            absorption_detected = True
                            # Debug вывод (раз в 100 сделок чтобы не спамить)
                            if self.book.trade_count % 100 == 0:
                                print(f"\n💧 ABSORPTION DETECTED! OFI={ofi_value:.2f}, Price Change={price_change_pct:.4f}%")
                    
                    # Сохраняем текущую цену для следующей итерации
                    self._last_mid_price = current_mid
                    
                    # === НОВОЕ: Update FeatureCollector price history (ШАГ 5.3) ===
                    # WHY: Обновляем историю цен для расчета TWAP/volatility
                    self.feature_collector.update_price(float(current_mid))
                    
                    # === ML LOGIC ===
                    # Определяем, сохранять ли данные (Крупная сделка > 0.1 BTC ИЛИ есть активный айсберг)
                    is_significant = (trade.quantity > Decimal("0.1"))
                    has_iceberg = trade.price in self.book.active_icebergs
                    
                    if is_significant or has_iceberg:
                        try:
                            # 1. Готовим данные
                            curr_obi = self.book.get_weighted_obi(depth=20)
                        
                            # === НОВОЕ: GEX-context для ML ===
                            dist_c, dist_p, t_gex = None, None, 0
                            is_near_wall = False
                            wall_type = None
                            
                            if self.book.gamma_profile:
                                p_flt = float(trade.price)
                                dist_c = p_flt - self.book.gamma_profile.call_wall
                                dist_p = p_flt - self.book.gamma_profile.put_wall
                                t_gex = self.book.gamma_profile.total_gex
                                
                                # Проверяем близость к Gamma Wall
                                is_near_wall, wall_type = self.book.is_near_gamma_wall(trade.price)

                            # 2. Определяем значения (проверяем активные айсберги)
                            lvl = self.book.active_icebergs.get(trade.price)
                            
                            if lvl:
                                save_added = Decimal("0")  # Будет обновлено в OrderBookUpdate
                                save_conf = lvl.confidence_score
                                ts = lvl.creation_time
                                save_total = lvl.total_hidden_volume
                            else:
                                save_added = Decimal("0")
                                save_conf = 0.0
                                ts = datetime.now()
                                save_total = Decimal("0")

                            # 3. Формируем словарь (с GEX-контекстом)
                            row = {
                                'event_time': ts, 'symbol': self.symbol,
                                'price': trade.price, 'is_ask': not trade.is_buyer_maker,
                                'trade_quantity': trade.quantity, 'visible_volume_before': target_vol,
                                'added_volume': save_added, 'total_accumulated': save_total,
                                'spread': self.book.get_spread(), 'obi_value': curr_obi,
                                'dist_call': dist_c, 'dist_put': dist_p, 'total_gex': t_gex,
                                'confidence': save_conf, 'is_breach': False,
                                'is_near_gamma_wall': is_near_wall,  # НОВОЕ ПОЛЕ
                                'gamma_wall_type': wall_type         # НОВОЕ ПОЛЕ
                            }
                        
                            # 4. Отправляем в базу
                            if self.repository:
                                await self.repository.log_training_event(row)
                        except Exception as e:
                            print(f"❌ [ERROR] Exception in ML LOGIC block: {e}")
                            import traceback
                            traceback.print_exc()
                    
                    # === GEMINI FIX: MARKET METRICS LOGGING (Migration 005) ===
                    # WHY: Логируем метрики с правильными именами + wall volumes
                    if self.repository and (self.book.trade_count % 10 == 0):
                        try:
                            # 1. Агрегируем wall volumes из активных айсбергов
                            wall_whale_vol = Decimal('0')
                            wall_dolphin_vol = Decimal('0')
                            
                            for iceberg in self.book.active_icebergs.values():
                                if iceberg.status.value == 'ACTIVE':  # Только активные
                                    if iceberg.is_dolphin:
                                        wall_dolphin_vol += iceberg.total_hidden_volume
                                    else:
                                        wall_whale_vol += iceberg.total_hidden_volume
                            
                            # 2. Логируем с новыми именами колонок
                            await self.repository.log_full_metric({
                                'timestamp': datetime.now(),
                                'symbol': self.symbol,
                                'price': current_mid,
                                'spread_bps': self.book.get_spread(),
                                'book_ofi': ofi_value,  # ✅ NEW NAME
                                'book_obi': curr_obi if 'curr_obi' in locals() else self.book.get_weighted_obi(depth=20),  # ✅ NEW NAME
                                'flow_whale_cvd_delta': self.book.whale_cvd.get('whale', 0),  # ✅ NEW NAME
                                'flow_dolphin_cvd_delta': self.book.whale_cvd.get('dolphin', 0),  # ✅ NEW COLUMN
                                'flow_minnow_cvd_delta': self.book.whale_cvd.get('minnow', 0),  # ✅ NEW NAME
                                'wall_whale_vol': float(wall_whale_vol),  # ✅ NEW COLUMN
                                'wall_dolphin_vol': float(wall_dolphin_vol),  # ✅ NEW COLUMN
                                'basis': None,  # TODO: подключить derivatives
                                'skew': None,   # TODO: подключить derivatives
                                'oi_delta': None
                            })
                        except Exception as e:
                            print(f"❌ [ERROR] log_full_metric failed: {e}")
                    
                    # === КОНЕЦ ML LOGIC ==="
                  
    async def _produce_gex(self):
        """Фоновый процесс: Обновление GEX раз в минуту"""
        print("🌊 Deribit GEX Monitor started...")
        while True:
            # Запрашиваем данные
            profile = await self.deribit.get_gamma_profile()
            
            if profile:
                # Обновляем "кармашек" в стакане
                self.book.gamma_profile = profile
                
                # (Опционально) Выводим в лог раз в минуту, чтобы знать, что работает
                # print(f"🌊 GEX Updated: ${profile.total_gex/1e6:.1f}M "
                #       f"| Call Wall: {profile.call_wall} | Put Wall: {profile.put_wall}")
            
            # Ждем 60 секунд перед следующим обновлением
            await asyncio.sleep(60)

    def _print_alert(self, event, obi: float):
        """Вывод найденного айсберга с контекстом"""
        sentiment = "NEUTRAL ⚪"
        if obi > 0.3: sentiment = "BULLISH 🟢 (Давление покупок)"
        elif obi < -0.3: sentiment = "BEARISH 🔴 (Давление продаж)"
        
        # --- БЛОК GEX ---
        gex_info = ""
        if self.book.gamma_profile:
            gex = self.book.gamma_profile
            
            # Проверяем, не стоит ли айсберг прямо на стене? (Диапазон +-100$)
            # Важно: Сравниваем Decimal и float, поэтому приводим типы
            if abs(float(event.price) - gex.call_wall) < 100:
                gex_info = f"\n   🧱 CALL WALL: Айсберг на уровне сопротивления {gex.call_wall:,.0f}!"
            elif abs(float(event.price) - gex.put_wall) < 100:
                gex_info = f"\n   🧱 PUT WALL: Айсберг на уровне поддержки {gex.put_wall:,.0f}!"
            else:
                # Если не на стене, просто покажем общую Гамму
                gex_info = f"\n   🌊 Market Gamma: ${gex.total_gex/1e6:.1f}M"
        # ----------------
        
        print(f"\n🧊 ICEBERG DETECTED! {event.symbol}")
        print(f"   💰 Цена: {event.price:,.2f}")
        print(f"   🕵️  Скрытый объем: {event.detected_hidden_volume:.4f}")
        print(f"   📊 Контекст (OBI): {obi:.2f} | {sentiment}")
        print(f"   🎯 Уверенность: {event.confidence * 100:.0f}%{gex_info}")
        print("-" * 50)

    def _print_whale_alert(self, trade: TradeEvent, volume_usd: float):
        """Красивое уведомление о крупной сделке"""
        is_sell = trade.is_buyer_maker
        side = f"{Colors.RED}SELL 🔴{Colors.RESET}" if is_sell else f"{Colors.GREEN}BUY 🟢{Colors.RESET}"
        print(f"\n🚀 {Colors.BLUE}WHALE ALERT!{Colors.RESET} {side} ${volume_usd:,.0f} @ {trade.price:,.2f}")

    def _print_cvd_status(self, current_price: Decimal):
        """Выводит текущий баланс сил (CVD)"""
        print(f"\n--- 📊 CVD STATUS (Баланс спроса) @ ${current_price:,.2f} ---")
        
        def color_val(val):
            c = Colors.GREEN if val > 0 else Colors.RED
            return f"{c}${val/1000:,.0f}k{Colors.RESET}"

        cvd = self.book.whale_cvd
        print(f"🐋 КИТЫ (Smart Money): {color_val(cvd['whale'])}")
        print(f"🐬 Дельфины (Трейдеры): {color_val(cvd['dolphin'])}")
        print(f"🐟 Рыбы (Толпа):        {color_val(cvd['minnow'])}")
        print("-" * 40)

    def _print_large_trade(self, trade: TradeEvent):
        """Просто выводит крупные сделки (Лента сделок)"""
        if trade.quantity < Decimal("0.5"): return  # Фильтр мелочи
        
        # Используем наши новые методы из domain.py
        best_bid = self.book.get_best_bid()
        best_ask = self.book.get_best_ask()
        spread = self.book.get_spread()
        
        # Защита от None (если стакан еще пустой)
        bid_price = best_bid[0] if best_bid else Decimal("0")
        ask_price = best_ask[0] if best_ask else Decimal("0")
        spread_val = spread if spread else Decimal("0")

        side = "BUY 🟢" if not trade.is_buyer_maker else "SELL 🔴"
        
        print(f"⚡ {side} {trade.quantity:.4f} BTC @ {trade.price:,.2f} | "
              f"Spread: {spread_val:.2f} | Bid: {bid_price:.2f} | Ask: {ask_price:.2f}")

    async def _resync(self):
        """
        Аварийная ресинхронизация при обнаружении ошибок.
        В production это критически важно.
        """
        print("🔄 Resyncing order book...")
        self.is_initialized = False
        
        # Очищаем очереди
        while not self.depth_queue.empty():
            await self.depth_queue.get()
        
        # Повторяем процесс инициализации
        snapshot = await self.infra.get_snapshot(self.symbol)
        self.book.apply_snapshot(
            bids=snapshot['bids'],
            asks=snapshot['asks'],
            last_update_id=snapshot['lastUpdateId']
        )
        
        # === НОВОЕ: Reconcile icebergs after resync (Critical Bug Fix - Gemini 2.2) ===
        # WHY: Удаляет "ghost" айсберги, которые исчезли во время disconnect
        self.book.reconcile_with_snapshot(
            bids=snapshot['bids'],
            asks=snapshot['asks']
        )
        
        await self._apply_buffered_updates()
        self.is_initialized = True
        print("✅ Resync completed")

    def _print_iceberg_update(self, event, total_hidden: Decimal, obi: float, lvl):
        """Вывод обновления по айсбергу (накопление)"""
        # Определяем тип стены
        wall_type = "RESISTANCE 🔴" if lvl and lvl.is_ask else "SUPPORT 🟢"
        
        # Маркер Gamma Wall
        gamma_badge = f" {Colors.YELLOW}[GAMMA WALL]{Colors.RESET}" if lvl and lvl.is_gamma_wall else ""
        
        print(f"\n🧊 {wall_type} REINFORCED! {event.symbol} @ {event.price:,.2f}{gamma_badge}")
        print(f"   ➕ Обнаружено сейчас: {event.detected_hidden_volume:.4f}")
        print(f"   📦 Всего скрытого (Total Absorbed): {Colors.BLUE}{total_hidden:.4f} BTC{Colors.RESET} [cite: 623]")
        print(f"   📊 OBI: {obi:.2f}")
        print("-" * 50)

    def _print_breakout_alert(self, lvl, current_price: Decimal):
        """Алерт о пробое уровня (Смерть айсберга)"""
        action = "BROKE UP 🚀" if lvl.is_ask else "FELL THROUGH 🔻"
        color = Colors.GREEN if lvl.is_ask else Colors.RED
        
        gamma_msg = f"{Colors.YELLOW}MAJOR GAMMA LEVEL LOST! EXPECT VOLATILITY!{Colors.RESET}" if lvl.is_gamma_wall else ""
        
        print(f"\n💥 {color}ICEBERG BREACHED!{Colors.RESET} {action}")
        print(f"   💀 Уровень: {lvl.price:,.2f} уничтожен.")
        print(f"   📉 Текущая цена: {current_price:,.2f}")
        print(f"   🪦 Всего впитано перед смертью: {lvl.total_hidden_volume:.4f} BTC")
        if gamma_msg:
            print(f"   ⚠️  {gamma_msg}")
        print("=" * 50)
    
    def _print_accumulation_alert(self, timeframe: str, result: dict):
        """
        WHY: Алерт о накоплении/дистрибуции (Wyckoff)
        
        Параметры:
        - SPRING: 🌱 (Bullish, идеальный сигнал)
        - UPTHRUST: 💥 (Bearish, ложный пробой)
        - ACCUMULATION: 👂 (Bullish, базовый)
        - DISTRIBUTION: 🐻 (Bearish, базовый)
        
        Args:
            timeframe: '1h', '4h', '1d', '1w'
            result: dict с результатами detect_accumulation()
        """
        # Определяем цвет и иконку по типу
        div_type = result['type']
        pattern = result['wyckoff_pattern']
        confidence = result['confidence']
        
        # Цвета и иконки
        if div_type == 'BULLISH':
            color = Colors.GREEN
            icon = "👂" if pattern == 'ACCUMULATION' else "🌱"
            type_label = "BULLISH ACCUMULATION"
        else:
            color = Colors.RED
            icon = "🐻" if pattern == 'DISTRIBUTION' else "💥"
            type_label = "BEARISH DISTRIBUTION"
        
        # Паттерн badge
        pattern_badge = f"{Colors.YELLOW}[{pattern}]{Colors.RESET}" if pattern in ['SPRING', 'UPTHRUST'] else f"[{pattern}]"
        
        print(f"\n{icon} {color}{type_label}{Colors.RESET} {pattern_badge} | Timeframe: {timeframe.upper()}")
        print(f"   🎯 Confidence: {confidence*100:.0f}%")
        
        # Дополнительные индикаторы
        if result.get('absorption_detected'):
            print(f"   💧 Passive Absorption: CONFIRMED")
        
        if result.get('obi_confirms'):
            print(f"   ⚖️  OBI Confirmation: CONFIRMED")
        
        if result.get('near_strong_zone'):
            zone_price = result.get('zone_price')
            print(f"   🎯 Near Strong Zone: ${zone_price:,.2f}")
        
        print("-" * 50)
    
    # WHY: Вспомогательные методы для Delta-t реализации
    
    def _cleanup_pending_checks(self, current_time_ms: int):
        """
        WHY: Удаляет устаревшие pending checks (старее 100ms).
        
        Предотвращает утечку памяти и избегает обработки
        несвязанных trade-update пар.
        
        Args:
            current_time_ms: Текущее время в миллисекундах (биржевое время)
        """
        CLEANUP_THRESHOLD_MS = 100  # Удаляем старье 100ms
        
        cutoff_time = current_time_ms - CLEANUP_THRESHOLD_MS
        
        # Удаляем старые элементы с начала очереди
        while self.book.pending_refill_checks:
            first = self.book.pending_refill_checks[0]
            if first['trade_time_ms'] < cutoff_time:
                self.book.pending_refill_checks.popleft()
            else:
                break  # Остальные элементы новее
    
    def _get_volume_at_price(self, price: Decimal, is_ask: bool) -> Decimal:
        """
        WHY: Получает текущий объем на уровне цены.
        
        Используется для проверки, восстановился ли объем после сделки (refill).
        
        Args:
            price: Ценовой уровень
            is_ask: True если Ask (сопротивление), False если Bid (поддержка)
        
        Returns:
            Decimal объем или 0 если уровня нет
        """
        if is_ask:
            return self.book.asks.get(price, Decimal("0"))
        else:
            return self.book.bids.get(price, Decimal("0"))
    
    async def _periodic_cleanup_task(self, interval_seconds: int = 300):
        """
        WHY: Периодическая очистка старых айсбергов (Memory Management)
        
        Запускается каждые interval_seconds (default 5 минут).
        Удаляет айсберги старше 1 часа и пробитые айсберги старше 5 минут.
        
        Преимущества таймера vs счётчика сделок:
        - Предсказуемое потребление памяти
        - Не зависит от волатильности (1000 сделок/сек vs 10 сделок/мин)
        - Меньше нагрузки на CPU (не вызывается на каждой 100-й сделке)
        
        Args:
            interval_seconds: Интервал между очистками (default 300с = 5 мин)
        """
        print(f"🧹 Cleanup task started (interval: {interval_seconds}s)")
        
        while True:
            try:
                # Wait for interval
                await asyncio.sleep(interval_seconds)
                
                # Cleanup old icebergs (TTL = 1 hour = 3600 seconds)
                before_count = len(self.book.active_icebergs)
                self.book.cleanup_old_levels(seconds=3600)
                after_count = len(self.book.active_icebergs)
                
                removed_count = before_count - after_count
                if removed_count > 0:
                    print(f"🧹 Cleanup: Removed {removed_count} old icebergs ({after_count} remaining)")
                
            except asyncio.CancelledError:
                print("🧹 Cleanup task cancelled")
                break
            except Exception as e:
                print(f"❌ Cleanup task error: {e}")
                # Continue running despite errors
                continue
    
    # === НОВОЕ: Derivatives Cache Background Task (ШАГ 6.4) ===
    
    async def _feed_derivatives_cache(self, interval_seconds: int = 300):
        """
        WHY: Фоновая задача обновления derivatives метрик.
        
        Clean Architecture (REFACTORED 2025-12-25):
        1. Infrastructure (self.deribit) - ТОЛЬКО IO: get_futures_data(), get_options_data()
        2. Analyzer (self.derivatives_analyzer) - ТОЛЬКО математика: calculate_annualized_basis(), calculate_options_skew()
        3. Services (этот метод) - Оркестрация: fetch → analyze → cache
        
        Обновляет кеш в FeatureCollector для использования при capture_snapshot().
        
        Теория (документ "Анализ умных денег"):
        - Basis > 20%: Перегрев, смарт-мани открывают Cash-and-Carry арбитраж
        - Skew > 10%: Экстремальный страх, противоположный сигнал
        
        Args:
            interval_seconds: Интервал обновления (default 300с = 5 мин)
        """
        print(f"📡 Derivatives Cache Monitor started (interval: {interval_seconds}s)")
        
        # WHY: Определяем currency из symbol (BTCUSDT → BTC)
        currency = self.symbol.replace('USDT', '')
        
        while True:
            try:
                # Wait for interval
                await asyncio.sleep(interval_seconds)
                
                # === 1. Infrastructure: Запрашиваем RAW данные (IO only) ===
                futures_data = await self.deribit.get_futures_data(currency=currency)
                options_data = await self.deribit.get_options_data(currency=currency)
                
                # === 2. Analyzer: Рассчитываем метрики (pure math) ===
                basis_apr = None
                if futures_data:
                    basis_apr = self.derivatives_analyzer.calculate_annualized_basis(
                        spot_price=futures_data['spot_price'],
                        futures_price=futures_data['futures_price'],
                        days_to_expiry=futures_data['days_to_expiry']
                    )
                
                skew = None
                if options_data:
                    skew = self.derivatives_analyzer.calculate_options_skew(
                        put_iv_25d=options_data['put_iv_25d'],
                        call_iv_25d=options_data['call_iv_25d']
                    )
                
                # === 3. Services: Обновляем кеш в FeatureCollector (orchestration) ===
                if basis_apr is not None:
                    self.feature_collector.cached_basis = basis_apr
                
                if skew is not None:
                    self.feature_collector.cached_skew = skew
                
                # Лог обновления (раз в 5 минут)
                if basis_apr is not None or skew is not None:
                    print(f"📡 Derivatives Cache: Basis={basis_apr:.2f}% | Skew={skew:.2f}%" if basis_apr and skew else f"📡 Derivatives Cache: Basis={basis_apr}% | Skew={skew}%")
                
            except asyncio.CancelledError:
                print("📡 Derivatives cache task cancelled")
                break
            except Exception as e:
                print(f"❌ Derivatives cache error: {e}")
                # Continue running despite errors
                continue
    
    async def _produce_gex(self):
        """
        WHY: Фоновый монитор Gamma Exposure (Clean Architecture).
        
        Orchestration:
        1. Infrastructure (IO): Запрос сырых данных опционов (get_gamma_data)
        2. Analyzer (Math): Расчет GEX по модели Блэка-Шоулза (calculate_gex)
        3. Domain (State): Обновление self.book.gamma_profile
        """
        print("🌊 Deribit GEX Monitor started...")
        
        # Определяем базовый актив (BTCUSDT -> BTC)
        # Если symbol сложный, нужна проверка, но пока assuming standard naming
        currency = self.symbol.replace('USDT', '')
        
        # WHY: First iteration delay = 0 (запускаем сразу), потом 60с
        delay = 0
        
        while True:
            try:
                # WHY: Sleep BEFORE get_gamma_data для правильного тестирования
                # Первый вызов: delay=0 (немедленно)
                # Последующие: delay=60 (каждые 60 секунд)
                await asyncio.sleep(delay)
                delay = 60  # Set delay for next iterations
                
                # === 1. IO: Получаем сырые данные (без блокировок) ===
                # Возвращает dict: keys=['strikes', 'ivs', 'expiry_years', ...]
                # Используем метод из infrastructure.py (DeribitInfrastructure)
                raw_data = await self.deribit.get_gamma_data(currency=currency)
                
                if raw_data:
                    # === 2. MATH: Считаем GEX через Analyzer ===
                    # Analyzer чистая функция, не делает запросов.
                    # Метод calculate_gex находится в analyzers_derivatives.py
                    profile = self.derivatives_analyzer.calculate_gex(
                        strikes=raw_data['strikes'],
                        types=raw_data['types'],
                        expiry_years=raw_data['expiry_years'],
                        ivs=raw_data['ivs'],
                        open_interest=raw_data['open_interest'],
                        underlying_price=raw_data['underlying_price']
                    )
                    
                    if profile:
                        # === 3. STATE: Обновляем состояние книги ===
                        # Это позволяет IcebergAnalyzer видеть стены в реальном времени
                        self.book.gamma_profile = profile
                        
                        # Опционально: Логируем обновление (можно закомментировать, чтобы не спамить)
                        # print(f"🌊 GEX Updated: ${profile.total_gex/1e6:.1f}M "
                        #       f"| Call Wall: {profile.call_wall} | Put Wall: {profile.put_wall}")
                else:
                    # Если данных нет (например, Rate Limit или ошибка сети)
                    pass

            except asyncio.CancelledError:
                print("🌊 GEX Monitor cancelled")
                break
            except Exception as e:
                print(f"❌ GEX Monitor Error: {e}")
                # WHY: Gemini - При ошибке делаем паузу чтобы не спамить API
                await asyncio.sleep(60)  # Пауза 60с перед retry
                continue
    
    # ========================================================================
    # GEMINI FIX: Periodic Cleanup (Fix: Zombie Icebergs)
    # ========================================================================
    
    async def _periodic_cleanup_task(self):
        """
        WHY: Периодическая очистка зомби-айсбергов (каждую минуту).
        
        ПРОБЛЕМА (Gemini Validation):
        - Айсберги без обновлений накапливались в памяти
        - ML features загрязнялись устаревшими данными
        - Память росла без ограничений
        
        РЕШЕНИЕ:
        - Вызывает book.cleanup_old_icebergs() каждые 60 секунд
        - Threshold: min_confidence=0.1 (10%)
        - Half-life: 300 секунд (5 минут для swing)
        
        Логика:
        - Айсберги без обновлений >10 минут → confidence < 0.1 → удаляются
        - Свежие айсберги (<5 минут) → остаются
        """
        print("🧹 Periodic Cleanup Task started...")
        
        # WHY: First iteration delay = 60 (начинаем через минуту после старта)
        await asyncio.sleep(60)
        
        while True:
            try:
                from datetime import datetime
                
                # Вызываем cleanup
                removed_count = self.book.cleanup_old_icebergs(
                    current_time=datetime.now(),
                    half_life_seconds=300,  # 5 минут для swing trading
                    min_confidence=0.1      # Удаляем айсберги с confidence <10%
                )
                
                # Логируем только если что-то удалено
                if removed_count > 0:
                    print(f"🧹 Cleaned up {removed_count} zombie iceberg(s)")
                
                # Повторяем каждые 60 секунд
                await asyncio.sleep(60)
                
            except asyncio.CancelledError:
                print("🧹 Cleanup Task cancelled")
                break
            except Exception as e:
                print(f"❌ Cleanup Task Error: {e}")
                # Продолжаем работу даже при ошибках
                await asyncio.sleep(60)
                continue