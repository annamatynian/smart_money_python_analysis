import asyncio
from decimal import Decimal
from domain import LocalOrderBook, TradeEvent, OrderBookUpdate, GapDetectedError
from infrastructure import IMarketDataSource, ReorderingBuffer, LatencyMonitor
from analyzers import IcebergAnalyzer, WhaleAnalyzer
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
        Метод-заглушка.
        Оставлен здесь для предотвращения ошибки, т.к. вся логика инициализации
        размещена прямо в run() после этого вызова.
        """
        pass

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
                                    
                                    iceberg_event = self.iceberg_analyzer.analyze_with_timing(
                                        book=self.book,
                                        trade=trade,
                                        visible_before=pending['visible_before'],
                                        delta_t_ms=delta_t,
                                        update_time_ms=update_time_ms
                                    )
                                    
                                    if iceberg_event:
                                        lvl = self.book.active_icebergs.get(trade.price)
                                        total_hidden = lvl.total_hidden_volume if lvl else iceberg_event.detected_hidden_volume
                                        obi = self.book.get_weighted_obi(depth=20)
                                        self._print_iceberg_update(iceberg_event, total_hidden, obi, lvl)
                                        
                                        if self.repository and lvl:
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
                    self.book.pending_refill_checks.append({
                        'trade': trade,
                        'visible_before': target_vol,
                        'trade_time_ms': trade.event_time,
                        'price': trade.price,
                        'is_ask': not trade.is_buyer_maker
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
                    
                    # === MARKET METRICS LOGGING (Gemini Phase 3.2) ===
                    # WHY: Логируем метрики OFI/OBI для ML-моделей (каждые 10 сделок)
                    if self.repository and (self.book.trade_count % 10 == 0):
                        try:
                            await self.repository.log_market_metrics(
                                symbol=self.symbol,
                                timestamp=datetime.now(),
                                mid_price=current_mid,
                                ofi=ofi_value,
                                obi=curr_obi if 'curr_obi' in locals() else self.book.get_weighted_obi(use_exponential=True),
                                spread_bps=self.book.get_spread()
                            )
                            # Note: absorption_detected не логируется отдельно (можно добавить колонку в БД позже)
                        except Exception as e:
                            print(f"❌ [ERROR] log_market_metrics failed: {e}")
                    
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