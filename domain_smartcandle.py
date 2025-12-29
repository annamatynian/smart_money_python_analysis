# ===========================================================================
# SMART CANDLES: Multi-Timeframe Context for Swing Trading (1D/1W)
# ===========================================================================

from pydantic import BaseModel, Field, validator
from decimal import Decimal
from datetime import datetime
from typing import Optional

class SmartCandle(BaseModel):
    """
    WHY: Агрегированная свеча с метриками "умных денег" для старших таймфреймов.
    
    Теория (документ "Анализ данных смарт-мани", разделы 4.1-4.3):
    - 1D/1W таймфреймы требуют макро-контекста: деривативы + on-chain
    - 3 критических метрики для Wyckoff анализа:
      1. Futures Basis (перегрев/дно рынка)
      2. Options Skew (страх институционалов)
      3. Open Interest Delta (топливо тренда)
    
    Используется для:
    - Swing trading решений (вход/выход)
    - Макро-анализа рыночной фазы (аккумуляция/дистрибуция)
    - Валидации сигналов с микроструктуры (icebergs + CVD)
    """
    
    # === БАЗОВЫЕ OHLCV ===
    symbol: str
    timeframe: str  # '1h', '4h', '1d', '1w'
    candle_time: datetime  # PRIMARY: Aligned with DB schema
    
    # WHY: Deprecated field for backward compatibility
    # Old code using .timestamp will continue working via validator
    timestamp: Optional[datetime] = None
    
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal  # Total volume (BTC или ETH)
    
    # === АГРЕССОРЫ (FLOW): CVD метрики - кто БЬЁТ по рынку ===
    # WHY: Префикс flow_ отделяет агрессоров от стен (Gemini validation)
    flow_whale_cvd: float  # CVD для whale агрессоров (>$100k trades)
    flow_dolphin_cvd: float  # CVD для dolphin агрессоров ($1k-$100k) - BRIDGE между retail и institutions
    flow_minnow_cvd: float  # CVD для minnow агрессоров (<$1k trades) - retail толпа
    total_trades: int  # Количество сделок за свечу
    
    # === КРИТИЧЕСКАЯ МЕТРИКА 1: FUTURES BASIS ===
    # WHY: Разница между фьючерсом и спотом (аннуализированная)
    # Источник: Документ "Анализ данных смарт-мани", раздел 4.2
    avg_basis_apr: Optional[float] = None  # Average Annualized Basis (%)
    """
    Формула: ((F - S) / S) * (365 / DTE) * 100
    
    Интерпретация:
    - basis > 15-20%: Рынок перегрет (Contango), киты открывают Cash & Carry хедж
    - basis < 0%: Дно рынка (Backwardation), острый дефицит или short squeeze
    - basis 5-10%: Нормальное состояние (carry cost)
    
    Example:
    - Spot: $60,000
    - Futures (30 days): $60,500
    - Basis: ((60500-60000)/60000) * (365/30) * 100 = 10.1% APR
    """
    
    # === КРИТИЧЕСКАЯ МЕТРИКА 2: OPTIONS SKEW ===
    # WHY: Разница IV между путами и коллами (страх институционалов)
    # Источник: Документ "Анализ данных смарт-мани", раздел 4.2
    options_skew: Optional[float] = None  # 25-delta Put IV - 25-delta Call IV (%)
    """
    Формула: IV_put_25d - IV_call_25d
    
    Интерпретация:
    - skew > +5%: Путы дороже коллов → страх падения (медвежий сигнал)
    - skew < -5%: Коллы дороже путов → ожидание роста (бычий сигнал)
    - skew ~0%: Нейтральное состояние
    
    CRITICAL: Если цена ↑ но skew ↑ → дивергенция (киты хеджируются) → разворот
    
    Example (Bitcoin):
    - 25d Put IV: 65%
    - 25d Call IV: 58%
    - Skew: 65 - 58 = +7% (страх падения)
    """
    
    # === КРИТИЧЕСКАЯ МЕТРИКА 3: OPEN INTEREST DELTA ===
    # WHY: Изменение открытых позиций (топливо тренда)
    # Источник: Документ "Анализ данных смарт-мани", раздел 4.3
    oi_delta: Optional[float] = None  # Change in Open Interest за свечу (BTC или $)
    """
    Формула: OI_close - OI_open (за свечу)
    
    Интерпретация:
    - Цена ↑ + OI ↑: Новые лонги (сильный тренд)
    - Цена ↑ + OI ↓: Short covering (слабый рост, скоро разворот)
    - Цена ↓ + OI ↑: Новые шорты (сильный медвежий тренд)
    - Цена ↓ + OI ↓: Long liquidations (слабое падение, возможно дно)
    
    Example:
    - OI at open: 50,000 BTC
    - OI at close: 52,000 BTC
    - oi_delta: +2,000 BTC (новые позиции открыты)
    """
    
    # === СТЕНЫ (WALL): Iceberg volumes - кто ПРИНИМАЕТ удар ===
    # WHY: Префикс wall_ для пассивных limit orders (айсбергов)
    wall_whale_vol: Optional[float] = None  # Detected whale iceberg volume (>$100k hidden liquidity)
    wall_dolphin_vol: Optional[float] = None  # Detected dolphin iceberg volume ($1k-$100k) - ex-shark
    
    # === ABSORBED VOLUMES: Исполненные айсберги - для ML предсказания пробоя ===
    # WHY: Показывает агрессивность атаки на уровни (в дополнение к wall_*)
    absorbed_whale_vol: Optional[float] = None  # Total executed whale iceberg volume за свечу
    absorbed_dolphin_vol: Optional[float] = None  # Total executed dolphin iceberg volume за свечу
    absorbed_total_vol: Optional[float] = None  # Total absorbed volume (whale + dolphin)
    """
    ML Feature Engineering:
    - wall_vol ↑ + absorbed_vol ↓ → Сильный уровень (ОТСКОК)
    - wall_vol ↑ + absorbed_vol ↑ → Истощение защиты (ПРОБОЙ близко)
    - wall_vol ↓ + absorbed_vol ↑ → Уровень уже проломлен
    
    Пример:
    Свеча 1H на BTC:
    - wall_whale_vol: 100 BTC (айсбергов стоит в стакане)
    - absorbed_whale_vol: 80 BTC (айсбергов исполнено за час)
    → Ratio = 80/100 = 0.8 → Уровень истощается, вероятен пробой
    """
    
    # === ORDERBOOK МЕТРИКИ ===
    # WHY: Префикс book_ для видимой ликвидности стакана
    total_gex: Optional[float] = None  # Total Gamma Exposure (из GammaProfile)
    book_obi: Optional[float] = None  # Order Book Imbalance exponentially weighted (-1.0 to 1.0)
    book_ofi: Optional[float] = None  # Order Flow Imbalance (изменение ликвидности)
    
    # === WYCKOFF КОНТЕКСТ (из AccumulationDetector) ===
    wyckoff_pattern: Optional[str] = None  # 'SPRING', 'UPTHRUST', 'ACCUMULATION', 'DISTRIBUTION'
    accumulation_confidence: Optional[float] = None  # 0.0-1.0 из detect_accumulation()
    
    class Config:
        arbitrary_types_allowed = True
    
    @validator('timestamp', always=True)
    def sync_timestamp(cls, v, values):
        """
        WHY: Backward compatibility validator.
        
        Ensures old code using .timestamp continues working:
        - If timestamp not provided -> copy from candle_time
        - If timestamp provided -> keep it (for old constructors)
        
        Example:
            # Old code (still works):
            SmartCandle(symbol='BTC', timeframe='1h', timestamp=now, ...)
            
            # New code (preferred):
            SmartCandle(symbol='BTC', timeframe='1h', candle_time=now, ...)
        """
        return v or values.get('candle_time')
    
    def is_overheated(self, basis_threshold: float = 15.0) -> bool:
        """
        WHY: Проверяет перегрев рынка по фьючерсному базису.
        
        Если basis > 15-20%, киты обычно открывают Cash & Carry арбитраж,
        создавая потолок для цены (продажа фьючерса + покупка спота).
        
        Args:
            basis_threshold: Порог перегрева в % APR (default 15%)
        
        Returns:
            True если рынок перегрет
        """
        if self.avg_basis_apr is None:
            return False
        return self.avg_basis_apr > basis_threshold
    
    def is_backwardation(self) -> bool:
        """
        WHY: Проверяет инверсию кривой (фьючерс дешевле спота).
        
        Backwardation часто сигнализирует:
        - Острый дефицит актива
        - Панический short squeeze
        - Локальное дно рынка
        
        Returns:
            True если фьючерс дешевле спота
        """
        if self.avg_basis_apr is None:
            return False
        return self.avg_basis_apr < 0.0
    
    def is_fear_divergence(self, price_rising: bool) -> bool:
        """
        WHY: Детектирует дивергенцию между ценой и страхом институционалов.
        
        CRITICAL PATTERN (из документа):
        - Цена растет + Skew растет (путы дорожают) → киты не верят в рост
        - Это "Smirk" (ухмылка) - классический сигнал разворота вниз
        
        Args:
            price_rising: True если цена выросла за свечу
        
        Returns:
            True если обнаружена медвежья дивергенция
        """
        if self.options_skew is None:
            return False
        
        # Skew > 5% означает путы значительно дороже
        high_fear = self.options_skew > 5.0
        
        # Дивергенция: цена ↑ но страх ↑
        return price_rising and high_fear
    
    def get_trend_fuel(self) -> str:
        """
        WHY: Определяет "топливо" текущего тренда по OI Delta.
        
        Returns:
            'STRONG_BULL': Цена ↑ + OI ↑ (новые лонги)
            'WEAK_BULL': Цена ↑ + OI ↓ (short covering)
            'STRONG_BEAR': Цена ↓ + OI ↑ (новые шорты)
            'WEAK_BEAR': Цена ↓ + OI ↓ (long liquidations)
            'NEUTRAL': Нет изменения OI
        """
        if self.oi_delta is None:
            return 'NEUTRAL'
        
        # Определяем направление цены
        price_rising = self.close > self.open
        
        # Определяем изменение OI (порог: 1% от объема)
        oi_threshold = float(self.volume) * 0.01
        oi_increasing = self.oi_delta > oi_threshold
        oi_decreasing = self.oi_delta < -oi_threshold
        
        # Классификация
        if price_rising and oi_increasing:
            return 'STRONG_BULL'  # Новые лонги
        elif price_rising and oi_decreasing:
            return 'WEAK_BULL'  # Short covering
        elif not price_rising and oi_increasing:
            return 'STRONG_BEAR'  # Новые шорты
        elif not price_rising and oi_decreasing:
            return 'WEAK_BEAR'  # Long liquidations
        else:
            return 'NEUTRAL'
