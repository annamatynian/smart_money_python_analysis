"""
WHY: Тест для оптимизации _save_book_snapshot() - использование peekitem().

Проблема: sorted(keys) занимает O(N log N) при каждом update.
Решение: SortedDict.peekitem() дает O(1) доступ к топ элементам.

Тест проверяет:
1. Корректность работы с peekitem()
2. Одинаковые результаты со старым методом
3. Нет regression в OFI расчётах
"""
import pytest
from decimal import Decimal
from domain import LocalOrderBook, OrderBookUpdate
from config import BTC_CONFIG


def test_save_snapshot_uses_peekitem():
    """
    WHY: Проверяем что _save_book_snapshot() корректно сохраняет топ-N уровней.
    
    После оптимизации результат должен быть идентичен старому методу.
    """
    book = LocalOrderBook(symbol="BTCUSDT", config=BTC_CONFIG)
    
    # Создаем стакан с 50 уровнями
    bids = [(Decimal(str(100000 - i*10)), Decimal("1.0")) for i in range(50)]
    asks = [(Decimal(str(100010 + i*10)), Decimal("1.0")) for i in range(50)]
    
    book.apply_snapshot(bids, asks, last_update_id=1)
    
    # Проверяем что snapshot сохранился
    assert book.previous_bid_snapshot is not None
    assert book.previous_ask_snapshot is not None
    
    # Проверяем размер (должен быть ограничен depth=20 по умолчанию)
    assert len(book.previous_bid_snapshot) <= 20, \
        f"FAIL: Bid snapshot слишком большой ({len(book.previous_bid_snapshot)})"
    assert len(book.previous_ask_snapshot) <= 20, \
        f"FAIL: Ask snapshot слишком большой ({len(book.previous_ask_snapshot)})"
    
    # Проверяем что сохранились ЛУЧШИЕ уровни
    # Для bids - это самые высокие цены
    saved_bid_prices = sorted(book.previous_bid_snapshot.keys(), reverse=True)
    assert saved_bid_prices[0] == Decimal("100000"), \
        f"FAIL: Лучший bid не сохранён, получили {saved_bid_prices[0]}"
    
    # Для asks - это самые низкие цены
    saved_ask_prices = sorted(book.previous_ask_snapshot.keys())
    assert saved_ask_prices[0] == Decimal("100010"), \
        f"FAIL: Лучший ask не сохранён, получили {saved_ask_prices[0]}"


def test_ofi_calculation_after_optimization():
    """
    WHY: Проверяем что OFI расчёты не сломались после оптимизации snapshot.
    
    Regression test - результаты должны быть идентичны.
    """
    book = LocalOrderBook(symbol="BTCUSDT", config=BTC_CONFIG)
    
    # Initial snapshot
    book.apply_snapshot(
        bids=[(Decimal("100000"), Decimal("5.0")),
              (Decimal("99990"), Decimal("3.0")),
              (Decimal("99980"), Decimal("2.0"))],
        asks=[(Decimal("100010"), Decimal("4.0")),
              (Decimal("100020"), Decimal("2.0"))],
        last_update_id=1
    )
    
    # Update: добавляем bid ликвидность
    update = OrderBookUpdate(
        bids=[(Decimal("100000"), Decimal("7.0"))],  # +2.0 BTC
        asks=[],
        first_update_id=2,
        final_update_id=2,
        event_time=1000  # FIX: Required field
    )
    book.apply_update(update)
    
    # Расчёт OFI
    ofi = book.calculate_ofi(depth=3)
    
    # ПРОВЕРКА: OFI должен быть положительным (~2.0)
    assert ofi > 0, f"FAIL: OFI={ofi}, ожидали положительное значение"
    assert 1.5 <= ofi <= 2.5, \
        f"FAIL: OFI={ofi}, ожидали ~2.0 (добавили 2 BTC на bid)"


