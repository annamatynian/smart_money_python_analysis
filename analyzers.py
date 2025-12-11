from decimal import Decimal
from typing import Optional, List, Tuple
from domain import LocalOrderBook, TradeEvent, IcebergLevel, CancellationContext, GammaProfile, AlgoDetectionMetrics
from events import IcebergDetectedEvent
# WHY: Импорт конфигурации для мульти-токен поддержки (Task: Multi-Asset Support)
from config import AssetConfig


class IcebergAnalyzer:
    """
    Анализатор айсбергов с поддержкой множества токенов.
    
    === ОБНОВЛЕНИЕ: Мульти-токен поддержка (Task: Multi-Asset Support) ===
    Больше не использует @staticmethod. Каждый экземпляр инициализируется
    с конкретным AssetConfig для адаптации к BTC/ETH/SOL и т.д.
    """
    
    def __init__(self, config: AssetConfig):
        """
        WHY: Храним config для доступа к параметрам токена.
        
        Args:
            config: Конфигурация актива (BTC_CONFIG, ETH_CONFIG и т.д.)
        """
        self.config = config
    
    def analyze(self, book: LocalOrderBook, trade: TradeEvent, visible_before: Decimal) -> Optional[IcebergDetectedEvent]:
        
        # --- 1. ФИЛЬТРЫ ШУМА ---
        
        # WHY: Фильтр "пыли" из config (для ETH/SOL пороги другие)
        if visible_before < self.config.dust_threshold: 
            return None

        # Если сделка меньше видимого объема -> скрытой части точно не было
        if trade.quantity <= visible_before: 
            return None

        # --- 2. РАСЧЕТ АЙСБЕРГА ---
        # (Блок проверки visible_after УДАЛЕН, так как он не работает в real-time без задержки)

        hidden_volume = trade.quantity - visible_before
        
        # Рассчитываем соотношение скрытого объема к размеру сделки
        if trade.quantity > 0:
             iceberg_ratio = hidden_volume / trade.quantity
        else:
             iceberg_ratio = Decimal("0")

        # WHY: Пороги из config (для ETH = 1.0, для SOL = 10.0)
        if hidden_volume > self.config.min_hidden_volume and iceberg_ratio > self.config.min_iceberg_ratio:
            
            # Определяем направление (True если это BID/Поддержка)
            is_ask_iceberg = not trade.is_buyer_maker 

            # Динамическая уверенность: чем больше Ratio, тем мы увереннее
            # Но не больше 0.95 (всегда есть шанс ошибки)
            dynamic_confidence = float(min(iceberg_ratio, Decimal("0.95")))

            # --- 3. ЗАПОМИНАЕМ В РЕЕСТР ---
            iceberg_lvl = book.register_iceberg(
                price=trade.price,
                hidden_vol=hidden_volume,
                is_ask=is_ask_iceberg,
                confidence=dynamic_confidence
            )
            
            # НОВОЕ: Инкрементируем счетчик рефиллов
            iceberg_lvl.refill_count += 1
            
            return IcebergDetectedEvent(
                symbol=book.symbol,
                price=trade.price,
                detected_hidden_volume=hidden_volume,
                visible_volume_before=visible_before,
                confidence=iceberg_lvl.confidence_score
            )
        
        return None
    
    def analyze_with_timing(
        self,
        book: LocalOrderBook,
        trade: TradeEvent,
        visible_before: Decimal,
        delta_t_ms: int,
        update_time_ms: int
    ) -> Optional[IcebergDetectedEvent]:
        """
        WHY: Анализ с учетом временной валидации (Delta-t).
        
        Различает биржевой refill (5-30ms) от нового ордера маркет-мейкера (50-500ms)
        на основе математической модели P(Refill|Δt) = 1 / (1 + e^(α(Δt - τ))).
        
        Args:
            book: Локальный стакан
            trade: Событие сделки
            visible_before: Видимый объем ДО trade
            delta_t_ms: Время между trade и update (в миллисекундах)
            update_time_ms: Timestamp update события (для логирования)
        
        Returns:
            IcebergDetectedEvent если найден РЕАЛЬНЫЙ айсберг, иначе None
        """
        
        # --- 1. КОНСТАНТЫ ВРЕМЕННОЙ ВАЛИДАЦИИ ---
        # WHY: Эмпирические значения для Binance Spot (cite: теор. документ раздел 1.2)
        MAX_REFILL_DELAY_MS = 50  # Жесткая граница для Public API
        CUTOFF_MS = 30  # τ_cutoff - точка перехода сигмоиды
        ALPHA = 0.15  # Коэффициент крутизны (чувствительность модели)
        MIN_REFILL_PROBABILITY = 0.6  # Минимальная уверенность для классификации
        
        # --- 2. ФИЛЬТР ВРЕМЕННОЙ ВАЛИДАЦИИ (КРИТИЧНО) ---
        
        # Race condition: update пришел раньше trade (сетевая аномалия)
        # ЛЮБАЯ отрицательная задержка подозрительна
        if delta_t_ms < 0:
            return None
        
        # ЖЕСТКАЯ ГРАНИЦА: Если delta_t > 50ms → точно НЕ refill
        if delta_t_ms > MAX_REFILL_DELAY_MS:
            return None
        
        # Вычисляем вероятность refill (сигмоида)
        # P(Refill|Δt) = 1 / (1 + e^(α(Δt - τ)))
        from math import exp
        
        exponent = ALPHA * (delta_t_ms - CUTOFF_MS)
        
        # Защита от overflow (важно для стабильности)
        if exponent > 50:
            refill_probability = 0.0
        elif exponent < -50:
            refill_probability = 1.0
        else:
            refill_probability = 1.0 / (1.0 + exp(exponent))
        
        # МЯГКАЯ ГРАНИЦА: Если вероятность < 0.6 → недостаточно уверенности
        if refill_probability < MIN_REFILL_PROBABILITY:
            return None
        
        # --- 3. ОСТАЛЬНЫЕ ФИЛЬТРЫ (ИЗ БАЗОВОГО МЕТОДА) ---
        
        # WHY: Фильтр "пыли" из config (для ETH/SOL пороги другие)
        if visible_before < self.config.dust_threshold:
            return None
        
        # Если сделка меньше видимого объема -> скрытой части точно не было
        if trade.quantity <= visible_before:
            return None
        
        hidden_volume = trade.quantity - visible_before
        
        # Рассчитываем соотношение скрытого объема к размеру сделки
        if trade.quantity > 0:
            iceberg_ratio = hidden_volume / trade.quantity
        else:
            iceberg_ratio = Decimal("0")
        
        # WHY: Пороги из config (для ETH = 1.0, для SOL = 10.0)
        if hidden_volume > self.config.min_hidden_volume and iceberg_ratio > self.config.min_iceberg_ratio:
            
            # Определяем направление
            is_ask_iceberg = not trade.is_buyer_maker
            
            # --- 4. МОДИФИЦИРОВАННАЯ УВЕРЕННОСТЬ (УЧИТЫВАЕМ DELTA-T) ---
            # WHY: Объединяем уверенность от объема И от времени
            
            # Базовая уверенность от объема (как в старом методе)
            volume_confidence = float(min(iceberg_ratio, Decimal("0.95")))
            
            # Базовая уверенность = volume_confidence * timing_confidence
            # Пример: volume=0.8, timing=0.9 → base=0.72
            base_confidence = volume_confidence * refill_probability
            
            # === НОВОЕ: GEX-ADJUSTMENT ===
            # Модифицируем уверенность на основе Gamma Exposure
            dynamic_confidence, is_major_gamma = self.adjust_confidence_by_gamma(
                base_confidence=base_confidence,
                gamma_profile=book.gamma_profile,
                price=trade.price,
                is_ask=is_ask_iceberg
            )
            
            # Если это major gamma event - логируем
            if is_major_gamma:
                print(f"🌊 [GAMMA ALERT] Айсберг на MAJOR GAMMA LEVEL @ {trade.price}")
            
            # --- 5. РЕГИСТРАЦИЯ В РЕЕСТРЕ ---
            iceberg_lvl = book.register_iceberg(
                price=trade.price,
                hidden_vol=hidden_volume,
                is_ask=is_ask_iceberg,
                confidence=dynamic_confidence
            )
            
            # Инкрементируем счетчик рефиллов
            iceberg_lvl.refill_count += 1
            
            return IcebergDetectedEvent(
                symbol=book.symbol,
                price=trade.price,
                detected_hidden_volume=hidden_volume,
                visible_volume_before=visible_before,
                confidence=dynamic_confidence  # Уже учитывает GEX-adjustment
            )
        
        return None

    def adjust_confidence_by_gamma(
        self,
        base_confidence: float,
        gamma_profile: Optional[GammaProfile],
        price: Decimal,
        is_ask: bool
    ) -> Tuple[float, bool]:
        """
        WHY: Модифицирует уверенность на основе GEX-контекста.
        
        Теория (документация "Анализ данных смарт-мани", раздел 4.1):
        - Положительная Гамма (+GEX): Дилеры гасят волатильность → айсберги на gamma_wall КРАЙНЕ надежны
        - Отрицательная Гамма (-GEX): Gamma Squeeze → айсберги менее стабильны
        - Пробой gamma_wall = major structural event
        
        Args:
            base_confidence: Исходная уверенность из analyze_with_timing()
            gamma_profile: Текущий профиль гаммы от Deribit (может быть None)
            price: Цена айсберга
            is_ask: True если Ask (сопротивление), False если Bid (поддержка)
        
        Returns:
            Tuple[adjusted_confidence, is_major_gamma_event]
        """
        
        # Если нет данных от Deribit - возвращаем без изменений
        if gamma_profile is None:
            return base_confidence, False
        
        adjusted = base_confidence
        is_major_event = False
        
        # 1. Проверяем близость к Gamma Walls
        price_float = float(price)
        # WHY: Используем процентный толеранс из config (адаптируется к цене)
        TOLERANCE = price_float * float(self.config.gamma_wall_tolerance_pct)
        
        # 2. Определяем, стоим ли мы на стене
        on_call_wall = abs(price_float - gamma_profile.call_wall) < TOLERANCE
        on_put_wall = abs(price_float - gamma_profile.put_wall) < TOLERANCE
        
        is_on_gamma_wall = on_call_wall or on_put_wall
        
        # 3. ПОЛОЖИТЕЛЬНАЯ ГАММА: Дилеры гасят волатильность
        if gamma_profile.total_gex > 0:
            if is_on_gamma_wall:
                # Айсберг НА gamma wall при +GEX = максимальная надежность
                adjusted = base_confidence * 1.8  # x1.8 multiplier
                is_major_event = True
            else:
                # Обычный айсберг при +GEX = умеренное повышение
                adjusted = base_confidence * 1.2  # x1.2 multiplier
        
        # 4. ОТРИЦАТЕЛЬНАЯ ГАММА: Gamma Squeeze режим
        elif gamma_profile.total_gex < 0:
            if is_on_gamma_wall:
                # Айсберг на gamma wall при -GEX = все еще значим, но менее надежен
                adjusted = base_confidence * 1.3  # x1.3 (меньше чем при +GEX)
                is_major_event = True
            else:
                # Обычный айсберг при -GEX = снижение надежности
                adjusted = base_confidence * 0.75  # x0.75 (рынок нестабилен)
        
        # 5. Обрезаем до [0.0, 1.0]
        adjusted = max(0.0, min(1.0, adjusted))
        
        return adjusted, is_major_event

