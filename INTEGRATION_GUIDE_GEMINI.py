# ========================================================================
# GEMINI CRYPTO-AWARE INTEGRATION GUIDE
# ========================================================================

"""
HOW TO: Подключить crypto-aware логику к IcebergOrchestrator

СТАТУС: ✅ domain.py готов (методы есть)
        ✅ utils_gemini.py готов (helpers есть)
        ⏳ services.py НУЖНО ОБНОВИТЬ

ШАГ 1: Импорт utils_gemini
ШАГ 2: Обновить on_iceberg_refill()
"""

# ========================================================================
# ШАГ 1: ДОБАВИТЬ ИМПОРТЫ В services.py
# ========================================================================

```python
# В начало services.py добавить:
from utils_gemini import calculate_cohort_distribution, calculate_price_drift_bps
```

# ========================================================================
# ШАГ 2: ОБНОВИТЬ МЕТОД on_iceberg_refill() В IcebergOrchestrator
# ========================================================================

"""
БЫЛО (старая логика, НЕ crypto-aware):
```python
def on_iceberg_refill(self, iceberg: IcebergLevel, trade: TradeEvent):
    # Старый код без когорт
    iceberg.update_micro_divergence(
        vpin_at_refill=current_vpin,
        flow_imbalance=100.0  # ← Этого больше нет!
    )
```

СТАЛО (crypto-aware логика):
```python
def on_iceberg_refill(self, iceberg: IcebergLevel, trade: TradeEvent):
    '''
    WHY: Обработка пополнения айсберга с crypto-aware анализом потока.
    
    Новая логика:
    1. Собираем последние N сделок на уровне айсберга
    2. Рассчитываем cohort distribution (whale vs minnow)
    3. Рассчитываем price drift (прогиб цены)
    4. Вызываем update_micro_divergence() с новыми параметрами
    '''
    
    # 1. Получаем последние сделки на этом уровне
    # WHY: Анализируем состав потока за последние 30-60 секунд
    recent_trades = iceberg.trade_footprint[-50:]  # Последние 50 сделок
    
    if not recent_trades:
        return  # Нет данных для анализа
    
    # 2. Конвертируем footprint обратно в TradeEvent
    # WHY: calculate_cohort_distribution ожидает List[TradeEvent]
    trades_list = []
    for t in recent_trades:
        trade_event = TradeEvent(
            price=iceberg.price,
            quantity=t['quantity'],
            is_buyer_maker=not t['is_buy'],  # Инвертируем обратно
            event_time=int(t['time'].timestamp() * 1000)
        )
        trades_list.append(trade_event)
    
    # 3. Рассчитываем cohort distribution
    cohort_dist = calculate_cohort_distribution(
        trades=trades_list,
        whale_threshold=Decimal('5.0'),  # TODO: Из config
        minnow_threshold=Decimal('1.0')
    )
    
    # 4. Рассчитываем price drift
    current_mid = (self.book.best_bid() + self.book.best_ask()) / 2
    price_drift = calculate_price_drift_bps(
        iceberg_price=iceberg.price,
        current_mid_price=current_mid
    )
    
    # 5. Получаем текущий VPIN
    # TODO: Зависит от где хранится VPIN в вашей системе
    # current_vpin = self.vpin_tracker.get_current_vpin()
    current_vpin = 0.6  # Placeholder
    
    # 6. CRYPTO-AWARE UPDATE!
    iceberg.update_micro_divergence(
        vpin_at_refill=current_vpin,
        whale_volume_pct=cohort_dist['whale_pct'],
        minnow_volume_pct=cohort_dist['minnow_pct'],
        price_drift_bps=price_drift
    )
    
    # 7. Логирование
    logger.info(
        f"Iceberg refill @ {iceberg.price}: "
        f"VPIN={current_vpin:.2f}, "
        f"Whales={cohort_dist['whale_pct']*100:.0f}%, "
        f"Minnows={cohort_dist['minnow_pct']*100:.0f}%, "
        f"Drift={price_drift:.1f}bps, "
        f"Confidence={iceberg.confidence_score:.2f}"
    )
```
"""

# ========================================================================
# ШАГ 3: ТЕСТИРОВАНИЕ
# ========================================================================

"""
1. Запустить pytest:
   pytest tests/test_gemini_enhancements_crypto_aware.py -v

2. Проверить логи:
   - Должны видеть логи "Iceberg refill @ ..."
   - Confidence должен РАСТИ при minnow panic
   - Confidence должен ПАДАТЬ при whale attack

3. Проверить БД:
   SELECT price, confidence_score, whale_volume_pct, minnow_volume_pct
   FROM iceberg_levels
   ORDER BY last_update_time DESC
   LIMIT 10;
"""

# ========================================================================
# ШАГ 4: МИГРАЦИЯ БД (если нужно)
# ========================================================================

"""
Если хочешь сохранять whale_volume_pct в БД:

CREATE MIGRATION:
```sql
-- migrations/004_add_gemini_cohorts.sql

ALTER TABLE iceberg_levels 
ADD COLUMN IF NOT EXISTS whale_volume_pct NUMERIC,
ADD COLUMN IF NOT EXISTS minnow_volume_pct NUMERIC,
ADD COLUMN IF NOT EXISTS price_drift_bps NUMERIC;

-- Index для аналитики
CREATE INDEX IF NOT EXISTS idx_iceberg_whale_pct 
ON iceberg_levels(whale_volume_pct) 
WHERE whale_volume_pct > 0.6;
```

APPLY MIGRATION:
```python
from repository import DatabaseManager
db = DatabaseManager()
db.apply_migrations()
```
"""

# ========================================================================
# ПРИМЕРЫ РЕАЛЬНЫХ СЦЕНАРИЕВ
# ========================================================================

"""
СЦЕНАРИЙ 1: Кит выкупает каскадные ликвидации на BID 60000
-------------
Input:
  - VPIN = 0.85 (высокий!)
  - whale_pct = 0.10 (только 10% от китов)
  - minnow_pct = 0.85 (85% толпа в панике!)
  - price_drift = 2.0 bps (цена стабильна)

Output:
  - confidence += 0.1 (БОНУС +10%)
  - Лог: "Panic Absorption detected: minnows=85%, confidence=0.95"

Трактовка: 🐳 ЛУЧШИЙ ЛОНГ-СИГНАЛ! Кит поглощает панику толпы.


СЦЕНАРИЙ 2: Киты ломают слабый ASK 61000
-------------
Input:
  - VPIN = 0.75
  - whale_pct = 0.70 (70% от китов!)
  - minnow_pct = 0.20
  - price_drift = 8.0 bps (цена "прогибается")

Output:
  - confidence -= 0.35 (ШТРАФ -35%)
  - Лог: "Whale Attack detected: whales=70%, drift=8bps, confidence=0.45"

Трактовка: ❌ ИЗБЕГАТЬ ШОРТА! Уровень НЕ устоит.
"""

print(__doc__)
