from pydantic import BaseModel, Field, ConfigDict
from typing import Dict, List, Tuple, Optional
from decimal import Decimal
from datetime import datetime
from dataclasses import dataclass
from sortedcontainers import SortedDict
from datetime import datetime
from typing import Dict, List, Tuple, Optional # Добавьте List
from collections import deque
from enum import Enum

# WHY: Импорт конфигурации для мульти-токен поддержки (Task: Multi-Asset Support)
from config import AssetConfig, get_config

# WHY: Import SmartCandle for multi-timeframe derivatives analysis
from domain_smartcandle import SmartCandle


class GapDetectedError(Exception):
    pass

# --- Value Objects ---

class GammaProfile(BaseModel):
    """
    Новая структура данных.
    Источник: Ваша теория [cite: 120-125] и логика из файла deribit_loader.py.
    """
    total_gex: float      # Общая гамма (Барометр: гасят волатильность или разгоняют)
    call_wall: float      # Уровень сопротивления (где дилеры продают)
    put_wall: float       # Уровень поддержки (где дилеры покупают)
    timestamp: datetime = Field(default_factory=datetime.now)

class PriceLevel(BaseModel):
    price: Decimal
    quantity: Decimal

class OrderBookUpdate(BaseModel):
    """Универсальная модель обновления (Diff), не зависящая от формата биржи"""
    bids: List[Tuple[Decimal, Decimal]]  # [(price, qty), ...]
    asks: List[Tuple[Decimal, Decimal]]
    first_update_id: Optional[int] = None  # U в Binance (первый update ID в этом пакете)
    final_update_id: Optional[int] = None  # u в Binance (последний update ID)
    event_time: datetime = Field(default_factory=datetime.now)

class TradeEvent(BaseModel):
    """Модель события сделки (Trade)"""
    price: Decimal
    quantity: Decimal
    is_buyer_maker: bool  # True = maker продавал (taker купил)
    event_time: int  # Timestamp в миллисекундах
    trade_id: Optional[int] = None


class VolumeBucket(BaseModel):
    """
    WHY: Building block для VPIN (Volume-Synchronized Probability of Informed Trading).
    
    Теория (Easley-O'Hara, 2012):
    - Вместо временных интервалов используем фиксированные объемы (Volume Bars)
    - Корзина закрывается при достижении bucket_size (например 10 BTC)
    - Анализ |Buy - Sell| внутри корзины даёт токсичность потока
    
    Токсичность (VPIN):
    - Высокая (>0.7): Агрессоры информированы → риск пробоя айсберга
    - Низкая (<0.3): Поток шумный (розничный) → айсберг устоит
    
    Источник: ТЗ "Flow Toxicity (VPIN)" в проекте.
    """
    bucket_size: Decimal  # Фиксированный размер корзины (в монетах токена)
    symbol: str  # BTCUSDT, ETHUSDT и т.д.
    
    buy_volume: Decimal = Decimal("0")  # Накопленный объём покупок (taker купил)
    sell_volume: Decimal = Decimal("0")  # Накопленный объём продаж (taker продал)
    is_complete: bool = False  # True когда корзина заполнена
    creation_time: datetime = Field(default_factory=datetime.now)
    
    model_config = ConfigDict(arbitrary_types_allowed=True)
    
    def total_volume(self) -> Decimal:
        """WHY: Общий объём корзины (buy + sell)"""
        return self.buy_volume + self.sell_volume
    
    def add_trade(self, trade: TradeEvent) -> Decimal:
        """
        WHY: Добавляет сделку в корзину с overflow protection.
        
        Логика:
        1. Определяем направление (buy/sell) по is_buyer_maker
        2. Добавляем объём в соответствующую сторону
        3. Если total > bucket_size → закрываем корзину и возвращаем overflow
        
        Args:
            trade: Событие сделки
        
        Returns:
            Decimal: Overflow объём (если корзина переполнена)
            0 если overflow не было
        """
        # Если корзина уже закрыта - игнорируем
        if self.is_complete:
            return trade.quantity
        
        # Определяем направление
        # is_buyer_maker=False → taker купил (агрессивная покупка)
        # is_buyer_maker=True → taker продал (агрессивная продажа)
        is_buy = not trade.is_buyer_maker
        
        # Проверяем сколько места осталось
        remaining_space = self.bucket_size - self.total_volume()
        
        # Если сделка помещается полностью
        if trade.quantity <= remaining_space:
            if is_buy:
                self.buy_volume += trade.quantity
            else:
                self.sell_volume += trade.quantity
            
            # Проверяем закрытие
            if self.total_volume() >= self.bucket_size:
                self.is_complete = True
            
            return Decimal("0")  # Нет overflow
        
        # Если сделка НЕ помещается → частичное добавление
        else:
            if is_buy:
                self.buy_volume += remaining_space
            else:
                self.sell_volume += remaining_space
            
            self.is_complete = True  # Корзина заполнена
            overflow = trade.quantity - remaining_space
            return overflow
    
    def calculate_imbalance(self) -> Decimal:
        """
        WHY: Вычисляет |Buy - Sell| для VPIN формулы.
        
        Формула VPIN:
        VPIN = Σ|Buy_i - Sell_i| / (n * bucket_size)
        
        Returns:
            Decimal: Абсолютное значение дисбаланса
        """
        return abs(self.buy_volume - self.sell_volume)

class IcebergDetectionResult(BaseModel):
    """Результат обнаружения айсберга"""
    price: Decimal
    detected_hidden_volume: Decimal
    confidence: float
    timestamp: datetime = Field(default_factory=datetime.now)

class IcebergStatus(str, Enum):
    ACTIVE = "ACTIVE"       # Уровень держится
    BREACHED = "BREACHED"   # Уровень пробит (Exhaustion/Breakout)
    CANCELLED = "CANCELLED" # Уровень отменен (для анализа спуфинга)

class CancellationContext(BaseModel):
    """
    WHY: Контекст отмены айсберга для ML-анализа спуфинга (Task 1.1)
    
    Сохраняет рыночную ситуацию в момент отмены айсберга.
    Используется для определения был ли это спуфинг или реальный уровень.
    """
    mid_price_at_cancel: Decimal
    distance_from_level_pct: Decimal  # (mid_price - iceberg_price) / iceberg_price * 100
    price_velocity_5s: Decimal        # Изменение цены за последние 5 сек (dP/dt)
    moving_towards_level: bool        # True если цена двигалась К айсбергу
    volume_executed_pct: Decimal      # Процент исполненного объема (0-100)
    
    model_config = ConfigDict(arbitrary_types_allowed=True)

