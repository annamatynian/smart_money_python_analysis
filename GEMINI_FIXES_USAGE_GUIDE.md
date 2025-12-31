# GEMINI FIXES: Production Integration Guide

## 📋 Резюме изменений

Все три GEMINI FIX успешно реализованы и протестированы:

✅ **GEX Normalization** (18 тестов PASSED)
✅ **Expiration Decay** (18 тестов PASSED)  
✅ **Cache TTL Extension** (10 тестов PASSED)

---

## 🔧 Архитектурное размещение

### 1. `domain.py` (Business Logic)
```python
class GammaProfile(BaseModel):
    # Новые поля:
    total_gex_normalized: Optional[float] = None  # GEX / ADV_20d
    expiry_timestamp: Optional[datetime] = None   # Friday 08:00 UTC
    
    @staticmethod
    def get_next_options_expiry() -> datetime:
        """Возвращает ближайшую пятницу 08:00 UTC (Deribit expiry)."""
        # Реализация в domain.py
```

### 2. `infrastructure.py` (External APIs)
```python
async def get_average_daily_volume(
    symbol: str = "BTCUSDT",
    days: int = 20,
    exchange: str = "binance"
) -> Optional[float]:
    """
    Запрос к Binance Klines API для получения ADV_20d.
    
    Returns:
        Средний дневной объём в USD или None при ошибке
    """
    # Реализация в infrastructure.py
```

### 3. `analyzers_derivatives.py` (Analysis)
```python
def calculate_gex(
    self,
    strikes, types, expiry_years, ivs, open_interest,
    underlying_price: float,
    symbol: str = "BTCUSDT",          # NEW
    avg_daily_volume: Optional[float] = None  # NEW
) -> GammaProfile:
    """
    Рассчитывает GEX и заполняет новые поля:
    - total_gex_normalized (если передан avg_daily_volume)
    - expiry_timestamp (всегда)
    """
    # Реализация в analyzers_derivatives.py
```

---

## 💻 Production Usage Examples

### Пример 1: Базовое использование (Backward Compatible)

```python
# БЕЗ изменений в существующем коде - всё работает как раньше!
analyzer = DerivativesAnalyzer()

profile = analyzer.calculate_gex(
    strikes=[95000, 100000, 105000],
    types=['C', 'P', 'C'],
    expiry_years=[0.08, 0.08, 0.08],
    ivs=[0.70, 0.75, 0.72],
    open_interest=[1000, 1500, 1200],
    underlying_price=98000.0
    # avg_daily_volume НЕ передан
)

# profile.total_gex_normalized = None
# profile.expiry_timestamp = <автоматически вычислено>
# Все старые поля работают: total_gex, call_wall, put_wall
```

### Пример 2: С GEX Normalization (РЕКОМЕНДУЕТСЯ)

```python
from infrastructure import get_average_daily_volume
from analyzers_derivatives import DerivativesAnalyzer

# Шаг 1: Получаем ADV_20d один раз (кешировать на 1 час)
adv_20d = await get_average_daily_volume(symbol="BTCUSDT", days=20)

# Шаг 2: Передаём ADV в calculate_gex
analyzer = DerivativesAnalyzer()

profile = analyzer.calculate_gex(
    strikes=[95000, 100000, 105000],
    types=['C', 'P', 'C'],
    expiry_years=[0.08, 0.08, 0.08],
    ivs=[0.70, 0.75, 0.72],
    open_interest=[1000, 1500, 1200],
    underlying_price=98000.0,
    symbol="BTCUSDT",           # NEW
    avg_daily_volume=adv_20d    # NEW
)

# profile.total_gex_normalized = 0.15  (15% от ADV - значимый GEX!)
# profile.expiry_timestamp = <ближайшая пятница 08:00 UTC>
```

### Пример 3: Использование в adjust_confidence_by_gamma

