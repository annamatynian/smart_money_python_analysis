"""
WHY: Тест для КРИТИЧЕСКОГО фикса reconnect bug.

Проблема: При разрыве WebSocket и повторном apply_snapshot(),
старые previous_snapshots остаются в памяти → ложный гигантский OFI.

Решение: apply_snapshot() ДОЛЖЕН обнулять previous_bid/ask_snapshot.
"""
import pytest
from decimal import Decimal
from domain import LocalOrderBook, OrderBookUpdate
from config import BTC_CONFIG


def test_apply_snapshot_resets_ofi_state():
    """
    WHY: При reconnect старое состояние OFI должно быть сброшено.
    
    Сценарий:
    1. Применяем начальный snapshot
    2. Делаем update (сохраняется previous_snapshot)
    3. Симулируем reconnect → новый snapshot
    4. ПРОВЕРКА: previous_snapshot должен быть None или равен новому
    """
    book = LocalOrderBook(symbol="BTCUSDT", config=BTC_CONFIG)
    
    # 1. Начальный snapshot
    initial_bids = [(Decimal("100000"), Decimal("1.0"))]
    initial_asks = [(Decimal("100010"), Decimal("1.0"))]
    book.apply_snapshot(initial_bids, initial_asks, last_update_id=100)
    
    # 2. Update → сохраняется previous_snapshot
    update = OrderBookUpdate(
        bids=[(Decimal("100000"), Decimal("2.0"))],  # Увеличили bid
        asks=[],
        first_update_id=101,
        final_update_id=101,
        event_time=1000  # FIX: Required field
    )
    book.apply_update(update)
    
    # Проверяем что snapshot сохранился
    assert book.previous_bid_snapshot is not None
    assert book.previous_ask_snapshot is not None
    old_bid_snapshot = book.previous_bid_snapshot.copy()
    
    # 3. RECONNECT → новый snapshot (другие уровни!)
    new_bids = [(Decimal("99000"), Decimal("5.0"))]  # Совсем другие цены!
    new_asks = [(Decimal("99010"), Decimal("3.0"))]
    book.apply_snapshot(new_bids, new_asks, last_update_id=200)
    
    # 4. КРИТИЧЕСКАЯ ПРОВЕРКА: previous_snapshot НЕ должен содержать старые данные
    # ВАРИАНТ 1: Должен быть None (обнулен)
    # ВАРИАНТ 2: Должен содержать НОВЫЙ snapshot, а не старый
    
    # Проверяем что старые цены (100000) НЕТ в новом previous_snapshot
    if book.previous_bid_snapshot is not None:
        assert Decimal("100000") not in book.previous_bid_snapshot, \
            "FAIL: Старый snapshot не сброшен! previous_bid содержит старые уровни"
        
        # И что новые уровни ЕСТЬ (если snapshot сохранился)
        assert Decimal("99000") in book.previous_bid_snapshot, \
            "FAIL: Новый snapshot не сохранился"


def test_ofi_after_reconnect_is_not_giant():
    """
    WHY: Проверяем что OFI после reconnect не дает ложный гигантский скачок.
    
    Сценарий:
    1. Snapshot: bid 100k = 1.0 BTC
    2. DISCONNECT (симулируем через новый snapshot)
    3. Новый snapshot: bid 99k = 5.0 BTC (совсем другой уровень)
    4. Update: bid 99k = 5.5 BTC
    5. OFI должен быть ~0.5, а НЕ гигантское число
    """
    book = LocalOrderBook(symbol="BTCUSDT", config=BTC_CONFIG)
    
    # 1. Начальный snapshot
    book.apply_snapshot(
        [(Decimal("100000"), Decimal("1.0"))],
        [(Decimal("100010"), Decimal("1.0"))],
        last_update_id=100
    )
    
    # 2. RECONNECT → совсем другой snapshot
    book.apply_snapshot(
        [(Decimal("99000"), Decimal("5.0"))],
        [(Decimal("99010"), Decimal("3.0"))],
        last_update_id=200
    )
    
    # 3. Небольшой update
    update = OrderBookUpdate(
        bids=[(Decimal("99000"), Decimal("5.5"))],  # +0.5 BTC
        asks=[],
        first_update_id=201,
        final_update_id=201,
        event_time=1000  # FIX: Required field
    )
    book.apply_update(update)
    
    # 4. Считаем OFI
    ofi = book.calculate_ofi(depth=20)
    
    # 5. ПРОВЕРКА: OFI должен быть около +0.5 (добавили 0.5 BTC на bid)
    # НЕ должен быть 5.5 - 1.0 = 4.5 (сравнение со старым snapshot!)
    assert -1.0 <= ofi <= 1.0, \
        f"FAIL: OFI после reconnect слишком большой! OFI={ofi} (ожидали ~0.5)"
    
    # Более точная проверка (с допуском)
    assert abs(ofi - 0.5) < 0.1, \
        f"FAIL: OFI={ofi}, ожидали ~0.5 (delta bid = +0.5)"


def test_multiple_reconnects():
    """
    WHY: Проверяем устойчивость к множественным reconnect.
    
    Каждый reconnect должен корректно сбрасывать state.
    """
    book = LocalOrderBook(symbol="BTCUSDT", config=BTC_CONFIG)
    
    # Цикл из 3 reconnect
    for i in range(3):
        base_price = Decimal("100000") + Decimal(i * 1000)
        book.apply_snapshot(
            [(base_price, Decimal("1.0"))],
            [(base_price + Decimal("10"), Decimal("1.0"))],
            last_update_id=100 * (i + 1)
        )
        
        # После каждого snapshot делаем update
        update = OrderBookUpdate(
            bids=[(base_price, Decimal("1.5"))],
            asks=[],
            first_update_id=100 * (i + 1) + 1,
            final_update_id=100 * (i + 1) + 1,
            event_time=1000 + i*100  # FIX: Required field
        )
        book.apply_update(update)
        
        # OFI должен быть стабильным (~0.5)
        ofi = book.calculate_ofi()
        assert abs(ofi - 0.5) < 0.2, \
            f"FAIL на итерации {i}: OFI={ofi}, ожидали ~0.5"
