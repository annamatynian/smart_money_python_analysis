# АНАЛИЗ: Мультитаймфреймовая архитектура

## ✅ ЧТО УЖЕ ЕСТЬ:

### 1. **AccumulationDetector с мультитаймфреймами** (analyzers.py)
```python
def detect_accumulation(self, timeframe: str = '1h')
def detect_accumulation_multi_timeframe()  # Проверяет 1H, 4H, 1D, 1W
```

**Текущая реализация:**
- ✅ Поддерживает timeframes: `'1h'`, `'4h'`, `'1d'`, `'1w'`
- ✅ CVD divergence detection на разных ТФ
- ✅ Wyckoff patterns (SPRING, UPTHRUST, ACCUMULATION, DISTRIBUTION)
- ✅ Корреляция с айсберг-зонами

**НО:**
- ❌ Данные читаются из `book.historical_memory` - но эта структура работает **только на тиковых данных**
- ❌ Нет агрегации тиков в свечи 1H/4H/1D/1W
- ❌ Нет хранения исторических свечей в БД
- ❌ `historical_memory.detect_cvd_divergence(timeframe)` - **НЕ РЕАЛИЗОВАНО**

---

## ❌ ЧТО НЕ ХВАТАЕТ:

### Проблема 1: Нет агрегации тиков в свечи
**Текущее состояние:**
- Система работает с real-time тиками (trades, depth updates)
- LocalOrderBook хранит `whale_cvd`, `obi`, `ofi` - но это **моментальные значения**
- Нет накопления этих метрик на часовых/дневных свечах

**Что нужно:**
- Механизм агрегации CVD/OBI/OFI в свечи (1H, 4H, 1D, 1W, 1M)
- Хранение свечей в PostgreSQL
- Чтение свечей для мультитаймфреймового анализа

---

### Проблема 2: `historical_memory` не существует
**Текущий код:**
```python
# analyzers.py:543
is_divergence, div_type = self.book.historical_memory.detect_cvd_divergence(timeframe)
```

**Факт:** `LocalOrderBook` (domain.py) **НЕ ИМЕЕТ** атрибута `historical_memory`

**Проверка domain.py:**
```python
class LocalOrderBook:
    def __init__(self, symbol: str):
        self.bids = SortedDict()
        self.asks = SortedDict()
        self.whale_cvd = {'whale': 0, 'dolphin': 0, 'minnow': 0}
        self.active_icebergs = {}
        # ... НЕТ historical_memory
```

**Вывод:** `AccumulationDetector` **НЕ РАБОТАЕТ** - вызовет AttributeError

---

## 🎯 РЕШЕНИЕ: 3-компонентная архитектура

### КОМПОНЕНТ 1: CandleAggregator (Новый класс)

**Задача:** Агрегировать тики в OHLCV + Smart Money metrics

```python
@dataclass
class SmartMoneyCandle:
    """Свеча со Smart Money метриками"""
    timestamp: datetime        # Открытие свечи
    timeframe: str            # '1H', '4H', '1D', '1W', '1M'
    
    # OHLCV
    open: float
    high: float
    low: float
    close: float
    volume: float
    
    # Smart Money Metrics (агрегированные за свечу)
    whale_cvd_delta: float    # Изменение whale CVD за свечу
    fish_cvd_delta: float     # Изменение fish CVD за свечу
    dolphin_cvd_delta: float  # Изменение dolphin CVD за свечу
    
    obi_avg: float            # Средний OBI за свечу
    ofi_sum: float            # Суммарный OFI за свечу
    
    # Айсберги
    iceberg_count: int        # Количество обнаруженных айсбергов
    iceberg_volume: float     # Суммарный скрытый объем
    
    # Derivatives (если доступно)
    basis_avg: Optional[float] = None
    skew_avg: Optional[float] = None

class CandleAggregator:
    """
    WHY: Агрегирует тиковые данные в мультитаймфреймовые свечи.
    
    Механизм:
    1. Слушает TradeEvent и обновления whale_cvd из LocalOrderBook
    2. Накапливает метрики в буферах (по таймфреймам)
    3. Когда свеча закрывается → flush в PostgreSQL
    """
    
    def __init__(self, symbol: str, repository):
        self.symbol = symbol
        self.repository = repository
        
        # Буферы для каждого таймфрейма
        self.buffers = {
            '1H': CandleBuffer(timedelta(hours=1)),
            '4H': CandleBuffer(timedelta(hours=4)),
            '1D': CandleBuffer(timedelta(days=1)),
            '1W': CandleBuffer(timedelta(weeks=1)),
            '1M': CandleBuffer(timedelta(days=30))  # Упрощение
        }
    
    def on_trade(self, trade: TradeEvent, whale_cvd_snapshot: dict):
        """
        Вызывается на каждой сделке.
        
        Args:
            trade: Событие сделки
            whale_cvd_snapshot: {'whale': 123.5, 'dolphin': 45.2, 'minnow': -20.1}
        """
        # Обновляем все буферы
        for tf, buffer in self.buffers.items():
            buffer.add_trade(trade, whale_cvd_snapshot)
            
            # Если свеча закрылась → сохраняем
            if buffer.should_flush():
                candle = buffer.flush()
                asyncio.create_task(self.repository.save_candle(candle))
```

---

### КОМПОНЕНТ 2: PostgreSQL схема для свечей