class WhaleAnalyzer:
    """
    Анализатор потока сделок с поддержкой множества токенов.
    Классифицирует участников (Киты/Дельфины/Рыбы) и детектит Алгоритмы.
    
    === ОБНОВЛЕНИЕ: Мульти-токен поддержка + Динамические пороги ===
    Больше не использует @staticmethod. Пороги рассчитываются на основе 
    перцентилей последних 1000 сделок + используют config для адаптации.
    """
    
    def __init__(self, config: AssetConfig):
        """
        WHY: Храним config для доступа к fallback-порогам и floor-значениям.
        
        Args:
            config: Конфигурация актива (BTC_CONFIG, ETH_CONFIG и т.д.)
        """
        self.config = config
        # WHY: Минимальное количество сделок для перцентильного анализа
        self.MIN_SAMPLES_FOR_DYNAMIC = 100
    
    def update_stats(self, book: LocalOrderBook, trade: TradeEvent) -> tuple[str, float, bool]:
        """
        WHY: Классификация сделок с динамической адаптацией к волатильности.
        
        Теория (документ "Анализ данных смарт-мани", раздел 3.1):
        - Статические пороги ($100k) ломаются при изменении цены BTC или режима рынка
        - Перцентильный подход: 95-й перцентиль = киты, 20-й = рыбы
        - Адаптация к дроблению ордеров во флэте
        
        Returns:
            category (str): 'whale', 'dolphin', 'minnow'
            volume_usd (float): объем сделки в $
            algo_detected (bool): True, если найден алгоритм
        """
        # 1. Считаем объем в долларах
        price_flt = float(trade.price)
        qty_flt = float(trade.quantity)
        volume_usd = price_flt * qty_flt
        
        # 2. Добавляем в историю ДО классификации (для будущих калибровок)
        book.trade_size_history.append(volume_usd)
        
        # 3. Определяем направление (True = Sell)
        is_sell = trade.is_buyer_maker
        signed_vol = -volume_usd if is_sell else volume_usd
        
        # 4. ДИНАМИЧЕСКАЯ КАЛИБРОВКА ПОРОГОВ
        whale_threshold, minnow_threshold = self._calculate_dynamic_thresholds(book)
        
        # 5. Сегментация с динамическими порогами
        category = 'dolphin'  # default
        if volume_usd > whale_threshold:
            category = 'whale'
        elif volume_usd <= minnow_threshold:  # FIX: INCLUSIVE boundary (edge case: volume = threshold)
            category = 'minnow'

        # 6. Обновляем статистику CVD
        book.whale_cvd[category] += signed_vol
        book.trade_count += 1
        
        # ===========================================================================
        # РАСШИРЕННАЯ ALGO DETECTION (Task: Advanced Algo Detection)
        # ===========================================================================
        algo_alert = False
        
        # Нас интересуют только "Рыбы" (алгоритмы дробят заявки на мелкие части)
        if category == 'minnow':
            # 1. Добавляем сделку в окно: (время, направление)
            book.algo_window.append((trade.event_time, is_sell))
            
            # 2. Добавляем в истории для расширенного анализа
            # WHY: Сохраняем объем сделки (в USD) для паттерн-анализа
            book.algo_size_pattern.append(volume_usd)
            
            # 3. Вычисляем временной интервал от ПРЕДЫДУЩЕЙ сделки
            if len(book.algo_window) >= 2:
                # Берем предпоследнюю сделку
                prev_time = book.algo_window[-2][0]
                current_time = trade.event_time
                interval_ms = float(current_time - prev_time)
                
                # Добавляем в историю интервалов
                book.algo_interval_history.append(interval_ms)
            
            # 4. Очищаем старые сделки (старше 60 секунд)
            # WHY: trade.event_time в миллисекундах
            cutoff = trade.event_time - 60000
            
            # КРИТИЧНО: Считаем сколько элементов нужно удалить
            # WHY: Все 3 deque должны удалять ОДИНАКОВОЕ количество
            trades_to_remove = 0
            for timestamp, _ in book.algo_window:
                if timestamp < cutoff:
                    trades_to_remove += 1
                else:
                    break  # Остальные сделки свежие
            
            # DEBUG: Логируем cleanup процесс (только если нужно удалять)
            if trades_to_remove > 0:
                print(f"\n[CLEANUP] cutoff={cutoff}, trades_to_remove={trades_to_remove}")
                print(f"[CLEANUP] Before: window={len(book.algo_window)}, intervals={len(book.algo_interval_history)}, sizes={len(book.algo_size_pattern)}")
            
            # Удаляем синхронно из всех 3 deque
            for i in range(trades_to_remove):
                if book.algo_window:
                    book.algo_window.popleft()
                if book.algo_size_pattern:
                    book.algo_size_pattern.popleft()
            
            # КРИТИЧНО: interval_history удаляем ОТДЕЛЬНО
            # WHY: interval_history всегда на 1 меньше (первая сделка не создает интервал)
            # Если удаляем N trades, нужно удалить min(N, len(interval_history)) intervals
            intervals_to_remove = min(trades_to_remove, len(book.algo_interval_history))
            for _ in range(intervals_to_remove):
                if book.algo_interval_history:
                    book.algo_interval_history.popleft()
            
            # DEBUG: Логируем результат cleanup
            if trades_to_remove > 0:
                print(f"[CLEANUP] After: window={len(book.algo_window)}, intervals={len(book.algo_interval_history)}, sizes={len(book.algo_size_pattern)}")
            
            # 5. ОСНОВНАЯ ПРОВЕРКА: Если набралось >= 200 сделок за минуту
            if len(book.algo_window) >= 200:
                # --- БАЗОВАЯ ПРОВЕРКА НАПРАВЛЕННОСТИ ---
                sell_count = sum(1 for _, side in book.algo_window if side)
                buy_count = len(book.algo_window) - sell_count
                total = len(book.algo_window)
                
                # Рассчитываем соотношение доминирующего направления
                if sell_count > buy_count:
                    directional_ratio = sell_count / total
                    dominant_direction = "SELL"
                else:
                    directional_ratio = buy_count / total
                    dominant_direction = "BUY"
                
                # КРИТЕРИЙ 1: Направленность >= 85% (главный фильтр)
                if directional_ratio >= 0.85:
                    # --- РАСШИРЕННЫЙ АНАЛИЗ ---
                    
                    # Анализируем временной паттерн (для TWAP vs VWAP)
                    std_dev_ms, mean_interval_ms = self._analyze_timing_pattern(book)
                    
                    # Анализируем размерный паттерн (для Iceberg Algo)
                    size_uniformity, dominant_size_usd = self._analyze_size_pattern(book)
                    
                    # Классифицируем тип алгоритма
                    algo_type, confidence = self._classify_algo_type(
                        std_dev_ms=std_dev_ms,
                        mean_interval_ms=mean_interval_ms,
                        size_uniformity=size_uniformity,
                        directional_ratio=directional_ratio
                    )
                    
                    # --- ГЕНЕРАЦИЯ ALERT ---
                    if algo_type is not None:
                        # Формат: "BUY_TWAP" или "SELL_ICEBERG" и т.д.
                        algo_alert = f"{dominant_direction}_{algo_type}"
                        
                        # WHY: Сохраняем метрики для последующего анализа
                        book.last_algo_detection = AlgoDetectionMetrics(
                            std_dev_intervals_ms=std_dev_ms,
                            mean_interval_ms=mean_interval_ms,
                            size_uniformity_score=size_uniformity,
                            dominant_size_usd=dominant_size_usd,
                            directional_ratio=directional_ratio,
                            algo_type=algo_type,
                            confidence=confidence
                        )
                        
                        # Очищаем окна, чтобы не спамить одинаковыми алертами
                        book.algo_window.clear()
                        book.algo_interval_history.clear()
                        book.algo_size_pattern.clear()
                    else:
                        # Если не смогли классифицировать - используем старую логику
                        # WHY: Fallback на "GENERIC_ALGO" если признаки недостаточны
                        if directional_ratio > 0.90:  # Очень высокая направленность
                            algo_alert = f"{dominant_direction}_ALGO"
                            book.algo_window.clear()
                    
        return category, volume_usd, algo_alert
    
    def _calculate_dynamic_thresholds(self, book: LocalOrderBook) -> tuple[float, float]:
        """
        WHY: Рассчитывает динамические пороги на основе перцентилей.
        
        Логика:
        - Если сделок < 100 → fallback к статическим порогам
        - Whale Threshold = 95-й перцентиль (только 5% сделок крупнее)
        - Minnow Threshold = 20-й перцентиль (20% сделок мельче)
        - Сглаживание: минимальный порог $1k для minnow, $10k для whale
        
        Теория:
        - Перцентильный подход адаптируется к волатильности автоматически
        - Во флэте: крупные игроки дробят ордера → 95-й перцентиль падает
        - В тренде: агрессивные покупки → 95-й перцентиль растет
        
        Returns:
            Tuple[whale_threshold, minnow_threshold] в USD
        """
        history = list(book.trade_size_history)  # Копия для безопасности
        
        # Fallback: недостаточно данных
        # WHY: Используем fallback-пороги из config (адаптированы под токен)
        if len(history) < self.MIN_SAMPLES_FOR_DYNAMIC:
            return (
                self.config.static_whale_threshold_usd,
                self.config.static_minnow_threshold_usd
            )
        
        # Расчет перцентилей
        import statistics
        
        # 95-й перцентиль = Киты (только 5% крупнее)
        whale_threshold = statistics.quantiles(history, n=20)[18]  # 19-ый из 20 ≈ 95%
        
        # 20-й перцентиль = Рыбы (20% мельче)
        minnow_threshold = statistics.quantiles(history, n=5)[0]  # 1-й из 5 = 20%
        
        # СГЛАЖИВАНИЕ: защита от экстремальных значений
        # WHY: Предотвращаем классификацию $100 как "whale" или $1M как "minnow"
        
        # WHY: Floor-значения из config (для ETH/SOL другие)
        whale_threshold = max(whale_threshold, self.config.min_whale_floor_usd)
        minnow_threshold = max(minnow_threshold, self.config.min_minnow_floor_usd)
        
        # Whale ДОЛЖЕН быть больше Minnow (санити)
        if whale_threshold <= minnow_threshold:
            whale_threshold = minnow_threshold * 10.0
        
        return whale_threshold, minnow_threshold
    
    # ===========================================================================
    # НОВЫЕ МЕТОДЫ: Расширенная Algo Detection (Task: Advanced Algo Detection)
    # ===========================================================================
    
    def _analyze_timing_pattern(self, book: LocalOrderBook) -> tuple[float, float]:
        """
        WHY: Анализирует временные интервалы между сделками для различения TWAP/VWAP.
        
        Теория (документ "Идентификация айсберг-ордеров", раздел 1.2):
        - TWAP: σ_Δt очень низкая (<10% от mean) - робот исполняет равномерно
        - VWAP: σ_Δt средняя (20-50% от mean) - робот синхронизируется с объемом
        - Обычная торговля: σ_Δt высокая (>50% от mean) - хаотичная активность
        
        PERFORMANCE OPTIMIZATION (Gemini замечание):
        - Избегаем list(deque) копирования (узкое место при 1000 TPS)
        - Используем прямую итерацию по deque для вычисления mean/stdev
        
        Args:
            book: Локальный стакан с algo_interval_history
        
        Returns:
            Tuple[std_dev_ms, mean_ms] - стандартное отклонение и среднее
        """
        # Если недостаточно данных - возвращаем нули
        n = len(book.algo_interval_history)
        if n < 10:
            return 0.0, 0.0
        
        # OPTIMIZATION: Прямой расчет без копирования списка
        # WHY: list(deque) занимает O(N) времени и памяти
        # Прямая итерация по deque быстрее (O(1) память)
        
        # Рассчитываем mean напрямую
        sum_intervals = sum(book.algo_interval_history)
        mean_interval = sum_intervals / n
        
        # Защита от деления на 0
        if mean_interval == 0.0:
            return 0.0, 0.0
        
        # Рассчитываем variance напрямую (Welford's algorithm)
        # WHY: Избегаем двойного прохода (как в statistics.stdev)
        if n >= 2:
            sum_squared_diffs = sum((x - mean_interval) ** 2 for x in book.algo_interval_history)
            variance = sum_squared_diffs / (n - 1)  # Sample variance
            std_dev = variance ** 0.5
        else:
            std_dev = 0.0
        
        return std_dev, mean_interval
    
    def _analyze_size_pattern(self, book: LocalOrderBook) -> tuple[float, Optional[float]]:
        """
        WHY: Анализирует паттерн размеров сделок для детекции Iceberg Algo.
        
        Iceberg Algo использует ФИКСИРОВАННЫЙ display_qty (например, всегда 0.01 BTC).
        Это легко обнаружить через анализ частоты доминирующего размера.
        
        Теория:
        - Iceberg: 90%+ сделок одного размера (size_uniformity_score > 0.9)
        - TWAP/VWAP: Размеры могут варьироваться (60-80%)
        - Обычная торговля: Хаотичные размеры (<50%)
        
        Args:
            book: Локальный стакан с algo_size_pattern
        
        Returns:
            Tuple[uniformity_score, dominant_size_usd]
            - uniformity_score: 0.0-1.0 (1.0 = все сделки одинаковые)
            - dominant_size_usd: Наиболее частый размер сделки (None если паттерна нет)
        """
        from collections import Counter
        
        # Если недостаточно данных
        if len(book.algo_size_pattern) < 10:
            return 0.0, None
        
        # Округляем размеры до 2 знаков (чтобы 1000.01 и 1000.02 считались одинаковыми)
        # WHY: Защита от float precision errors
        rounded_sizes = [round(size, 2) for size in book.algo_size_pattern]
        
        # Подсчитываем частоту каждого размера
        size_counts = Counter(rounded_sizes)
        
        # Находим доминирующий размер
        most_common_size, most_common_count = size_counts.most_common(1)[0]
        
        # Вычисляем score uniformity = count(dominant) / total
        total_trades = len(rounded_sizes)
        uniformity_score = most_common_count / total_trades
        
        return uniformity_score, float(most_common_size)
    
    def _classify_algo_type(
        self,
        std_dev_ms: float,
        mean_interval_ms: float,
        size_uniformity: float,
        directional_ratio: float
    ) -> tuple[Optional[str], float]:
        """
        WHY: Классифицирует тип алгоритма на основе метрик.
        
        Решающее дерево (ПРИОРИТЕТ СВЕРХУ ВНИЗ):
        1. Проверка направленности (directional_ratio > 0.85) - обязательно для algo
        2. Если size_uniformity > 0.9 → ICEBERG (фиксированный размер)
        3. Если mean_interval < 50ms → SWEEP (агрессивный, ДОЛЖЕН БЫТЬ ПЕРЕД TWAP/VWAP!)
        4. Если σ/μ < 0.10 (низкая дисперсия) → TWAP
        5. Если 0.10 < σ/μ < 0.50 → VWAP
        6. Иначе → None (недостаточно признаков)
        
        Args:
            std_dev_ms: Стандартное отклонение интервалов
            mean_interval_ms: Среднее время между сделками
            size_uniformity: Score однородности размеров (0.0-1.0)
            directional_ratio: Процент сделок в доминирующем направлении
        
        Returns:
            Tuple[algo_type, confidence]
            - algo_type: 'TWAP', 'VWAP', 'ICEBERG', 'SWEEP', или None
            - confidence: 0.0-1.0
        """
        
        # КРИТЕРИЙ 0: Направленность (главный фильтр)
        # Если сделки в разные стороны - это не алгоритм
        if directional_ratio < 0.85:
            return None, 0.0
        
        # КРИТЕРИЙ 1: Iceberg Algo (НАИВЫСШИЙ ПРИОРИТЕТ)
        # WHY: Iceberg - самый явный паттерн (все сделки одинаковые)
        if size_uniformity > 0.90:
            # Confidence = размерная однородность * направленность
            confidence = (size_uniformity + directional_ratio) / 2.0
            return 'ICEBERG', confidence
        
        # Защита от деления на 0
        if mean_interval_ms == 0.0:
            return None, 0.0
        
        # КРИТЕРИЙ 2: SWEEP (ВТОРОЙ ПРИОРИТЕТ - ПРОВЕРЯЕТСЯ ДО CV!)
        # WHY: SWEEP имеет очень короткие интервалы И может иметь любой CV
        # Если проверять после CV, то SWEEP с mean=16ms может попасть в VWAP!
        if mean_interval_ms < 50.0:
            # Confidence базируется на скорости
            speed_score = 1.0 - (mean_interval_ms / 50.0)  # Чем быстрее, тем выше
            confidence = (speed_score + directional_ratio) / 2.0
            return 'SWEEP', confidence
        
        # Коэффициент вариации (CV) = σ / μ
        cv = std_dev_ms / mean_interval_ms
        
        # КРИТЕРИЙ 3: TWAP (очень низкая дисперсия)
        # WHY: TWAP = равномерные интервалы (~const)
        if cv < 0.10:
            # Confidence базируется на стабильности интервалов
            interval_stability = 1.0 - cv  # Чем меньше CV, тем выше стабильность
            confidence = (interval_stability + directional_ratio) / 2.0
            return 'TWAP', confidence
        
        # КРИТЕРИЙ 4: VWAP (средняя дисперсия)
        # WHY: VWAP адаптируется к волатильности, но не хаотичен
        if 0.10 <= cv < 0.50:
            # Confidence падает с ростом CV
            volatility_adaptation = 1.0 - (cv - 0.10) / 0.40  # Normalize [0.1-0.5] -> [1.0-0.0]
            confidence = (volatility_adaptation + directional_ratio) / 2.0
            return 'VWAP', confidence
        
        # Если ничего не подошло - недостаточно признаков
        return None, 0.0


