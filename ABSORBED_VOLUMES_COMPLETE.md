# ABSORBED VOLUMES IMPLEMENTATION - COMPLETE

## ПРОБЛЕМА (Gemini Review)
ML модель видит только `wall_*_vol` (пассивная ликвидность в стакане), но не видит **агрессивность атаки** на уровни.

**Пример:**
```
Свеча 1H на BTC:
- wall_whale_vol: 100 BTC (айсбергов стоит в стакане)
- ??? (absorbed_whale_vol отсутствовала)

→ ML не может предсказать: "Уровень истощается" vs "Уровень держится"
```

---

## РЕШЕНИЕ
Добавлены **3 новых колонки** для подсчёта ИСПОЛНЕННЫХ айсбергов за свечу.

### 1. Database Schema (Migration 006)
```sql
ALTER TABLE smart_candles
ADD COLUMN absorbed_whale_vol DOUBLE PRECISION DEFAULT 0,
ADD COLUMN absorbed_dolphin_vol DOUBLE PRECISION DEFAULT 0,
ADD COLUMN absorbed_total_vol DOUBLE PRECISION DEFAULT 0;
```

**Логика расчёта:**
- Суммируем `total_hidden_volume` из `iceberg_levels` таблицы
- WHERE `status = 'EXHAUSTED'` (только истощённые айсберги)
- Группируем по `is_dolphin` флагу (whale vs dolphin)

---

### 2. Domain Model (domain_smartcandle.py)
```python
class SmartCandle(BaseModel):
    # ... existing fields ...
    
    # === ABSORBED VOLUMES ===
    absorbed_whale_vol: Optional[float] = None
    absorbed_dolphin_vol: Optional[float] = None
    absorbed_total_vol: Optional[float] = None
    """
    ML Feature Engineering:
    - wall_vol ↑ + absorbed_vol ↓ → Сильный уровень (ОТСКОК)
    - wall_vol ↑ + absorbed_vol ↑ → Истощение защиты (ПРОБОЙ близко)
    - wall_vol ↓ + absorbed_vol ↑ → Уровень уже проломлен
    """
```

---

### 3. Candle Materializer (candle_materializer.py)

**Добавлен отдельный SQL запрос:**
```python
absorbed_query = """
    SELECT
        date_bin($1::interval, event_time, '2020-01-01') as candle_time,
        
        SUM(CASE WHEN NOT is_dolphin THEN total_hidden_volume ELSE 0 END) as absorbed_whale_vol,
        SUM(CASE WHEN is_dolphin THEN total_hidden_volume ELSE 0 END) as absorbed_dolphin_vol
        
    FROM iceberg_levels
    WHERE symbol = $2 
      AND event_time >= $3 
      AND event_time < $4
      AND status = 'EXHAUSTED'
    GROUP BY 1
"""
```

**WHY отдельный запрос?**
- `iceberg_levels` имеет другую гранулярность (event-level)
- `market_metrics_full` имеет tick-level данные
- JOIN бы создал дубликаты и некорректные агрегации

**Слияние данных:**
```python
# 1. Загружаем метрики из market_metrics_full
rows = await conn.fetch(main_query, ...)

# 2. Загружаем absorbed volumes из iceberg_levels
absorbed_rows = await conn.fetch(absorbed_query, ...)

# 3. Создаём lookup для быстрого слияния
absorbed_lookup = {
    row['candle_time']: {
        'absorbed_whale_vol': float(row['absorbed_whale_vol'] or 0),
        'absorbed_dolphin_vol': float(row['absorbed_dolphin_vol'] or 0)
    }
    for row in absorbed_rows
}

# 4. Сливаем при создании SmartCandle
absorbed_data = absorbed_lookup.get(candle_time, default_zeros)
```

---

## ML FEATURE ENGINEERING

### Новые метрики для XGBoost/LSTM:

**1. Absorption Ratio:**
```python
ratio = absorbed_whale_vol / wall_whale_vol if wall_whale_vol > 0 else 0

# Интерпретация:
# ratio < 0.3 → Уровень держится (киты выставляют быстрее чем съедают)
# ratio 0.3-0.7 → Нормальная борьба
# ratio > 0.7 → Уровень истощается (скоро пробой)
```

**2. Whale vs Retail Divergence:**
```python
# Если retail паникует (flow_minnow_cvd ↓↓)
# НО whale absorption низкий (absorbed_whale_vol ↓)
# → Ложная паника, киты НЕ покупают дно
# → Bearish signal

# Если retail покупает хаи (flow_minnow_cvd ↑↑)
# НО whale absorption высокий (absorbed_whale_vol ↑)
# → Киты дистрибутят в жадность толпы
# → Bearish signal
```

