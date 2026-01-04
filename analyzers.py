from decimal import Decimal
from typing import Optional, List, Tuple
from domain import LocalOrderBook, TradeEvent, IcebergLevel, CancellationContext, GammaProfile, AlgoDetectionMetrics, VolumeBucket
from events import IcebergDetectedEvent
# WHY: Импорт конфигурации для мульти-токен поддержки (Task: Multi-Asset Support)
from config import AssetConfig
import asyncio  # WHY: Gemini recommendation - Thread Safety для кеша
import logging  # WHY: Gemini recommendation - Memory Management логирование
from datetime import datetime, timedelta  # WHY: Для cleanup task

class RegimeAdapter:
    """Dynamic threshold adjustment based on spread volatility."""
    
    @staticmethod
    def calculate_volatility_factor(
        current_spread: float,
        mean_spread: float,
        std_spread: float
    ) -> float:
        """Z-score capped at [0.0, 3.0]."""
        if std_spread == 0:
            return 0.0
        z_score = (current_spread - mean_spread) / std_spread
        return max(0.0, min(3.0, z_score))
    
    @staticmethod
    def get_dynamic_native_limit(base_ms: float, vol_factor: float) -> float:
        """Exponential scaling: base * exp(vol/2), capped at 12ms."""
        import math
        scaled = base_ms * math.exp(vol_factor / 2)
        return min(12.0, scaled)
    
    @staticmethod
    def get_dynamic_ratio(base_ratio: float, vol_factor: float) -> float:
        """Linear reduction: base * (1 - vol/5), floored at 0.10."""
        scaled = base_ratio * (1 - vol_factor / 5)
        return max(0.10, scaled)

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
        update_time_ms: int,
        vpin_score: Optional[float] = None,
        cvd_divergence: Optional[dict] = None
    ) -> Optional[IcebergDetectedEvent]:
        """
        WHY: Анализ с учетом временной валидации (Delta-t).
        
        === GEMINI FIX: Native vs Synthetic Split ===
        Теперь использует РАЗНЫЕ пути детекции:
        - Native (delta_t ≤ 5ms): Детерминированный (confidence=1.0)
        - Synthetic (5ms < delta_t ≤ 50ms): Стохастический (sigmoid)
        
        Теория: Документ "Идентификация айсберг-ордеров", раздел 1.2
        
        Args:
            book: Локальный стакан
            trade: Событие сделки
            visible_before: Видимый объем ДО trade
            delta_t_ms: Время между trade и update (в миллисекундах)
            update_time_ms: Timestamp update события (для логирования)
            vpin_score: VPIN токсичность потока (опционально)
            cvd_divergence: CVD дивергенция из AccumulationDetector (опционально)
        
        Returns:
            IcebergDetectedEvent если найден айсберг, иначе None
        """
        
        # --- 1. ФИЛЬТР RACE CONDITION ---
        # Race condition: update пришел раньше trade (сетевая аномалия)
        if delta_t_ms < 0:
            return None

        if book.spread_mean and book.spread_std:
            current_spread = float(book.get_spread() or 0)
            vol_factor = RegimeAdapter.calculate_volatility_factor(
                current_spread, book.spread_mean, book.spread_std
            )
            native_refill_max = RegimeAdapter.get_dynamic_native_limit(
                self.config.native_refill_max_ms, vol_factor
            )
            min_iceberg_ratio = RegimeAdapter.get_dynamic_ratio(
                self.config.min_iceberg_ratio, vol_factor
            )
        else:
            # Fallback to static config values
            native_refill_max = self.config.native_refill_max_ms
            min_iceberg_ratio = self.config.min_iceberg_ratio
        
        # --- 2. EARLY EXIT PATTERN: РАЗДЕЛЕНИЕ NATIVE vs SYNTHETIC ---
        # WHY: Используем config для адаптации под токен (BTC/ETH/SOL разные пороги)
        
        if delta_t_ms <= self.config.native_refill_max_ms:
            # NATIVE PATH: Биржевой refill (детерминированный)
            return self._analyze_native(
                book=book,
                trade=trade,
                visible_before=visible_before,
                delta_t_ms=delta_t_ms,
                vpin_score=vpin_score,
                cvd_divergence=cvd_divergence
            )
        
        elif delta_t_ms <= self.config.synthetic_refill_max_ms:
            # SYNTHETIC PATH: API бот (стохастический, sigmoid)
            return self._analyze_synthetic(
                book=book,
                trade=trade,
                visible_before=visible_before,
                delta_t_ms=delta_t_ms,
                vpin_score=vpin_score,
                cvd_divergence=cvd_divergence
            )
        
        else:
            # TOO SLOW: delta_t > synthetic_max → точно не refill
            return None
    
    def _analyze_native(
        self,
        book: LocalOrderBook,
        trade: TradeEvent,
        visible_before: Decimal,
        delta_t_ms: int,
        vpin_score: Optional[float] = None,
        cvd_divergence: Optional[dict] = None
    ) -> Optional[IcebergDetectedEvent]:
        """
        WHY: NATIVE PATH - биржевой refill (100μs-10ms).
        
        === GEMINI FIX: Детерминированная детекция ===
        Для Native рефиллов используется confidence=1.0 (без sigmoid).
        
        Теория: Биржевой матчинг-движок (Binance Spot) обрабатывает refill
        детерминированно. Если delta_t ≤ 5ms → это НЕ API roundtrip.
        
        Args:
            book, trade, visible_before: Стандартные параметры
            delta_t_ms: Уже проверено <= native_refill_max_ms
            vpin_score, cvd_divergence: Для GEX adjustments
        
        Returns:
            IcebergDetectedEvent или None
        """
        # --- ФИЛЬТРЫ ШУМА ---
        if visible_before < self.config.dust_threshold:
            return None
        
        if trade.quantity <= visible_before:
            return None
        
        hidden_volume = trade.quantity - visible_before
        
        if trade.quantity > 0:
            iceberg_ratio = hidden_volume / trade.quantity
        else:
            iceberg_ratio = Decimal("0")
        
        # WHY: Проверяем пороги из config
        if hidden_volume <= self.config.min_hidden_volume or iceberg_ratio <= self.config.min_iceberg_ratio:
            return None
        
        # --- ДЕТЕРМИНИРОВАННАЯ УВЕРЕННОСТЬ (NATIVE) ---
        # WHY: Native refill = биржевой механизм, НЕ API бот
        # Confidence = 1.0 (максимальная уверенность)
        
        is_ask_iceberg = not trade.is_buyer_maker
        
        # Для Native: базовая confidence = 1.0 (детерминированный)
        base_confidence = 1.0
        
        # --- GEX/VPIN ADJUSTMENTS (общий код для Native и Synthetic) ---
        cvd_tuple = None
        if cvd_divergence is not None:
            cvd_tuple = (
                True,
                cvd_divergence.get('type', 'BULLISH'),
                cvd_divergence.get('confidence', 0.0)
            )
        
        dynamic_confidence, is_major_gamma = self.adjust_confidence_by_gamma(
            base_confidence=base_confidence,
            gamma_profile=book.gamma_profile,
            price=trade.price,
            is_ask=is_ask_iceberg,
            vpin_score=vpin_score,
            cvd_divergence=cvd_tuple
        )
        
        if is_major_gamma:
            print(f"🌊 [NATIVE GAMMA] Айсберг на MAJOR GAMMA LEVEL @ {trade.price}")
        
        # --- РЕГИСТРАЦИЯ В РЕЕСТРЕ ---
        iceberg_lvl = book.register_iceberg(
            price=trade.price,
            hidden_vol=hidden_volume,
            is_ask=is_ask_iceberg,
            confidence=dynamic_confidence
        )
        iceberg_lvl.refill_count += 1
        
        return IcebergDetectedEvent(
            symbol=book.symbol,
            price=trade.price,
            detected_hidden_volume=hidden_volume,
            visible_volume_before=visible_before,
            confidence=dynamic_confidence
        )
    
    def _analyze_synthetic(
        self,
        book: LocalOrderBook,
        trade: TradeEvent,
        visible_before: Decimal,
        delta_t_ms: int,
        vpin_score: Optional[float] = None,
        cvd_divergence: Optional[dict] = None
    ) -> Optional[IcebergDetectedEvent]:
        """
        WHY: SYNTHETIC PATH - API бот (10ms-50ms).
        
        === GEMINI FIX: Стохастическая детекция ===
        Для Synthetic используется sigmoid для вероятности refill.
        
        Теория: API боты имеют network latency (10-50ms).
        Sigmoid модель: P(Refill|Δt) = 1 / (1 + e^(α(Δt - τ)))
        
        Args:
            book, trade, visible_before: Стандартные параметры
            delta_t_ms: Уже проверено: native_max < delta_t <= synthetic_max
            vpin_score, cvd_divergence: Для GEX adjustments
        
        Returns:
            IcebergDetectedEvent или None
        """
        # --- ФИЛЬТРЫ ШУМА ---
        if visible_before < self.config.dust_threshold:
            return None
        
        if trade.quantity <= visible_before:
            return None
        
        hidden_volume = trade.quantity - visible_before
        
        if trade.quantity > 0:
            iceberg_ratio = hidden_volume / trade.quantity
        else:
            iceberg_ratio = Decimal("0")
        
        if hidden_volume <= self.config.min_hidden_volume or iceberg_ratio <= self.config.min_iceberg_ratio:
            return None
        
        # --- СТОХАСТИЧЕСКАЯ УВЕРЕННОСТЬ (SYNTHETIC) ---
        # WHY: Используем sigmoid для вычисления P(Refill|Δt)
        
        from math import exp
        
        # Параметры из config (адаптированы под токен)
        CUTOFF_MS = self.config.synthetic_cutoff_ms  # τ (точка P=0.5)
        ALPHA = self.config.synthetic_probability_decay  # α (крутизна)
        
        exponent = ALPHA * (delta_t_ms - CUTOFF_MS)
        
        # Защита от overflow
        if exponent > 50:
            refill_probability = 0.0
        elif exponent < -50:
            refill_probability = 1.0
        else:
            refill_probability = 1.0 / (1.0 + exp(exponent))
        
        # WHY: Для Synthetic минимальная вероятность = 0.2 (20%)
        # Если меньше - слишком неуверенны
        if refill_probability < 0.2:
            return None
        
        is_ask_iceberg = not trade.is_buyer_maker
        
        # Базовая уверенность от объема
        volume_confidence = float(min(iceberg_ratio, Decimal("0.95")))
        
        # Для Synthetic: base = volume * timing
        base_confidence = volume_confidence * refill_probability
        
        # --- GEX/VPIN ADJUSTMENTS (общий код) ---
        cvd_tuple = None
        if cvd_divergence is not None:
            cvd_tuple = (
                True,
                cvd_divergence.get('type', 'BULLISH'),
                cvd_divergence.get('confidence', 0.0)
            )
        
        dynamic_confidence, is_major_gamma = self.adjust_confidence_by_gamma(
            base_confidence=base_confidence,
            gamma_profile=book.gamma_profile,
            price=trade.price,
            is_ask=is_ask_iceberg,
            vpin_score=vpin_score,
            cvd_divergence=cvd_tuple
        )
        
        if is_major_gamma:
            print(f"🌊 [SYNTHETIC GAMMA] Айсберг на MAJOR GAMMA LEVEL @ {trade.price}")
        
        # --- РЕГИСТРАЦИЯ В РЕЕСТРЕ ---
        iceberg_lvl = book.register_iceberg(
            price=trade.price,
            hidden_vol=hidden_volume,
            is_ask=is_ask_iceberg,
            confidence=dynamic_confidence
        )
        iceberg_lvl.refill_count += 1
        
        return IcebergDetectedEvent(
            symbol=book.symbol,
            price=trade.price,
            detected_hidden_volume=hidden_volume,
            visible_volume_before=visible_before,
            confidence=dynamic_confidence
        )

    def adjust_confidence_by_gamma(
        self,
        base_confidence: float,
        gamma_profile: Optional[GammaProfile],
        price: Decimal,
        is_ask: bool,
        vpin_score: Optional[float] = None,
        cvd_divergence: Optional[Tuple[bool, str, float]] = None
    ) -> Tuple[float, bool]:
        """
        WHY: Модифицирует уверенность на основе GEX, VPIN и CVD дивергенций.
        
        === UPDATE: CVD Enhancement (Phase 2) ===
        Теперь учитывает дивергенции Whale CVD для улучшения свинг-трейдинг сигналов.
        
        Теория (документация "Анализ данных смарт-мани"):
        
        ФАЗА 1 - GEX ADJUSTMENT:
        - Положительная Гамма (+GEX): Дилеры гасят волатильность → айсберги на gamma_wall КРАЙНЕ надежны
        - Отрицательная Гамма (-GEX): Gamma Squeeze → айсберги менее стабильны
        - Пробой gamma_wall = major structural event
        
        ФАЗА 2 - VPIN ADJUSTMENT:
        - VPIN > 0.7: Токсичный поток (информированные агрессоры) → СНИЖАЕМ confidence
        - VPIN < 0.3: Шумный поток (розничные) → ПОВЫШАЕМ confidence
        
        ФАЗА 3 - CVD DIVERGENCE ADJUSTMENT (НОВОЕ):
        - BULLISH divergence (цена ↓, whale CVD ↑) + айсберг на BID → УСИЛИВАЕМ (+25%)
        - BEARISH divergence (цена ↑, whale CVD ↓) + айсберг на ASK → УСИЛИВАЕМ (+25%)
        - Айсберг ПРОТИВ дивергенции → СНИЖАЕМ (-15%)
        
        Args:
            base_confidence: Исходная уверенность из analyze_with_timing()
            gamma_profile: Текущий профиль гаммы от Deribit (может быть None)
            price: Цена айсберга
            is_ask: True если Ask (сопротивление), False если Bid (поддержка)
            vpin_score: Текущий VPIN (0.0-1.0), или None если недостаточно данных
            cvd_divergence: Tuple[is_divergence, div_type, confidence] из detect_cvd_divergence()
        
        Returns:
            Tuple[adjusted_confidence, is_major_event]
            - adjusted_confidence: Модифицированная уверенность [0.0-1.0]
            - is_major_event: True если это major event (gamma wall + CVD divergence)
        """
        
        adjusted = base_confidence
        is_major_event = False
        
        # === GEMINI FIX: EXPIRATION DECAY ===
        # WHY: Устраняем "Expiration Cliff" проблему (Friday 08:00 UTC trap)
        decay_factor = 1.0
        if gamma_profile and gamma_profile.expiry_timestamp:
            from datetime import timezone
            # Считаем часы до экспирации
            hours_left = (gamma_profile.expiry_timestamp - datetime.now(timezone.utc)).total_seconds() / 3600
            if 0 < hours_left < 2.0:
                # Линейное затухание: за 2 часа до экспирации влияние падает с 100% до 0%
                decay_factor = hours_left / 2.0
        
        # === ФАЗА 1: GEX ADJUSTMENT (GEMINI FIX: Normalization) ===
        if gamma_profile is not None:
            # FIX VULNERABILITY #4: Decimal-safe comparison
            # WHY: price уже Decimal, gamma_profile.call/put_wall теперь тоже Decimal
            # НЕ конвертируем в float - сравниваем Decimal с Decimal!
            
            # 1. Вычисляем tolerance как Decimal
            # WHY: Используем процентный толеранс из config (адаптируется к цене)
            tolerance_pct = Decimal(str(self.config.gamma_wall_tolerance_pct))
            TOLERANCE = price * tolerance_pct
            
            # 2. Определяем, стоим ли мы на стене (Decimal comparison)
            on_call_wall = abs(price - gamma_profile.call_wall) < TOLERANCE
            on_put_wall = abs(price - gamma_profile.put_wall) < TOLERANCE
            
            is_on_gamma_wall = on_call_wall or on_put_wall
            
            # === GEMINI FIX: GEX NORMALIZATION ===
            # WHY: Используем normalized GEX вместо абсолютного значения
            # Порог 0.1 означает GEX > 10% от дневного объема
            gex_significant = (
                gamma_profile.total_gex_normalized is not None and
                abs(gamma_profile.total_gex_normalized) > 0.1
            )
            
            # Применяем GEX adjustment только если GEX значимый
            if gex_significant:
                # 3. ПОЛОЖИТЕЛЬНАЯ ГАММА: Дилеры гасят волатильность
                if gamma_profile.total_gex > 0:
                    if is_on_gamma_wall:
                        # Айсберг НА gamma wall при +GEX = максимальная надежность
                        # Применяем decay к бонусу: если скоро экспирация, бонус исчезает
                        bonus = 0.8 * decay_factor  # Максимум x1.8 (1.0 + 0.8)
                        adjusted = adjusted * (1.0 + bonus)
                        is_major_event = True
                    else:
                        # Обычный айсберг при +GEX = умеренное повышение
                        bonus = 0.2 * decay_factor  # Максимум x1.2 (1.0 + 0.2)
                        adjusted = adjusted * (1.0 + bonus)
                
                # 4. ОТРИЦАТЕЛЬНАЯ ГАММА: Gamma Squeeze режим
                elif gamma_profile.total_gex < 0:
                    if is_on_gamma_wall:
                        # Айсберг на gamma wall при -GEX = все еще значим, но менее надежен
                        bonus = 0.3 * decay_factor  # Максимум x1.3 (1.0 + 0.3)
                        adjusted = adjusted * (1.0 + bonus)
                        is_major_event = True
                    else:
                        # Обычный айсберг при -GEX = снижение надежности
                        penalty = 0.25 * decay_factor  # Минимум x0.75 (1.0 - 0.25)
                        adjusted = adjusted * (1.0 - penalty)
        
        # === ФАЗА 2: VPIN ADJUSTMENT (НОВОЕ) ===
        if vpin_score is not None:
            # КРИТИЧНО: VPIN применяется ПОСЛЕ GEX adjustment
            # WHY: GEX модифицирует структурный контекст, VPIN - краткосрочный риск
            
            # ТОКСИЧНЫЙ ПОТОК (VPIN > 0.7): Информированные агрессоры
            # Риск пробоя айсберга ВЫСОКИЙ → СНИЖАЕМ confidence
            if vpin_score > 0.7:
                # Чем выше VPIN, тем сильнее снижение
                # 0.7 → x0.85, 0.8 → x0.75, 0.9 → x0.65, 1.0 → x0.55
                toxicity_multiplier = 1.0 - (vpin_score - 0.7) * 1.5  # Linear decay
                toxicity_multiplier = max(0.55, toxicity_multiplier)  # Floor at 0.55
                adjusted = adjusted * toxicity_multiplier
            
            # ШУМНЫЙ ПОТОК (VPIN < 0.3): Розничные трейдеры
            # Айсберг УСТОИТ → ПОВЫШАЕМ confidence
            elif vpin_score < 0.3:
                # Чем ниже VPIN, тем сильнее повышение
                # 0.3 → x1.05, 0.2 → x1.10, 0.1 → x1.15, 0.0 → x1.20
                noise_multiplier = 1.0 + (0.3 - vpin_score) * 0.67  # Linear growth
                noise_multiplier = min(1.20, noise_multiplier)  # Cap at 1.20
                adjusted = adjusted * noise_multiplier
            
            # НЕЙТРАЛЬНЫЙ ПОТОК (0.3 <= VPIN <= 0.7): Не модифицируем
        
        # === ФАЗА 3: CVD DIVERGENCE ADJUSTMENT (НОВОЕ) ===
        if cvd_divergence is not None:
            is_div, div_type, div_confidence = cvd_divergence
            
            if is_div and div_confidence > 0.5:
                # BULLISH DIVERGENCE (накопление): Цена падает, Whale CVD растёт
                # Если айсберг на BID (поддержка) → УСИЛИВАЕМ
                if div_type == 'BULLISH' and not is_ask:
                    cvd_multiplier = 1.0 + (div_confidence * 0.25)  # До +25%
                    adjusted = adjusted * cvd_multiplier
                    is_major_event = True  # CVD дивергенция = major event
                
                # BEARISH DIVERGENCE (дистрибуция): Цена растёт, Whale CVD падает
                # Если айсберг на ASK (сопротивление) → УСИЛИВАЕМ
                elif div_type == 'BEARISH' and is_ask:
                    cvd_multiplier = 1.0 + (div_confidence * 0.25)  # До +25%
                    adjusted = adjusted * cvd_multiplier
                    is_major_event = True
                
                # Если айсберг ПРОТИВ дивергенции → СНИЖАЕМ
                # BULLISH divergence но айсберг на ASK = противоречие
                elif div_type == 'BULLISH' and is_ask:
                    cvd_multiplier = 1.0 - (div_confidence * 0.15)  # До -15%
                    adjusted = adjusted * cvd_multiplier
                
                # BEARISH divergence но айсберг на BID = противоречие
                elif div_type == 'BEARISH' and not is_ask:
                    cvd_multiplier = 1.0 - (div_confidence * 0.15)  # До -15%
                    adjusted = adjusted * cvd_multiplier
        
        # === ФИНАЛИЗАЦИЯ ===
        # Обрезаем до [0.0, 1.0]
        adjusted = max(0.0, min(1.0, adjusted))
        
        return adjusted, is_major_event
    
    def _is_vpin_reliable(self, book: LocalOrderBook) -> bool:
        """
        WHY: Проверяет надежность VPIN в текущих рыночных условиях.
        
        VPIN может давать ложные сигналы в:
        1. Флэте (низкая волатильность) - маркет-мейкеры создают псевдо-имбаланс
        2. Низкой ликвидности (< 100 сделок/мин) - недостаточно данных
        3. Экстремальной волатильности (> 5%) - шум перебивает сигнал
        
        Теория:
        - VPIN из TradFi (Easley 2012) предполагает "нормальные" условия
        - Во флэте VPIN ошибочно показывает "токсичность" от MM-ботов
        - При низкой ликвидности bucket_size слишком велик
        
        Args:
            book: LocalOrderBook с метриками
        
        Returns:
            True если VPIN можно доверять, False если рискованно
        """
        # 1. Проверка ликвидности
        # WHY: Минимум 100 сделок за последнюю минуту для статистики
        if book.trade_count < 100:
            return False  # Недостаточно данных
        
        # 2. Проверка волатильности (защита от флэта)
        # WHY: Во флэте (<1% волатильность) VPIN дает ложные сигналы
        mid_price = book.get_mid_price()
        if mid_price:
            # Простая эвристика: проверяем spread
            if book.best_bid and book.best_ask:
                spread_pct = float((book.best_ask - book.best_bid) / mid_price) * 100
                
                # Если spread < 0.01% = мертвый флэт (для BTC)
                # Адаптируем под токен через config
                min_spread_threshold = 0.01  # 1 basis point
                if spread_pct < min_spread_threshold:
                    return False  # Слишком узкий спред = флэт
        
        # 3. Проверка экстремальной волатильности
        # WHY: При >5% волатильности VPIN перенасыщен шумом
        # TODO: Добавить когда будет реализована volatility_1h в book
        # if hasattr(book, 'volatility_1h') and book.volatility_1h > 5.0:
        #     return False
        
        # 4. Все проверки пройдены
        return True
    
    def classify_intention(self, hidden_volume: Decimal, adv_20d: Optional[Decimal] = None) -> str:
        """
        WHY: Классифицирует айсберг по его размеру относительно рынка (IIR).
        
        Args:
            hidden_volume: Скрытый объем айсберга
            adv_20d: Средний дневной объем (Average Daily Volume)
            
        Returns:
            'SCALPER' | 'INTRADAY' | 'POSITIONAL' | 'UNKNOWN'
        """
        # Защита от отсутствия данных
        if adv_20d is None or adv_20d == 0:
            return "UNKNOWN"
            
        # Расчет Impact Ratio
        iir = hidden_volume / adv_20d
        
        # Эвристики из Research Paper
        if iir < Decimal("0.0001"):  # < 0.01%
            return "SCALPER"    # Шум/Маркет-мейкинг
        elif iir < Decimal("0.001"):  # < 0.1%
            return "INTRADAY"   # Алго-исполнение
        else:
            return "POSITIONAL"  # Smart Money Accumulation (>= 0.1%)

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
    
    === GEMINI FIX: Мульти-токен поддержка ===
    Больше не использует @staticmethod. Использует config для адаптации порогов.
    
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
    
    def __init__(self, config: AssetConfig):
        """
        WHY: GEMINI FIX - Инициализация с конфигурацией актива.
        
        Args:
            config: AssetConfig (BTC_CONFIG, ETH_CONFIG, SOL_CONFIG)
        """
        self.config = config
    
    def calculate_spoofing_probability(
        self,
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
        duration_score = self._analyze_duration(iceberg_level)
        
        # 2. Анализ контекста отмены (50%)
        cancellation_score = self._analyze_cancellation_context(
            iceberg_level, current_mid_price, price_history
        )
        
        # 3. Анализ паттерна исполнения (20%)
        execution_score = self._analyze_execution_pattern(iceberg_level)
        
        # Взвешенная сумма
        total_score = (
            duration_score * SpoofingAnalyzer.WEIGHT_DURATION +
            cancellation_score * SpoofingAnalyzer.WEIGHT_CANCELLATION +
            execution_score * SpoofingAnalyzer.WEIGHT_EXECUTION
        )
        
        # Обрезаем до [0.0, 1.0]
        return max(0.0, min(1.0, total_score))
    
    def _analyze_duration(self, iceberg_level: IcebergLevel) -> float:
        """
        WHY: Короткоживущие айсберги (<5 сек) - это почти всегда спуфинг
        
        === GEMINI FIX: Гладкая функция ===
        Вместо ступенчатой логики используется логарифмическое затухание.
        
        Логика:
        - Формула: score = 1.0 / (1.0 + 0.1 * duration_seconds)
        - Примеры:
          - 4.9 сек → 0.67
          - 5.1 сек → 0.66
          - 60 сек → 0.14
          - 300 сек → 0.03
        
        Returns:
            Score от 0.0 до 1.0 (плавное затухание)
        """
        from datetime import datetime
        
        lifetime_seconds = (datetime.now() - iceberg_level.creation_time).total_seconds()
        
        # === GEMINI FIX: Гладкая функция (логарифмическое затухание) ===
        # Преимущества:
        # - ML-friendly: Нет резких скачков (4.9→0.67, 5.1→0.66)
        # - Быстрое затухание: 60 сек → 0.14 (HFT фильтруется)
        # - Асимптота к 0: 300+ сек → ~0.03 (реальный уровень)
        score = 1.0 / (1.0 + 0.1 * lifetime_seconds)
        
        return score
    
    def _analyze_cancellation_context(
        self,
        iceberg_level: IcebergLevel,
        current_mid_price: Decimal,
        price_history: List[Decimal]
    ) -> float:
        """
        WHY: Отмена при приближении цены - главный признак спуфинга
        
        === GEMINI FIX: Динамический порог близости ===
        Вместо хардкода 0.5% используется config.spoofing_distance_pct.
        
        Спуфер ставит fake wall, чтобы запугать других трейдеров.
        Когда цена начинает двигаться К этому уровню → он отменяет.
        
        Логика:
        - Нет контекста отмены → score = 0.0 (не можем судить)
        - moving_towards_level = True → score += 0.6
        - distance < config.spoofing_distance_pct → score += 0.3
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
        
        # === GEMINI FIX: Динамический порог ===
        # КРИТЕРИЙ 2: Цена была близко к уровню (+0.3)
        # Вместо хардкода 0.5% используем config
        # BTC: 0.5%, ETH: 1.0%, SOL: 2.0%
        distance_threshold_pct = float(self.config.spoofing_distance_pct) * 100  # Переводим в %
        if abs(float(ctx.distance_from_level_pct)) < distance_threshold_pct:
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
    
    def _analyze_execution_pattern(self, iceberg_level: IcebergLevel) -> float:
        """
        WHY: Реальные айсберги активно исполняются, спуфинг - нет
        
        === GEMINI FIX: Мульти-токен поддержка ===
        Вместо хардкода 0.1 BTC используется config.spoofing_volume_threshold.
        
        Логика:
        - refill_frequency > 10/мин → score = 0.0 (агрессивный алго, легит)
        - refill_frequency < 1/мин → score = 0.5 (подозрительно мало активности)
        - total_hidden_volume < config.spoofing_volume_threshold → score += 0.3
        
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
        
        # === GEMINI FIX: Мульти-токен порог ===
        # КРИТЕРИЙ 2: Очень маленький общий объем (+0.3)
        # Вместо хардкода 0.1 BTC используем config
        # BTC: 0.1, ETH: 2.0, SOL: 20.0
        if float(iceberg_level.total_hidden_volume) < float(self.config.spoofing_volume_threshold):
            score += 0.3
        
        return min(1.0, score)


# ===========================================================================
# НОВЫЙ КЛАСС: AccumulationDetector (Task 3.2 - Multi-Timeframe Context)
# ===========================================================================

class AccumulationDetector:
    """
    WHY: Детектор накопления/дистрибуции для свинг-трейдинга.
    
    Теория (документ "Smart Money Analysis", раздел 3.2):
    - Накопление = Whale CVD растет, пока цена падает (BULLISH divergence)
    - Дистрибуция = Whale CVD падает, пока цена растет (BEARISH divergence)
    - Корреляция с айсберг-зонами усиливает сигнал
    
    Использует:
    - LocalOrderBook.historical_memory для CVD дивергенции
    - LocalOrderBook.cluster_icebergs_to_zones() для корреляции
    """
    
    def __init__(self, book: LocalOrderBook, config: AssetConfig):
        """
        Args:
            book: LocalOrderBook с historical_memory и active_icebergs
            config: AssetConfig для мульти-ассет поддержки (Gemini Fix)
        """
        self.book = book
        self.config = config  # FIX: Gemini Validation - мульти-ассет пороги
        
        # === НОВОЕ: КЕШ ДЛЯ O(1) ДОСТУПА (Gemini Fix) ===
        # WHY: Предотвращает пересчет дивергенции на каждой сделке
        # Обновляется раз в 30 секунд через detect_accumulation_multi_timeframe()
        self._cached_divergence_state: Optional[dict] = None
        
        # === GEMINI RECOMMENDATION 1: Thread Safety ===
        # WHY: Защита кеша от race conditions при параллельных запросах
        self._cache_lock = asyncio.Lock()
        
        # === GEMINI RECOMMENDATION 2: Memory Management ===
        # WHY: Храним зоны для очистки (cleanup task)
        # Dict[Tuple[Decimal, bool], dict] - key: (price, is_ask)
        self.price_zones: dict = {}
    
    def detect_accumulation(self, timeframe: str = '1h') -> Optional[dict]:
        """
        WHY: Детектирует накопление/дистрибуцию на заданном таймфрейме.
        
        === DIGITAL WYCKOFF IMPLEMENTATION ===
        Логика:
        1. DIVERGENCE CHECK: Проверяем CVD дивергенцию (Price vs Whale/Minnow)
        2. ABSORPTION CHECK: Проверяем пассивное поглощение айсбергами
        3. TRAP CHECK: Проверяем Weighted OBI для ложных пробоев
        4. ZONE CORRELATION: Проверяем близость к сильным зонам
        
        Args:
            timeframe: '1h', '4h', '1d', или '1w'
        
        Returns:
            dict с полями:
            - type: 'BULLISH' | 'BEARISH'
            - timeframe: str
            - confidence: float (0.0-1.0)
            - near_strong_zone: bool
            - zone_price: Optional[Decimal]
            - wyckoff_pattern: str ('SPRING', 'UPTHRUST', 'ACCUMULATION', 'DISTRIBUTION')
            - absorption_detected: bool
            - obi_confirms: bool
            
            Или None если дивергенции нет
        """
        # === 1. DIVERGENCE CHECK (уже реализовано) ===
        is_divergence, div_type = self.book.historical_memory.detect_cvd_divergence(timeframe)
        
        if not is_divergence:
            return None
        
        # Базовая confidence зависит от таймфрейма
        base_confidence = {
            '1h': 0.5,
            '4h': 0.6,
            '1d': 0.7,
            '1w': 0.8
        }.get(timeframe, 0.5)
        
        # === 2. ABSORPTION CHECK (НОВОЕ - Wyckoff) ===
        absorption_detected = self._check_passive_absorption(div_type)
        if absorption_detected:
            base_confidence += 0.15  # Бонус за подтверждение поглощения
        
        # === 3. TRAP CHECK (НОВОЕ - Wyckoff) ===
        obi_confirms = self._check_weighted_obi(div_type)
        if obi_confirms:
            base_confidence += 0.10  # Бонус за подтверждение OBI
        
        # === 4. ZONE CORRELATION (улучшено) ===
        zones = self.book.cluster_icebergs_to_zones()
        current_price = self.book.get_mid_price()
        
        near_strong_zone = False
        zone_price = None
        
        if current_price and zones:
            # Ищем ближайшую сильную зону (подходящего типа)
            is_ask_zone = (div_type == 'BEARISH')
            relevant_zones = [z for z in zones if z.is_ask == is_ask_zone and z.is_strong()]
            
            if relevant_zones:
                closest_zone = min(relevant_zones, 
                                 key=lambda z: abs(float(z.center_price - current_price)))
                
                distance_pct = abs(float(closest_zone.center_price - current_price) / float(current_price)) * 100
                
                if distance_pct < 0.5:
                    near_strong_zone = True
                    zone_price = closest_zone.center_price
                    base_confidence += 0.15  # Бонус за зону (увеличен с 0.2)
        
        # === 5. WYCKOFF PATTERN CLASSIFICATION ===
        wyckoff_pattern = self._classify_wyckoff_pattern(
            div_type=div_type,
            absorption=absorption_detected,
            obi_confirms=obi_confirms,
            near_zone=near_strong_zone
        )
        
        # Обрезаем confidence до [0.0, 1.0]
        final_confidence = min(1.0, base_confidence)
        
        return {
            'type': div_type,
            'timeframe': timeframe,
            'confidence': final_confidence,
            'near_strong_zone': near_strong_zone,
            'zone_price': zone_price,
            'wyckoff_pattern': wyckoff_pattern,
            'absorption_detected': absorption_detected,
            'obi_confirms': obi_confirms
        }
    
    def _check_passive_absorption(self, div_type: str) -> bool:
        """
        WHY: Wyckoff "Spring" detection - пассивное поглощение.
        
        Теория (документ Gemini):
        - BULLISH: Цена падает, Minnow CVD падает (паника)
          НО при этом на стороне BID есть крупные айсберги
          → Это "Spring" (пружина) - киты поглощают панические продажи
        
        - BEARISH: Цена растет, Minnow CVD растет (жадность)
          НО при этом на стороне ASK есть крупные айсберги
          → Это "Upthrust" (ложный пробой) - киты разгружаются
        
        Args:
            div_type: 'BULLISH' или 'BEARISH'
        
        Returns:
            True если найдены айсберги на правильной стороне
        """
        # BULLISH: Ищем крупные BID-айсберги (поддержка)
        if div_type == 'BULLISH':
            # === FIX: Gemini Validation - порог из config (мульти-ассет) ===
            # WHY: Без near_zone (кластера) нужен крупный айсберг для SPRING
            # Теория: R_abs = total/visible. Если total=threshold, visible=threshold/10 → R_abs=10 (кит)
            # Порог: BTC=2.0, ETH=30.0, SOL=500.0 (адаптируется под токен)
            large_bid_icebergs = [
                ice for ice in self.book.active_icebergs.values()
                if not ice.is_ask  # BID-сторона
                and ice.confidence_score > 0.7  # Высокая уверенность
                and float(ice.total_hidden_volume) > float(self.config.accumulation_whale_threshold)
            ]
            return len(large_bid_icebergs) > 0
        
        # BEARISH: Ищем крупные ASK-айсберги (сопротивление)
        elif div_type == 'BEARISH':
            # === FIX: Gemini Validation - порог из config (мульти-ассет) ===
            large_ask_icebergs = [
                ice for ice in self.book.active_icebergs.values()
                if ice.is_ask  # ASK-сторона
                and ice.confidence_score > 0.7
                and float(ice.total_hidden_volume) > float(self.config.accumulation_whale_threshold)
            ]
            return len(large_ask_icebergs) > 0
        
        return False
    
    def _check_weighted_obi(self, div_type: str) -> bool:
        """
        WHY: Wyckoff "Effort vs Result" - проверка Weighted OBI.
        
        Теория (документ Gemini):
        - BULLISH: Цена падает, НО OBI растет (лимитная поддержка усиливается)
          → Это накопление, а не реальное падение
        
        - BEARISH: Цена растет, НО OBI падает (лимитное сопротивление усиливается)
          → Это дистрибуция, а не реальный рост
        
        Args:
            div_type: 'BULLISH' или 'BEARISH'
        
        Returns:
            True если OBI подтверждает дивергенцию
        """
        # Рассчитываем Weighted OBI (с затуханием по глубине)
        weighted_obi = self.book.get_weighted_obi(depth=10)
        
        # BULLISH: OBI должен быть положительным (давление покупателей)
        if div_type == 'BULLISH':
            return weighted_obi > 0.2  # Порог 20% дисбаланса
        
        # BEARISH: OBI должен быть отрицательным (давление продавцов)
        elif div_type == 'BEARISH':
            return weighted_obi < -0.2  # Порог -20%
        
        return False
    
    def _classify_wyckoff_pattern(
        self,
        div_type: str,
        absorption: bool,
        obi_confirms: bool,
        near_zone: bool
    ) -> str:
        """
        WHY: Классификация паттерна Wyckoff.
        
        FIX (Task: Gemini Validation): Смягчили требования SPRING/UPTHRUST.
        
        ТЕОРИЯ (документ "Анализ смарт-мани", раздел 2.1):
        - SPRING = дивергенция + пассивное поглощение (absorption) + OBI подтверждение
        - "Один крупный айсберг (R_abs > 10) УЖЕ является сильным уровнем"
        - near_zone (кластер из 3+ айсбергов) - это БОНУС, но НЕ обязательное условие
        
        Решающее дерево:
        - BULLISH + Absorption + OBI → 'SPRING' (лучший сигнал)
        - BULLISH без подтверждения → 'ACCUMULATION' (слабее)
        - BEARISH + Absorption + OBI → 'UPTHRUST' (ложный пробой)
        - BEARISH без подтверждения → 'DISTRIBUTION'
        
        Args:
            div_type: 'BULLISH' или 'BEARISH'
            absorption: Поглощение обнаружено?
            obi_confirms: OBI подтверждает?
            near_zone: Рядом с сильной зоной? (опциональный усилитель)
        
        Returns:
            'SPRING', 'UPTHRUST', 'ACCUMULATION', или 'DISTRIBUTION'
        """
        if div_type == 'BULLISH':
            # FIX: Достаточно Absorption + OBI. Zone - опциональный бонус.
            # WHY: Один крупный айсберг (5 BTC) = уже сильная защита уровня
            if absorption and obi_confirms:
                return 'SPRING'
            return 'ACCUMULATION'
        
        elif div_type == 'BEARISH':
            # FIX: Достаточно Absorption + OBI
            if absorption and obi_confirms:
                return 'UPTHRUST'
            return 'DISTRIBUTION'
        
        return 'UNKNOWN'
    
    def get_current_divergence_state(self) -> Optional[dict]:
        """
        WHY: O(1) доступ к последнему результату дивергенции (КЕШ).
        
        === GEMINI FIX: Data Fusion Architecture ===
        Предотвращает пересчет дивергенции на каждой сделке (1000+ TPS).
        Кеш обновляется раз в 30 секунд через detect_accumulation_multi_timeframe().
        
        Используется в services.py при TradeEvent для захвата контекста.
        
        Returns:
            dict или None:
            {
                'type': 'BULLISH' | 'BEARISH',
                'confidence': float,
                'timeframe': str,  # Наиболее сильный таймфрейм
                'wyckoff_pattern': str
            }
        """
        return self._cached_divergence_state
    
    def detect_accumulation_multi_timeframe(self) -> dict:
        """
        WHY: Анализ на всех таймфреймах одновременно.
        
        Логика:
        - Проверяем 1H, 4H, 1D, 1W
        - Возвращаем только те таймфреймы, где есть дивергенция
        
        === GEMINI FIX: Обновляет кеш ===
        После анализа сохраняет наиболее сильный сигнал в _cached_divergence_state.
        
        Returns:
            dict: {
                '1h': {...},  # Результат detect_accumulation
                '4h': {...},
                # и т.д. (только таймфреймы с дивергенцией)
            }
        """
        timeframes = ['1h', '4h', '1d', '1w']
        results = {}
        
        for tf in timeframes:
            result = self.detect_accumulation(timeframe=tf)
            if result is not None:
                results[tf] = result
        
        # === GEMINI FIX: ОБНОВЛЕНИЕ КЕША ===
        # WHY: Выбираем наиболее сильный сигнал (высший таймфрейм = больше вес)
        if results:
            # Приоритет: 1W > 1D > 4H > 1H
            for priority_tf in ['1w', '1d', '4h', '1h']:
                if priority_tf in results:
                    self._cached_divergence_state = results[priority_tf]
                    break
        else:
            # Нет дивергенции - очищаем кеш
            self._cached_divergence_state = None
        
        return results
    
    def _periodic_cleanup_task(self):
        """
        WHY: Очищает старые зоны из памяти.
        
        === GEMINI RECOMMENDATION 2: Memory Management ===
        Логирует удаляемые "тяжёлые" зоны для отслеживания утечек памяти.
        
        Логика:
        - Удаляем зоны старше 30 минут
        - Логируем количество айсбергов и уровень цен
        """
        logger = logging.getLogger(__name__)
        cutoff_time = datetime.now() - timedelta(minutes=30)
        
        zones_to_remove = []
        for zone_id, zone_data in self.price_zones.items():
            if zone_data['created_at'] < cutoff_time:
                zones_to_remove.append(zone_id)
        
        # Логирование удаления
        if zones_to_remove:
            for zone_id in zones_to_remove:
                zone_data = self.price_zones[zone_id]
                price, is_ask = zone_id
                num_icebergs = len(zone_data.get('icebergs', []))
                
                logger.info(
                    f"Removed PriceZone: price={price}, "
                    f"side={'ASK' if is_ask else 'BID'}, "
                    f"icebergs={num_icebergs}"
                )
                
                # Удаляем зону
                del self.price_zones[zone_id]
        else:
            # Нет удалений - не логируем (избегаем спама)
            pass


# ===========================================================================
# НОВЫЙ КЛАСС: FlowToxicityAnalyzer (Task: VPIN Implementation)
# ===========================================================================

class FlowToxicityAnalyzer:
    """
    WHY: Анализатор токсичности потока на основе VPIN.
    
    Теория (Easley-O'Hara, 2012 - документ "Анализ данных смарт-мани"):
    - VPIN = Volume-Synchronized Probability of Informed Trading
    - Измеряет вероятность того, что агрессоры информированы (знают будущее движение)
    - Высокий VPIN (>0.7) = токсичный поток → риск пробоя айсберга
    - Низкий VPIN (<0.3) = шумный поток → айсберг устоит
    
    Формула:
    VPIN = Σ|Buy_i - Sell_i| / (n * bucket_size)
    
    Где:
    - Buy_i, Sell_i = объёмы в корзине i
    - n = количество корзин (window_size, обычно 50)
    - bucket_size = фиксированный размер корзины (например 10 BTC)
    """
    
    def __init__(self, book: LocalOrderBook, bucket_size: Decimal):
        """
        Args:
            book: LocalOrderBook с vpin_buckets и current_vpin_bucket
            bucket_size: Размер корзины в монетах токена (например Decimal("10") для BTC)
        """
        self.book = book
        self.bucket_size = bucket_size
        
        # WHY: Инициализируем первую корзину если её нет
        if self.book.current_vpin_bucket is None:
            self.book.current_vpin_bucket = VolumeBucket(
                bucket_size=bucket_size,
                symbol=book.symbol
            )
    
    def update_vpin(self, trade: TradeEvent) -> Optional[float]:
        """
        WHY: Обновляет VPIN при каждой сделке.
        
        Логика:
        1. Добавляем сделку в current_bucket
        2. Если bucket заполнен → перемещаем в историю, создаём новый
        3. Пересчитываем VPIN на основе скользящего окна
        
        Args:
            trade: Событие сделки
        
        Returns:
            float: Текущий VPIN (0.0-1.0), или None если недостаточно корзин
        """
        # 1. Добавляем сделку в текущую корзину
        overflow = self.book.current_vpin_bucket.add_trade(trade)
        
        # 2. Если корзина заполнена
        if self.book.current_vpin_bucket.is_complete:
            # Сохраняем в историю
            self.book.vpin_buckets.append(self.book.current_vpin_bucket)
            
            # Если есть overflow → создаём новую корзину с этим overflow
            if overflow > 0:
                # Создаём новую корзину
                new_bucket = VolumeBucket(
                    bucket_size=self.bucket_size,
                    symbol=self.book.symbol
                )
                
                # Создаём trade-событие для overflow
                overflow_trade = TradeEvent(
                    price=trade.price,
                    quantity=overflow,
                    is_buyer_maker=trade.is_buyer_maker,
                    event_time=trade.event_time,
                    trade_id=trade.trade_id
                )
                
                # Добавляем overflow в новую корзину
                new_bucket.add_trade(overflow_trade)
                self.book.current_vpin_bucket = new_bucket
            else:
                # Просто создаём пустую корзину
                self.book.current_vpin_bucket = VolumeBucket(
                    bucket_size=self.bucket_size,
                    symbol=self.book.symbol
                )
        
        # 3. Пересчитываем VPIN
        vpin = self.get_current_vpin()
        
        # === GEMINI RECOMMENDATION 3: VPIN Reliable Check ===
        # WHY: Возвращаем None если VPIN unreliable
        if vpin is not None and not self._is_vpin_reliable():
            return None
        
        return vpin
    
    def get_current_vpin(self) -> Optional[float]:
        """
        WHY: Рассчитывает текущий VPIN на основе истории корзин.
        
        === GEMINI FIX: Real-Time VPIN ===
        Теперь учитывает current_vpin_bucket если она заполнена >20%.
        
        Формула (Volume-Weighted):
        VPIN = Σ|OI_i| / ΣV_i
        
        Где:
        - |OI_i| = abs(buy - sell) в корзине i
        - V_i = total_volume корзины i
        
        Это позволяет корректно объединять полные и неполные корзины.
        
        Returns:
            float: VPIN значение (0.0-1.0), или None если корзин < 10
        """
        # WHY: Минимум 10 корзин для надёжного расчёта
        if len(self.book.vpin_buckets) < 10:
            return None
        
        # === GEMINI FIX: Собираем ВСЕ корзины (история + текущая) ===
        buckets_to_include = list(self.book.vpin_buckets)  # Копия истории
        
        # Проверяем текущую корзину
        if self.book.current_vpin_bucket is not None:
            # Рассчитываем % заполнения
            current_volume = self.book.current_vpin_bucket.total_volume()
            if self.bucket_size > 0:
                fill_percentage = float(current_volume / self.bucket_size)
                
                # WHY: Из config.py - vpin_inclusion_threshold = 0.2 (20%)
                from config import get_config
                config = get_config(self.book.symbol)
                
                # Если заполнена больше порога - включаем
                if fill_percentage >= config.vpin_inclusion_threshold:
                    buckets_to_include.append(self.book.current_vpin_bucket)
        
        # Если после добавления current bucket меньше 10 - выходим
        if len(buckets_to_include) < 10:
            return None
        
        # === VOLUME-WEIGHTED FORMULA ===
        # WHY: Корзины могут быть разного размера (полные + частичная)
        total_imbalance = Decimal("0")
        total_volume = Decimal("0")
        
        for bucket in buckets_to_include:
            total_imbalance += bucket.calculate_imbalance()
            total_volume += bucket.total_volume()
        
        # Защита от деления на 0
        if total_volume == 0:
            return None
        
        # VPIN = sum(|imbalance|) / sum(volume)
        vpin = float(total_imbalance / total_volume)
        
        # Обрезаем до [0.0, 1.0]
        return max(0.0, min(1.0, vpin))
    
    def get_vpin_status(self, current_time: Optional[datetime] = None) -> dict:
        """
        WHY: GEMINI FIX - Возвращает статус VPIN со свежестью.
        
        Используется ML-моделью для фильтрации stale VPIN:
        - Если is_stale = True → не использовать в обучении
        - Если freshness > 300с (5 мин) → VPIN устарел
        
        Args:
            current_time: Текущее время (для тестирования)
        
        Returns:
            dict: {
                'vpin': float | None,
                'is_stale': bool,
                'freshness': float,  # секунд с последней сделки
                'buckets_used': int
            }
        """
        if current_time is None:
            current_time = datetime.now()
        
        # Рассчитываем VPIN
        vpin = self.get_current_vpin()
        
        # === ОПРЕДЕЛЯЕМ СВЕЖЕСТЬ ===
        freshness_seconds = 0.0
        is_stale = True  # default: считаем stale пока не доказано обратное
        
        # Находим самую свежую корзину
        most_recent_bucket = None
        
        # 1. Проверяем current_bucket (он самый свежий если есть)
        if self.book.current_vpin_bucket is not None:
            most_recent_bucket = self.book.current_vpin_bucket
        # 2. Иначе берём последнюю из истории
        elif len(self.book.vpin_buckets) > 0:
            most_recent_bucket = self.book.vpin_buckets[-1]  # Последняя в списке
        
        # Если нашли корзину
        if most_recent_bucket is not None:
            freshness_seconds = most_recent_bucket.age_seconds(current_time)
            
            # WHY: Из config.py - vpin_stale_threshold_seconds = 300 (5 мин)
            from config import get_config
            config = get_config(self.book.symbol)
            
            # Проверяем stale
            if freshness_seconds <= config.vpin_stale_threshold_seconds:
                is_stale = False
        
        # Считаем количество корзин
        buckets_used = len(self.book.vpin_buckets)
        
        # Добавляем current если она была включена в get_current_vpin()
        if self.book.current_vpin_bucket is not None:
            current_volume = self.book.current_vpin_bucket.total_volume()
            if self.bucket_size > 0:
                fill_percentage = float(current_volume / self.bucket_size)
                from config import get_config
                config = get_config(self.book.symbol)
                if fill_percentage >= config.vpin_inclusion_threshold:
                    buckets_used += 1
        
        return {
            'vpin': vpin,
            'is_stale': is_stale,
            'freshness': freshness_seconds,
            'buckets_used': buckets_used
        }
    
    def is_flow_toxic(self, threshold: float = 0.7) -> bool:
        """
        WHY: Проверяет токсичность потока.
        
        Теория:
        - VPIN > 0.7 = токсичный поток (информированные агрессоры)
        - Риск пробоя айсберга высокий
        
        Args:
            threshold: Порог токсичности (default 0.7)
        
        Returns:
            True если поток токсичный
        """
        vpin = self.get_current_vpin()
        if vpin is None:
            return False
        return vpin > threshold
    
    def get_toxicity_level(self) -> str:
        """
        WHY: Возвращает категориальный уровень токсичности.
        
        Levels:
        - EXTREME: VPIN > 0.8 (критический риск пробоя)
        - HIGH: VPIN > 0.7 (высокий риск)
        - MODERATE: VPIN 0.5-0.7 (умеренный)
        - LOW: VPIN 0.3-0.5 (низкий)
        - MINIMAL: VPIN < 0.3 (минимальный, шумный поток)
        - UNKNOWN: Недостаточно данных
        
        Returns:
            str: Уровень токсичности
        """
        vpin = self.get_current_vpin()
        
        if vpin is None:
            return 'UNKNOWN'
        
        if vpin > 0.8:
            return 'EXTREME'
        elif vpin > 0.7:
            return 'HIGH'
        elif vpin > 0.5:
            return 'MODERATE'
        elif vpin > 0.3:
            return 'LOW'
        else:
            return 'MINIMAL'
    
    def _is_vpin_reliable(self) -> bool:
        """
        WHY: Проверяет надёжность VPIN в текущих рыночных условиях.
        
        === GEMINI RECOMMENDATION 3: VPIN Reliable Check ===
        Фильтрует "флэтовые" сигналы где VPIN шумный.
        
        VPIN может давать ложные сигналы в:
        1. Флэте (низкая волатильность) - маркет-мейкеры создают псевдо-имбаланс
        2. Недостаточно данных (< 10 корзин)
        
        Теория (документ "Анализ смарт-мани"):
        - VPIN из TradFi (Easley 2012) предполагает "нормальные" условия
        - Во флэте VPIN ошибочно показывает "токсичность" от MM-ботов
        - При низкой ликвидности bucket_size слишком велик
        
        Returns:
            True если VPIN можно доверять, False если рискованно
        """
        # 1. Проверка наличия данных
        # WHY: Минимум 10 корзин для надёжного расчёта
        if len(self.book.vpin_buckets) < 10:
            return False  # Недостаточно данных
        
        # 2. Проверка флэта (низкая волатильность)
        # WHY: Во флэте Buy ≈ Sell в каждой корзине = VPIN даёт ложные сигналы
        total_imbalance = sum(
            bucket.calculate_imbalance() 
            for bucket in self.book.vpin_buckets
        )
        
        # Если общий дисбаланс очень мал (< 5% от общего объёма) = флэт
        total_volume = len(self.book.vpin_buckets) * self.bucket_size
        if total_volume > 0:
            imbalance_ratio = float(total_imbalance / total_volume)
            if imbalance_ratio < 0.05:  # Меньше 5% = флэт
                return False
        
        # 3. Все проверки пройдены
        return True


# ===========================================================================
# GAMMA PROVIDER: Извлечение GEX метрик из LocalOrderBook
# ===========================================================================

class GammaProvider:
    """
    WHY: Читает GEX данные из LocalOrderBook.gamma_profile.
    
    Интерфейс:
    - get_total_gex() → суммарная гамма-экспозиция
    - get_gamma_wall_distance(price) → расстояние до ближайшей стены
    """
    
    def __init__(self, order_book):
        """
        Args:
            order_book: LocalOrderBook с gamma_profile
        """
        self.book = order_book
    
    def get_total_gex(self) -> Optional[float]:
        """
        WHY: Возвращает суммарную gamma exposure.
        
        Returns:
            float: Суммарная GEX (может быть + или -)
            None: Если данных нет
        """
        if not self.book or not self.book.gamma_profile:
            return None
        
        try:
            return float(self.book.gamma_profile.total_gex)
        except:
            return None
    
    def get_gamma_wall_distance(self, current_price: Decimal) -> Tuple[Optional[float], Optional[str]]:
        """
        WHY: Рассчитывает расстояние до ближайшей gamma wall.
        
        FIX VULNERABILITY #4: Decimal-safe distance calculation
        - current_price: Decimal (вместо float)
        - gamma_profile.call/put_wall: Decimal (после fix)
        - Расстояние вычисляется в Decimal, конвертируется в float только для return
        
        Теория (документ "Анализ данных смарт-мани"):
        - Gamma Wall = страйк с максимальной концентрацией гаммы
        - Call Wall = сопротивление (дилеры продают на росте)
        - Put Wall = поддержка (дилеры покупают на падении)
        
        Args:
            current_price: Текущая цена актива (Decimal)
        
        Returns:
            Tuple[distance_pct, wall_type]:
            - distance_pct: Процентное расстояние до ближайшей wall (float для DB)
            - wall_type: 'CALL' | 'PUT' | None
        """
        if not self.book or not self.book.gamma_profile:
            return None, None
        
        try:
            gamma_profile = self.book.gamma_profile
            
            # FIX: Decimal-safe distance calculation
            # Расстояния до стен (Decimal - Decimal = Decimal)
            dist_to_call = abs(current_price - gamma_profile.call_wall)
            dist_to_put = abs(current_price - gamma_profile.put_wall)
            
            # Находим ближайшую (Decimal comparison)
            if dist_to_call < dist_to_put:
                closest_wall = gamma_profile.call_wall
                wall_type = 'CALL'
                distance = dist_to_call
            else:
                closest_wall = gamma_profile.put_wall
                wall_type = 'PUT'
                distance = dist_to_put
            
            # Процентное расстояние (Decimal arithmetic)
            distance_pct = (distance / current_price) * Decimal("100")
            
            # Конвертируем в float ТОЛЬКО для return (для записи в DB)
            return float(distance_pct), wall_type
            
        except:
            return None, None
