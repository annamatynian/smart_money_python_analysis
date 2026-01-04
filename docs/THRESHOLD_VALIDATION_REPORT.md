# THRESHOLD VALIDATION - Dolphin Segment Safety Check

## КРИТИЧЕСКОЕ ЗАМЕЧАНИЕ (Gemini Review)

**Проблема:**
В коде есть `dolphin_cvd_delta` (repository.py:457), но пороги whale/minnow могут "схлопнуться", оставив dolphin-сегмент пустым.

**Защита в коде:**
```python
# whale_analyzer.py (lines 266-267)
whale_threshold = minnow_threshold * 10.0  # Минимальный gap 10x
```

---

## ТЕКУЩИЕ ПОРОГИ (config.py)

### BTC_CONFIG
```python
static_whale_threshold_usd:  $100,000
static_minnow_threshold_usd: $1,000
```
**Gap:** 100x (100,000 / 1,000)  
**Dolphin range:** $1,000 - $100,000 ✅

### ETH_CONFIG
```python
static_whale_threshold_usd:  $50,000
static_minnow_threshold_usd: $500
```
**Gap:** 100x (50,000 / 500)  
**Dolphin range:** $500 - $50,000 ✅

### SOL_CONFIG
```python
static_whale_threshold_usd:  $25,000
static_minnow_threshold_usd: $200
```
**Gap:** 125x (25,000 / 200)  
**Dolphin range:** $200 - $25,000 ✅

---

## ВАЛИДАЦИЯ

### ✅ Static Thresholds - SAFE

Все конфиги имеют gap >= 100x (значительно выше минимального 10x).

**Размер dolphin-сегмента:**
- BTC: 2 порядка величины ($1k - $100k)
- ETH: 2 порядка величины ($500 - $50k)
- SOL: 2+ порядка величины ($200 - $25k)

### ✅ Floor Thresholds - SAFE

```python
# BTC
min_whale_floor_usd:  $10,000
min_minnow_floor_usd: $100
# Gap: 100x
```

Даже в экстремальных условиях (очень спокойный рынок) floor thresholds не схлопнутся.

---

## DYNAMIC THRESHOLDS (если реализованы)

**Защитный механизм:**
```python
# Псевдокод из whale_analyzer._calculate_dynamic_thresholds()
if whale_threshold <= minnow_threshold * 10.0:
    whale_threshold = minnow_threshold * 10.0  # Force минимальный gap
```

**Гарантия:** Даже при динамической адаптации dolphin-сегмент >= 10x.

---

## ТЕСТИРОВАНИЕ

### Запустить валидацию:
```bash
cd smart_money_python_analysis
python validate_thresholds.py
```

**Ожидаемый вывод:**
```
=== BTC_CONFIG ===
Whale threshold:   $100,000
Minnow threshold:  $1,000
Gap ratio:         100.0x
✅ SAFE: Gap 100.0x >= 10.0x (dolphin segment OK)

=== ETH_CONFIG ===
...

✅ ALL CONFIGS SAFE - Dolphin segments will not collapse
```

---

## КАТЕГОРИЗАЦИЯ В PRODUCTION

### Логика классификации:

```python
volume_usd = trade.quantity * trade.price

if volume_usd >= config.static_whale_threshold_usd:
    category = "whale"  # >$100k (BTC)
elif volume_usd >= config.static_minnow_threshold_usd:
    category = "dolphin"  # $1k-$100k (BTC)
else:
    category = "minnow"  # <$1k (BTC)
```

### Примеры (BTC @ $95,000):

| Trade Size | USD Value | Category |
|-----------|-----------|----------|
| 0.005 BTC | $475 | Minnow |
| 0.02 BTC | $1,900 | **Dolphin** |
| 0.5 BTC | $47,500 | **Dolphin** |
| 2.0 BTC | $190,000 | Whale |

**Dolphin сегмент:** 0.0105 BTC - 1.05 BTC (широкий диапазон)

---

## ML IMPACT

### SmartCandles columns:

```sql
flow_whale_cvd DOUBLE PRECISION    -- >$100k агрессоры
flow_dolphin_cvd DOUBLE PRECISION  -- $1k-$100k агрессоры (BRIDGE)
flow_minnow_cvd DOUBLE PRECISION   -- <$1k агрессоры (retail)

wall_whale_vol DOUBLE PRECISION    -- >$100k айсберги
wall_dolphin_vol DOUBLE PRECISION  -- $1k-$100k айсберги
```

**WHY Dolphin важен:**
- **Bridge между retail и institutions**
- Часто первые сигналы накопления/дистрибуции
- Более стабильный чем minnow, более чувствительный чем whale

**XGBoost features:**
```python
# Дивергенции между когортами
dolphin_whale_divergence = (flow_dolphin_cvd - flow_whale_cvd)
retail_panic_ratio = abs(flow_minnow_cvd) / (abs(flow_whale_cvd) + 1)
```

---

## ВЫВОДЫ

✅ **ВСЕ ПОРОГИ БЕЗОПАСНЫ**
- Gap >= 100x (значительно выше минимума 10x)
- Dolphin-сегмент охватывает 2 порядка величины
- Floor thresholds также имеют достаточный gap

✅ **ЗАЩИТА ОТ СХЛОПЫВАНИЯ**
- Код имеет встроенную защиту (whale >= minnow * 10)
- Динамические пороги (если есть) гарантируют минимальный gap

✅ **ML ГОТОВ**
- 3 категории чётко разделены
- Dolphin-сегмент богатый данными
- Нет риска пустых колонок

---

## РЕКОМЕНДАЦИИ

### 1. Периодическая проверка:
```bash
# Раз в квартал запускать:
python validate_thresholds.py
```

### 2. Логирование edge cases:
```python
# В production добавить метрику:
if minnow_threshold < whale_threshold < minnow_threshold * 15:
    logger.warning(f"Narrow gap detected: {whale_threshold / minnow_threshold:.1f}x")
```

### 3. A/B тестирование порогов:
```python
# Если хочешь экспериментировать с порогами:
# Создай BTC_CONFIG_V2 с другими значениями
# Используй aggregation_version='2.0' в SmartCandles
```

---

## AUTHOR
Basilisca + Claude  
Date: 2025-12-24
