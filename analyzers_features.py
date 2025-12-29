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
    obi_value: Optional[float] = None              # Order Book Imbalance
    ofi_value: Optional[float] = None              # Order Flow Imbalance
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
        
        # OFI tracking (для Order Flow Imbalance)
        self._prev_bid_depth: Optional[float] = None
        self._prev_ask_depth: Optional[float] = None
        self._ofi_value: float = 0.0
        
        # === НОВОЕ: Derivatives cache (ШАГ 6.5) ===
        # WHY: Кешируем basis/skew для неблокирующего capture_snapshot()
        self.cached_basis: Optional[float] = None  # Обновляется через _feed_derivatives_cache()
        self.cached_skew: Optional[float] = None   # Обновляется через _feed_derivatives_cache()
        
        # === НОВОЕ: ADAPTIVE THROTTLING (Gemini рекомендация) ===
        # WHY: Предотвращаем перегрузку БД при лавинообразных рефиллах айсбергов
        self._last_snapshot_time: Optional[datetime] = None
        self._last_snapshot_cache: Optional[FeatureSnapshot] = None
        self.throttle_interval_ms: int = 100  # Минимальный интервал между снапшотами
    
    def update_price(self, price: float):
        """
        WHY: Обновляет историю цен для расчета TWAP/volatility.
        
        Вызывать на каждом тике (или хотя бы раз в секунду).
        """
        self.price_history.append({
            'time': datetime.now(timezone.utc),
            'price': price
        })
    
    def update_ofi(self, bid_depth: float, ask_depth: float):
        """
        WHY: Обновляет Order Flow Imbalance.
        
        Теория: OFI = ΔBid - ΔAsk (изменение объемов на лучших уровнях).
        Положительный OFI → давление покупателей.
        
        Args:
            bid_depth: Объем на best bid
            ask_depth: Объем на best ask
        """
        if self._prev_bid_depth is not None:
            delta_bid = bid_depth - self._prev_bid_depth
            delta_ask = ask_depth - self._prev_ask_depth
            
            self._ofi_value = delta_bid - delta_ask
        
        self._prev_bid_depth = bid_depth
        self._prev_ask_depth = ask_depth
    
    def capture_snapshot(
        self,
        historical_memory: Optional['HistoricalMemory'] = None
    ) -> FeatureSnapshot:
        """
        WHY: Собирает текущее состояние ВСЕХ метрик.
        
        Гарантии:
        - Неблокирующий (только чтение из памяти)
        - Никаких сетевых запросов
        - Толерантен к отсутствию данных (возвращает None)
        - THROTTLED: не чаще раза в 100 мс (предотвращает перегрузку БД)
        
        Returns:
            FeatureSnapshot с заполненными полями (None если метрика недоступна)
        """
        now = datetime.now(timezone.utc)
        
        # === ADAPTIVE THROTTLING: Проверка elapsed time ===
        # WHY: При лавинообразных рефиллах айсбергов может быть сотни событий в секунду
        # Чтобы не перегружать БД, возвращаем кешированный снапшот если < 100 мс
        if self._last_snapshot_time is not None:
            elapsed_ms = (now - self._last_snapshot_time).total_seconds() * 1000
            
            if elapsed_ms < self.throttle_interval_ms:
                # Слишком рано - возвращаем кешированный снапшот
                if self._last_snapshot_cache is not None:
                    return self._last_snapshot_cache
                # Если кеша нет (первый вызов) - продолжаем создание
        
        # === ORDERBOOK METRICS ===
        obi = self._get_obi()
        ofi = self._ofi_value
        spread_bps = self._get_spread_bps()
        depth_ratio = self._get_depth_ratio()
        
        # === FLOW METRICS ===
        whale_cvd = self._get_whale_cvd()
        fish_cvd = self._get_fish_cvd()
        dolphin_cvd = self._get_dolphin_cvd()
        total_cvd = self._get_total_cvd()
        whale_cvd_delta = self._get_whale_cvd_delta(minutes=5)
        
        # === DERIVATIVES METRICS ===
        basis_apr = self._get_cached_basis()
        basis_state = self._get_basis_state(basis_apr)
        skew = self._get_cached_skew()
        skew_state = self._get_skew_state(skew)
        total_gex = self._get_total_gex()
        dist_gamma, gamma_type = self._get_gamma_wall_info()
        
        # === PRICE METRICS ===
        current_price = self._get_current_price()
        twap_5m = self._calculate_twap(minutes=5)
        price_vs_twap = self._calculate_price_vs_twap(current_price, twap_5m)
        volatility_1h = self._calculate_volatility(minutes=60)
        
        # === SPOOFING ===
        spoofing_score = None  # Заполняется через iceberg.spoofing_probability
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
            
            # Orderbook
            obi_value=obi,
            ofi_value=ofi,
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
        
        # === UPDATE THROTTLING CACHE ===
        # WHY: Сохраняем снапшот для быстрого возврата при повторных вызовах < 100 мс
        self._last_snapshot_time = now
        self._last_snapshot_cache = snapshot
        
        return snapshot
    
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
    
    def _get_whale_cvd(self) -> Optional[float]:
        """CVD китов - читаем напрямую из book.whale_cvd"""
        if not self.order_book:
            return None
        try:
            # WHY: LocalOrderBook хранит whale_cvd как словарь {'whale': float, 'dolphin': float, 'minnow': float}
            if hasattr(self.order_book, 'whale_cvd') and isinstance(self.order_book.whale_cvd, dict):
                return float(self.order_book.whale_cvd.get('whale', 0))
        except:
            pass
        return None
    
    def _get_fish_cvd(self) -> Optional[float]:
        """CVD рыб - читаем напрямую из book.whale_cvd['minnow']"""
        if not self.order_book:
            return None
        try:
            # WHY: В LocalOrderBook используется ключ 'minnow', а не 'fish'
            if hasattr(self.order_book, 'whale_cvd') and isinstance(self.order_book.whale_cvd, dict):
                return float(self.order_book.whale_cvd.get('minnow', 0))
        except:
            pass
        return None
    
    def _get_dolphin_cvd(self) -> Optional[float]:
        """CVD дельфинов - читаем напрямую из book.whale_cvd['dolphin']"""
        if not self.order_book:
            return None
        try:
            if hasattr(self.order_book, 'whale_cvd') and isinstance(self.order_book.whale_cvd, dict):
                return float(self.order_book.whale_cvd.get('dolphin', 0))
        except:
            pass
        return None
    
    def _get_total_cvd(self) -> Optional[float]:
        """Суммарный CVD"""
        whale = self._get_whale_cvd()
        fish = self._get_fish_cvd()
        dolphin = self._get_dolphin_cvd()
        
        if whale is None and fish is None and dolphin is None:
            return None
        
        return (whale or 0) + (fish or 0) + (dolphin or 0)
    
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
        """Расстояние до ближайшей gamma wall и её тип"""
        if not self.gamma:
            return None, None
        try:
            # FIX: Lobotomy Issue - используем GammaProvider.get_gamma_wall_distance()
            current_price = self._get_current_price()
            if current_price is None:
                return None, None
            
            return self.gamma.get_gamma_wall_distance(current_price)
        except:
            return None, None
    
    def _get_current_price(self) -> Optional[float]:
        """Текущая mid price"""
        if not self.order_book:
            return None
        try:
            # FIX: LocalOrderBook использует методы get_best_bid/ask(), а не поля
            bid_tuple = self.order_book.get_best_bid()  # Returns (price, qty) or None
            ask_tuple = self.order_book.get_best_ask()  # Returns (price, qty) or None
            
            if bid_tuple and ask_tuple:
                bid = bid_tuple[0]  # Извлекаем цену
                ask = ask_tuple[0]
                return float((bid + ask) / 2)
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