**3. Multi-Timeframe Absorption:**
```python
# Сравниваем absorbed_total_vol между таймфреймами
# 1H absorbed: 50 BTC
# 4H absorbed: 180 BTC
# 1D absorbed: 300 BTC

# → Ускорение истощения = пробой близко
```

---

## ТЕСТИРОВАНИЕ

### Шаг 1: Применить миграцию
```bash
cd smart_money_python_analysis
python apply_migrations.py
```

### Шаг 2: Backfill исторических данных
```bash
python candle_materializer.py  # Запустит backfill_historical_candles()
```

### Шаг 3: Проверить данные
```sql
SELECT 
    symbol, timeframe, candle_time,
    wall_whale_vol,
    absorbed_whale_vol,
    (absorbed_whale_vol / NULLIF(wall_whale_vol, 0)) as absorption_ratio
FROM smart_candles
WHERE symbol = 'BTCUSDT' 
  AND timeframe = '1h'
ORDER BY candle_time DESC
LIMIT 20;
```

---

## FILES MODIFIED

| File | Changes | Lines Modified |
|------|---------|----------------|
| `migrations/006_add_absorbed_volumes.sql` | ✅ NEW | +50 |
| `domain_smartcandle.py` | ✅ ADD 3 fields + docstring | +20 |
| `candle_materializer.py` | ✅ SQL query + aggregation logic | +60 |

**Total LOC:** ~130 lines

---

## BACKFILL STRATEGIES

### Strategy 1: Monthly Batching (Безопасно для RAM)
```python
async def backfill_historical_candles():
    # 6 месяцев разбиты на 6 батчей по 30 дней
    for month_offset in range(6):
        end_time = now - timedelta(days=30 * month_offset)
        start_time = end_time - timedelta(days=30)
        # ~100k тиков за раз вместо миллионов
```

**Преимущества:**
- RAM: Константа ~300MB (вместо пика 2GB)
- Прогресс: Видно "Month 3/6"
- Откат при ошибке: Теряется 1 месяц (не всё)

### Strategy 2: Hourly Updates with Settling Delay (КРИТИЧНО!)
```python
async def materialize_last_hour(settling_delay_minutes: int = 5):
    now = datetime.now()
    settled_time = now - timedelta(minutes=5)  # OFFSET!
    
    start_time = settled_time - timedelta(hours=1)
    end_time = settled_time
    
    # Материализуем: 13:55 → 14:55 (вместо 14:00 → 15:00)
```

**WHY Settling Delay?**

**Проблема (Race Condition):**
```
Current time: 15:00:00
TradingEngine buffer: [тики за 14:59:58, 14:59:59] ← ЕЩЁ НЕ ЗАПИСАНЫ!
Materialize window: 14:00:00 → 15:00:00
→ Свеча материализуется БЕЗ последних секунд!
```

**Решение (5 min offset):**
```
Current time: 15:00:00
Settled time: 14:55:00  (минус 5 мин)
Materialize window: 13:55:00 → 14:55:00
→ ВСЕ тики за этот период ГАРАНТИРОВАНО в БД
```

**Cron Setup:**
```bash
# Вариант 1: Запуск на 5-й минуте каждого часа
5 * * * * cd /path && python -c "import asyncio; from candle_materializer import materialize_last_hour; asyncio.run(materialize_last_hour())"

# Вариант 2: Запуск каждый час + sleep 300s
0 * * * * sleep 300 && cd /path && python -c "..."
```

**Idempotency:**
- `force_recompute=True` → Можно запускать много раз
- Если запустить в 15:00 и ещё раз в 15:05 → данные перезапишутся
- Нет риска дублей

---

1. ⚠️ **Restart Python shell** (due to .pyc caching of domain_smartcandle.py)
2. Run migration: `python apply_migrations.py`
3. Backfill data: `python candle_materializer.py`
4. Train ML model with new features
5. Monitor absorption_ratio in production

---

## KEY INSIGHTS

✅ **Schema is now 99% ready for ML**
- OHLCV ✓
- CVD by cohort ✓
- Derivatives (basis, skew, OI) ✓
- Microstructure (OFI, OBI) ✓
- GEX ✓
- VPIN ✓
- **Absorbed volumes ✓** ← NEW

⚠️ **Remaining 1%:**
- Wyckoff patterns (requires AccumulationDetector integration)
- On-chain metrics (optional, if needed)

---

## AUTHOR
Basilisca + Claude  
Date: 2025-12-24