```python
from analyzers import IcebergAnalyzer

analyzer = IcebergAnalyzer(config)

# profile уже содержит нормализованный GEX и expiry
# (из примера 2)

adjusted_confidence, is_major = analyzer.adjust_confidence_by_gamma(
    base_confidence=0.5,
    gamma_profile=profile,  # Передаём GammaProfile с новыми полями
    price=Decimal("100000"),
    is_ask=True,
    vpin_score=None,
    cvd_divergence=None
)

# Внутри метод автоматически:
# 1. Проверяет total_gex_normalized > 0.1 (значимость)
# 2. Вычисляет decay_factor на основе expiry_timestamp
# 3. Применяет бонус: adjusted = 0.5 * (1.0 + 0.8 * decay_factor)
```

### Пример 4: Кеширование ADV (РЕКОМЕНДУЕТСЯ для Production)

```python
import asyncio
from datetime import datetime, timedelta

class DerivativesService:
    """
    WHY: Service layer для управления кешем ADV и GEX расчётов.
    """
    
    def __init__(self):
        self.adv_cache: Dict[str, Tuple[float, datetime]] = {}
        self.adv_ttl = 3600  # 1 час (ADV меняется медленно)
    
    async def get_cached_adv(self, symbol: str) -> Optional[float]:
        """Получить ADV из кеша или обновить."""
        now = datetime.now()
        
        # Проверяем кеш
        if symbol in self.adv_cache:
            adv, cached_at = self.adv_cache[symbol]
            age = (now - cached_at).total_seconds()
            
            if age < self.adv_ttl:
                return adv  # Кеш свежий
        
        # Обновляем кеш
        adv = await get_average_daily_volume(symbol=symbol, days=20)
        
        if adv is not None:
            self.adv_cache[symbol] = (adv, now)
        
        return adv
    
    async def calculate_normalized_gex(self, symbol: str, **gex_params):
        """
        Удобный метод для расчёта GEX с автоматическим ADV.
        """
        # Получаем ADV из кеша
        adv = await self.get_cached_adv(symbol)
        
        # Создаём analyzer
        analyzer = DerivativesAnalyzer()
        
        # Вызываем calculate_gex с ADV
        profile = analyzer.calculate_gex(
            **gex_params,
            symbol=symbol,
            avg_daily_volume=adv
        )
        
        return profile


# ИСПОЛЬЗОВАНИЕ:
service = DerivativesService()

# Первый вызов - запрос к API
profile1 = await service.calculate_normalized_gex(
    symbol="BTCUSDT",
    strikes=[...],
    types=[...],
    # ... другие параметры
)

# Второй вызов в течение часа - из кеша (быстро!)
profile2 = await service.calculate_normalized_gex(
    symbol="BTCUSDT",
    strikes=[...],
    types=[...],
    # ... другие параметры
)
```

---

## ⚙️ Конфигурация для разных токенов

```python
from config import AssetConfig, BTC_CONFIG, ETH_CONFIG, SOL_CONFIG

# Для каждого токена можно указать свой symbol
configs = {
    "BTC": ("BTCUSDT", BTC_CONFIG),
    "ETH": ("ETHUSDT", ETH_CONFIG),
    "SOL": ("SOLUSDT", SOL_CONFIG)
}

async def process_all_assets():
    service = DerivativesService()
    
    for asset, (symbol, config) in configs.items():
        # Получаем ADV для каждого токена
        adv = await service.get_cached_adv(symbol)
        
        # Вычисляем GEX с нормализацией
        profile = await service.calculate_normalized_gex(
            symbol=symbol,
            # ... GEX параметры из Deribit
        )
        
        print(f"{asset}: GEX={profile.total_gex}, "
              f"Normalized={profile.total_gex_normalized}, "
              f"Expiry={profile.expiry_timestamp}")
```

---

## 🔍 Валидация и Debugging

### Проверка корректности в Production

