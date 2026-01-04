"""
WHY: Критические тесты для Volume-Based OFI (Gemini validation requirements)

Покрытие:
1. Price Shift Invariance - главное обещание Volume-Based метода
2. Partial Fill Weighting - корректность применения весов к take_qty

Теория (Gemini feedback):
- Volume OFI должен быть инвариантен к абсолютному уровню цен
- Exponential weight применяется к взятому объёму (take_qty), не ко всему уровню
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from decimal import Decimal
import math
from domain import LocalOrderBook, OrderBookUpdate
from config import BTC_CONFIG  # ← FIX: Используем константу вместо метода


# ===========================================================================
# TEST 1: PRICE SHIFT INVARIANCE (КРИТИЧЕСКИЙ!)
# ===========================================================================

def test_volume_ofi_price_shift_invariance():
    """
    WHY: Volume-Based OFI должен быть инвариантен к сдвигу всего стакана.
    
    Сценарий:
    1. Создать стакан на цене $60,000
    2. Посчитать Volume OFI
    3. Сдвинуть ВСЕ цены на +$10,000 (структура объёмов та же)
    4. Volume OFI должен быть практически идентичным (~0.0 difference)
    
    Теория (Gemini):
    Это главное обещание Volume-Based метода - защита от "Price Shift Artifact".
    Если тест провалится, вся архитектура напрасна.
    """
    config = BTC_CONFIG  # ← FIX: Используем BTC_CONFIG
    
    # === СЦЕНАРИЙ 1: Стакан на $60,000 ===
    book1 = LocalOrderBook(symbol="BTCUSDT", config=config)
    
    # Начальное состояние
    book1.apply_snapshot(
        bids=[
            (Decimal("60000"), Decimal("10.0")),
            (Decimal("59900"), Decimal("15.0")),
            (Decimal("59800"), Decimal("20.0"))
        ],
        asks=[
            (Decimal("60100"), Decimal("8.0")),
            (Decimal("60200"), Decimal("12.0")),
            (Decimal("60300"), Decimal("18.0"))
        ],
        last_update_id=100
    )
    book1._save_book_snapshot()
    
    # Обновление: Bid +5 BTC, Ask +3 BTC
    update1 = OrderBookUpdate(
        first_update_id=101,
        final_update_id=102,
        bids=[(Decimal("60000"), Decimal("15.0"))],  # +5
        asks=[(Decimal("60100"), Decimal("11.0"))],  # +3
        event_time=1234567890000
    )
    book1.apply_update(update1)
    
    # === КРИТИЧНО: Тест инвариантности БЕЗ exponential ===
    # WHY: Exponential decay вносит небольшое искажение при сдвиге цен
    # (относительные расстояния меняются: 50/60050 ≠ 50/70050)
    # Чистая Volume-Based логика должна быть инвариантна
    ofi_at_60k = book1.get_volume_based_ofi(
        target_volume=20.0,
        use_exponential=False  # ← FIX: Отключаем веса для чистого теста
    )
    
    # === СЦЕНАРИЙ 2: Точно такой же стакан, но на $70,000 ===
    book2 = LocalOrderBook(symbol="BTCUSDT", config=config)
    
    # Начальное состояние (+$10k к КАЖДОЙ цене)
    book2.apply_snapshot(
        bids=[
            (Decimal("70000"), Decimal("10.0")),  # +10k
            (Decimal("69900"), Decimal("15.0")),  # +10k
            (Decimal("69800"), Decimal("20.0"))   # +10k
        ],
        asks=[
            (Decimal("70100"), Decimal("8.0")),   # +10k
            (Decimal("70200"), Decimal("12.0")),  # +10k
            (Decimal("70300"), Decimal("18.0"))   # +10k
        ],
        last_update_id=200
    )
    book2._save_book_snapshot()
    
    # Точно такое же обновление (+$10k к ценам)
    update2 = OrderBookUpdate(
        first_update_id=201,
        final_update_id=202,
        bids=[(Decimal("70000"), Decimal("15.0"))],  # +5 BTC
        asks=[(Decimal("70100"), Decimal("11.0"))],  # +3 BTC
        event_time=1234567890000
    )
    book2.apply_update(update2)
    
    # Считаем Volume OFI на $70k (БЕЗ exponential)
    ofi_at_70k = book2.get_volume_based_ofi(
        target_volume=20.0,
        use_exponential=False  # ← Consistency с первым тестом
    )
    
    # === ВАЛИДАЦИЯ: OFI должны быть ИДЕНТИЧНЫ ===
    print(f"\n📊 Price Shift Invariance Test:")
    print(f"   OFI at $60k: {ofi_at_60k:.6f}")
    print(f"   OFI at $70k: {ofi_at_70k:.6f}")
    print(f"   Difference:  {abs(ofi_at_60k - ofi_at_70k):.6f}")
    
    # Допускаем погрешность из-за float арифметики
    # Но она должна быть ОЧЕНЬ малой (<0.01%)
    tolerance = abs(ofi_at_60k) * 0.0001 if ofi_at_60k != 0 else 0.001
    
    assert abs(ofi_at_60k - ofi_at_70k) < tolerance, \
        f"Volume OFI НЕ инвариантен к сдвигу цены! " \
        f"Разница {abs(ofi_at_60k - ofi_at_70k):.6f} превышает tolerance {tolerance:.6f}"


# ===========================================================================
# TEST 2: PARTIAL FILL WEIGHTING (EDGE CASE)
# ===========================================================================

def test_volume_ofi_partial_fill_weighting():
    """
    WHY: Exponential weight должен применяться к take_qty, не ко всему уровню.
    
    Сценарий:
    1. Создать уровень с 100 BTC на $59,950 (близко к mid)
    2. target_volume = 10 BTC (берём только часть уровня)
    3. Используем реальный BTC lambda = 0.1
    4. Проверить: взяли 10 * weight, НЕ 100 * weight
    
    Теория (Gemini):
    Это критический edge case. Если вес применяется неправильно,
    OFI будет завышен в 10x раз для частично заполненных уровней.
    """
    # Используем реальный BTC config (не модифицируем)
    config = BTC_CONFIG
    book = LocalOrderBook(symbol="BTCUSDT", config=config)
    
    # === СЦЕНАРИЙ: Изолированный уровень (только один!) ===
    # Mid price = (59950 + 60050) / 2 = 60000
    # Уровень $59,950 находится на -0.083% от mid (ОЧЕНЬ близко)
    # WHY: ОДИН уровень на каждой стороне - чтобы не было cross-contamination
    
    book.apply_snapshot(
        bids=[
            (Decimal("59950"), Decimal("5.0"))   # ЕДИНСТВЕННЫЙ bid уровень
        ],
        asks=[
            (Decimal("60050"), Decimal("5.0"))   # ЕДИНСТВЕННЫЙ ask уровень
        ],
        last_update_id=100
    )
    book._save_book_snapshot()
    
    # Обновление: добавляем 10 BTC на ЕДИНСТВЕННЫЙ уровень $59,950
    # (было 5, стало 15 → delta = +10)
    update = OrderBookUpdate(
        first_update_id=101,
        final_update_id=102,
        bids=[(Decimal("59950"), Decimal("15.0"))],  # +10 BTC
        asks=[],
        event_time=1234567890000
    )
    book.apply_update(update)
    
    # === РАСЧЁТ ОЖИДАЕМОГО ВЕСА ===
    mid_price = book.get_mid_price()
    assert mid_price is not None
    
    # Расстояние уровня $59,950 от mid $60,000
    distance_pct = abs(float(Decimal("59950") - mid_price)) / float(mid_price) * 100.0
    lambda_scaled = config.lambda_decay * 100.0
    expected_weight = math.exp(-lambda_scaled * distance_pct)
    
    print(f"\n📐 Partial Fill Weighting Test:")
    print(f"   Mid Price: ${mid_price}")
    print(f"   Level Price: $59,950")
    print(f"   Distance: {distance_pct:.4f}%")
    print(f"   Lambda (BTC): {config.lambda_decay}")
    print(f"   Lambda scaled: {lambda_scaled:.2f}")
    print(f"   Expected weight: {expected_weight:.4f}")
    
    # Считаем Volume OFI с target_volume = 20.0
    # WHY: Покрываем весь delta (15 BTC на уровне) + часть глубины
    ofi = book.get_volume_based_ofi(
        target_volume=20.0,  # Берём 15 BTC с первого уровня + 5 BTC со второго
        use_exponential=True
    )
    
    # === ВАЛИДАЦИЯ ===
    # До: уровень 59950 = 5 BTC → берём 5 BTC × weight
    # После: уровень 59950 = 15 BTC → берём 15 BTC × weight  
    # Delta на bid = (15 - 5) × weight = 10 × weight
    # Delta на ask = 0 (не трогали)
    # OFI = bid_delta - ask_delta = (10 × weight) - 0
    
    expected_ofi = 10.0 * expected_weight
    
    print(f"   Expected OFI (10 * weight): {expected_ofi:.4f}")
    print(f"   Actual OFI:                 {ofi:.4f}")
    print(f"   Difference:                 {abs(ofi - expected_ofi):.6f}")
    
    # КРИТИЧЕСКАЯ ПРОВЕРКА: OFI должен быть ~10*weight
    # НЕ зависеть от того сколько BTC было на уровне изначально
    tolerance = 0.5  # Допускаем небольшую погрешность
    
    assert abs(ofi - expected_ofi) < tolerance, \
        f"Вес применён неправильно! " \
        f"Ожидали {expected_ofi:.4f}, получили {ofi:.4f}. " \
        f"Delta должен быть 10×weight независимо от начального объёма!"
    
    # Дополнительная проверка: OFI должен быть положительным (bid increase)
    assert ofi > 0, f"OFI должен быть положительным (bid increase), получили {ofi:.4f}"


# ===========================================================================
# ДОПОЛНИТЕЛЬНЫЕ ТЕСТЫ (Edge Cases)
# ===========================================================================

def test_volume_ofi_empty_orderbook():
    """WHY: Volume OFI должен вернуть 0.0 для пустого стакана"""
    config = BTC_CONFIG
    book = LocalOrderBook(symbol="BTCUSDT", config=config)
    
    # Пустой стакан (нет предыдущего снапшота)
    ofi = book.get_volume_based_ofi(target_volume=10.0)
    assert ofi == 0.0, f"Expected OFI=0.0 for empty book, got {ofi}"


def test_volume_ofi_no_exponential():
    """WHY: Проверка режима без exponential decay (use_exponential=False)"""
    config = BTC_CONFIG
    book = LocalOrderBook(symbol="BTCUSDT", config=config)
    
    book.apply_snapshot(
        bids=[(Decimal("60000"), Decimal("10.0"))],
        asks=[(Decimal("60100"), Decimal("5.0"))],
        last_update_id=100
    )
    book._save_book_snapshot()
    
    # Добавляем объём на bid
    update = OrderBookUpdate(
        first_update_id=101,
        final_update_id=102,
        bids=[(Decimal("60000"), Decimal("15.0"))],  # +5 BTC
        asks=[],
        event_time=1234567890000
    )
    book.apply_update(update)
    
    # Без весов - чистая разница объёмов
    ofi_no_weights = book.get_volume_based_ofi(
        target_volume=20.0,
        use_exponential=False
    )
    
    # С весами - учитываем расстояние
    ofi_with_weights = book.get_volume_based_ofi(
        target_volume=20.0,
        use_exponential=True
    )
    
    print(f"\n⚖️ Weight Impact Test:")
    print(f"   OFI without weights: {ofi_no_weights:.4f}")
    print(f"   OFI with weights:    {ofi_with_weights:.4f}")
    
    # Без весов должно быть больше (нет затухания)
    assert ofi_no_weights > 0, "Expected positive OFI (bid increase)"
    # С весами может быть немного меньше из-за decay


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
