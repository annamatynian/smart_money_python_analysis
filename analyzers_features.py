# ===========================================================================
# FEATURE COLLECTOR: Snapshot всех метрик для ML
# ===========================================================================

"""
WHY: Агрегирует метрики со всех анализаторов в единый снимок для ML.

Цель: Собрать полный контекст в момент айсберг-события для Feature Importance.

Источники данных:
- OrderFlowAnalyzer: OBI, CVD (whale/fish/dolphin)
- DerivativesAnalyzer: Basis, Skew, GEX
- SpoofingDetector: Spoofing score
- LocalOrderBook: Spread, depth

Критические требования (от Gemini):
1. ТОЛЬКО кеш - никаких сетевых запросов (неблокирующий)
2. Throttling - не писать снапшот на каждый refill
"""

from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from collections import deque
import math

# Избегаем circular imports
if TYPE_CHECKING:
    from analyzers_derivatives import DerivativesAnalyzer
    from domain import HistoricalMemory

@dataclass
class FeatureSnapshot:
    """
    WHY: Полный снимок всех метрик в момент айсберг-события.
    
    Используется для:
    - ML feature engineering (XGBoost/CatBoost)
    - Ретроспективного анализа важности метрик
    - A/B тестирования детекторов
    
    NOTE: Все поля Optional - если метрика недоступна, сохраняем NULL в БД.
    """
    snapshot_time: datetime
    
    # === ORDERBOOK METRICS ===
    # VULNERABILITY A FIX: Vectorized OBI (multiple depths)
    obi_L1: Optional[float] = None                 # OBI at L1 (spread, tactical HFT)
    obi_L5: Optional[float] = None                 # OBI at L5 (main liquidity)
    obi_L20: Optional[float] = None                # OBI at L20 (strategic depth)
    
    # VULNERABILITY C FIX: Dual OFI (depth + volume based)
    ofi_depth: Optional[float] = None              # Depth-based OFI (fast, HFT sensitive)
    ofi_volume: Optional[float] = None             # Volume-based OFI (shift-resistant)
    
    spread_bps: Optional[float] = None             # Спред в basis points
    depth_ratio: Optional[float] = None            # Bid depth / Ask depth
    
    # === FLOW METRICS (CVD) ===
    whale_cvd: Optional[float] = None              # CVD китов (>$100k)
    fish_cvd: Optional[float] = None               # CVD рыб (<$1k)
    dolphin_cvd: Optional[float] = None            # CVD дельфинов ($1k-$100k)
    whale_cvd_delta_5m: Optional[float] = None     # Изменение whale CVD за 5 мин
    total_cvd: Optional[float] = None              # Общий CVD
    
    # === DERIVATIVES METRICS ===
    futures_basis_apr: Optional[float] = None      # Аннуализированный базис
    basis_state: Optional[str] = None              # 'NORMAL'|'OPTIMISTIC'|...
    options_skew: Optional[float] = None           # Put IV - Call IV
    skew_state: Optional[str] = None               # 'FEAR'|'NEUTRAL'|...
    total_gex: Optional[float] = None              # Суммарная гамма
    dist_to_gamma_wall: Optional[float] = None     # Расстояние до GEX wall (%)
    gamma_wall_type: Optional[str] = None          # 'CALL'|'PUT'|NULL
    
    # === PRICE METRICS ===
    current_price: Optional[float] = None          # Текущая mid price
    twap_5m: Optional[float] = None                # 5-минутная TWAP
    price_vs_twap_pct: Optional[float] = None      # (Price - TWAP) / TWAP * 100
    volatility_1h: Optional[float] = None          # Реализованная волатильность
    
    # === SPOOFING DETECTION ===
    spoofing_score: Optional[float] = None         # 0-100
    cancel_ratio_5m: Optional[float] = None        # Доля отмененных ордеров
    
    # === VPIN (FLOW TOXICITY) ===
    vpin_score: Optional[float] = None             # 0.0-1.0 (VPIN токсичность)
    vpin_level: Optional[str] = None               # 'EXTREME'|'HIGH'|'MODERATE'|'LOW'|'MINIMAL'
    
    # === MARKET REGIME (заполним позже) ===
    trend_regime: Optional[str] = None             # 'UPTREND'|'DOWNTREND'|'RANGING'
    volatility_regime: Optional[str] = None        # 'LOW'|'MEDIUM'|'HIGH'
    
    # === SAFE CHANGE: SMART MONEY CONTEXT ===
    # WHY: Данные из HistoricalMemory (заполняются пока None, логику добавим позже)
    
    # Тренды китов (CVD Trends)
    whale_cvd_trend_1w: Optional[float] = None     # Изменение whale CVD за неделю
    whale_cvd_trend_1m: Optional[float] = None     # Изменение whale CVD за месяц
    whale_cvd_trend_3m: Optional[float] = None     # КВАРТАЛ (3 месяца) - институциональная ребалансировка
    whale_cvd_trend_6m: Optional[float] = None     # GLOBAL CONTEXT (6 месяцев)
    
    # Вайкофф и Макро
    is_htf_divergence: Optional[int] = None        # 1 (bullish) / -1 (bearish) / None
    basis_regime_weekly: Optional[str] = None      # 'CONTANGO' | 'BACKWARDATION' | None