class IcebergLevel(BaseModel):
    """
    Реестр активных айсбергов.
    Хранит состояние уровня, а не отдельного ордера.
    """
    price: Decimal
    is_ask: bool            # True = Ask (Сопротивление), False = Bid (Поддержка)
    total_hidden_volume: Decimal = Decimal("0") # Накопленный скрытый объем
    creation_time: datetime = Field(default_factory=datetime.now)
    last_update_time: datetime = Field(default_factory=datetime.now)
    status: IcebergStatus = IcebergStatus.ACTIVE
    
    # Флаги контекста
    is_gamma_wall: bool = False  # Совпадает ли с Call/Put Wall 
    confidence_score: float = 0.0
    
    # === НОВЫЕ ПОЛЯ ДЛЯ АНТИСПУФИНГА (Task 1.1) ===
    cancellation_context: Optional[CancellationContext] = None  # Контекст отмены
    spoofing_probability: float = 0.0  # Вероятность спуфинга (0.0-1.0)
    refill_count: int = 0  # Количество пополнений (для refill_frequency)
    
    # === НОВОЕ: Wall Resilience (Устойчивость Стены) ===
    # WHY: Скорость восстановления айсберга после "удара" → признак силы стены
    last_refill_time: Optional[datetime] = None  # Время последнего пополнения
    average_refill_delay_ms: Optional[float] = None  # Средняя задержка пополнения
    
    # === GEMINI FIX: Категоризация по размеру (Wall Semantics) ===
    # WHY: Разделяем whale ($100k+) и dolphin ($1k-$100k) стены для wall_*_vol метрик
    is_dolphin: bool = False  # True если $1k-$100k, False если whale >$100k
    
    # === GEMINI ENHANCEMENT #2: Micro-Divergence VPIN Tracking ===
    # WHY: Отслеживаем токсичность потока ВНУТРИ жизненного цикла айсберга
    vpin_history: List[Tuple[datetime, float]] = Field(default_factory=list)  # История VPIN при рефиллах
    
    # === GEMINI ENHANCEMENT #3: Trade Footprint (для визуализации) ===
    # WHY: Сохраняем распределение сделок для ретроспективного анализа
    trade_footprint: List[Dict] = Field(default_factory=list)  # [{time, qty, is_buy, cohort}, ...]

    model_config = ConfigDict(arbitrary_types_allowed=True)
    
    # WHY: Фильтрация спуфинга - айсберг должен жить >5 мин для свинг-трейдинга
    def is_significant_for_swing(self, min_lifetime_seconds: int = 300) -> bool:
        """
        Проверяет значимость айсберга для свинг-трейдинга.
        
        Фильтрует HFT-спуфинг (<5 сек) и краткосрочные алгоритмы.
        
        Args:
            min_lifetime_seconds: Минимальное время жизни (по умолчанию 300с = 5мин)
        
        Returns:
            True если айсберг живет достаточно долго
        """
        now = datetime.now()
        lifetime_seconds = (now - self.creation_time).total_seconds()
        return lifetime_seconds >= min_lifetime_seconds
    
    # WHY: Расчет частоты пополнений для отличия пассивного накопления от агрессивного
    def get_refill_frequency(self) -> float:
        """
        Рассчитывает частоту пополнений (рефиллов в минуту).
        
        Высокая частота (Агрессивный алго):
        - Institutional algo с TWAP/VWAP
        - Рефиллы каждые несколько секунд
        
        Низкая частота (Пассивное накопление):
        - Уровень поддержки/сопротивления
        - Рефиллы редко (<1 в минуту)
        
        Returns:
            Частота в refills/minute. 0.0 если айсберг слишком молодой.
        """
        now = datetime.now()
        lifetime_seconds = (now - self.creation_time).total_seconds()
        
        # Избегаем деления на 0
        if lifetime_seconds < 1.0:
            return 0.0
        
        # Переводим в минуты
        lifetime_minutes = lifetime_seconds / 60.0
        return self.refill_count / lifetime_minutes if lifetime_minutes > 0 else 0.0
    
    def calculate_wall_resilience(self) -> Optional[str]:
        """
        WHY: Рассчитывает устойчивость "стены" (Wall Resilience).
        
        Теория (документ "Разделение стены и удара"):
        - Быстрое восстановление (<50ms) = "железобетонная" стена
        - Медленное восстановление (>200ms) = стена истощена
        - Нет восстановления = отмена (спуфинг)
        
        Returns:
            'STRONG' | 'MODERATE' | 'WEAK' | 'EXHAUSTED' | None
        """
        # Если нет данных о пополнениях
        if self.average_refill_delay_ms is None:
            return None
        
        delay = self.average_refill_delay_ms
        
        # STRONG: <50ms - биржевой refill (нативный айсберг)
        if delay < 50:
            return 'STRONG'
        
        # MODERATE: 50-200ms - алгоритмическое пополнение
        elif delay < 200:
            return 'MODERATE'
        
        # WEAK: 200-500ms - медленный алгоритм
        elif delay < 500:
            return 'WEAK'
        
        # EXHAUSTED: >500ms - стена истощена
        else:
            return 'EXHAUSTED'
    
    # ========================================================================
    # GEMINI ENHANCEMENT #1: Relative Depth Absorption
    # ========================================================================
    
    def calculate_relative_depth_ratio(
        self, 
        order_book: 'LocalOrderBook', 
        depth: int = 20
    ) -> float:
        """
        WHY: Рассчитывает отношение скрытого объёма к видимой ликвидности.
        
        Теория (Gemini Enhancement #1):
        - Если айсберг поглотил 200% видимой ликвидности → Institutional Anchor
        - ratio > 1.5 = Мощная стена
        - ratio < 0.5 = Мелкий айсберг (шум)
        
        Args:
            order_book: LocalOrderBook для расчёта видимой ликвидности
            depth: Глубина анализа (топ-N уровней)
        
        Returns:
            float: ratio = total_hidden_volume / visible_depth
        
        Example:
            >>> # 10 BTC скрытого vs 5 BTC видимого
            >>> iceberg.total_hidden_volume = Decimal('10.0')
            >>> book.bids = {Decimal('60000'): Decimal('5.0')}
            >>> ratio = iceberg.calculate_relative_depth_ratio(book)
            >>> assert ratio == 2.0  # 200% absorption!
        """
        # 1. Определяем сторону стакана
        book_side = order_book.asks if self.is_ask else order_book.bids
        
        if not book_side:
            return 0.0  # Нет ликвидности в стакане
        
        # 2. Суммируем видимую ликвидность топ-N уровней
        visible_volume = Decimal('0')
        
        if self.is_ask:
            # ASK: берём самые дешёвые (с начала)
            for i, (price, qty) in enumerate(book_side.items()):
                if i >= depth:
                    break
                visible_volume += qty
        else:
            # BID: берём самые дорогие (с конца)
            for i, (price, qty) in enumerate(reversed(book_side.items())):
                if i >= depth:
                    break
                visible_volume += qty
        
        if visible_volume == 0:
            return 0.0
        
        # 3. Рассчитываем ratio
        ratio = float(self.total_hidden_volume / visible_volume)
        return ratio
    
    # ========================================================================
    # GEMINI ENHANCEMENT #2: Micro-Divergence (VPIN inside iceberg)
    # ========================================================================
    
    def update_micro_divergence(
        self,
        vpin_at_refill: float,
        whale_volume_pct: float,
        minnow_volume_pct: float,
        price_drift_bps: float = 0.0
    ):
        """
        WHY: Отслеживаем VPIN ВНУТРИ жизненного цикла айсберга (CRYPTO-AWARE).
        
        === КРИТИЧЕСКОЕ ОТЛИЧИЕ ОТ TradFi (Gemini Fix) ===
        TradFi: Высокий VPIN = Informed Trading → ШТРАФ
        Crypto: Высокий VPIN может быть:
          A) Whale Attack → ШТРАФ (confidence DOWN)
          B) Minnow Panic → БОНУС (confidence UP) - айсберг ест ликвидации!
        
        Решение: Смотрим КТО создаёт VPIN (whale_volume_pct vs minnow_volume_pct)
        
        Args:
            vpin_at_refill: VPIN в момент рефилла (0.0-1.0)
            whale_volume_pct: Доля whale объёма в потоке (0.0-1.0)
            minnow_volume_pct: Доля minnow объёма в потоке (0.0-1.0)
            price_drift_bps: Смещение цены против айсберга в bps (>0 = слабость)
        
        Updates:
            - vpin_history: Добавляет точку данных
            - confidence_score: УМНАЯ корректировка на основе состава потока
        
        Examples:
            >>> # СЦЕНАРИЙ А: Whale Attack (VPIN 0.8, whale 70%)
            >>> iceberg.update_micro_divergence(0.8, whale_volume_pct=0.7, minnow_volume_pct=0.2)
            >>> # confidence ПАДАЕТ (киты атакуют)
            
            >>> # СЦЕНАРИЙ Б: Panic Absorption (VPIN 0.9, minnow 80%)
            >>> iceberg.update_micro_divergence(0.9, whale_volume_pct=0.1, minnow_volume_pct=0.8)
            >>> # confidence НЕ падает или даже РАСТЁТ (поглощение паники!)
        """
        now = datetime.now()
        
        # 1. Сохраняем VPIN в историю
        self.vpin_history.append((now, vpin_at_refill))
        
        # 2. БАЗОВАЯ ОЦЕНКА: Низкий VPIN = всё хорошо
        if vpin_at_refill < 0.5:
            return  # Нормальный поток, ничего не делаем
        
        # 3. УМНАЯ ЛОГИКА: Анализируем СОСТАВ потока
        # WHY: Высокий VPIN может быть как угрозой, так и возможностью
        
        # === СЦЕНАРИЙ А: WHALE ATTACK (Киты пытаются пробить) ===
        if whale_volume_pct > 0.6:  # >60% объёма от китов
            # WHY: Крупные игроки атакуют → айсберг под угрозой
            if vpin_at_refill > 0.7:
                penalty = 0.25  # Сильный штраф
            else:
                penalty = 0.15  # Умеренный штраф
            
            # Дополнительный штраф за дрейф цены
            if price_drift_bps > 5.0:  # Цена "прогибается" >5 bps
                penalty += 0.1
            
            self.confidence_score = max(0.0, self.confidence_score - penalty)
            return
        
        # === СЦЕНАРИЙ Б: PANIC ABSORPTION (Айсберг ест толпу) ===
        elif minnow_volume_pct > 0.6:  # >60% объёма от minnows
            # WHY: Толпа в панике → айсберг поглощает ликвидации
            # В крипте это БЫЧИЙ сигнал для лимитного ордера!
            
            if vpin_at_refill > 0.8:
                # ЭКСТРЕМАЛЬНАЯ паника → ОЧЕНЬ сильный уровень
                bonus = 0.1  # +10% confidence
                self.confidence_score = min(1.0, self.confidence_score + bonus)
            
            # Проверка стабильности цены (защита от Adverse Selection)
            if price_drift_bps > 10.0:  # Сильный дрейф = айсберг слабеет
                penalty = 0.05
                self.confidence_score = max(0.0, self.confidence_score - penalty)
            
            return
        
        # === СЦЕНАРИЙ В: СМЕШАННЫЙ ПОТОК (Осторожность) ===
        else:
            # WHY: Нет доминирующей когорты → консервативный подход
            if vpin_at_refill > 0.7:
                penalty = 0.1  # Лёгкий штраф (неопределённость)
            else:
                penalty = 0.05
            
            self.confidence_score = max(0.0, self.confidence_score - penalty)
    
    # ========================================================================
    # GEMINI ENHANCEMENT #3: Trade Footprint (Histogram)
    # ========================================================================
    
    def add_trade_to_footprint(self, trade: TradeEvent):
        """
        WHY: Сохраняет сделку для гистограммы footprint.
        
        Теория (Gemini Enhancement #3):
        - Сохраняем все сделки на уровне айсберга
        - Разделяем по cohort (whale/dolphin/fish)
        - Используется для визуализации и анализа
        
        Args:
            trade: TradeEvent (сделка на этом уровне)
        
        Updates:
            trade_footprint: Добавляет запись
        """
        # 1. Определяем направление
        is_buy = not trade.is_buyer_maker  # False = buyer aggressive
        
        # 2. Определяем cohort (по размеру сделки)
        # WHY: Используем те же пороги что и в OrderFlowAnalyzer
        qty_float = float(trade.quantity)
        
        if qty_float >= 5.0:  # BTC единицы (adjustable per asset)
            cohort = 'WHALE'
        elif qty_float >= 1.0:
            cohort = 'DOLPHIN'
        else:
            cohort = 'FISH'
        
        # 3. Сохраняем запись
        self.trade_footprint.append({
            'time': datetime.fromtimestamp(trade.event_time / 1000),  # ms → seconds
            'quantity': trade.quantity,
            'is_buy': is_buy,
            'cohort': cohort
        })
    
    def get_footprint_buy_ratio(self) -> float:
        """
        WHY: Рассчитывает долю покупок в footprint.
        
        Returns:
            float: 0.0-1.0 (1.0 = все сделки были покупками)
        
        Example:
            >>> # 7 buy, 3 sell → 0.7
            >>> iceberg.get_footprint_buy_ratio()
            0.7
        """
        if not self.trade_footprint:
            return 0.0
        
        buy_count = sum(1 for t in self.trade_footprint if t['is_buy'])
        return buy_count / len(self.trade_footprint)