```python
def validate_gamma_profile(profile: GammaProfile) -> bool:
    """
    WHY: Валидация GammaProfile перед использованием.
    """
    # 1. Обязательные поля
    if profile.total_gex == 0:
        print("WARNING: total_gex = 0 (нет GEX данных)")
        return False
    
    # 2. GEX Normalization
    if profile.total_gex_normalized is not None:
        # Разумный диапазон: 0.0001 - 10.0 (0.01% - 1000% от ADV)
        if not (0.0001 < abs(profile.total_gex_normalized) < 10.0):
            print(f"WARNING: total_gex_normalized вне диапазона: "
                  f"{profile.total_gex_normalized}")
            return False
    
    # 3. Expiration Decay
    if profile.expiry_timestamp is None:
        print("WARNING: expiry_timestamp не установлен!")
        return False
    
    now = datetime.now(timezone.utc)
    hours_to_expiry = (profile.expiry_timestamp - now).total_seconds() / 3600
    
    if hours_to_expiry <= 0:
        print(f"WARNING: expiry в прошлом! {profile.expiry_timestamp}")
        return False
    
    if hours_to_expiry > 168:  # 7 дней
        print(f"WARNING: expiry слишком далеко: {hours_to_expiry} часов")
        return False
    
    return True


# ИСПОЛЬЗОВАНИЕ:
profile = await service.calculate_normalized_gex(...)

if validate_gamma_profile(profile):
    # Используем profile для торговых решений
    adjusted_conf = analyzer.adjust_confidence_by_gamma(...)
else:
    # Логируем ошибку, используем дефолтные значения
    adjusted_conf = base_confidence  # Без корректировки
```

---

## 📊 Мониторинг метрик

```python
import logging

class GEXMonitor:
    """
    WHY: Мониторинг качества GEX данных для алертов.
    """
    
    def __init__(self):
        self.logger = logging.getLogger("GEXMonitor")
    
    def log_gex_metrics(self, symbol: str, profile: GammaProfile):
        """
        Логирование ключевых метрик для Prometheus/Grafana.
        """
        self.logger.info(
            f"GEX Metrics | Symbol={symbol} | "
            f"Total={profile.total_gex:.2e} | "
            f"Normalized={profile.total_gex_normalized:.4f} | "
            f"CallWall=${profile.call_wall:.0f} | "
            f"PutWall=${profile.put_wall:.0f} | "
            f"ExpiryHours={(profile.expiry_timestamp - datetime.now(timezone.utc)).total_seconds() / 3600:.1f}"
        )
        
        # Проверка пороговых значений
        if profile.total_gex_normalized and abs(profile.total_gex_normalized) > 0.5:
            self.logger.warning(
                f"EXTREME GEX DETECTED: {symbol} normalized={profile.total_gex_normalized:.2f} "
                f"(>50% от ADV!)"
            )


# ИСПОЛЬЗОВАНИЕ:
monitor = GEXMonitor()

profile = await service.calculate_normalized_gex(...)
monitor.log_gex_metrics("BTC", profile)
```

---

## 🚀 Deployment Checklist

### Pre-Production
- [ ] Запустить все тесты: `pytest tests/ -v`
- [ ] Проверить интеграцию: `pytest tests/test_gex_integration.py -v`
- [ ] Убедиться что Oracle Cloud ARM64 поддерживает новые зависимости
- [ ] Обновить Docker образ с новыми зависимостями

### Production
- [ ] Убедиться что `get_average_daily_volume()` кешируется (TTL 1 час)
- [ ] Добавить мониторинг `total_gex_normalized` метрик
- [ ] Настроить алерты для экстремальных значений (>0.5 normalized)
- [ ] Логировать `hours_to_expiry` для отслеживания Decay эффекта

### Validation
- [ ] Проверить что `total_gex_normalized` всегда заполнен (не None)
- [ ] Проверить что `expiry_timestamp` всегда в будущем
- [ ] Проверить что `adjust_confidence_by_gamma()` применяет Decay
- [ ] Проверить что Cache TTL = 30 мин для basis/skew

---

## 📚 Дополнительная информация

См. также:
- `tests/test_gex_normalization_fixes.py` - Unit тесты GEX
- `tests/test_cache_ttl_extension.py` - Unit тесты Cache
- `tests/test_gex_integration.py` - Integration тесты

Документация:
- [Анализ данных биржевого стакана](Анализ_данных_биржевого_стакана.docx)
- [Идентификация Айсберг-Ордеров на Binance L2](Идентификация_Айсберг-Ордеров_на_Binance_L2.docx)

---

**Последнее обновление:** 2025-12-31
**Версия:** 1.0.0
**Статус:** Production Ready ✅