```sql
CREATE TABLE smart_money_candles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    symbol VARCHAR(20) NOT NULL,
    timeframe VARCHAR(5) NOT NULL,  -- '1H', '4H', '1D', '1W', '1M'
    timestamp TIMESTAMP WITH TIME ZONE NOT NULL,
    
    -- OHLCV
    open DECIMAL(20, 8),
    high DECIMAL(20, 8),
    low DECIMAL(20, 8),
    close DECIMAL(20, 8),
    volume DECIMAL(20, 8),
    
    -- Smart Money Metrics
    whale_cvd_delta DECIMAL(20, 4),
    fish_cvd_delta DECIMAL(20, 4),
    dolphin_cvd_delta DECIMAL(20, 4),
    
    obi_avg DECIMAL(10, 6),
    ofi_sum DECIMAL(20, 4),
    
    iceberg_count INT,
    iceberg_volume DECIMAL(20, 8),
    
    -- Derivatives
    basis_avg DECIMAL(10, 2),
    skew_avg DECIMAL(10, 2),
    
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    
    -- Индексы для быстрого поиска
    UNIQUE(symbol, timeframe, timestamp)
);

CREATE INDEX idx_candles_lookup ON smart_money_candles(symbol, timeframe, timestamp DESC);
```

---

### КОМПОНЕНТ 3: HistoricalMemory (Новый класс)

```python
class HistoricalMemory:
    """
    WHY: Загружает исторические свечи и детектирует дивергенции.
    
    Заменяет несуществующий book.historical_memory.
    """
    
    def __init__(self, symbol: str, repository):
        self.symbol = symbol
        self.repository = repository
    
    async def detect_cvd_divergence(
        self, 
        timeframe: str,
        lookback_periods: int = 20
    ) -> Tuple[bool, Optional[str]]:
        """
        WHY: Детектирует CVD дивергенцию на заданном таймфрейме.
        
        Логика:
        1. Загружаем последние N свечей из БД
        2. Извлекаем price_history и whale_cvd_history
        3. Применяем алгоритм из LocalOrderBook.detect_cvd_divergence()
        
        Args:
            timeframe: '1H', '4H', '1D', '1W', '1M'
            lookback_periods: Сколько свечей анализировать
        
        Returns:
            (is_divergence, divergence_type)
            - True, 'BULLISH': Цена падает, CVD растет
            - True, 'BEARISH': Цена растет, CVD падает
            - False, None: Нет дивергенции
        """
        # 1. Загружаем свечи из БД
        candles = await self.repository.get_candles(
            symbol=self.symbol,
            timeframe=timeframe,
            limit=lookback_periods
        )
        
        if len(candles) < 3:
            return False, None
        
        # 2. Извлекаем данные
        price_history = [c.close for c in candles]
        whale_cvd_history = [c.whale_cvd_delta for c in candles]
        
        # 3. Применяем алгоритм (уже есть в domain.py)
        # NOTE: Нужно перенести логику или вызвать напрямую
        is_div, div_type, confidence = self._calculate_divergence(
            price_history, 
            whale_cvd_history
        )
        
        return is_div, div_type
```

---

## 🚀 ПЛАН РЕАЛИЗАЦИИ (3 шага):

### ШАГ 7.1: Создать CandleAggregator + PostgreSQL схему
- Добавить таблицу `smart_money_candles` (миграция)
- Реализовать `CandleAggregator` класс
- Интегрировать в `TradingEngine._consume_and_analyze()`

### ШАГ 7.2: Создать HistoricalMemory класс
- Реализовать `detect_cvd_divergence()` с загрузкой из БД
- Добавить методы `get_candles()` в Repository

### ШАГ 7.3: Подключить к AccumulationDetector
- Передавать `HistoricalMemory` в конструктор
- Исправить `detect_accumulation()` чтобы работал с реальными данными

---

## ⚡ АЛЬТЕРНАТИВНЫЙ ПОДХОД (быстрее, но менее точный):

### In-Memory кеш вместо БД (для MVP)

```python
class InMemoryHistoricalCache:
    """
    WHY: Временное решение без БД.
    
    Держит последние N свечей в RAM для каждого таймфрейма.
    """
    
    def __init__(self, max_candles_per_tf: int = 100):
        self.candles = {
            '1H': deque(maxlen=max_candles_per_tf),
            '4H': deque(maxlen=max_candles_per_tf),
            '1D': deque(maxlen=max_candles_per_tf),
            '1W': deque(maxlen=max_candles_per_tf),
        }
```

**Плюсы:**
- ✅ Быстрая реализация (1-2 часа)
- ✅ Нет зависимости от БД
- ✅ Достаточно для live-трейдинга

**Минусы:**
- ❌ Данные теряются при перезапуске
- ❌ Нет исторического бэктеста
- ❌ Ограничен размер памяти

---

## 📊 ТЕКУЩИЙ ВЕРДИКТ:

### Реализуемо? **ДА, 100%** ✅

### Сложность: **СРЕДНЯЯ** (2-3 дня работы)

### Что уже есть:
1. ✅ Логика мультитаймфреймового анализа (AccumulationDetector)
2. ✅ Wyckoff паттерны
3. ✅ CVD divergence алгоритм (в domain.py)
4. ✅ PostgreSQL infrastructure

### Что нужно добавить:
1. ❌ CandleAggregator (агрегация тиков)
2. ❌ PostgreSQL таблица smart_money_candles
3. ❌ HistoricalMemory (чтение свечей + divergence)
4. ❌ Интеграция в TradingEngine

---

## 🎯 РЕКОМЕНДАЦИЯ:

**Начать с MVP (In-Memory кеш):**
- День 1: CandleAggregator с in-memory кешем
- День 2: HistoricalMemory + интеграция в AccumulationDetector
- День 3: PostgreSQL persistence (опционально)

**Хочешь начать? Предлагаю:**
1. Создать `CandleAggregator` с простым буфером
2. Добавить `InMemoryHistoricalCache`
3. Исправить `AccumulationDetector` чтобы работал

**Или сразу делать full PostgreSQL версию?**

Твой выбор! 🚀