# ===========================================================================
# НОВЫЙ КЛАСС: PriceZone (Task 3.2 - Context Multi-Timeframe)
# ===========================================================================

class PriceZone(BaseModel):
    """
    WHY: Кластеризация айсбергов на близких уровнях в единую зону.
    
    Теория (документ "Smart Money Analysis", раздел 3.2):
    - Айсберги на уровнях 95000, 95050, 95100 (<0.2% разница) = одна зона
    - Зона с 3+ айсбергами = "сильная зона" (институциональный интерес)
    - Используется для свинг-трейдинга: вход у зон, стоп за зонами
    
    Алгоритм кластеризации:
    1. Сортируем айсберги по цене
    2. Если разница между соседними < tolerance_pct → объединяем
    3. Вычисляем центр зоны (средняя цена), total_volume (сумма)
    """
    center_price: Decimal  # Средняя цена зоны (взвешенная по объёму)
    is_ask: bool  # True = сопротивление, False = поддержка
    total_volume: Decimal  # Суммарный скрытый объём всех айсбергов
    iceberg_count: int  # Количество айсбергов в зоне
    price_range: Tuple[Decimal, Decimal]  # (min_price, max_price)
    
    # Список айсбергов в зоне (для детального анализа)
    icebergs: List[IcebergLevel] = Field(default_factory=list)
    
    # Метаданные
    creation_time: datetime = Field(default_factory=datetime.now)
    
    model_config = ConfigDict(arbitrary_types_allowed=True)
    
    def is_strong(self, min_count: int = 3) -> bool:
        """
        WHY: Зона с 3+ айсбергами = "сильная зона".
        
        Сильные зоны имеют:
        - Больше институционального интереса
        - Выше вероятность отбоя цены
        - Подходят для свинг-трейдинга (вход у зоны)
        
        Args:
            min_count: Минимальное количество айсбергов (default 3)
        
        Returns:
            True если зона сильная
        """
        return self.iceberg_count >= min_count
    
    def get_width_pct(self) -> float:
        """
        WHY: Ширина зоны в процентах.
        
        Узкие зоны (<0.1%) = точечная поддержка/сопротивление
        Широкие зоны (>0.5%) = размытая защита
        
        Returns:
            Ширина в процентах от центральной цены
        """
        min_p, max_p = self.price_range
        width = float(max_p - min_p)
        return (width / float(self.center_price)) * 100.0


# ===========================================================================
# НОВЫЙ КЛАСС: HistoricalMemory (Task 3.2 - Multi-Timeframe Context)
# ===========================================================================

class HistoricalMemory(BaseModel):
    """
    WHY: Хранилище исторических данных для свинг-трейдинга.
    
    Теория (документ "Smart Money Analysis", раздел 3.2):
    - Свинг-трейдинг требует контекста на нескольких таймфреймах
    - CVD дивергенция (whale CVD ↑ while price ↓) = накопление
    - Работает на 1H/4H/1D/1W таймфреймах
    
    Таймфреймы:
    - 1H (60 мин): Краткосрочное накопление, точка входа
    - 4H (240 мин): Основной свинг-таймфрейм (тренд)
    - 1D (1440 мин): Среднесрочное позиционирование
    - 1W (10080 мин): Долгосрочный контекст (мажоры vs свинг)
    - 1M (43200 мин): Макро-тренд (структурный анализ)
    """
    
    # История Whale CVD
    cvd_history_1h: deque = Field(default_factory=lambda: deque(maxlen=60))   # 60 часов
    
    # === SAFE CHANGE: SCALING MEMORY FOR SWING ===
    # WHY: 6 месяцев контекста для детекции долгосрочных накоплений
    cvd_history_4h: deque = Field(default_factory=lambda: deque(maxlen=1100))  # ~6 месяцев (180 дней * 6 баров)
    cvd_history_1d: deque = Field(default_factory=lambda: deque(maxlen=180))   # 6 месяцев
    cvd_history_1w: deque = Field(default_factory=lambda: deque(maxlen=52))   # 52 недели (год) - unchanged
    cvd_history_1m: deque = Field(default_factory=lambda: deque(maxlen=12))   # 12 месяцев (год)
    
    # WHY: История Minnow CVD для Wyckoff накопления (Task: Full Wyckoff Implementation)
    minnow_cvd_history_1h: deque = Field(default_factory=lambda: deque(maxlen=60))
    minnow_cvd_history_4h: deque = Field(default_factory=lambda: deque(maxlen=1100))  # ~6 месяцев
    minnow_cvd_history_1d: deque = Field(default_factory=lambda: deque(maxlen=180))   # 6 месяцев
    minnow_cvd_history_1w: deque = Field(default_factory=lambda: deque(maxlen=52))   # unchanged
    minnow_cvd_history_1m: deque = Field(default_factory=lambda: deque(maxlen=12))
    
    # === НОВОЕ: Разделение Whale CVD на Passive/Aggressive (Wall Resilience) ===
    # WHY: Различаем "стену" (passive accumulation) и "удар" (aggressive entry)
    # Теория: Passive = киты стоят айсбергом, Aggressive = киты бьют по рынку
    whale_passive_accumulation_1h: deque = Field(default_factory=lambda: deque(maxlen=60))
    whale_aggressive_entry_1h: deque = Field(default_factory=lambda: deque(maxlen=60))
    
    # История цены (mid_price)
    price_history_1h: deque = Field(default_factory=lambda: deque(maxlen=60))
    price_history_4h: deque = Field(default_factory=lambda: deque(maxlen=1100))  # ~6 месяцев
    price_history_1d: deque = Field(default_factory=lambda: deque(maxlen=180))   # 6 месяцев
    price_history_1w: deque = Field(default_factory=lambda: deque(maxlen=52))   # unchanged
    price_history_1m: deque = Field(default_factory=lambda: deque(maxlen=12))
    
    # Метаданные для downsampling
    last_update_1h: Optional[datetime] = None
    last_update_4h: Optional[datetime] = None
    last_update_1d: Optional[datetime] = None
    last_update_1w: Optional[datetime] = None
    last_update_1m: Optional[datetime] = None
    
    model_config = ConfigDict(arbitrary_types_allowed=True)
    
    def update_history(self, timestamp: datetime, whale_cvd: float, minnow_cvd: float, price: Decimal, is_passive: bool = True):
        """
        WHY: Добавляет новую точку данных и агрегирует в старшие таймфреймы.
        
        === UPDATE: Full Wyckoff Support (Task: Minnow CVD Integration) ===
        Теперь сохраняет как Whale так и Minnow CVD для детекции паники толпы.
        
        === UPDATE: Passive/Aggressive Separation (Wall Resilience) ===
        Теперь разделяет Whale CVD на:
        - Passive: Киты стоят айсбергами (СТЕНА)
        - Aggressive: Киты бьют по рынку (УДАР)
        
        Логика:
        1. Всегда добавляем в 1H (самый мелкий таймфрейм)
        2. Если прошло 4+ часа → агрегируем в 4H
        3. Если прошло 24+ часа → агрегируем в 1D
        4. Если прошло 168+ часов (неделя) → агрегируем в 1W
        
        Args:
            timestamp: Время события
            whale_cvd: Whale CVD в этот момент
            minnow_cvd: Minnow CVD в этот момент (для Wyckoff паники)
            price: Mid price в этот момент
            is_passive: True если киты стоят айсбергом, False если бьют по рынку
        """
        # 1. Всегда добавляем в 1H
        self.cvd_history_1h.append((timestamp, whale_cvd))
        self.minnow_cvd_history_1h.append((timestamp, minnow_cvd))
        self.price_history_1h.append((timestamp, price))
        
        # === PASSIVE/AGGRESSIVE SEPARATION ===
        # WHY: Разделяем whale CVD на "стену" и "удар"
        if is_passive:
            # Киты стоят айсбергом (пассивное накопление)
            self.whale_passive_accumulation_1h.append((timestamp, whale_cvd))
        else:
            # Киты бьют по рынку (агрессивный вход)
            self.whale_aggressive_entry_1h.append((timestamp, whale_cvd))
        
        # WHY: Инициализируем last_update при первом вызове (но НЕ добавляем в старшие таймфреймы)
        if self.last_update_1h is None:
            self.last_update_1h = timestamp
            self.last_update_4h = timestamp
            self.last_update_1d = timestamp
            self.last_update_1w = timestamp
            self.last_update_1m = timestamp
            return  # Первая точка - только инициализация
        
        self.last_update_1h = timestamp
        
        # 2. Downsample в 4H (если прошло 4+ часа)
        if (timestamp - self.last_update_4h).total_seconds() >= 4 * 3600:
            self.cvd_history_4h.append((timestamp, whale_cvd))
            self.minnow_cvd_history_4h.append((timestamp, minnow_cvd))
            self.price_history_4h.append((timestamp, price))
            self.last_update_4h = timestamp
        
        # 3. Downsample в 1D (если прошло 24+ часа)
        if (timestamp - self.last_update_1d).total_seconds() >= 24 * 3600:
            self.cvd_history_1d.append((timestamp, whale_cvd))
            self.minnow_cvd_history_1d.append((timestamp, minnow_cvd))
            self.price_history_1d.append((timestamp, price))
            self.last_update_1d = timestamp
        
        # 4. Downsample в 1W (если прошло 168+ часов)
        if (timestamp - self.last_update_1w).total_seconds() >= 168 * 3600:
            self.cvd_history_1w.append((timestamp, whale_cvd))
            self.minnow_cvd_history_1w.append((timestamp, minnow_cvd))
            self.price_history_1w.append((timestamp, price))
            self.last_update_1w = timestamp
        
        # 5. Downsample в 1M (если прошло 720+ часов = 30 дней)
        if (timestamp - self.last_update_1m).total_seconds() >= 720 * 3600:
            self.cvd_history_1m.append((timestamp, whale_cvd))
            self.minnow_cvd_history_1m.append((timestamp, minnow_cvd))
            self.price_history_1m.append((timestamp, price))
            self.last_update_1m = timestamp
    
    def detect_cvd_divergence(self, timeframe: str = '1h') -> Tuple[bool, Optional[str]]:
        """
        WHY: Детектирует CVD дивергенцию (накопление/дистрибуция).
        
        === UPDATE: Full Wyckoff Logic (Task: Minnow Panic Detection) ===
        Теперь проверяет ПОЛНОЕ Wyckoff условие:
        - BULLISH: Price ↓ + Whale CVD ↑ + Minnow CVD ↓ (паника толпы)
        - BEARISH: Price ↑ + Whale CVD ↓ + Minnow CVD ↑ (жадность толпы)
        
        Логика (из документа "Smart Money Analysis"):
        - БЫЧЬЯ дивергенция: Цена делает Lower Low, CVD делает Higher Low
          → Киты накапливают (покупают на падении)
        - МЕДВЕЖЬЯ дивергенция: Цена делает Higher High, CVD делает Lower High
          → Киты дистрибутируют (продают на росте)
        
        Args:
            timeframe: '1h', '4h', '1d', или '1w'
        
        Returns:
            (is_divergence: bool, divergence_type: 'BULLISH' | 'BEARISH' | None)
        """
        # Выбираем нужный таймфрейм
        if timeframe == '1h':
            cvd_hist = self.cvd_history_1h
            minnow_hist = self.minnow_cvd_history_1h
            price_hist = self.price_history_1h
        elif timeframe == '4h':
            cvd_hist = self.cvd_history_4h
            minnow_hist = self.minnow_cvd_history_4h
            price_hist = self.price_history_4h
        elif timeframe == '1d':
            cvd_hist = self.cvd_history_1d
            minnow_hist = self.minnow_cvd_history_1d
            price_hist = self.price_history_1d
        elif timeframe == '1w':
            cvd_hist = self.cvd_history_1w
            minnow_hist = self.minnow_cvd_history_1w
            price_hist = self.price_history_1w
        elif timeframe == '1m':
            cvd_hist = self.cvd_history_1m
            minnow_hist = self.minnow_cvd_history_1m
            price_hist = self.price_history_1m
        else:
            return False, None
        
        # Нужно минимум 3 точки для дивергенции
        if len(cvd_hist) < 3 or len(minnow_hist) < 3 or len(price_hist) < 3:
            return False, None
        
        # Берем последние 3 точки
        recent_cvds = list(cvd_hist)[-3:]
        recent_minnows = list(minnow_hist)[-3:]
        recent_prices = list(price_hist)[-3:]
        
        # Извлекаем значения
        whale_cvd_values = [c[1] for c in recent_cvds]
        minnow_cvd_values = [m[1] for m in recent_minnows]
        price_values = [float(p[1]) for p in recent_prices]
        
        # WHY: Full Wyckoff conditions (3 компонента)
        price_falling = price_values[-1] < price_values[0]  # Lower Low
        price_rising = price_values[-1] > price_values[0]   # Higher High
        
        whale_buying = whale_cvd_values[-1] > whale_cvd_values[0]  # Higher Low (accumulation)
        whale_selling = whale_cvd_values[-1] < whale_cvd_values[0]  # Lower High (distribution)
        
        minnow_panic = minnow_cvd_values[-1] < minnow_cvd_values[0]  # Minnows selling (panic)
        minnow_greed = minnow_cvd_values[-1] > minnow_cvd_values[0]  # Minnows buying (greed)
        
        # Проверяем БЫЧЬЮ дивергенцию (ACCUMULATION)
        # Price ↓ + Whale CVD ↑ + Minnow CVD ↓
        if price_falling and whale_buying and minnow_panic:
            return True, 'BULLISH'
        
        # Проверяем МЕДВЕЖЬЮ дивергенцию (DISTRIBUTION)
        # Price ↑ + Whale CVD ↓ + Minnow CVD ↑
        if price_rising and whale_selling and minnow_greed:
            return True, 'BEARISH'
        
        return False, None