# ===========================================================================
# НОВЫЙ КЛАСС: SpoofingAnalyzer (Task 1.2)
# ===========================================================================

class SpoofingAnalyzer:
    """
    WHY: Многоуровневая система детекции спуфинга.
    
    Использует временной, поведенческий и статистический анализ для определения
    вероятности того, что айсберг является манипуляцией (спуфингом).
    
    Методы:
    - calculate_spoofing_probability: Главная функция (0.0-1.0)
    - _analyze_duration: Анализ времени жизни (30% веса)
    - _analyze_cancellation_context: Анализ контекста отмены (50% веса)
    - _analyze_execution_pattern: Анализ паттерна исполнения (20% веса)
    """
    
    # Константы для весов
    WEIGHT_DURATION = 0.3
    WEIGHT_CANCELLATION = 0.5
    WEIGHT_EXECUTION = 0.2
    
    @staticmethod
    def calculate_spoofing_probability(
        iceberg_level: IcebergLevel,
        current_mid_price: Decimal,
        price_history: List[Decimal]  # Последние 10 секунд
    ) -> float:
        """
        WHY: Вычисляет вероятность спуфинга (0.0-1.0)
        
        Алгоритм:
        1. Временной анализ (30% веса) - айсберги <5 сек = спуфинг
        2. Анализ отмены при приближении (50% веса) - главный индикатор
        3. Анализ паттерна исполнения (20% веса) - низкий execution % = спуфинг
        
        Args:
            iceberg_level: Айсберг для анализа
            current_mid_price: Текущая средняя цена
            price_history: История цен за последние 10 секунд
            
        Returns:
            Вероятность спуфинга от 0.0 (реальный уровень) до 1.0 (точно спуфинг)
        """
        
        # 1. Временной анализ (30%)
        duration_score = SpoofingAnalyzer._analyze_duration(iceberg_level)
        
        # 2. Анализ контекста отмены (50%)
        cancellation_score = SpoofingAnalyzer._analyze_cancellation_context(
            iceberg_level, current_mid_price, price_history
        )
        
        # 3. Анализ паттерна исполнения (20%)
        execution_score = SpoofingAnalyzer._analyze_execution_pattern(iceberg_level)
        
        # Взвешенная сумма
        total_score = (
            duration_score * SpoofingAnalyzer.WEIGHT_DURATION +
            cancellation_score * SpoofingAnalyzer.WEIGHT_CANCELLATION +
            execution_score * SpoofingAnalyzer.WEIGHT_EXECUTION
        )
        
        # Обрезаем до [0.0, 1.0]
        return max(0.0, min(1.0, total_score))
    
    @staticmethod
    def _analyze_duration(iceberg_level: IcebergLevel) -> float:
        """
        WHY: Короткоживущие айсберги (<5 сек) - это почти всегда спуфинг
        
        Логика:
        - T_life < 5 секунд  → score = 1.0 (100% спуфинг)
        - T_life < 60 секунд → score = 0.7 (вероятно HFT)
        - T_life < 300 секунд → score = 0.3 (краткосрочный алго)
        - T_life >= 300 секунд → score = 0.0 (свинг-уровень)
        
        Returns:
            Score от 0.0 до 1.0
        """
        from datetime import datetime
        
        lifetime_seconds = (datetime.now() - iceberg_level.creation_time).total_seconds()
        
        if lifetime_seconds < 5:
            return 1.0  # Гарантированно спуфинг
        elif lifetime_seconds < 60:
            return 0.7  # Вероятно HFT-манипуляция
        elif lifetime_seconds < 300:
            return 0.3  # Краткосрочный алго (может быть легитимным)
        else:
            return 0.0  # Долгоживущий = реальный уровень
    
    @staticmethod
    def _analyze_cancellation_context(
        iceberg_level: IcebergLevel,
        current_mid_price: Decimal,
        price_history: List[Decimal]
    ) -> float:
        """
        WHY: Отмена при приближении цены - главный признак спуфинга
        
        Спуфер ставит fake wall, чтобы запугать других трейдеров.
        Когда цена начинает двигаться К этому уровню → он отменяет.
        
        Логика:
        - Нет контекста отмены → score = 0.0 (не можем судить)
        - moving_towards_level = True → score += 0.6
        - distance < 0.5% → score += 0.3
        - volume_executed < 10% → score += 0.1
        
        Returns:
            Score от 0.0 до 1.0
        """
        ctx = iceberg_level.cancellation_context
        
        # Если айсберг еще активен (не отменен) - не можем анализировать
        if ctx is None:
            return 0.0
        
        score = 0.0
        
        # КРИТЕРИЙ 1: Цена двигалась К уровню (+0.6)
        if ctx.moving_towards_level:
            score += 0.6
        
        # КРИТЕРИЙ 2: Цена была близко к уровню (+0.3)
        if abs(float(ctx.distance_from_level_pct)) < 0.5:  # Меньше 0.5%
            score += 0.3
        
        # КРИТЕРИЙ 3: Исполнено очень мало (+0.1)
        if float(ctx.volume_executed_pct) < 10.0:  # Меньше 10% исполнено
            score += 0.1
        
        # КРИТЕРИЙ 4: Высокий процент исполнения СНИЖАЕТ подозрительность
        # Если исполнено >30%, это реальные деньги, а не спуфинг
        if float(ctx.volume_executed_pct) > 30.0:
            # Чем больше исполнено, тем сильнее снижение
            # 30% -> -0.2, 50% -> -0.4, 70% -> -0.6
            reduction = min(0.6, (float(ctx.volume_executed_pct) - 30.0) / 100.0 * 2.0)
            score -= reduction
        
        return max(0.0, min(1.0, score))
    
    @staticmethod
    def _analyze_execution_pattern(iceberg_level: IcebergLevel) -> float:
        """
        WHY: Реальные айсберги активно исполняются, спуфинг - нет
        
        Логика:
        - refill_frequency > 10/мин → score = 0.0 (агрессивный алго, легит)
        - refill_frequency < 1/мин → score = 0.5 (подозрительно мало активности)
        - total_hidden_volume очень маленький → score += 0.3
        
        Returns:
            Score от 0.0 до 1.0
        """
        score = 0.0
        
        # КРИТЕРИЙ 1: Частота рефиллов
        refill_freq = iceberg_level.get_refill_frequency()
        
        if refill_freq > 10.0:
            score = 0.0  # Высокая активность = реальный алго
        elif refill_freq < 1.0:
            score = 0.5  # Низкая активность = подозрительно
        else:
            # Линейная интерполяция между 1 и 10
            score = 0.5 * (1.0 - (refill_freq - 1.0) / 9.0)
        
        # КРИТЕРИЙ 2: Очень маленький общий объем (+0.3)
        if float(iceberg_level.total_hidden_volume) < 0.1:  # < 0.1 BTC
            score += 0.3
        
        return min(1.0, score)
