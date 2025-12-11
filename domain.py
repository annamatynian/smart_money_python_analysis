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
    
    # === НОВЫЕ ПОЛЯ ДЛЯ OFI (Task: OFI Implementation) ===
    # WHY: Хранение предыдущего состояния для расчета Order Flow Imbalance
    # Храним только топ-20 уровней для экономии памяти
    previous_bid_snapshot: Optional[Dict[Decimal, Decimal]] = Field(default=None)
    previous_ask_snapshot: Optional[Dict[Decimal, Decimal]] = Field(default=None)

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
        self.previous_bid_snapshot = None
        self.previous_ask_snapshot = None
        
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
        
        # Создаем новый
        new_lvl = IcebergLevel(
            price=price,
            is_ask=is_ask,
            total_hidden_volume=hidden_vol,
            is_gamma_wall=is_gamma,
            confidence_score=confidence
        )
        self.active_icebergs[price] = new_lvl
        return new_lvl

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
        
        # Сохраняем топ-N бидов (самые дорогие)
        self.previous_bid_snapshot = {}
        n_bids = min(depth, len(self.bids))
        for i in range(n_bids):
            # peekitem(-1) = best, peekitem(-2) = 2nd best, ...
            price, qty = self.bids.peekitem(-(i + 1))
            self.previous_bid_snapshot[price] = qty
        
        # Сохраняем топ-N асков (самые дешевые)
        self.previous_ask_snapshot = {}
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
        if self.previous_bid_snapshot is None or self.previous_ask_snapshot is None:
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