# --- Entity ---

class LocalOrderBook(BaseModel):
    """
    Сущность Локального Стакана.
    Хранит состояние рынка в памяти.
    Используем Dict для быстрого доступа O(1) по цене.
    
    === ОБНОВЛЕНИЕ: Мульти-токен поддержка (Task: Multi-Asset Support) ===
    Теперь использует AssetConfig для адаптации к разным токенам (BTC/ETH/SOL).
    """
    symbol: str
    
    # WHY: Конфигурация загружается автоматически по symbol при создании
    config: AssetConfig = Field(default=None)
    
    bids: SortedDict = Field(default_factory=SortedDict)
    asks: SortedDict = Field(default_factory=SortedDict)
    gamma_profile: Optional[GammaProfile] = None
    latest_wyckoff_divergence: Optional[dict] = None  # ✅ GEMINI: Best divergence from AccumulationDetector
    last_update_id: int = 0
    
    def __init__(self, **data):
        # WHY: Автоматически загружаем config если не передан явно
        if 'config' not in data or data['config'] is None:
            data['config'] = get_config(data.get('symbol', 'BTCUSDT'))
        super().__init__(**data)

    # --- НОВОЕ: Реестр Айсбергов ---
    # Ключ: Decimal (Цена), Значение: IcebergLevel
    active_icebergs: Dict[Decimal, IcebergLevel] = Field(default_factory=dict)

    # State для китов и алго
    whale_cvd: Dict[str, float] = Field(default_factory=lambda: {'whale': 0.0, 'dolphin': 0.0, 'minnow': 0.0})
    trade_count: int = 0
    algo_window: deque = Field(default_factory=deque)
    
    # WHY: Историческая память для свинг-трейдинга (Task 3.2 - Multi-Timeframe Context)
    historical_memory: HistoricalMemory = Field(default_factory=HistoricalMemory)
    
    # WHY: Расширенная детекция алгоритмов (Task: Advanced Algo Detection)
    # История интервалов между сделками для анализа σ_Δt (TWAP vs VWAP)
    algo_interval_history: deque = Field(default_factory=lambda: deque(maxlen=200))
    
    # История размеров последних мелких сделок для детекции Iceberg display_qty
    algo_size_pattern: deque = Field(default_factory=lambda: deque(maxlen=200))
    
    # Последняя детекция алгоритма (для анализа и логирования)
    last_algo_detection: Optional['AlgoDetectionMetrics'] = None
    
    # WHY: История размеров сделок для динамической калибровки порогов (Task: Dynamic Thresholds)
    # Хранит последние 1000 сделок в USD для расчета перцентилей
    trade_size_history: deque = Field(default_factory=lambda: deque(maxlen=1000))
    
    # Для детекции айсбергов с временной валидацией (Delta-t)
    # Структура: [{'trade': TradeEvent, 'visible_before': Decimal, 'trade_time_ms': int, 'price': Decimal, 'is_ask': bool}, ...]
    pending_refill_checks: deque = Field(default_factory=deque)
    
    # === НОВОЕ ПОЛЕ ДЛЯ VPIN (Task: Flow Toxicity) ===
    # WHY: История корзин для расчёта VPIN (Volume-Synchronized Probability of Informed Trading)
    # Храним последние N корзин (обычно 50) для скользящего окна
    vpin_buckets: deque = Field(default_factory=lambda: deque(maxlen=50))
    
    # WHY: Текущая незакрытая корзина (наполняется сделками)
    current_vpin_bucket: Optional[VolumeBucket] = None
    
    # === НОВЫЕ ПОЛЯ ДЛЯ OFI (Task: OFI Implementation) ===
    # WHY: Хранение предыдущего состояния для расчета Order Flow Imbalance
    # Храним только топ-20 уровней для экономии памяти
    # === OPTIMIZATION (Task: Double Buffering - Gemini Phase 2.1) ===
    # Pre-allocated буферы для переиспользования (избегаем 2000 аллокаций/сек)
    previous_bid_snapshot: Dict[Decimal, Decimal] = Field(default_factory=dict)
    previous_ask_snapshot: Dict[Decimal, Decimal] = Field(default_factory=dict)

    model_config = ConfigDict(arbitrary_types_allowed=True)

    def apply_snapshot(self, bids: List[Tuple[Decimal, Decimal]], 
                      asks: List[Tuple[Decimal, Decimal]], 
                      last_update_id: int):
        """
        Применяет полный снапшот стакана (база для дальнейших diffs).
        КРИТИЧНО: Вызывается ОДИН раз при инициализации.
        """
        self.bids.clear()
        self.asks.clear()
        
        for price, qty in bids:
            if qty > 0:
                self.bids[price] = qty
        
        for price, qty in asks:
            if qty > 0:
                self.asks[price] = qty
        
        self.last_update_id = last_update_id
        print(f"📚 Snapshot applied: {len(self.bids)} bids, {len(self.asks)} asks. LastUpdateId: {last_update_id}")
        
        # WHY: CRITICAL FIX (Task: Reconnect Bug Fix) - Gemini Phase 1.1
        # При reconnect сбрасываем старое состояние OFI
        # Иначе calculate_ofi() будет сравнивать новый стакан со старым (до разрыва)
        # === DOUBLE BUFFERING: Используем clear() вместо = None ===
        self.previous_bid_snapshot.clear()
        self.previous_ask_snapshot.clear()
        
        # Сохраняем новое начальное состояние
        self._save_book_snapshot()

    def apply_update(self, update: OrderBookUpdate) -> bool:
        """
        ЧИСТАЯ БИЗНЕС-ЛОГИКА:
        Принимает diff, изменяет состояние стакана.
        
        Returns:
            bool: True если обновление применено, False если пропущено (старое)
        """
        # КРИТИЧЕСКАЯ ПРОВЕРКА: Игнорируем устаревшие updates
        if update.final_update_id and update.final_update_id <= self.last_update_id:
            return False  # Этот update мы уже обработали
        
        # Если пришел update 105, а у нас последний был 100 (мы ждем 101), значит мы потеряли пакеты.
        if update.first_update_id and update.first_update_id > self.last_update_id + 1:
            # ВМЕСТО print и return False -> КИДАЕМ ОШИБКУ
            raise GapDetectedError(f"Gap detected: {self.last_update_id} -> {update.first_update_id}")

        # WHY: Сохраняем снапшот ДО применения update (для OFI) - Task: OFI Implementation
        # Это должно быть ДО _process_side!
        self._save_book_snapshot()

        self._process_side(self.bids, update.bids)
        self._process_side(self.asks, update.asks)
        
        if update.final_update_id:
            self.last_update_id = update.final_update_id
        
        return True

    def _process_side(self, book_side: Dict[Decimal, Decimal], 
                     updates: List[Tuple[Decimal, Decimal]]):
        for price, qty in updates:
            if qty == 0:
                # Если объем 0 - удаляем уровень (если он был)
                book_side.pop(price, None)
            else:
                # Иначе обновляем или вставляем новый объем
                book_side[price] = qty

    # УСТАРЕВШИЙ МЕТОД УДАЛЕН - используется новая архитектура с pending_refill_checks
        
    
    def get_top_bids(self, n: int = 5) -> List[Tuple[Decimal, Decimal]]:
        """Вспомогательный метод для отображения (сортировка O(N log N))"""
        if not self.bids:
            return []
        sorted_bids = sorted(self.bids.items(), key=lambda x: x[0], reverse=True)
        return sorted_bids[:n]

    def get_top_asks(self, n: int = 5) -> List[Tuple[Decimal, Decimal]]:
        """Вспомогательный метод для отображения"""
        if not self.asks:
            return []
        sorted_asks = sorted(self.asks.items(), key=lambda x: x[0])
        return sorted_asks[:n]
    
    def validate_integrity(self) -> bool:
        """Проверка на Crossed Book (bid >= ask)"""
        if not self.bids or not self.asks:
            return True
        
        best_bid = max(self.bids.keys())
        best_ask = min(self.asks.keys())
        
        if best_bid >= best_ask:
            print(f"❌ CROSSED BOOK DETECTED! Bid: {best_bid}, Ask: {best_ask}")
            return False
        return True
    
    def get_spread(self) -> Optional[Decimal]:
        """Вычисляет текущий спред"""
        if not self.bids or not self.asks:
            return None
        return min(self.asks.keys()) - max(self.bids.keys())
    
    def get_mid_price(self) -> Optional[Decimal]:
        """Вычисляет середину спреда"""
        if not self.bids or not self.asks:
            return None
        return (min(self.asks.keys()) + max(self.bids.keys())) / 2

    # --- Этих методов не хватает, вставьте их внутрь LocalOrderBook ---

    def get_best_bid(self) -> Optional[Tuple[Decimal, Decimal]]:
        """Возвращает (price, qty) лучшего бида"""
        if not self.bids: return None
        # Bids сортированы по возрастанию (100, 101, 102). Лучший - последний.
        return self.bids.peekitem(-1)

    def get_best_ask(self) -> Optional[Tuple[Decimal, Decimal]]:
        """Возвращает (price, qty) лучшего аска"""
        if not self.asks: return None
        # Asks сортированы по возрастанию (103, 104, 105). Лучший - первый.
        return self.asks.peekitem(0)

    def get_spread(self) -> Optional[Decimal]:
        bid = self.get_best_bid()
        ask = self.get_best_ask()
        if bid and ask:
            return ask[0] - bid[0]
        return None


    def get_weighted_obi(self, depth: int = 20) -> float:
        """
        Считает Взвешенный Дисбаланс Стакана (Weighted Order Book Imbalance).
        
        Теория: 
        Обычный OBI = (Bid - Ask) / (Bid + Ask) часто манипулируется.
        Мы добавляем 'вес' (decay), который уменьшается по мере удаления от спреда.
        Это фильтрует 'спуфинг' на дальних уровнях.
        
        Returns:
            Число от -1.0 (сильные продажи) до +1.0 (сильные покупки).
        """
        if not self.bids or not self.asks:
            return 0.0

        bid_vol_weighted = 0.0
        ask_vol_weighted = 0.0
        
        # 1. Считаем взвешенные Bids (Покупки)
        # Bids в SortedDict идут min -> max. Нам нужны лучшие (дорогие), поэтому reversed (идем с конца).
        for i, (_, qty) in enumerate(reversed(self.bids.items())):
            if i >= depth: break
            # Вес падает линейно: 1.0, 0.5, 0.33, 0.25...
            weight = 1.0 / (i + 1) 
            bid_vol_weighted += float(qty) * weight

        # 2. Считаем взвешенные Asks (Продажи)
        # Asks идут min -> max. Нам нужны лучшие (дешевые), поэтому берем с начала.
        for i, (_, qty) in enumerate(self.asks.items()):
            if i >= depth: break
            weight = 1.0 / (i + 1)
            ask_vol_weighted += float(qty) * weight

        total_weighted_vol = bid_vol_weighted + ask_vol_weighted
        
        if total_weighted_vol == 0:
            return 0.0

        # Формула дисбаланса: (Bids - Asks) / Total
        return (bid_vol_weighted - ask_vol_weighted) / total_weighted_vol
    
    def register_iceberg(self, price: Decimal, hidden_vol: Decimal, is_ask: bool, confidence: float):
        """
        Обновляет или создает запись об айсберге.
        
        WHY: Использует config.gamma_wall_tolerance_pct для мульти-токен поддержки.
        """
        # 1. Проверяем Gamma Context
        is_gamma = False
        if self.gamma_profile:
            gex = self.gamma_profile
            # WHY: Используем процентный толеранс из конфига (адаптируется к цене токена)
            p_float = float(price)
            tolerance = p_float * float(self.config.gamma_wall_tolerance_pct)
            
            if (abs(p_float - gex.call_wall) < tolerance) or (abs(p_float - gex.put_wall) < tolerance):
                is_gamma = True

        # 2. Обновляем или создаем
        if price in self.active_icebergs:
            lvl = self.active_icebergs[price]
            if lvl.status == IcebergStatus.ACTIVE:
                lvl.total_hidden_volume += hidden_vol
                lvl.last_update_time = datetime.now()
                lvl.confidence_score = max(lvl.confidence_score, confidence)
                # Если вдруг стал гамма-уровнем (обновились данные Deribit)
                lvl.is_gamma_wall = lvl.is_gamma_wall or is_gamma 
                return lvl
        
        # === GEMINI FIX: Категоризация по размеру (Wall Semantics) ===
        # WHY: Определяем is_dolphin для wall_whale_vol vs wall_dolphin_vol метрик
        volume_usd = float(hidden_vol) * float(price)
        is_dolphin = (1000 < volume_usd <= 100000)  # $1k-$100k = dolphin, >$100k = whale
        
        # Создаем новый
        new_lvl = IcebergLevel(
            price=price,
            is_ask=is_ask,
            total_hidden_volume=hidden_vol,
            is_gamma_wall=is_gamma,
            confidence_score=confidence,
            is_dolphin=is_dolphin  # ✅ Категоризация
        )
        self.active_icebergs[price] = new_lvl
        return new_lvl

    def cluster_icebergs_to_zones(self, tolerance_pct: float = 0.002) -> List[PriceZone]:
        """
        WHY: Кластеризация айсбергов в зоны (Task 3.2).
        
        Алгоритм:
        1. Разделяем bid/ask айсберги
        2. Сортируем по цене
        3. Группируем соседние уровни с разницей < tolerance_pct
        4. Создаем PriceZone для каждой группы
        
        Args:
            tolerance_pct: Максимальная разница цен для объединения (default 0.2%)
        
        Returns:
            List[PriceZone]: Список зон (bid + ask)
        """
        zones = []
        
        # Фильтруем только активные айсберги
        active = [lvl for lvl in self.active_icebergs.values() 
                  if lvl.status == IcebergStatus.ACTIVE]
        
        if not active:
            return zones
        
        # Разделяем на bid/ask
        bid_icebergs = sorted([lvl for lvl in active if not lvl.is_ask], 
                             key=lambda x: x.price)
        ask_icebergs = sorted([lvl for lvl in active if lvl.is_ask], 
                             key=lambda x: x.price)
        
        # Кластеризуем каждую сторону
        for is_ask, icebergs in [(False, bid_icebergs), (True, ask_icebergs)]:
            if not icebergs:
                continue
            
            # Начинаем первый кластер
            current_cluster = [icebergs[0]]
            
            for i in range(1, len(icebergs)):
                prev_price = icebergs[i-1].price
                curr_price = icebergs[i].price
                
                # Проверяем близость
                price_diff_pct = float(abs(curr_price - prev_price) / prev_price)
                
                if price_diff_pct <= tolerance_pct:
                    # Добавляем в текущий кластер
                    current_cluster.append(icebergs[i])
                else:
                    # Создаем зону из текущего кластера
                    zones.append(self._create_zone_from_cluster(current_cluster, is_ask))
                    # Начинаем новый кластер
                    current_cluster = [icebergs[i]]
            
            # Не забываем последний кластер
            if current_cluster:
                zones.append(self._create_zone_from_cluster(current_cluster, is_ask))
        
        return zones
    
    def _create_zone_from_cluster(self, cluster: List[IcebergLevel], is_ask: bool) -> PriceZone:
        """
        WHY: Вспомогательный метод для создания PriceZone из кластера айсбергов.
        
        Вычисляет:
        - Взвешенную среднюю цену (weighted by volume)
        - Суммарный объем
        - Диапазон цен
        """
        total_vol = sum(lvl.total_hidden_volume for lvl in cluster)
        
        # Взвешенная средняя цена
        weighted_sum = sum(lvl.price * lvl.total_hidden_volume for lvl in cluster)
        center_price = weighted_sum / total_vol if total_vol > 0 else cluster[0].price
        
        # Диапазон
        prices = [lvl.price for lvl in cluster]
        price_range = (min(prices), max(prices))
        
        return PriceZone(
            center_price=center_price,
            is_ask=is_ask,
            total_volume=total_vol,
            iceberg_count=len(cluster),
            price_range=price_range,
            icebergs=cluster
        )

    def check_breaches(self, current_trade_price: Decimal) -> List[IcebergLevel]:
        """
        Проверяет пробой айсберг-уровней.
        
        WHY: Использует config.breach_tolerance_pct для адаптации к волатильности токена.
        """
        breached = []
        # WHY: Берем толеранс из конфига (для ETH может быть шире чем для BTC)
        tolerance_pct = self.config.breach_tolerance_pct

        for price, lvl in list(self.active_icebergs.items()):
            if lvl.status != IcebergStatus.ACTIVE:
                continue
            
            # Расчет порога пробоя
            tolerance = price * tolerance_pct

            # Если ASK (продавец), пробой — это цена сильно ВЫШЕ
            if lvl.is_ask and current_trade_price > (price + tolerance):
                lvl.status = IcebergStatus.BREACHED
                breached.append(lvl)
            
            # Если BID (покупатель), пробой — это цена сильно НИЖЕ
            elif not lvl.is_ask and current_trade_price < (price - tolerance):
                lvl.status = IcebergStatus.BREACHED
                breached.append(lvl)
                
        return breached
    
    def reconcile_with_snapshot(self, bids: List[Tuple[Decimal, Decimal]], asks: List[Tuple[Decimal, Decimal]]):
        """
        WHY: Reconcile icebergs after snapshot resync (Critical Bug Fix - Gemini 2.2)
        
        После WebSocket reconnect и resync проверяет, какие айсберги больше не существуют
        в новом снапшоте и помечает их как CANCELLED (ghost icebergs).
        
        Scenario:
        1. Before resync: Iceberg at 60000 BID
        2. Network disconnect → iceberg cancelled by trader during disconnect
        3. After resync: Snapshot has no liquidity at 60000
        4. This method: Marks iceberg as CANCELLED (not ACTIVE)
        
        Args:
            bids: New snapshot bids [(price, qty), ...]
            asks: New snapshot asks [(price, qty), ...]
        """
        # WHY: Convert snapshot to dict for O(1) lookup
        snapshot_bid_prices = {price for price, qty in bids if qty > self.config.dust_threshold}
        snapshot_ask_prices = {price for price, qty in asks if qty > self.config.dust_threshold}
        
        # WHY: Iterate through active icebergs and check if they still exist
        for price, iceberg in self.active_icebergs.items():
            # Skip already invalidated icebergs
            if iceberg.status != IcebergStatus.ACTIVE:
                continue
            
            # Check BID icebergs
            if not iceberg.is_ask:
                # If price not in snapshot OR volume is dust → mark as CANCELLED
                if price not in snapshot_bid_prices:
                    iceberg.status = IcebergStatus.CANCELLED
                    iceberg.last_update_time = datetime.now()
                    
                    # WHY: Store cancellation context for spoofing analysis
                    mid = self.get_mid_price()
                    if mid:
                        distance_pct = abs((mid - price) / price * 100)
                        iceberg.cancellation_context = CancellationContext(
                            mid_price_at_cancel=mid,
                            distance_from_level_pct=distance_pct,
                            price_velocity_5s=Decimal("0"),  # Not tracked here
                            moving_towards_level=False,
                            volume_executed_pct=Decimal("0")  # Unknown after resync
                        )
            
            # Check ASK icebergs
            else:
                if price not in snapshot_ask_prices:
                    iceberg.status = IcebergStatus.CANCELLED
                    iceberg.last_update_time = datetime.now()
                    
                    # Store context
                    mid = self.get_mid_price()
                    if mid:
                        distance_pct = abs((price - mid) / price * 100)
                        iceberg.cancellation_context = CancellationContext(
                            mid_price_at_cancel=mid,
                            distance_from_level_pct=distance_pct,
                            price_velocity_5s=Decimal("0"),
                            moving_towards_level=False,
                            volume_executed_pct=Decimal("0")
                        )
    
    def get_iceberg_at_price(self, price: Decimal, is_ask: bool) -> Optional[IcebergLevel]:
        """
        WHY: Helper method to retrieve iceberg at specific price and side.
        
        Used by reconciliation and tests to verify iceberg state.
        
        Args:
            price: Price level to check
            is_ask: True for ASK iceberg, False for BID
        
        Returns:
            IcebergLevel if exists, None otherwise
        """
        iceberg = self.active_icebergs.get(price)
        if iceberg and iceberg.is_ask == is_ask:
            return iceberg
        return None

    def cleanup_old_levels(self, seconds=3600):
        """Удаляет старые уровни (TTL), чтобы не засорять память [cite: 541]"""
        now = datetime.now()
        keys_to_delete = []
        for price, lvl in self.active_icebergs.items():
            if (now - lvl.last_update_time).total_seconds() > seconds:
                keys_to_delete.append(price)
            # Также удаляем пробитые уровни, если они старые (например, > 5 мин)
            elif lvl.status == IcebergStatus.BREACHED and (now - lvl.last_update_time).total_seconds() > 300:
                keys_to_delete.append(price)
                
        for k in keys_to_delete:
            del self.active_icebergs[k]
    
    def is_near_gamma_wall(self, price: Decimal, tolerance_pct: float = 0.5) -> Tuple[bool, Optional[str]]:
        """
        WHY: Проверяет, находится ли цена близко к Gamma Wall.
        
        Args:
            price: Цена для проверки
            tolerance_pct: Допуск в процентах (default 0.5%)
        
        Returns:
            Tuple[is_near, wall_type] где wall_type = 'CALL' | 'PUT' | None
        """
        if self.gamma_profile is None:
            return False, None
        
        price_float = float(price)
        gex = self.gamma_profile
        
        # Вычисляем абсолютный толеранс
        tolerance = price_float * (tolerance_pct / 100.0)
        
        # Проверяем Call Wall
        if abs(price_float - gex.call_wall) < tolerance:
            return True, 'CALL'
        
        # Проверяем Put Wall
        if abs(price_float - gex.put_wall) < tolerance:
            return True, 'PUT'
        
        return False, None
    
    # ===================================================================
    # НОВЫЕ МЕТОДЫ: OFI + Exponential OBI (Task: OFI Implementation)
    # ===================================================================
    
    def _save_book_snapshot(self, depth: int = None):
        """
        WHY: Сохраняет текущее состояние топ-N уровней для расчета OFI.
        
        Вызывается ПОСЛЕ каждого apply_update() для отслеживания изменений.
        Использует shallow copy только для необходимых уровней.
        
        === OPTIMIZATION (Task: Gemini Phase 2.1) ===
        Используем SortedDict.peekitem() вместо sorted(keys) для O(1) доступа.
        
        === UPDATE (Task: Gemini Phase 2.2) ===
        Теперь использует config.ofi_depth по умолчанию.
        
        Args:
            depth: Количество уровней для сохранения. Если None - берётся config.ofi_depth
        """
        # WHY: Если depth не передан - берём из config
        if depth is None:
            depth = self.config.ofi_depth
        
        # WHY: Используем peekitem() - O(1) вместо sorted() - O(N log N)
        # peekitem(-1) = последний (лучший bid)
        # peekitem(0) = первый (лучший ask)
        
        # === DOUBLE BUFFERING: Очищаем буферы вместо создания новых ===
        self.previous_bid_snapshot.clear()  # ✅ Переиспользование памяти
        self.previous_ask_snapshot.clear()  # ✅ Нет новой аллокации!
        
        # Сохраняем топ-N бидов (самые дорогие)
        n_bids = min(depth, len(self.bids))
        for i in range(n_bids):
            # peekitem(-1) = best, peekitem(-2) = 2nd best, ...
            price, qty = self.bids.peekitem(-(i + 1))
            self.previous_bid_snapshot[price] = qty
        
        # Сохраняем топ-N асков (самые дешевые)
        n_asks = min(depth, len(self.asks))
        for i in range(n_asks):
            # peekitem(0) = best, peekitem(1) = 2nd best, ...
            price, qty = self.asks.peekitem(i)
            self.previous_ask_snapshot[price] = qty
    
    def calculate_ofi(self, depth: int = None) -> float:
        """
        WHY: Вычисляет Order Flow Imbalance (OFI) - изменение ликвидности.
        
        Теория (документ "Анализ данных смарт-мани", раздел 3.2):
        - OFI = Δ(bid_volume) - Δ(ask_volume)
        - Положительный OFI при стабильной цене = скрытое предложение (Sell Iceberg)
        - Отрицательный OFI при стабильной цене = скрытый спрос (Buy Iceberg)
        
        Формула:
        OFI = Σ(bid_add - bid_cancel) - Σ(ask_add - ask_cancel)
        
        === UPDATE (Task: Gemini Phase 2.2 - Dynamic OFI Depth) ===
        Теперь использует config.ofi_depth по умолчанию.
        
        Args:
            depth: Глубина анализа. Если None - берётся из config.ofi_depth
        
        Returns:
            float: OFI значение (положительное = давление покупателей)
        """
        # WHY: Если depth не передан - берём из config
        if depth is None:
            depth = self.config.ofi_depth
        # Если это первый update - нет предыдущего состояния
        # === DOUBLE BUFFERING: Буферы всегда dict, проверяем пустые ===
        if not self.previous_bid_snapshot or not self.previous_ask_snapshot:
            return 0.0
        
        delta_bid_volume = 0.0
        delta_ask_volume = 0.0
        
        # 1. Анализируем изменения BIDS
        # Берем топ-N бидов (самые дорогие)
        current_bids = dict(sorted(self.bids.items(), reverse=True)[:depth])
        
        for price, current_qty in current_bids.items():
            previous_qty = self.previous_bid_snapshot.get(price, Decimal("0"))
            delta = float(current_qty - previous_qty)
            delta_bid_volume += delta
        
        # Проверяем удаленные уровни (были в previous, нет в current)
        for price, previous_qty in self.previous_bid_snapshot.items():
            if price not in current_bids:
                delta_bid_volume -= float(previous_qty)
        
        # 2. Анализируем изменения ASKS
        current_asks = dict(sorted(self.asks.items())[:depth])
        
        for price, current_qty in current_asks.items():
            previous_qty = self.previous_ask_snapshot.get(price, Decimal("0"))
            delta = float(current_qty - previous_qty)
            delta_ask_volume += delta
        
        # Проверяем удаленные уровни
        for price, previous_qty in self.previous_ask_snapshot.items():
            if price not in current_asks:
                delta_ask_volume -= float(previous_qty)
        
        # 3. Расчет OFI = dBid - dAsk
        # Положительное значение = больше bid ликвидности добавлено
        ofi = delta_bid_volume - delta_ask_volume
        
        return ofi
    
    def get_weighted_obi(self, depth: int = 20, use_exponential: bool = True) -> float:
        """
        WHY: Считает Взвешенный Дисбаланс Стакана (Weighted Order Book Imbalance).
        
        === ОБНОВЛЕНИЕ: Экспоненциальный decay (Task: Exponential Weight Decay) ===
        Теперь поддерживает экспоненциальное затухание весов по формуле:
        weight = e^(-λ * distance_from_mid)
        
        Теория (документ "Анализ данных биржевого стакана"):
        - Линейный decay (1/i) неоптимален - переоценивает дальние уровни
        - Экспоненциальный decay отражает реальную вероятность исполнения
        - λ (лямбда) - коэффициент ликвидности (из config)
        
        Args:
            depth: Количество уровней для анализа (default 20)
            use_exponential: True = exponential decay, False = linear (legacy)
        
        Returns:
            Число от -1.0 (сильные продажи) до +1.0 (сильные покупки)
        """
        if not self.bids and not self.asks:
            return 0.0
        
        # WHY: Edge case - если только одна сторона стакана
        if not self.bids:
            return -1.0
        if not self.asks:
            return 1.0
        
        # Получаем mid-price для расчета distance
        mid_price = self.get_mid_price()
        if mid_price is None:
            return 0.0
        
        # WHY: Параметр λ из config (адаптирован под волатильность токена)
        # Для BTC λ=0.1, для ETH λ=0.15 (больше волатильность → быстрее затухание)
        lambda_decay = 0.1  # DEFAULT (если нет в config)
        if hasattr(self.config, 'lambda_decay'):
            lambda_decay = float(self.config.lambda_decay)
        
        # WHY: Масштабируем λ для процентных расстояний (x100 для радикальной фильтрации)
        # Расчет: 0.33% расстояние → 0.33 * 100 = 33 → e^(-0.1 * 33) ≈ 0.000037
        # Для 0.83% (спуф $500) → e^(-8.3) ≈ 0.00025
        # Для 0.08% (реал $50) → e^(-0.8) ≈ 0.45
        lambda_decay_scaled = lambda_decay * 100.0
        
        bid_vol_weighted = 0.0
        ask_vol_weighted = 0.0
        
        # --- 1. WEIGHTED BIDS ---
        for i, (price, qty) in enumerate(reversed(self.bids.items())):
            if i >= depth:
                break
            
            if use_exponential:
                # WHY: Расчет расстояния в ПРОЦЕНТАХ от mid (более универсально)
                # distance = |price - mid| / mid * 100
                distance_from_mid = abs(float(mid_price - price))
                distance_pct = (distance_from_mid / float(mid_price)) * 100.0
                
                # WHY: Используем SCALED λ (радикальная фильтрация спуфинга)
                # Для BTC: 0.0017% (~1 тик) → вес = e^(-10.0 * 0.0017) ≈ 0.983
                # Для BTC: 0.08% ($50) → вес = e^(-10.0 * 0.08) ≈ 0.45 (реальная ликвидность)
                # Для BTC: 0.33% ($200) → вес = e^(-10.0 * 0.33) ≈ 0.000037 (спуф фильтруется)
                from math import exp
                weight = exp(-lambda_decay_scaled * distance_pct)
            else:
                # LEGACY: Линейное затухание (для сравнения)
                weight = 1.0 / (i + 1)
            
            bid_vol_weighted += float(qty) * weight
        
        # --- 2. WEIGHTED ASKS ---
        for i, (price, qty) in enumerate(self.asks.items()):
            if i >= depth:
                break
            
            if use_exponential:
                # WHY: Та же логика - % расстояние от mid
                distance_from_mid = abs(float(price - mid_price))
                distance_pct = (distance_from_mid / float(mid_price)) * 100.0
                
                from math import exp
                weight = exp(-lambda_decay_scaled * distance_pct)
            else:
                weight = 1.0 / (i + 1)
            
            ask_vol_weighted += float(qty) * weight
        
        # --- 3. CALCULATE IMBALANCE ---
        total_weighted_vol = bid_vol_weighted + ask_vol_weighted
        
        if total_weighted_vol == 0:
            return 0.0
        
        # Формула дисбаланса: (Bids - Asks) / Total
        obi = (bid_vol_weighted - ask_vol_weighted) / total_weighted_vol
        
        return obi
    
    # ===================================================================
    # CVD DIVERGENCE DETECTION (Decision Layer - Critical Tag)
    # ===================================================================
    
    def detect_cvd_divergence(
        self,
        price_history: List[float],
        cvd_history: List[float],
        min_points: int = 3,
        timeframe_min: Tuple[float, float] = (1.0, 60.0)
    ) -> Tuple[bool, Optional[str], float]:
        """
        WHY: Детектирует дивергенцию между ценой и Whale CVD.
        
        Теория (документ "Smart Money Analysis", раздел 3.1):
        - Bullish Divergence: Цена делает Lower Low, CVD делает Higher Low
        - Bearish Divergence: Цена делает Higher High, CVD делает Lower High
        - Это CONTRARIAN SIGNAL - показывает скрытую аккумуляцию/дистрибуцию
        
        Args:
            price_history: Список цен (минимум 3 точки)
            cvd_history: Список Whale CVD значений (синхронизирован с ценами)
            min_points: Минимальное количество точек для детекции (default 3)
            timeframe_min: (min, max) временной фрейм в минутах для валидной дивергенции
        
        Returns:
            Tuple[is_divergence, divergence_type, confidence]
            - is_divergence: True если дивергенция обнаружена
            - divergence_type: 'BULLISH' | 'BEARISH' | None
            - confidence: 0.0-1.0 (сила дивергенции)
        
        Examples:
            >>> # Bullish Divergence (цена падает, CVD растёт)
            >>> prices = [100000, 99000, 98500]  # Lower Lows
            >>> cvds = [-10000, -5000, -2000]    # Higher Lows (киты покупают)
            >>> is_div, div_type, conf = book.detect_cvd_divergence(prices, cvds)
            >>> assert is_div == True
            >>> assert div_type == 'BULLISH'
        """
        # 1. Валидация входных данных
        if len(price_history) < min_points or len(cvd_history) < min_points:
            return False, None, 0.0
        
        if len(price_history) != len(cvd_history):
            return False, None, 0.0
        
        # 2. Проверяем что достаточно данных для анализа
        n = len(price_history)
        if n < 3:
            return False, None, 0.0
        
        # 3. Определяем направление ЦЕНЫ (используем первую и последнюю точки)
        price_start = price_history[0]
        price_end = price_history[-1]
        price_change_pct = ((price_end - price_start) / price_start) * 100.0
        
        # 4. Определяем направление CVD
        cvd_start = cvd_history[0]
        cvd_end = cvd_history[-1]
        cvd_change = cvd_end - cvd_start
        
        # 5. Проверяем наличие дивергенции
        is_divergence = False
        divergence_type = None
        confidence = 0.0
        
        # BULLISH DIVERGENCE: Цена падает (Lower Lows), CVD растёт (Higher Lows)
        # Признак: Киты покупают на падении (аккумуляция)
        if price_change_pct < -0.5 and cvd_change > 0:  # Цена упала >0.5%, CVD вырос
            is_divergence = True
            divergence_type = 'BULLISH'
            
            # Confidence = сила расхождения
            # Чем больше цена упала И чем больше CVD вырос → выше confidence
            price_strength = abs(price_change_pct) / 5.0  # Нормализуем к 5% падению
            cvd_strength = abs(cvd_change) / 50000.0     # Нормализуем к $50k CVD
            confidence = min(1.0, (price_strength + cvd_strength) / 2.0)
        
        # BEARISH DIVERGENCE: Цена растёт (Higher Highs), CVD падает (Lower Highs)
        # Признак: Киты продают в рост (дистрибуция)
        elif price_change_pct > 0.5 and cvd_change < 0:  # Цена выросла >0.5%, CVD упал
            is_divergence = True
            divergence_type = 'BEARISH'
            
            price_strength = abs(price_change_pct) / 5.0
            cvd_strength = abs(cvd_change) / 50000.0
            confidence = min(1.0, (price_strength + cvd_strength) / 2.0)
        
        return is_divergence, divergence_type, confidence
    
    def get_latest_cvd(self, timeframe: str = '1h', cohort: str = 'whale') -> Optional[float]:
        """
        WHY: Helper для получения последнего CVD значения по таймфрейму.
        
        Args:
            timeframe: '1h', '4h', '1d', '1w', '1m'
            cohort: 'whale' или 'minnow'
        
        Returns:
            float: Последнее CVD значение или None если нет данных
        """
        # Выбираем нужную историю
        if cohort == 'whale':
            hist_map = {
                '1h': self.historical_memory.cvd_history_1h,
                '4h': self.historical_memory.cvd_history_4h,
                '1d': self.historical_memory.cvd_history_1d,
                '1w': self.historical_memory.cvd_history_1w,
                '1m': self.historical_memory.cvd_history_1m
            }
        else:  # minnow
            hist_map = {
                '1h': self.historical_memory.minnow_cvd_history_1h,
                '4h': self.historical_memory.minnow_cvd_history_4h,
                '1d': self.historical_memory.minnow_cvd_history_1d,
                '1w': self.historical_memory.minnow_cvd_history_1w,
                '1m': self.historical_memory.minnow_cvd_history_1m
            }
        
        hist = hist_map.get(timeframe)
        if hist and len(hist) > 0:
            # Возвращаем последнее значение (timestamp, cvd)
            return hist[-1][1]
        return None
    
    def get_cvd_change(self, timeframe: str = '1h', cohort: str = 'whale', periods: int = 3) -> Optional[float]:
        """
        WHY: Вычисляет изменение CVD за последние N периодов.
        
        Args:
            timeframe: '1h', '4h', '1d', '1w', '1m'
            cohort: 'whale' или 'minnow'
            periods: Количество периодов для анализа
        
        Returns:
            float: CVD_latest - CVD_start (положительное = покупки)
        """
        # Выбираем историю
        if cohort == 'whale':
            hist_map = {
                '1h': self.historical_memory.cvd_history_1h,
                '4h': self.historical_memory.cvd_history_4h,
                '1d': self.historical_memory.cvd_history_1d,
                '1w': self.historical_memory.cvd_history_1w,
                '1m': self.historical_memory.cvd_history_1m
            }
        else:
            hist_map = {
                '1h': self.historical_memory.minnow_cvd_history_1h,
                '4h': self.historical_memory.minnow_cvd_history_4h,
                '1d': self.historical_memory.minnow_cvd_history_1d,
                '1w': self.historical_memory.minnow_cvd_history_1w,
                '1m': self.historical_memory.minnow_cvd_history_1m
            }
        
        hist = hist_map.get(timeframe)
        if not hist or len(hist) < periods:
            return None
        
        # Берём последние N точек
        recent = list(hist)[-periods:]
        cvd_start = recent[0][1]
        cvd_end = recent[-1][1]
        
        return cvd_end - cvd_start


