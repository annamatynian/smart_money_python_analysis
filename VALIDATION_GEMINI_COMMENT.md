# Проверка готовности domain.py для Delta-t реализации

## ✅ ПРОВЕРКА ПРОЙДЕНА

### Существующие поля в IcebergLevel (строки 85-107):

```python
class IcebergLevel(BaseModel):
    price: Decimal
    is_ask: bool
    total_hidden_volume: Decimal = Decimal("0")
    creation_time: datetime = Field(default_factory=datetime.now)
    last_update_time: datetime = Field(default_factory=datetime.now)
    status: IcebergStatus = IcebergStatus.ACTIVE
    
    is_gamma_wall: bool = False
    confidence_score: float = 0.0
    
    # === ДЛЯ АНТИСПУФИНГА ===
    cancellation_context: Optional[CancellationContext] = None
    spoofing_probability: float = 0.0
    refill_count: int = 0  # ✅ УЖЕ ЕСТЬ!
```

### Что НУЖНО ДОБАВИТЬ для Delta-t:

#### 1. В LocalOrderBook - очередь pending checks

**ТЕКУЩЕЕ СОСТОЯНИЕ** (строка 154):
```python
class LocalOrderBook(BaseModel):
    symbol: str
    bids: SortedDict = Field(default_factory=SortedDict)
    asks: SortedDict = Field(default_factory=SortedDict)
    gamma_profile: Optional[GammaProfile] = None 
    last_update_id: int = 0
    
    active_icebergs: Dict[Decimal, IcebergLevel] = Field(default_factory=dict)
    whale_cvd: Dict[str, float] = Field(default_factory=lambda: {'whale': 0.0, 'dolphin': 0.0, 'minnow': 0.0})
    trade_count: int = 0
    algo_window: deque = Field(default_factory=deque)
    
    _pending_trade_check: Optional[Tuple[TradeEvent, Decimal]] = None  # ❌ УСТАРЕВШЕЕ
```

**ЧТО ДОБАВИТЬ**:
```python
# ЗАМЕНИТЬ _pending_trade_check НА:
pending_refill_checks: deque = Field(default_factory=deque)
# Структура элемента: {
#     'trade': TradeEvent,
#     'visible_before': Decimal,
#     'trade_time_ms': int,
#     'price': Decimal,
#     'is_ask': bool
# }
```

#### 2. Никаких изменений в IcebergLevel не требуется!

Все необходимые поля уже есть:
- ✅ `refill_count` - для подсчета рефиллов
- ✅ `creation_time` - для расчета lifetime
- ✅ `confidence_score` - будем модифицировать с учетом Delta-t
- ✅ `get_refill_frequency()` - метод уже реализован

## 📝 ИТОГОВЫЙ ВЫВОД

**Замечание Gemini НЕ АКТУАЛЬНО** - поле `refill_count` уже существует.

**ЕДИНСТВЕННОЕ ИЗМЕНЕНИЕ** которое нужно сделать в `domain.py`:

1. **УДАЛИТЬ** устаревшее поле:
   ```python
   _pending_trade_check: Optional[Tuple[TradeEvent, Decimal]] = None
   ```

2. **ДОБАВИТЬ** новое поле:
   ```python
   pending_refill_checks: deque = Field(default_factory=deque)
   ```

Все остальное готово к реализации!