def test_snapshot_with_large_orderbook():
    """
    WHY: Тест производительности - проверяем что snapshot работает с большими книгами.
    
    Создаём стакан с 1000 уровнями, сохраняем только топ-20.
    """
    book = LocalOrderBook(symbol="BTCUSDT", config=BTC_CONFIG)
    
    # Генерируем 1000 уровней (WHY: FIX - избегаем qty=0)
    # Старый код генерировал i/10, что давало 0.0 для i=0
    # Новый код: (i+1)/10 → начинаем с 0.1, а не 0.0
    bids = [(Decimal(str(100000 - i)), Decimal(str(float(i + 1) / 10))) for i in range(1000)]
    asks = [(Decimal(str(100001 + i)), Decimal(str(float(i + 1) / 10))) for i in range(1000)]
    
    book.apply_snapshot(bids, asks, last_update_id=1)
    
    # Проверяем что snapshot компактный (только топ-20)
    assert len(book.previous_bid_snapshot) == 20, \
        f"FAIL: Сохранено {len(book.previous_bid_snapshot)} bids, ожидали 20"
    assert len(book.previous_ask_snapshot) == 20, \
        f"FAIL: Сохранено {len(book.previous_ask_snapshot)} asks, ожидали 20"
    
    # Проверяем корректность топ-уровней
    best_bid = max(book.previous_bid_snapshot.keys())
    worst_bid_in_snapshot = min(book.previous_bid_snapshot.keys())
    
    assert best_bid == Decimal("100000"), \
        f"FAIL: Best bid={best_bid}, ожидали 100000"
    assert worst_bid_in_snapshot == Decimal("99981"), \
        f"FAIL: Worst bid in snapshot={worst_bid_in_snapshot}, ожидали 99981 (20-й уровень)"


def test_snapshot_depth_parameter():
    """
    WHY: Проверяем что параметр depth корректно работает.
    
    Можем сохранять разную глубину (10, 20, 50).
    """
    book = LocalOrderBook(symbol="BTCUSDT", config=BTC_CONFIG)
    
    # Стакан с 100 уровнями
    bids = [(Decimal(str(100000 - i*10)), Decimal("1.0")) for i in range(100)]
    asks = [(Decimal(str(100010 + i*10)), Decimal("1.0")) for i in range(100)]
    
    book.apply_snapshot(bids, asks, last_update_id=1)
    
    # По умолчанию depth=20
    assert len(book.previous_bid_snapshot) == 20
    
    # Теперь делаем update и вызываем _save_book_snapshot с depth=50
    # (это внутренний метод, но проверим через apply_update)
    
    # Сначала проверим что можем вызвать метод напрямую
    book._save_book_snapshot(depth=50)
    
    # Теперь должно быть 50 уровней
    assert len(book.previous_bid_snapshot) == 50, \
        f"FAIL: После depth=50 сохранено {len(book.previous_bid_snapshot)} уровней"
    assert len(book.previous_ask_snapshot) == 50, \
        f"FAIL: После depth=50 сохранено {len(book.previous_ask_snapshot)} уровней"


def test_snapshot_preserves_exact_quantities():
    """
    WHY: Проверяем что snapshot сохраняет точные количества (Decimal precision).
    
    Критично для OFI - нельзя терять точность при копировании.
    """
    book = LocalOrderBook(symbol="BTCUSDT", config=BTC_CONFIG)
    
    # Стакан с нестандартными количествами
    bids = [
        (Decimal("100000"), Decimal("1.23456789")),
        (Decimal("99990"), Decimal("0.00000001")),  # Dust level
    ]
    asks = [
        (Decimal("100010"), Decimal("9.87654321")),
    ]
    
    book.apply_snapshot(bids, asks, last_update_id=1)
    
    # Проверяем точность
    saved_qty_1 = book.previous_bid_snapshot[Decimal("100000")]
    saved_qty_2 = book.previous_bid_snapshot[Decimal("99990")]
    
    assert saved_qty_1 == Decimal("1.23456789"), \
        f"FAIL: Потеряна точность bid[100000], сохранено {saved_qty_1}"
    assert saved_qty_2 == Decimal("0.00000001"), \
        f"FAIL: Потеряна точность bid[99990], сохранено {saved_qty_2}"
    
    saved_ask_qty = book.previous_ask_snapshot[Decimal("100010")]
    assert saved_ask_qty == Decimal("9.87654321"), \
        f"FAIL: Потеряна точность ask[100010], сохранено {saved_ask_qty}"
