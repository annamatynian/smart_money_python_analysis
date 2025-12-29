# ШАГ 6 ЗАВЕРШЕН: Derivatives Cache Background Task

## ✅ Что сделано:

### 1. Добавлены методы в DeribitInfrastructure (infrastructure.py)

**`get_futures_basis(currency="BTC")`**
- Запрашивает Deribit API для futures instruments
- Находит ближайший квартальный контракт
- Рассчитывает annualized basis: `((F - S) / S) * (365 / DTE) * 100`
- Returns: Basis в % (например, 15.5% APR) или None

**`get_options_skew(currency="BTC")`**
- Запрашивает Deribit API для options
- Фильтрует 30-дневные опционы 25-delta OTM
- Рассчитывает: `Put IV avg - Call IV avg`
- Returns: Skew в % (например, 5.2%) или None

### 2. Фоновая задача в TradingEngine (services.py)

**`_feed_derivatives_cache(interval_seconds=300)`**
- Запускается автоматически при наличии deribit infrastructure
- Каждые 5 минут:
  1. Запрашивает `get_futures_basis()`
  2. Запрашивает `get_options_skew()`
  3. Обновляет `feature_collector.cached_basis`
  4. Обновляет `feature_collector.cached_skew`
- Логирует обновления: `"📡 Derivatives Cache: Basis=15.5% | Skew=6.3%"`

### 3. Кеш в FeatureCollector (analyzers_features.py)

**Новые поля:**
```python
self.cached_basis: Optional[float] = None
self.cached_skew: Optional[float] = None
```

**Обновленные методы:**
- `_get_cached_basis()` - читает из `self.cached_basis`
- `_get_cached_skew()` - читает из `self.cached_skew`
- `_get_basis_state()` - интерпретирует basis (CONTANGO/BACKWARDATION/NEUTRAL)
- `_get_skew_state()` - интерпретирует skew (FEAR/GREED/NEUTRAL)

## 🎯 Результат:

**При захвате snapshot:**
```python
snapshot = await feature_collector.capture_snapshot()
# snapshot.futures_basis_apr = 15.5  (из кеша)
# snapshot.basis_state = 'CONTANGO'
# snapshot.options_skew = 6.3  (из кеша)
# snapshot.skew_state = 'FEAR'
```

**Преимущества:**
- ✅ **Неблокирующий** - capture_snapshot() не делает HTTP запросов
- ✅ **Эффективный** - обновление раз в 5 минут (вместо каждой сделки)
- ✅ **Устойчивый** - Rate Limit 429 обрабатывается gracefully

## 📊 Интерпретация метрик:

### Futures Basis:
- **EXTREME_CONTANGO** (>20%): Перегрев, смарт-мани открывают Cash-and-Carry
- **CONTANGO** (10-20%): Нормальное бычье состояние
- **NEUTRAL** (-5% to 10%): Сбалансированный рынок
- **BACKWARDATION** (<-5%): Дефицит/медвежий страх

### Options Skew:
- **EXTREME_FEAR** (>10%): Путы значительно дороже коллов
- **FEAR** (5-10%): Умеренный страх падения
- **NEUTRAL** (-5% to 5%): Сбалансированный
- **GREED** (<-5%): Коллы дороже (редко, bullish euphoria)

## 🚀 Готово к использованию!

**Запуск:**
```python
engine = TradingEngine(
    symbol='BTCUSDT',
    infra=binance_infra,
    deribit_infra=deribit_infra  # ← Автоматически запускает _feed_derivatives_cache()
)
await engine.run()
```

**В консоли каждые 5 минут:**
```
📡 Derivatives Cache Monitor started (interval: 300s)
📡 Derivatives Cache: Basis=15.3% | Skew=6.8%
```

## 📝 TODO (опционально):

- Шаг 7: Grim Reaper task (мониторинг мертвых айсбергов, обновление outcomes через 1 час)
- Добавить поддержку ETH/SOL для derivatives (сейчас только BTC)
- Расширить skew analysis (добавить ATM volatility, term structure)

**ГОТОВО К ТЕСТИРОВАНИЮ!** ✅