# ===========================================================================
# НОВЫЙ КЛАСС: AlgoDetectionMetrics (Task: Advanced Algo Detection)
# ===========================================================================

@dataclass
class AlgoDetectionMetrics:
    """
    WHY: Структура для хранения метрик детекции алгоритмов.
    
    Используется для различения TWAP/VWAP/Iceberg/Sweep алгоритмов
    на основе математического анализа временных рядов.
    
    Теория (документ "Идентификация айсберг-ордеров", раздел 1.2):
    - TWAP: σ_Δt очень низкая (~const intervals)
    - VWAP: σ_Δt коррелирует с волатильностью
    - Iceberg Algo: Использует фиксированный display_qty
    - Sweep Algo: Агрессивные market orders без паттерна
    """
    
    # Временная метрика (для TWAP vs VWAP)
    std_dev_intervals_ms: float  # Стандартное отклонение времени между сделками
    mean_interval_ms: float      # Среднее время между сделками
    
    # Размерная метрика (для Iceberg Algo)
    size_uniformity_score: float  # 0.0-1.0 (1.0 = все сделки одинакового размера)
    dominant_size_usd: Optional[float]  # Доминирующий размер сделки (если есть)
    
    # Направленность
    directional_ratio: float  # Процент сделок в доминирующем направлении (0.0-1.0)
    
    # Классификация
    algo_type: Optional[str] = None  # 'TWAP', 'VWAP', 'ICEBERG', 'SWEEP', None
    confidence: float = 0.0  # 0.0-1.0


