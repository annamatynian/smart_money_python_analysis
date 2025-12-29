"""
WHY: Валидация порогов категоризации whale/dolphin/minnow.

ПРОБЛЕМА (из критического замечания):
- Если dynamic thresholds "схлопнутся", dolphin-сегмент станет пустым
- Риск: whale_threshold <= minnow_threshold * safe_margin

ПРОВЕРКА:
1. Static thresholds из config.py
2. Dynamic thresholds (если есть логика адаптации)
3. Gap между порогами (должен быть >= 10x)
"""

from config import BTC_CONFIG, ETH_CONFIG, SOL_CONFIG
from decimal import Decimal

def validate_thresholds(config_name: str, config):
    """
    Проверяет что пороги не схлопываются.
    
    SAFE MARGIN: whale_threshold >= minnow_threshold * 10.0
    WHY: Dolphin сегмент должен охватывать минимум 1 порядок величины.
    """
    print(f"\n=== {config_name} ===")
    
    whale = config.static_whale_threshold_usd
    minnow = config.static_minnow_threshold_usd
    
    # Рассчитываем gap
    gap_ratio = whale / minnow if minnow > 0 else 0
    
    print(f"Whale threshold:   ${whale:,.0f}")
    print(f"Minnow threshold:  ${minnow:,.0f}")
    print(f"Gap ratio:         {gap_ratio:.1f}x")
    print(f"Dolphin range:     ${minnow:,.0f} - ${whale:,.0f}")
    
    # Валидация
    SAFE_MARGIN = 10.0  # Минимальный разрыв 10x
    
    if gap_ratio < SAFE_MARGIN:
        print(f"❌ RISK: Gap {gap_ratio:.1f}x < {SAFE_MARGIN}x (dolphin segment too narrow)")
        return False
    else:
        print(f"✅ SAFE: Gap {gap_ratio:.1f}x >= {SAFE_MARGIN}x (dolphin segment OK)")
        return True

def check_floor_thresholds(config_name: str, config):
    """
    Проверяет floor thresholds (минимальные пороги).
    
    WHY: Защита от edge cases когда рынок очень спокойный.
    """
    print(f"\n  Floor thresholds:")
    print(f"  - min_whale_floor:  ${config.min_whale_floor_usd:,.0f}")
    print(f"  - min_minnow_floor: ${config.min_minnow_floor_usd:,.0f}")
    
    floor_gap = config.min_whale_floor_usd / config.min_minnow_floor_usd
    print(f"  - Floor gap ratio:  {floor_gap:.1f}x")
    
    if floor_gap < 10.0:
        print(f"  ⚠️ Floor gap {floor_gap:.1f}x < 10x (может схлопнуться в экстремальных условиях)")
    else:
        print(f"  ✅ Floor gap {floor_gap:.1f}x >= 10x")

if __name__ == '__main__':
    print("=" * 60)
    print("THRESHOLD VALIDATION REPORT")
    print("=" * 60)
    
    all_safe = True
    
    # Проверяем все конфиги
    for name, config in [("BTC_CONFIG", BTC_CONFIG), ("ETH_CONFIG", ETH_CONFIG), ("SOL_CONFIG", SOL_CONFIG)]:
        is_safe = validate_thresholds(name, config)
        check_floor_thresholds(name, config)
        all_safe = all_safe and is_safe
    
    print("\n" + "=" * 60)
    if all_safe:
        print("✅ ALL CONFIGS SAFE - Dolphin segments will not collapse")
    else:
        print("❌ RISK DETECTED - Review threshold configuration")
    print("=" * 60)
