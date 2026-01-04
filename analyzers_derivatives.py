# ===========================================================================
# DERIVATIVES ANALYZER: Futures Basis + Options Skew + OI Delta
# ===========================================================================

from typing import Optional, Tuple, Dict, Any, List
from decimal import Decimal
from datetime import datetime, timedelta
import numpy as np
from scipy.stats import norm

class DerivativesAnalyzer:
    """
    WHY: Анализ деривативов для метрик "умных денег" (старшие таймфреймы).
    
    === MULTI-ASSET SUPPORT ===
    Принимает AssetConfig для получения symbol и других параметров.
    
    Теория (документ "Анализ данных смарт-мани", раздел 4):
    - Futures Basis: Перегрев/дно рынка (Contango vs Backwardation)
    - Options Skew: Страх институционалов (Put vs Call IV)
    - OI Delta: Топливо тренда (новые позиции vs ликвидации)
    
    Используется для:
    - Расчета SmartCandle метрик (1D/1W свечи)
    - Валидации Wyckoff сигналов (накопление/дистрибуция)
    - Макро-анализа фазы рынка
    """
    
    def __init__(self, config: Optional['AssetConfig'] = None):
        """
        WHY: Инициализация с конфигурацией актива для мульти-токен поддержки.
        
        Args:
            config: AssetConfig (BTC_CONFIG, ETH_CONFIG, SOL_CONFIG)
                    Если None - используется BTC_CONFIG по умолчанию (backward compatibility)
        """
        # WHY: Import here to avoid circular dependency
        from config import AssetConfig, BTC_CONFIG
        
        self.config = config if config is not None else BTC_CONFIG
        
        # WHY: Domain модель для GEX результатов (Clean Architecture)
        from domain import GammaProfile  # Import here to avoid circular dependency
        
        # Кеш для FeatureCollector (неблокирующий доступ)
        self._cached_basis: Optional[float] = None
        self._cached_skew: Optional[float] = None
        self._last_basis_update: Optional[datetime] = None
        self._last_skew_update: Optional[datetime] = None
    
    def calculate_annualized_basis(
        self,
        spot_price: Decimal,
        futures_price: Decimal,
        days_to_expiry: int
    ) -> float:
        """
        WHY: Рассчитывает аннуализированный базис фьючерсов.
        
        Теория (документ "Анализ данных смарт-мани", раздел 4.2):
        - Basis = (F - S) / S (разница цен)
        - Annualized = basis * (365 / DTE) (переводим в годовую ставку)
        
        Формула: ((Futures - Spot) / Spot) * (365 / DTE) * 100
        
        NOTE: Для perpetual futures (DTE=1) это упрощается до:
        Basis APR = ((F - S) / S) * 365 * 100
        
        Args:
            spot_price: Цена спота (например, $60,000)
            futures_price: Цена фьючерса (например, $60,500)
            days_to_expiry: Дней до экспирации (например, 30; для perpetual = 1)
        
        Returns:
            Аннуализированный базис в % (например, 10.1%)
        
        Example:
            >>> spot = Decimal('60000')
            >>> futures = Decimal('60500')
            >>> dte = 30
            >>> analyzer = DerivativesAnalyzer()
            >>> basis = analyzer.calculate_annualized_basis(spot, futures, dte)
            >>> # basis ≈ 10.1% APR
        """
        # Валидация
        if spot_price <= 0 or futures_price <= 0 or days_to_expiry <= 0:
            raise ValueError("Prices and DTE must be positive")
        
        # Расчет базового базиса
        basis = float((futures_price - spot_price) / spot_price)
        
        # Аннуализация
        annualized_basis_pct = basis * (365 / days_to_expiry) * 100
        
        return annualized_basis_pct
    
    def calculate_gex(
        self,
        strikes: List[float],
        types: List[str],
        expiry_years: List[float],
        ivs: List[float],
        open_interest: List[float],
        underlying_price: float,
        avg_daily_volume: Optional[float] = None  # NEW: Можно передать снаружи
    ):
        """
        WHY: Рассчитывает Gamma Exposure (GEX) для опционов.
        
        Теория (документ "Анализ биржевого стакана", раздел GEX):
        - Black-Scholes модель для расчета Gamma
        - GEX = Gamma * OI * S^2 * 0.01 (для каждого страйка)
        - Put GEX инвертируется (умножается на -1)
        - Агрегация: Total GEX, Call Wall (max), Put Wall (min)
        
        Clean Architecture:
        - RAW данные приходят от Infrastructure
        - Математика здесь (Black-Scholes)
        - Результат: GammaProfile (domain model)
        
        Args:
            strikes: Список страйков
            types: Список типов ('C' или 'P')
            expiry_years: Список времени до экспирации в годах
            ivs: Список Implied Volatility (decimal, например 0.65)
            open_interest: Список Open Interest
            underlying_price: Цена базового актива (спот)
        
        Returns:
            GammaProfile(
                total_gex=float,
                call_wall=float,  # Страйк с максимальным Call GEX
                put_wall=float    # Страйк с максимальным Put GEX (абс. значение)
            )
            или None если нет данных
        
        Example:
            >>> strikes = [90000, 95000, 100000]
            >>> types = ['C', 'C', 'C']
            >>> expiry_years = [0.08, 0.08, 0.08]  # ~30 дней
            >>> ivs = [0.65, 0.70, 0.75]
            >>> oi = [1000, 1500, 2000]
            >>> spot = 98000.0
            >>> analyzer = DerivativesAnalyzer()
            >>> profile = analyzer.calculate_gex(...)
            >>> # profile.total_gex, profile.call_wall, profile.put_wall
        """
        from domain import GammaProfile
        
        if not strikes or len(strikes) == 0:
            return None
        
        # Конвертируем в numpy arrays для векторизации
        S = np.array([underlying_price] * len(strikes))  # Spot price
        K = np.array(strikes)  # Strike prices
        T = np.array(expiry_years)  # Time to expiry (years)
        sigma = np.array(ivs)  # Implied Volatility
        
        # Black-Scholes: d1 = (ln(S/K) + 0.5*σ²*T) / (σ*√T)
        d1 = (np.log(S / K) + (0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
        
        # Gamma = N'(d1) / (S * σ * √T)
        # где N'(d1) - плотность стандартного нормального распределения
        gamma = norm.pdf(d1) / (S * sigma * np.sqrt(T))
        
        # GEX = Gamma * OI * S² (removing * 0.01 multiplier)
        # WHY: The 0.01 was causing 100x underestimation
        oi_array = np.array(open_interest)
        gex_array = gamma * oi_array * (S**2)
        
        # Инвертируем Put GEX (умножаем на -1)
        # WHY: Поддержка как uppercase 'P' так и lowercase 'put'
        for i, opt_type in enumerate(types):
            if opt_type.upper() == 'P' or opt_type.lower() == 'put':
                gex_array[i] *= -1
        
        # Агрегация по страйкам
        strike_gex = {}
        for i, strike in enumerate(strikes):
            if strike not in strike_gex:
                strike_gex[strike] = 0.0
            strike_gex[strike] += gex_array[i]
        
        # Total GEX
        total_gex = sum(strike_gex.values())
        
        # Call Wall: страйк с максимальным положительным GEX
        call_strikes = {k: v for k, v in strike_gex.items() if v > 0}
        call_wall = max(call_strikes.keys(), key=lambda k: call_strikes[k]) if call_strikes else None
        
        # Put Wall: страйк с максимальным отрицательным GEX (по абсолютному значению)
        put_strikes = {k: v for k, v in strike_gex.items() if v < 0}
        put_wall = min(put_strikes.keys(), key=lambda k: put_strikes[k]) if put_strikes else None
        
        # === GEMINI FIX: GEX Normalization ===
        # Рассчитываем total_gex_normalized = total_gex / ADV_20d
        total_gex_normalized = None
        if avg_daily_volume is not None and avg_daily_volume > 0:
            total_gex_normalized = total_gex / avg_daily_volume
        
        # === GEMINI FIX: Expiration Decay ===
        # Используем статический метод GammaProfile для расчёта expiry
        expiry_timestamp = GammaProfile.get_next_options_expiry()
        
        # === FIX VULNERABILITY #4: Convert float → Decimal ===
        # WHY: GammaProfile.call_wall/put_wall теперь Decimal (точность цен)
        # Страйки приходят как float из Deribit API → конвертируем в Decimal
        call_wall_decimal = Decimal(str(call_wall)) if call_wall else Decimal("0")
        put_wall_decimal = Decimal(str(put_wall)) if put_wall else Decimal("0")
        
        return GammaProfile(
            total_gex=total_gex,
            total_gex_normalized=total_gex_normalized,  # NEW
            call_wall=call_wall_decimal,  # FIX: Decimal вместо float
            put_wall=put_wall_decimal,    # FIX: Decimal вместо float
            expiry_timestamp=expiry_timestamp  # NEW
        )
    
    def calculate_options_skew(
        self,
        put_iv_25d: float,
        call_iv_25d: float
    ) -> float:
        """
        WHY: Рассчитывает 25-delta Options Skew (страх институционалов).
        
        Теория (документ "Анализ данных смарт-мани", раздел 4.2):
        - Skew = IV_put - IV_call (разница подразумеваемой волатильности)
        - Положительный Skew: Путы дороже (страх падения)
        - Отрицательный Skew: Коллы дороже (ожидание роста)
        
        Формула: IV_put_25d - IV_call_25d
        
        Args:
            put_iv_25d: Implied Volatility 25-delta Put (например, 65%)
            call_iv_25d: Implied Volatility 25-delta Call (например, 58%)
        
        Returns:
            Skew в процентных пунктах (например, +7.0 означает путы на 7% дороже)
        
        Example:
            >>> put_iv = 65.0  # 65%
            >>> call_iv = 58.0  # 58%
            >>> analyzer = DerivativesAnalyzer()
            >>> skew = analyzer.calculate_options_skew(put_iv, call_iv)
            >>> # skew = 7.0% (страх падения)
        """
        # Валидация
        if put_iv_25d < 0 or call_iv_25d < 0:
            raise ValueError("IV values must be non-negative")
        
        # Расчет skew (в процентных пунктах)
        skew = (put_iv_25d - call_iv_25d) * 100
        
        return skew
    
    def calculate_oi_delta(
        self,
        oi_start: float,
        oi_end: float,
        volume_reference: Optional[float] = None
    ) -> Tuple[float, str]:
        """
        WHY: Рассчитывает изменение Open Interest (топливо тренда).
        
        Теория (документ "Анализ данных смарт-мани", раздел 4.3):
        - OI Delta = OI_end - OI_start (новые/закрытые позиции)
        - Интерпретация зависит от направления цены:
          - Цена ↑ + OI ↑: STRONG_BULL (новые лонги)
          - Цена ↑ + OI ↓: WEAK_BULL (short covering)
          - Цена ↓ + OI ↑: STRONG_BEAR (новые шорты)
          - Цена ↓ + OI ↓: WEAK_BEAR (long liquidations)
        
        Args:
            oi_start: Open Interest в начале свечи (BTC или $)
            oi_end: Open Interest в конце свечи
            volume_reference: Объем за свечу (для нормализации, опционально)
        
        Returns:
            Tuple[delta, magnitude]:
            - delta: Абсолютное изменение OI
            - magnitude: 'MAJOR' (>5% от volume), 'MODERATE' (1-5%), 'MINOR' (<1%)
        
        Example:
            >>> oi_start = 50000.0  # 50k BTC
            >>> oi_end = 52000.0    # 52k BTC
            >>> volume = 100000.0   # 100k BTC торговый объем
            >>> analyzer = DerivativesAnalyzer()
            >>> delta, mag = analyzer.calculate_oi_delta(oi_start, oi_end, volume)
            >>> # delta = +2000.0 BTC, magnitude = 'MODERATE' (2% от объема)
        """
        # Расчет delta
        delta = oi_end - oi_start
        
        # Определение величины изменения
        if volume_reference is not None and volume_reference > 0:
            # Процент от объема
            pct_of_volume = abs(delta) / volume_reference * 100
            
            if pct_of_volume > 5.0:
                magnitude = 'MAJOR'
            elif pct_of_volume > 1.0:
                magnitude = 'MODERATE'
            else:
                magnitude = 'MINOR'
        else:
            # Если нет reference volume - используем абсолютные значения
            # (адаптируйте пороги под ваш токен)
            abs_delta = abs(delta)
            
            if abs_delta > 1000:
                magnitude = 'MAJOR'
            elif abs_delta > 100:
                magnitude = 'MODERATE'
            else:
                magnitude = 'MINOR'
        
        return delta, magnitude
    
    def interpret_basis_contango(self, basis_apr: float) -> str:
        """
        WHY: Интерпретирует аннуализированный базис (APR для quarterly futures).
        
        Теория (документ "Анализ данных смарт-мани", раздел 4.2):
        - basis_apr 0-5%: Нормальное состояние (cost of carry)
        - basis_apr 5-15%: Умеренный оптимизм
        - basis_apr 15-25%: Перегрев (арбитражеры активируются)
        - basis_apr >25%: Экстремальный перегрев (разворот близко)
        - basis_apr <0%: Backwardation (инверсия кривой, дефицит/дно)
        
        Args:
            basis_apr: Аннуализированный базис в % (output от calculate_annualized_basis)
        
        Returns:
            'NORMAL', 'OPTIMISTIC', 'OVERHEATED', 'EXTREME', 'BACKWARDATION'
        """
        if basis_apr < 0:
            return 'BACKWARDATION'  # Инверсия кривой
        elif basis_apr < 5.0:
            return 'NORMAL'  # 0-5% APR: нормальная премия
        elif basis_apr < 15.0:
            return 'OPTIMISTIC'  # 5-15% APR: умеренный оптимизм
        elif basis_apr < 25.0:
            return 'OVERHEATED'  # 15-25% APR: перегрев рынка
        else:
            return 'EXTREME'  # >25% APR: экстремальный перегрев
    
    def interpret_skew(self, skew: float, price_rising: bool) -> str:
        """
        WHY: Интерпретирует Options Skew с учетом направления цены.
        
        Теория:
        - skew > +5%: Высокий страх (путы дороже)
        - skew -5% to +5%: Нейтральное состояние
        - skew < -5%: Жадность (коллы дороже)
        
        CRITICAL: Если цена ↑ но skew ↑ → дивергенция (медвежий сигнал)
        
        Args:
            skew: Options Skew в %
            price_rising: True если цена растет
        
        Returns:
            'FEAR', 'NEUTRAL', 'GREED', 'DIVERGENCE' (если skew не совпадает с ценой)
        """
        # Проверка дивергенции
        if price_rising and skew > 5.0:
            return 'DIVERGENCE'  # Цена ↑ но страх ↑ (медвежий сигнал)
        
        if not price_rising and skew < -5.0:
            return 'DIVERGENCE'  # Цена ↓ но жадность ↑ (бычий сигнал)
        
        # Нормальная интерпретация
        if skew > 5.0:
            return 'FEAR'
        elif skew < -5.0:
            return 'GREED'
        else:
            return 'NEUTRAL'
    
    def get_trend_fuel_interpretation(
        self,
        price_rising: bool,
        oi_delta: float,
        oi_magnitude: str
    ) -> dict:
        """
        WHY: Полная интерпретация "топлива" тренда по OI Delta.
        
        Теория (Wyckoff + Futures Analysis):
        - Цена ↑ + OI ↑: Новые лонги (сильный бычий тренд)
        - Цена ↑ + OI ↓: Short covering (слабый рост, скоро разворот)
        - Цена ↓ + OI ↑: Новые шорты (сильный медвежий тренд)
        - Цена ↓ + OI ↓: Long liquidations (слабое падение, возможно дно)
        
        Args:
            price_rising: True если цена выросла
            oi_delta: Изменение OI
            oi_magnitude: 'MAJOR', 'MODERATE', 'MINOR'
        
        Returns:
            dict: {
                'trend_type': 'STRONG_BULL' | 'WEAK_BULL' | 'STRONG_BEAR' | 'WEAK_BEAR' | 'NEUTRAL',
                'magnitude': 'MAJOR' | 'MODERATE' | 'MINOR',
                'signal': 'CONTINUATION' | 'REVERSAL' | 'NEUTRAL',
                'description': str
            }
        """
        # Определяем направление OI
        oi_threshold = 100  # Минимальное изменение для "значимости"
        oi_increasing = oi_delta > oi_threshold
        oi_decreasing = oi_delta < -oi_threshold
        
        # Классификация
        if price_rising and oi_increasing:
            trend_type = 'STRONG_BULL'
            signal = 'CONTINUATION'
            desc = "Новые лонги открываются - сильный бычий тренд"
        
        elif price_rising and oi_decreasing:
            trend_type = 'WEAK_BULL'
            signal = 'REVERSAL'
            desc = "Short covering - слабый рост, скоро разворот"
        
        elif not price_rising and oi_increasing:
            trend_type = 'STRONG_BEAR'
            signal = 'CONTINUATION'
            desc = "Новые шорты - сильный медвежий тренд"
        
        elif not price_rising and oi_decreasing:
            trend_type = 'WEAK_BEAR'
            signal = 'REVERSAL'
            desc = "Long liquidations - слабое падение, возможно дно"
        
        else:
            trend_type = 'NEUTRAL'
            signal = 'NEUTRAL'
            desc = "Нет значимого изменения OI"
        
        return {
            'trend_type': trend_type,
            'magnitude': oi_magnitude,
            'signal': signal,
            'description': desc
        }
    
    # ========================================================================
    # CACHE METHODS: Для FeatureCollector (неблокирующий доступ)
    # ========================================================================
    
    def update_basis_cache(self, basis_apr: float):
        """
        WHY: Обновляет кеш basis для неблокирующего чтения.
        
        Вызывать из фонового процесса (раз в минуту).
        """
        self._cached_basis = basis_apr
        self._last_basis_update = datetime.now()
    
    def update_skew_cache(self, skew: float):
        """
        WHY: Обновляет кеш skew для неблокирующего чтения.
        
        Вызывать из фонового процесса (раз в минуту).
        """
        self._cached_skew = skew
        self._last_skew_update = datetime.now()
    
    def get_cached_basis(self) -> Optional[float]:
        """
        WHY: Возвращает последний кешированный basis (без сетевых запросов).
        
        === GEMINI FIX: Cache TTL Extension ===
        TTL увеличен с 5 мин до 30 мин для макро-данных.
        
        Returns:
            Cached basis APR или None если кеш пустой/устарел
        """
        if self._cached_basis is None:
            return None
        
        # Проверяем свежесть (не старее 30 минут)
        if self._last_basis_update:
            age = (datetime.now() - self._last_basis_update).total_seconds()
            # FIX: Увеличено с 300 до 1800 сек (с 5 мин до 30 мин)
            if age > 1800:
                return None
        
        return self._cached_basis
    
    def get_cached_skew(self) -> Optional[float]:
        """
        WHY: Возвращает последний кешированный skew (без сетевых запросов).
        
        === GEMINI FIX: Cache TTL Extension ===
        TTL увеличен с 5 мин до 30 мин для макро-данных.
        
        Returns:
            Cached skew или None если кеш пустой/устарел
        """
        if self._cached_skew is None:
            return None
        
        # Проверяем свежесть (не старее 30 минут)
        if self._last_skew_update:
            age = (datetime.now() - self._last_skew_update).total_seconds()
            # FIX: Увеличено с 300 до 1800 сек (с 5 мин до 30 мин)
            if age > 1800:
                return None
        
        return self._cached_skew
