# ========================================================================
# GEMINI CRYPTO-AWARE: COHORT DISTRIBUTION HELPER
# ========================================================================

"""
WHY: Вспомогательная функция для расчёта whale/minnow распределения.

Используется в IcebergOrchestrator для подачи данных в 
IcebergLevel.update_micro_divergence().

Теория: Разделение потока по размеру сделок позволяет различить
Whale Attack (киты атакуют) от Panic Absorption (толпа в панике).
"""

from decimal import Decimal
from typing import List, Dict
from domain import TradeEvent


def calculate_cohort_distribution(
    trades: List[TradeEvent],
    whale_threshold: Decimal = Decimal('5.0'),
    minnow_threshold: Decimal = Decimal('1.0')
) -> Dict[str, float]:
    """
    WHY: Рассчитывает долю whale/dolphin/minnow в потоке сделок.
    
    Используется для crypto-aware анализа VPIN:
    - Высокий VPIN + whale доминируют → Whale Attack (штраф)
    - Высокий VPIN + minnow доминируют → Panic Absorption (бонус!)
    
    Args:
        trades: Список сделок для анализа (обычно последние 10-50 сделок)
        whale_threshold: Порог для классификации whale (default: 5.0 BTC)
        minnow_threshold: Порог для классификации minnow (default: 1.0 BTC)
    
    Returns:
        {
            'whale_pct': 0.7,    # 70% объёма от китов
            'dolphin_pct': 0.2,  # 20% от дельфинов
            'minnow_pct': 0.1    # 10% от рыб
        }
    
    Example:
        >>> trades = [
        ...     TradeEvent(price=Decimal('60000'), quantity=Decimal('10.0'), ...),  # Whale
        ...     TradeEvent(price=Decimal('60000'), quantity=Decimal('0.5'), ...)    # Minnow
        ... ]
        >>> dist = calculate_cohort_distribution(trades)
        >>> assert dist['whale_pct'] > 0.9  # Киты доминируют
    """
    if not trades:
        return {
            'whale_pct': 0.0,
            'dolphin_pct': 0.0,
            'minnow_pct': 0.0
        }
    
    # 1. Считаем объём по когортам
    total_volume = Decimal('0')
    whale_volume = Decimal('0')
    dolphin_volume = Decimal('0')
    minnow_volume = Decimal('0')
    
    for trade in trades:
        qty = trade.quantity
        total_volume += qty
        
        # Классификация
        if qty >= whale_threshold:
            whale_volume += qty
        elif qty >= minnow_threshold:
            dolphin_volume += qty
        else:
            minnow_volume += qty
    
    # 2. Рассчитываем проценты
    if total_volume == 0:
        return {
            'whale_pct': 0.0,
            'dolphin_pct': 0.0,
            'minnow_pct': 0.0
        }
    
    return {
        'whale_pct': float(whale_volume / total_volume),
        'dolphin_pct': float(dolphin_volume / total_volume),
        'minnow_pct': float(minnow_volume / total_volume)
    }


def calculate_price_drift_bps(
    iceberg_price: Decimal,
    current_mid_price: Decimal
) -> float:
    """
    WHY: Рассчитывает "прогиб" цены против айсберга.
    
    Используется для детекции Adverse Selection:
    - Spread stable = Стена держится
    - Spread widen = Стена слабеет (киты прогибают цену)
    
    Args:
        iceberg_price: Цена уровня айсберга
        current_mid_price: Текущая mid-price в стакане
    
    Returns:
        float: Смещение в basis points (положительное = слабость)
    
    Example:
        >>> # Bid iceberg на 60000, mid упал до 59950
        >>> drift = calculate_price_drift_bps(Decimal('60000'), Decimal('59950'))
        >>> assert drift > 0  # Положительный drift = слабость
    """
    if iceberg_price == 0:
        return 0.0
    
    # Drift в процентах
    drift_pct = abs((current_mid_price - iceberg_price) / iceberg_price)
    
    # Конвертируем в basis points (1% = 100 bps)
    drift_bps = float(drift_pct * 10000)
    
    return drift_bps


# ========================================================================
# UNIT TESTS
# ========================================================================

if __name__ == '__main__':
    """Quick self-test"""
    
    # Test 1: Whale dominance
    trades = [
        TradeEvent(
            price=Decimal('60000'),
            quantity=Decimal('10.0'),
            is_buyer_maker=True,
            event_time=1000
        ),
        TradeEvent(
            price=Decimal('60000'),
            quantity=Decimal('0.5'),
            is_buyer_maker=True,
            event_time=1001
        )
    ]
    
    dist = calculate_cohort_distribution(trades)
    print(f"Whale distribution test: {dist}")
    assert dist['whale_pct'] > 0.9, "Whale should dominate"
    
    # Test 2: Minnow panic
    trades = [
        TradeEvent(
            price=Decimal('60000'),
            quantity=Decimal('0.1'),
            is_buyer_maker=True,
            event_time=1000
        ) for _ in range(10)
    ]
    
    dist = calculate_cohort_distribution(trades)
    print(f"Minnow panic test: {dist}")
    assert dist['minnow_pct'] == 1.0, "Minnows should be 100%"
    
    # Test 3: Price drift
    drift = calculate_price_drift_bps(Decimal('60000'), Decimal('59950'))
    print(f"Price drift test: {drift} bps")
    assert drift > 0, "Should have positive drift"
    
    print("✅ All tests passed!")