class FeatureCollector:
    """
    WHY: Собирает снимок всех метрик для ML.
    
    Архитектура:
    - Неблокирующий (только чтение из кеша)
    - Толерантный к отсутствию данных (None вместо exception)
    - Минимальные зависимости (композиция, не наследование)
    
    Использование:
        collector = FeatureCollector(flow_analyzer, derivatives_analyzer, ...)
        snapshot = await collector.capture_snapshot()
    """
    
    def __init__(
        self,
        # Обязательные зависимости
        order_book=None,              # LocalOrderBook для spread/depth
        flow_analyzer=None,            # OrderFlowAnalyzer для OBI/CVD
        
        # Опциональные зависимости (могут быть None)
        derivatives_analyzer: Optional['DerivativesAnalyzer'] = None,
        spoofing_detector=None,
        gamma_provider=None,           # DeribitGammaProvider для GEX
        flow_toxicity_analyzer=None    # FlowToxicityAnalyzer для VPIN (НОВОЕ)
    ):
        """
        WHY: Инициализирует коллектор с зависимостями.
        
        Args:
            order_book: LocalOrderBook (spread, depth)
            flow_analyzer: OrderFlowAnalyzer (OBI, CVD)
            derivatives_analyzer: DerivativesAnalyzer (basis, skew) - опционально
            spoofing_detector: SpoofingDetector - опционально
            gamma_provider: DeribitGammaProvider - опционально
            flow_toxicity_analyzer: FlowToxicityAnalyzer - опционально
        
        NOTE: Если зависимость None, соответствующие метрики будут NULL.
        """
        self.order_book = order_book
        self.flow = flow_analyzer
        self.derivatives = derivatives_analyzer
        self.spoofing = spoofing_detector
        self.gamma = gamma_provider
        self.flow_toxicity = flow_toxicity_analyzer  # НОВОЕ
        
        # Price history для TWAP/volatility (5 минут при 1 сек)
        self.price_history: deque = deque(maxlen=300)
        
        # === FIX VULNERABILITY A: CVD Stationarity (State-based Delta) ===
        # WHY: Предотвращает non-stationary features в ML
        # Теория: ML модели требуют ДЕЛЬТУ CVD между событиями, а не абсолютный счетчик
        # Решение: Хранить last value, возвращать current - last
        self._last_whale_cvd: float = 0.0
        self._last_minnow_cvd: float = 0.0
        self._last_dolphin_cvd: float = 0.0
        
        # === НОВОЕ: Derivatives cache (ШАГ 6.5) ===
        # WHY: Кешируем basis/skew для неблокирующего capture_snapshot()
        self.cached_basis: Optional[float] = None  # Обновляется через _feed_derivatives_cache()
        self.cached_skew: Optional[float] = None   # Обновляется через _feed_derivatives_cache()
        
        # === FIX VULNERABILITY #4: Cold Start Warm-Up ===
        # WHY: Предотвращает мусорные снапшоты на холодном старте
        # Теория: ML модели требуют чистых данных, без артефактов инициализации
        self.is_warmed_up: bool = False  # Флаг готовности к работе
    
    def update_price(self, price: float):
        """
        WHY: Обновляет историю цен для расчета TWAP/volatility.
        
        Вызывать на каждом тике (или хотя бы раз в секунду).
        """
        self.price_history.append({
            'time': datetime.now(timezone.utc),
            'price': price
        })
    
    # ========================================================================
    # FIX VULNERABILITY #4: Cold Start Warm-Up Methods
    # ========================================================================
    
    def is_ready(self) -> bool:
        """
        WHY: Проверяет критерии готовности к созданию снапшотов.
        
        Критерии warm-up (Гемини Валидация):
        1. State инициализирован: _last_whale_cvd != 0.0
           WHY: Предотвращает whale_cvd=0.0 в первом снапшоте
        
        2. Price history заполнена: len(price_history) >= 60
           WHY: 60 секунд = минимум для TWAP/volatility
        
        Returns:
            bool: True если готов к работе
        
        Example:
            >>> collector = FeatureCollector(order_book)
            >>> collector.is_ready()  # False (холодный старт)
            False
            >>> # ... 60 секунд работы ...
            >>> collector.is_ready()  # True
            True
        """
        # Критерий 1: State инициализирован
        # WHY: Хотя бы один CVD state должен быть не 0
        state_initialized = (
            self._last_whale_cvd != 0.0 or
            self._last_minnow_cvd != 0.0 or
            self._last_dolphin_cvd != 0.0
        )
        
        # Критерий 2: Price history заполнена
        # WHY: 60 точек = 1 минута при 1 Hz частоте update_price
        has_price_history = len(self.price_history) >= 60
        
        return state_initialized and has_price_history
    
    def check_warmup(self) -> None:
        """
        WHY: Автоматически устанавливает флаг is_warmed_up при готовности.
        
        Использование:
            # В TradingEngine после каждого update_price:
            feature_collector.update_price(mid_price)
            feature_collector.check_warmup()  # Auto-update флага
        
        Side effects:
            Устанавливает self.is_warmed_up = True когда is_ready() == True
        """
        if not self.is_warmed_up and self.is_ready():
            self.is_warmed_up = True
    
    def capture_snapshot(
        self,
        event_time: Optional[datetime] = None,
        historical_memory: Optional['HistoricalMemory'] = None,
        iceberg: Optional['IcebergLevel'] = None,
        skip_warmup_check: bool = False
    ) -> Optional[FeatureSnapshot]:
        """
        WHY: Собирает текущее состояние ВСЕХ метрик.
        
        FIX VULNERABILITY #2: Clock Skew (Python vs Postgres)
        - event_time берется из OrderBookUpdate.event_time (Exchange source of truth)
        - Гарантирует синхронизацию с SQL date_bin() при merge_asof
        - Устраняет processing lag и clock drift между контейнерами
        
        FIX VULNERABILITY #4: Cold Start Warm-Up
        - Возвращает None если is_warmed_up == False
        - Предотвращает мусорные снапшоты в ML датасете
        
        Гарантии:
        - Неблокирующий (только чтение из памяти)
        - Никаких сетевых запросов
        - Толерантен к отсутствию данных (возвращает None)
        - Всегда обновляет state для CVD delta (критично!)
        - Time-Deterministic: одинаковый event_time = одинаковый snapshot_time
        
        Args:
            event_time: Exchange timestamp (from OrderBookUpdate.event_time). If None, fallback to datetime.now()
            historical_memory: Исторические данные для whale CVD trends
            iceberg: Айсберг для spoofing features (опционально)
            skip_warmup_check: True = skip warm-up check (для unit-тестов Clock Skew)
        
        Returns:
            FeatureSnapshot: Снапшот с заполненными полями
            None: Если is_warmed_up == False (холодный старт)
        """
        # FIX VULNERABILITY #2: Use exchange time if provided, fallback to now()
        now = event_time if event_time else datetime.now(timezone.utc)
        
        # === FIX VULNERABILITY #4: Cold Start Guard ===
        # WHY: Не возвращаем мусорные снапшоты на холодном старте
        # Критерии: state инициализирован + 60 секунд price history
        # EXCEPT: skip_warmup_check=True для unit-тестов (тестируют другую логику)
        if not skip_warmup_check and not self.is_warmed_up:
            return None
        
        # === VULNERABILITY A FIX: Vectorized OBI (multiple depths) ===
        obi_L1 = self._get_vectorized_obi(depth=1)
        obi_L5 = self._get_vectorized_obi(depth=5)
        obi_L20 = self._get_vectorized_obi(depth=20)
        
        # === VULNERABILITY C FIX: Dual OFI ===
        ofi_depth = self._get_depth_ofi()  # Existing calculate_ofi()
        ofi_volume = self._get_volume_ofi()  # New volume-based
        
        spread_bps = self._get_spread_bps()
        depth_ratio = self._get_depth_ratio()
        
        # === FLOW METRICS ===
        whale_cvd = self._get_whale_cvd()
        fish_cvd = self._get_fish_cvd()
        dolphin_cvd = self._get_dolphin_cvd()
        # WHY: Используем уже вычисленные значения вместо повторного вызова методов
        total_cvd = self._calculate_total_cvd(whale_cvd, fish_cvd, dolphin_cvd)
        whale_cvd_delta = self._get_whale_cvd_delta(minutes=5)
        
        # === DERIVATIVES METRICS ===
        basis_apr = self._get_cached_basis()
        basis_state = self._get_basis_state(basis_apr)
        skew = self._get_cached_skew()
        skew_state = self._get_skew_state(skew)
        total_gex = self._get_total_gex()
        dist_gamma, gamma_type = self._get_gamma_wall_info()
        
        # === PRICE METRICS ===
        # FIX VULNERABILITY #4: _get_current_price() теперь возвращает Decimal
        current_price_decimal = self._get_current_price()
        # Конвертируем в float ТОЛЬКО для FeatureSnapshot (DB storage)
        current_price = float(current_price_decimal) if current_price_decimal else None
        twap_5m = self._calculate_twap(minutes=5)
        price_vs_twap = self._calculate_price_vs_twap(current_price, twap_5m)
        volatility_1h = self._calculate_volatility(minutes=60)
        
        # === SPOOFING ===
        # GEMINI FIX: Используем decayed confidence если iceberg передан
        spoofing_score = None
        if iceberg is not None:
            # Используем get_iceberg_features() для получения decayed confidence
            iceberg_features = self.get_iceberg_features(iceberg, now, half_life_seconds=300)
            spoofing_score = iceberg_features['spoofing_probability']
        
        cancel_ratio = None  # TODO: Добавить когда будет tracking отмен
        
        # === VPIN (НОВОЕ) ===
        vpin_score = self._get_vpin_score()
        vpin_level = self._get_vpin_level()
        
        # === SMART MONEY CONTEXT (Step 2): Whale CVD Trends ===
        # WHY: Заполняем макро-контекст из HistoricalMemory
        whale_cvd_trend_1w = None
        whale_cvd_trend_1m = None
        whale_cvd_trend_3m = None  # КВАРТАЛ
        whale_cvd_trend_6m = None
        
        if historical_memory:
            # Тренды китов (CVD Trends)
            # WHY: 1W = 7 дней дневных баров
            whale_cvd_trend_1w = self._calculate_trend(
                historical_memory.cvd_history_1d,
                bars_count=7
            )
            
            # WHY: 1M = 30 дней
            whale_cvd_trend_1m = self._calculate_trend(
                historical_memory.cvd_history_1d,
                bars_count=30
            )
            
            # WHY: 3M = 90 дней (КВАРТАЛ - институциональная ребалансировка)
            whale_cvd_trend_3m = self._calculate_trend(
                historical_memory.cvd_history_1d,
                bars_count=90
            )
            
            # WHY: 6M = 180 дней (ГЛАВНЫЙ ФАКТОР свинг-трейдинга)
            whale_cvd_trend_6m = self._calculate_trend(
                historical_memory.cvd_history_1d,
                bars_count=180
            )
        
        snapshot = FeatureSnapshot(
            snapshot_time=now,
            
            # === VULNERABILITY A & C FIXES ===
            obi_L1=obi_L1,
            obi_L5=obi_L5,
            obi_L20=obi_L20,
            ofi_depth=ofi_depth,
            ofi_volume=ofi_volume,
            spread_bps=spread_bps,
            depth_ratio=depth_ratio,
            
            # Flow
            whale_cvd=whale_cvd,
            fish_cvd=fish_cvd,
            dolphin_cvd=dolphin_cvd,
            whale_cvd_delta_5m=whale_cvd_delta,
            total_cvd=total_cvd,
            
            # Derivatives
            futures_basis_apr=basis_apr,
            basis_state=basis_state,
            options_skew=skew,
            skew_state=skew_state,
            total_gex=total_gex,
            dist_to_gamma_wall=dist_gamma,
            gamma_wall_type=gamma_type,
            
            # Price
            current_price=current_price,
            twap_5m=twap_5m,
            price_vs_twap_pct=price_vs_twap,
            volatility_1h=volatility_1h,
            
            # Spoofing
            spoofing_score=spoofing_score,
            cancel_ratio_5m=cancel_ratio,
            
            # VPIN (НОВОЕ)
            vpin_score=vpin_score,
            vpin_level=vpin_level,
            
            # Smart Money Context (Step 2)
            whale_cvd_trend_1w=whale_cvd_trend_1w,
            whale_cvd_trend_1m=whale_cvd_trend_1m,
            whale_cvd_trend_3m=whale_cvd_trend_3m,  # КВАРТАЛ
            whale_cvd_trend_6m=whale_cvd_trend_6m
        )
        
        return snapshot
    
    # ========================================================================
    # GEMINI FIX: Iceberg Confidence with Decay (Fix Zombie Icebergs)
    # ========================================================================
    
    def get_iceberg_features(
        self,
        iceberg: 'IcebergLevel',
        current_time: datetime,
        half_life_seconds: float = 300.0
    ) -> dict:
        """
        WHY: Извлекает features айсберга с DECAYED confidence (Fix: Zombie Icebergs).
        
        ПРОБЛЕМА (Gemini Validation):
        - Раньше: confidence = iceberg.confidence_score (статический)
        - ML features загрязнялись зомби-айсбергами
        
        РЕШЕНИЕ:
        - Теперь: confidence = iceberg.get_decayed_confidence(now)
        - Айсберги без обновлений автоматически теряют confidence
        
        ИСПОЛЬЗОВАНИЕ:
        ```python
        # При сохранении в БД или ML features:
        for price, iceberg in order_book.active_icebergs.items():
            features = feature_collector.get_iceberg_features(
                iceberg, 
                current_time=datetime.now(),
                half_life_seconds=300  # 5 мин для swing
            )
            
            # features['confidence'] = decayed confidence
            # features['is_significant'] = True если confidence > 0.3
        ```
        
        Args:
            iceberg: IcebergLevel для анализа
            current_time: Текущее время (datetime.now())
            half_life_seconds: Период полураспада (default: 300 = swing)
        
        Returns:
            dict: {
                'confidence': float (decayed),
                'confidence_raw': float (исходный),
                'is_significant': bool (confidence > 0.3),
                'time_since_update_sec': float,
                'refill_count': int,
                'refill_frequency': float,
                'is_gamma_wall': bool,
                'spoofing_probability': float
            }
        
        Example:
            >>> iceberg = IcebergLevel(
            ...     price=Decimal('60000'),
            ...     confidence_score=0.9,
            ...     last_update_time=now - timedelta(minutes=10)
            ... )
            >>> features = collector.get_iceberg_features(iceberg, now)
            >>> assert features['confidence'] < 0.3  # Decayed!
            >>> assert features['is_significant'] == False  # Filtered out
        """
        # 1. Вычисляем decayed confidence
        decayed_confidence = iceberg.get_decayed_confidence(
            current_time, 
            half_life_seconds=half_life_seconds
        )
        
        # 2. Время без обновлений
        time_since_update = (current_time - iceberg.last_update_time).total_seconds()
        
        # 3. Значимость для ML (threshold: 0.3)
        is_significant = decayed_confidence > 0.3
        
        return {
            'confidence': decayed_confidence,
            'confidence_raw': iceberg.confidence_score,
            'is_significant': is_significant,
            'time_since_update_sec': time_since_update,
            'refill_count': iceberg.refill_count,
            'refill_frequency': iceberg.get_refill_frequency(),
            'is_gamma_wall': iceberg.is_gamma_wall,
            'spoofing_probability': iceberg.spoofing_probability
        }
    
    # ========================================================================
    # TREND CALCULATION: Smart Money Context (Step 2)
    # ========================================================================
    
    def _calculate_trend(self, history: deque, bars_count: int) -> Optional[float]:
        """
        WHY: Считает изменение CVD за последние N баров.
        
        Теория (Gemini Step 2):
        - Тренд = current_value - old_value
        - history[-1] = последнее значение (текущее)
        - history[-(N+1)] = значение N баров назад
        
        Args:
            history: deque с данными формата [(timestamp, cvd), ...]
            bars_count: Количество баров для анализа
        
        Returns:
            float: Изменение CVD (положительное = накопление)
            None: Если недостаточно данных
        
        Examples:
            >>> # 7 дней роста CVD
            >>> history = deque([(datetime(2025,1,i), 1000+i*100) for i in range(7)])
            >>> trend = collector._calculate_trend(history, bars_count=7)
            >>> assert trend == 600.0  # 1600 - 1000
        """
        # 1. Проверка минимальных данных
        if not history or len(history) < 2:
            return None
        
        # 2. Определяем lookback (сколько баров назад смотрим)
        # WHY: Если истории меньше чем просим, берём сколько есть
        lookback = min(len(history) - 1, bars_count)
        
        if lookback <= 0:
            return 0.0
        
        # 3. Извлекаем значения CVD (второй элемент tuple)
        # WHY: history содержит [(timestamp, cvd), ...]
        current_val = history[-1][1]  # Последнее значение
        old_val = history[-(lookback + 1)][1]  # Значение lookback баров назад
        
        # 4. Рассчитываем изменение
        return float(current_val - old_val)
    
    # ========================================================================
    # PRIVATE HELPERS: Безопасное извлечение метрик
    # ========================================================================
    
    def _get_obi(self) -> Optional[float]:
        """Order Book Imbalance - читаем из LocalOrderBook"""
        if not self.order_book:
            return None
        try:
            # WHY: LocalOrderBook имеет метод get_weighted_obi() для расчета OBI
            return self.order_book.get_weighted_obi(depth=20, use_exponential=True)
        except:
            return None
    
    def _get_ofi(self) -> Optional[float]:
        """
        FIX (Gemini Validation): Читаем OFI напрямую из book.
        
        WHY: LocalOrderBook.calculate_ofi() уже вызывается в services.py при каждой сделке.
        Просто берем актуальное значение, как с OBI и CVD.
        
        Returns:
            float: Order Flow Imbalance
            None: Если book недоступен
        """
        if not self.order_book:
            return None
        try:
            return self.order_book.calculate_ofi()
        except:
            return None
    
    def _get_whale_cvd(self) -> Optional[float]:
        """
        FIX VULNERABILITY A: Возвращает ДЕЛЬТУ CVD китов с прошлого вызова.
        
        WHY: ML модели требуют stationary features (дельты), не абсолютные счетчики.
        
        Теория:
        - Абсолют: whale_cvd(t) = whale_cvd(0) + Σsigned_volumes (random walk)
        - Дельта: whale_cvd_delta(t) = current - last (осциллирует вокруг 0)
        
        Returns:
            float: Изменение CVD с прошлого вызова (stationary)
            0.0: Первый вызов (инициализация state)
            None: order_book недоступен
        """
        if not self.order_book:
            return None
        
        try:
            # 1. Получаем текущий АБСОЛЮТ из order_book
            if not (hasattr(self.order_book, 'whale_cvd') and isinstance(self.order_book.whale_cvd, dict)):
                return None
            
            current_abs = float(self.order_book.whale_cvd.get('whale', 0))
            
            # 2. Первый вызов - инициализируем state
            if self._last_whale_cvd == 0.0:
                self._last_whale_cvd = current_abs
                return 0.0  # Нет дельты на первом вызове
            
            # 3. Вычисляем дельту
            delta = current_abs - self._last_whale_cvd
            
            # 4. Обновляем state
            self._last_whale_cvd = current_abs
            
            # 5. Возвращаем дельту
            return delta
            
        except Exception:
            return None
    
    def _get_fish_cvd(self) -> Optional[float]:
        """
        FIX VULNERABILITY A: Возвращает ДЕЛЬТУ CVD рыб (minnow) с прошлого вызова.
        
        WHY: ML модели требуют stationary features (дельты), не абсолютные счетчики.
        
        Returns:
            float: Изменение CVD с прошлого вызова (stationary)
            0.0: Первый вызов (инициализация state)
            None: order_book недоступен
        """
        if not self.order_book:
            return None
        
        try:
            # 1. Получаем текущий АБСОЛЮТ из order_book (ключ 'minnow', а не 'fish')
            if not (hasattr(self.order_book, 'whale_cvd') and isinstance(self.order_book.whale_cvd, dict)):
                return None
            
            current_abs = float(self.order_book.whale_cvd.get('minnow', 0))
            
            # 2. Первый вызов - инициализируем state
            if self._last_minnow_cvd == 0.0:
                self._last_minnow_cvd = current_abs
                return 0.0
            
            # 3. Вычисляем дельту
            delta = current_abs - self._last_minnow_cvd
            
            # 4. Обновляем state
            self._last_minnow_cvd = current_abs
            
            # 5. Возвращаем дельту
            return delta
            
        except Exception:
            return None
    
    def _get_dolphin_cvd(self) -> Optional[float]:
        """
        FIX VULNERABILITY A: Возвращает ДЕЛЬТУ CVD дельфинов с прошлого вызова.
        
        WHY: ML модели требуют stationary features (дельты), не абсолютные счетчики.
        
        Returns:
            float: Изменение CVD с прошлого вызова (stationary)
            0.0: Первый вызов (инициализация state)
            None: order_book недоступен
        """
        if not self.order_book:
            return None
        
        try:
            # 1. Получаем текущий АБСОЛЮТ из order_book
            if not (hasattr(self.order_book, 'whale_cvd') and isinstance(self.order_book.whale_cvd, dict)):
                return None
            
            current_abs = float(self.order_book.whale_cvd.get('dolphin', 0))
            
            # 2. Первый вызов - инициализируем state
            if self._last_dolphin_cvd == 0.0:
                self._last_dolphin_cvd = current_abs
                return 0.0
            
            # 3. Вычисляем дельту
            delta = current_abs - self._last_dolphin_cvd
            
            # 4. Обновляем state
            self._last_dolphin_cvd = current_abs
            
            # 5. Возвращаем дельту
            return delta
            
        except Exception:
            return None
    
    def _calculate_total_cvd(
        self,
        whale: Optional[float],
        fish: Optional[float],
        dolphin: Optional[float]
    ) -> Optional[float]:
        """
        WHY: Вычисляет total CVD из уже полученных значений.
        
        НЕ вызывает _get_*_cvd() повторно, чтобы избежать двойного обновления state.
        
        Args:
            whale: Delta whale CVD
            fish: Delta fish CVD  
            dolphin: Delta dolphin CVD
        
        Returns:
            Сумма всех CVD или None если все None
        """
        if whale is None and fish is None and dolphin is None:
            return None
        
        return (whale or 0) + (fish or 0) + (dolphin or 0)
    
    
    
    # ========================================================================
    # VULNERABILITY FIXES: Vectorized OBI + Dual OFI
    # ========================================================================
    
    def _get_vectorized_obi(self, depth: int) -> Optional[float]:
        """
        WHY: Calculates OBI at specific depth (L1/L5/L20).
        
        VULNERABILITY A FIX: Provides multiple depth perspectives.
        
        Args:
            depth: Number of price levels (1, 5, or 20)
        
        Returns:
            float: OBI at this depth or None
        """
        if not self.order_book:
            return None
        try:
            # use_exponential=True for L5/L20 (anti-spoofing)
            # use_exponential=False for L1 (raw spread signal)
            use_exp = (depth > 1)
            return self.order_book.get_weighted_obi(depth=depth, use_exponential=use_exp)
        except:
            return None
    
    def _get_depth_ofi(self) -> Optional[float]:
        """
        WHY: Depth-based OFI (existing calculate_ofi method).
        
        VULNERABILITY C FIX: Fast baseline OFI.
        
        Returns:
            float: OFI value or None
        """
        if not self.order_book:
            return None
        try:
            return self.order_book.calculate_ofi()
        except:
            return None
    
    def _get_volume_ofi(self) -> Optional[float]:
        """
        WHY: Volume-based OFI (new method from domain.py).
        
        VULNERABILITY C FIX: Price-shift resistant OFI.
        
        Returns:
            float: Volume-based OFI or None
        """
        if not self.order_book:
            return None
        try:
            # Use config.spoofing_volume_threshold * 5 as target_volume
            # WHY: ~50 BTC for BTCUSDT, adaptive per asset
            target_vol = float(self.order_book.config.spoofing_volume_threshold) * 5
            return self.order_book.get_volume_based_ofi(target_volume=target_vol)
        except:
            return None
    
    def _get_whale_cvd_delta(self, minutes: int) -> Optional[float]:
        """Изменение whale CVD за N минут"""
        # TODO: Требует исторического tracking
        # Пока возвращаем None
        return None
    
    def _get_spread_bps(self) -> Optional[float]:
        """Спред в basis points"""
        if not self.order_book:
            return None
        try:
            # FIX: LocalOrderBook использует методы get_best_bid/ask(), а не поля
            bid_tuple = self.order_book.get_best_bid()  # Returns (price, qty) or None
            ask_tuple = self.order_book.get_best_ask()  # Returns (price, qty) or None
            
            if bid_tuple and ask_tuple:
                bid = bid_tuple[0]  # Извлекаем цену
                ask = ask_tuple[0]
                mid = (bid + ask) / 2
                spread = ask - bid
                return float((spread / mid) * 10000)  # basis points
        except:
            pass
        return None
    
    def _get_depth_ratio(self) -> Optional[float]:
        """Bid depth / Ask depth (top 10 levels)"""
        if not self.order_book:
            return None
        try:
            bid_depth = sum(v for p, v in list(self.order_book.bids.items())[:10])
            ask_depth = sum(v for p, v in list(self.order_book.asks.items())[:10])
            if ask_depth > 0:
                return float(bid_depth / ask_depth)
        except:
            pass
        return None
    
    def _get_cached_basis(self) -> Optional[float]:
        """
        WHY: Возвращает кешированный futures basis APR.
        
        Кеш обновляется через _feed_derivatives_cache() в TradingEngine.
        """
        return self.cached_basis
    
    def _get_basis_state(self, basis_apr: Optional[float]) -> Optional[str]:
        """Интерпретация basis"""
        if basis_apr is None:
            return None
        
        # Упрощенная логика (без DerivativesAnalyzer)
        if basis_apr > 20:
            return 'EXTREME_CONTANGO'  # Перегрев
        elif basis_apr > 10:
            return 'CONTANGO'  # Нормальное состояние
        elif basis_apr < -5:
            return 'BACKWARDATION'  # Медвежий страх
        else:
            return 'NEUTRAL'
    
    def _get_cached_skew(self) -> Optional[float]:
        """
        WHY: Возвращает кешированный options skew.
        
        Кеш обновляется через _feed_derivatives_cache() в TradingEngine.
        """
        return self.cached_skew
    
    def _get_skew_state(self, skew: Optional[float]) -> Optional[str]:
        """Интерпретация skew"""
        if skew is None:
            return None
        
        # Упрощенная логика
        if skew > 10:
            return 'EXTREME_FEAR'  # Путы значительно дороже
        elif skew > 5:
            return 'FEAR'  # Умеренный страх
        elif skew < -5:
            return 'GREED'  # Коллы дороже (редко)
        else:
            return 'NEUTRAL'
    
    def _get_total_gex(self) -> Optional[float]:
        """Суммарная гамма-экспозиция"""
        if not self.gamma:
            return None
        try:
            return self.gamma.get_total_gex()  # Предполагаем что метод есть
        except:
            return None
    
    def _get_gamma_wall_info(self) -> tuple[Optional[float], Optional[str]]:
        """
        Расстояние до ближайшей gamma wall и её тип.
        
        FIX VULNERABILITY #4: Передаём Decimal в get_gamma_wall_distance().
        """
        if not self.gamma:
            return None, None
        try:
            # FIX: _get_current_price() теперь возвращает Decimal
            current_price_decimal = self._get_current_price()
            if current_price_decimal is None:
                return None, None
            
            # Передаём Decimal в get_gamma_wall_distance() (не float!)
            return self.gamma.get_gamma_wall_distance(current_price_decimal)
        except:
            return None, None
    
    def _get_current_price(self) -> Optional[Decimal]:
        """
        FIX VULNERABILITY #4: Возвращает Decimal mid price.
        
        WHY: Для точного сравнения с gamma_profile.call/put_wall.
        Конвертация в float происходит ТОЛЬКО при записи в FeatureSnapshot.
        """
        if not self.order_book:
            return None
        try:
            # FIX: LocalOrderBook использует методы get_best_bid/ask(), а не поля
            bid_tuple = self.order_book.get_best_bid()  # Returns (price, qty) or None
            ask_tuple = self.order_book.get_best_ask()  # Returns (price, qty) or None
            
            if bid_tuple and ask_tuple:
                bid = bid_tuple[0]  # Decimal price
                ask = ask_tuple[0]  # Decimal price
                return (bid + ask) / Decimal("2")  # Decimal mid price
        except:
            pass
        return None
    
    def _calculate_twap(self, minutes: int) -> Optional[float]:
        """Time-Weighted Average Price за N минут"""
        if len(self.price_history) < 2:
            return None
        
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
        recent_prices = [p['price'] for p in self.price_history if p['time'] >= cutoff]
        
        if not recent_prices:
            return None
        
        return sum(recent_prices) / len(recent_prices)
    
    def _calculate_price_vs_twap(
        self, 
        current: Optional[float], 
        twap: Optional[float]
    ) -> Optional[float]:
        """(Price - TWAP) / TWAP * 100"""
        if current is None or twap is None or twap == 0:
            return None
        
        return ((current - twap) / twap) * 100
    
    def _calculate_volatility(self, minutes: int) -> Optional[float]:
        """Реализованная волатильность (std dev of returns)"""
        if len(self.price_history) < 10:  # Минимум 10 точек
            return None
        
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
        recent_prices = [p['price'] for p in self.price_history if p['time'] >= cutoff]
        
        if len(recent_prices) < 10:
            return None
        
        # Рассчитываем log returns
        returns = []
        for i in range(1, len(recent_prices)):
            if recent_prices[i-1] > 0:
                ret = math.log(recent_prices[i] / recent_prices[i-1])
                returns.append(ret)
        
        if not returns:
            return None
        
        # Std dev
        mean_ret = sum(returns) / len(returns)
        variance = sum((r - mean_ret) ** 2 for r in returns) / len(returns)
        std_dev = math.sqrt(variance)
        
        # Аннуализируем (предполагаем 1 сек между точками)
        annualized = std_dev * math.sqrt(365 * 24 * 60 * 60)
        
        return annualized * 100  # В процентах
    
    def _get_spoofing_score(self, iceberg: Optional['IcebergLevel'] = None) -> Optional[float]:
        """Текущий spoofing confidence score"""
        # WHY: SpoofingAnalyzer - static methods, нужен конкретный iceberg
        if iceberg is None or not hasattr(iceberg, 'spoofing_probability'):
            return None
        return iceberg.spoofing_probability
    
    # ========================================================================
    # VPIN HELPERS (НОВОЕ)
    # ========================================================================
    
    def _get_vpin_score(self) -> Optional[float]:
        """
        WHY: Возвращает текущий VPIN score (0.0-1.0).
        
        Читает из FlowToxicityAnalyzer.get_current_vpin()
        """
        if not self.flow_toxicity:
            return None
        try:
            return self.flow_toxicity.get_current_vpin()
        except:
            return None
    
    def _get_vpin_level(self) -> Optional[str]:
        """
        WHY: Возвращает категориальный уровень токсичности.
        
        Levels: 'EXTREME', 'HIGH', 'MODERATE', 'LOW', 'MINIMAL', 'UNKNOWN'
        """
        if not self.flow_toxicity:
            return None
        try:
            return self.flow_toxicity.get_toxicity_level()
        except:
            return None
