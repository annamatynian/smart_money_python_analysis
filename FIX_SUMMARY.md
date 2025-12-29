# QUICK FIX SUMMARY

## 1. analyzers_features.py ✅ FIXED

**Проблема:** `_get_spoofing_score()` вызывал несуществующий метод
**Решение:** Читать `iceberg.spoofing_probability` напрямую

## 2. GEMINI INTEGRATION = добавить в services.py:

```python
# В on_iceberg_refill():
from utils_gemini import calculate_cohort_distribution, calculate_price_drift_bps

# Собрать cohorts
recent_trades = iceberg.trade_footprint[-50:]
trades_list = [TradeEvent(...) for t in recent_trades]
cohort_dist = calculate_cohort_distribution(trades_list)

# Получить VPIN
vpin = flow_toxicity.get_current_vpin() or 0.5

# Вызвать crypto-aware update
iceberg.update_micro_divergence(
    vpin_at_refill=vpin,
    whale_volume_pct=cohort_dist['whale_pct'],
    minnow_volume_pct=cohort_dist['minnow_pct'],
    price_drift_bps=calculate_price_drift_bps(iceberg.price, book.get_mid_price())
)
```

Делать?