# ===========================================================================
# DECISION LAYER: Quality Tags for Swing Trading Signals
# ===========================================================================

@dataclass
class IcebergQualityTags:
    """
    WHY: Enriches iceberg detection with actionable intelligence for swing trading.
    
    Теория (документ "Smart Money Analysis", разделы 2.1-2.3):
    - Не все айсберги равны: мелкие HFT-алгоритмы vs крупные институционалы
    - Контекст имеет значение: совпадение с Gamma Walls повышает вероятность удержания уровня
    - Временные характеристики: долгоживущие айсберги (>5 мин) = позиционные игроки
    
    Categories:
    1. Size Tags: WHALE, SHARK, INSTITUTIONAL_BLOCK
    2. Context Tags: GAMMA_SUPPORT, OFI_CONFIRMED, CVD_DIVERGENCE
    3. Time Tags: PERSISTENT, FLASH
    4. Quality Metrics: Win Rate, Absorbed Volume Ratio
    """
    
    # --- SIZE CLASSIFICATION ---
    is_whale: bool = False  # Volume > $100k or 95th percentile
    is_shark: bool = False  # Volume $10k-$100k
    is_institutional_block: bool = False  # Uniform size pattern (algo signature)
    
    # --- MARKET CONTEXT ---
    gamma_support: bool = False  # Coincides with high GEX Put Wall
    gamma_resistance: bool = False  # Coincides with high GEX Call Wall
    ofi_confirmed: bool = False  # OFI aligns with hidden volume direction
    cvd_divergence: bool = False  # Price vs Whale CVD divergence (contrarian signal)
    
    # --- TEMPORAL CHARACTERISTICS ---
    is_persistent: bool = False  # Lifetime > 5 minutes (positional player)
    is_flash: bool = False  # Lifetime < 1 second (HFT/Spoofing)
    
    # --- QUALITY METRICS ---
    absorbed_volume_ratio: float = 0.0  # V_total_exec / V_visible (раздел 4.1)
    iceberg_win_rate: Optional[float] = None  # Historical bounce probability at this level
    distance_to_gamma_wall_bps: Optional[float] = None  # Distance to nearest GEX level (basis points)
    
    # --- META ---
    confidence_score: float = 0.0  # 0.0-1.0: aggregated quality score
    recommended_action: Optional[str] = None  # 'BUY', 'SELL', 'HOLD', 'AVOID'
    
    def get_tag_summary(self) -> str:
        """Returns emoji-rich human-readable summary of tags."""
        tags = []
        if self.is_whale: tags.append("🐳WHALE")
        if self.is_shark: tags.append("🦈SHARK")
        if self.gamma_support: tags.append("🛡️GAMMA_SUPPORT")
        if self.ofi_confirmed: tags.append("✅OFI_CONFIRMED")
        if self.cvd_divergence: tags.append("🔀CVD_DIVERGENCE")
        if self.is_persistent: tags.append("⏳PERSISTENT")
        return " ".join(tags) if tags else "NO_TAGS"


