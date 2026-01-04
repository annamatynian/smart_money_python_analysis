"""
WHY: Unit-тесты для Order Flow Imbalance (OFI) функциональности

Покрытие:
1. LocalOrderBook._save_book_snapshot() - сохранение состояния
2. LocalOrderBook.calculate_ofi() - расчет OFI
3. Edge cases: первый update, удаление уровней, множественные изменения

Теория (документ "Анализ данных смарт-мани", раздел 3.2):
- OFI = Δ(bid_volume) - Δ(ask_volume)
- Положительный OFI при стабильной цене = скрытое предложение (Sell Iceberg)
- Отрицательный OFI при стабильной цене = скрытый спрос (Buy Iceberg)
"""

# WHY: Добавляем родительскую папку в sys.path для импорта модулей
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from decimal import Decimal
from domain import LocalOrderBook, OrderBookUpdate


# ===========================================================================
# ТЕСТЫ _save_book_snapshot()
# ===========================================================================

def test_save_snapshot_basic():
    """WHY: Проверка базового сохранения снапшота"""
    book = LocalOrderBook(symbol="BTCUSDT")
    
    # Инициализируем стакан
    book.apply_snapshot(
        bids=[(Decimal("60000"), Decimal("10.0")), (Decimal("59999"), Decimal("5.0"))],
        asks=[(Decimal("60100"), Decimal("7.0")), (Decimal("60101"), Decimal("3.0"))],
        last_update_id=100
    )
    
    # Сохраняем снапшот
    book._save_book_snapshot(depth=20)
    
    # Проверяем что снапшот сохранен
    assert book.previous_bid_snapshot is not None
    assert book.previous_ask_snapshot is not None
    assert len(book.previous_bid_snapshot) == 2
    assert len(book.previous_ask_snapshot) == 2
    assert book.previous_bid_snapshot[Decimal("60000")] == Decimal("10.0")
    assert book.previous_ask_snapshot[Decimal("60100")] == Decimal("7.0")


def test_save_snapshot_depth_limit():
    """WHY: Проверка что сохраняется только топ-N уровней"""
    book = LocalOrderBook(symbol="BTCUSDT")
    
    # Создаем 30 уровней
    bids = [(Decimal(f"60000.{i:02d}"), Decimal("1.0")) for i in range(30)]
    asks = [(Decimal(f"60100.{i:02d}"), Decimal("1.0")) for i in range(30)]
    
    book.apply_snapshot(bids=bids, asks=asks, last_update_id=100)
    
    # Сохраняем только топ-10
    book._save_book_snapshot(depth=10)
    
    # Должно быть ровно 10 уровней
    assert len(book.previous_bid_snapshot) == 10
    assert len(book.previous_ask_snapshot) == 10


# ===========================================================================
# ТЕСТЫ calculate_ofi() - БАЗОВЫЕ СЛУЧАИ
# ===========================================================================

def test_ofi_no_change():
    """WHY: OFI = 0 когда стакан не изменился"""
    book = LocalOrderBook(symbol="BTCUSDT")
    
    book.apply_snapshot(
        bids=[(Decimal("60000"), Decimal("10.0"))],
        asks=[(Decimal("60100"), Decimal("5.0"))],
        last_update_id=100
    )
    
    # Сохраняем снапшот
    book._save_book_snapshot()
    
    # Применяем пустое обновление
    update = OrderBookUpdate(
        first_update_id=101,
        final_update_id=102,
        bids=[],
        asks=[],
        event_time=1234567890000  # WHY: Required field
    )
    book.apply_update(update)
    
    ofi = book.calculate_ofi()
    assert ofi == 0.0, f"Expected OFI=0.0, got {ofi}"


def test_ofi_bid_increase():
    """WHY: Положительный OFI при добавлении bid ликвидности"""
    book = LocalOrderBook(symbol="BTCUSDT")
    
    book.apply_snapshot(
        bids=[(Decimal("60000"), Decimal("10.0"))],
        asks=[(Decimal("60100"), Decimal("5.0"))],
        last_update_id=100
    )
    book._save_book_snapshot()
    
    # Добавляем 5 BTC на bid
    update = OrderBookUpdate(
        first_update_id=101,
        final_update_id=102,
        bids=[(Decimal("60000"), Decimal("15.0"))],  # Было 10, стало 15
        asks=[],
        event_time=1234567890000
    )
    book.apply_update(update)
    
    ofi = book.calculate_ofi()
    assert ofi > 0, f"Expected positive OFI, got {ofi}"
    assert abs(ofi - 5.0) < 0.01, f"Expected OFI≈5.0, got {ofi}"


def test_ofi_ask_increase():
    """WHY: Отрицательный OFI при добавлении ask ликвидности"""
    book = LocalOrderBook(symbol="BTCUSDT")
    
    book.apply_snapshot(
        bids=[(Decimal("60000"), Decimal("10.0"))],
        asks=[(Decimal("60100"), Decimal("5.0"))],
        last_update_id=100
    )
    book._save_book_snapshot()
    
    # Добавляем 3 BTC на ask
    update = OrderBookUpdate(
        first_update_id=101,
        final_update_id=102,
        bids=[],
        asks=[(Decimal("60100"), Decimal("8.0"))],  # Было 5, стало 8
        event_time=1234567890000
    )
    book.apply_update(update)
    
    ofi = book.calculate_ofi()
    assert ofi < 0, f"Expected negative OFI, got {ofi}"
    assert abs(ofi + 3.0) < 0.01, f"Expected OFI≈-3.0, got {ofi}"


def test_ofi_bid_decrease():
    """WHY: Отрицательный OFI при удалении bid ликвидности (отмена)"""
    book = LocalOrderBook(symbol="BTCUSDT")
    
    book.apply_snapshot(
        bids=[(Decimal("60000"), Decimal("10.0"))],
        asks=[(Decimal("60100"), Decimal("5.0"))],
        last_update_id=100
    )
    book._save_book_snapshot()
    
    # Убираем 4 BTC с bid
    update = OrderBookUpdate(
        first_update_id=101,
        final_update_id=102,
        bids=[(Decimal("60000"), Decimal("6.0"))],  # Было 10, стало 6
        asks=[],
        event_time=1234567890000
    )
    book.apply_update(update)
    
    ofi = book.calculate_ofi()
    assert ofi < 0, f"Expected negative OFI (bid removal), got {ofi}"
    assert abs(ofi + 4.0) < 0.01, f"Expected OFI≈-4.0, got {ofi}"


def test_ofi_ask_decrease():
    """WHY: Положительный OFI при удалении ask ликвидности (отмена)"""
    book = LocalOrderBook(symbol="BTCUSDT")
    
    book.apply_snapshot(
        bids=[(Decimal("60000"), Decimal("10.0"))],
        asks=[(Decimal("60100"), Decimal("5.0"))],
        last_update_id=100
    )
    book._save_book_snapshot()
    
    # Убираем 2 BTC с ask
    update = OrderBookUpdate(
        first_update_id=101,
        final_update_id=102,
        bids=[],
        asks=[(Decimal("60100"), Decimal("3.0"))],  # Было 5, стало 3
        event_time=1234567890000
    )
    book.apply_update(update)
    
    ofi = book.calculate_ofi()
    assert ofi > 0, f"Expected positive OFI (ask removal), got {ofi}"
    assert abs(ofi - 2.0) < 0.01, f"Expected OFI≈2.0, got {ofi}"


# ===========================================================================
# ТЕСТЫ calculate_ofi() - EDGE CASES
# ===========================================================================

def test_ofi_first_update():
    """WHY: Первый update без предыдущего состояния должен вернуть 0"""
    book = LocalOrderBook(symbol="BTCUSDT")
    
    book.apply_snapshot(
        bids=[(Decimal("60000"), Decimal("10.0"))],
        asks=[(Decimal("60100"), Decimal("5.0"))],
        last_update_id=100
    )
    
    # НЕ сохраняем снапшот - имитируем первый update
    
    ofi = book.calculate_ofi()
    assert ofi == 0.0, f"Expected OFI=0.0 for first update, got {ofi}"


def test_ofi_level_deletion():
    """WHY: Удаление ценового уровня (qty=0) должно учитываться"""
    book = LocalOrderBook(symbol="BTCUSDT")
    
    book.apply_snapshot(
        bids=[(Decimal("60000"), Decimal("10.0")), (Decimal("59999"), Decimal("5.0"))],
        asks=[(Decimal("60100"), Decimal("5.0"))],
        last_update_id=100
    )
    book._save_book_snapshot()
    
    # Удаляем уровень 60000 (qty=0)
    update = OrderBookUpdate(
        first_update_id=101,
        final_update_id=102,
        bids=[(Decimal("60000"), Decimal("0"))],  # Удаление!
        asks=[],
        event_time=1234567890000
    )
    book.apply_update(update)
    
    ofi = book.calculate_ofi()
    # Удалили 10 BTC bid → OFI = -10.0
    assert ofi < 0, f"Expected negative OFI, got {ofi}"
    assert abs(ofi + 10.0) < 0.01, f"Expected OFI≈-10.0, got {ofi}"


def test_ofi_new_level_addition():
    """WHY: Появление нового ценового уровня"""
    book = LocalOrderBook(symbol="BTCUSDT")
    
    book.apply_snapshot(
        bids=[(Decimal("60000"), Decimal("10.0"))],
        asks=[(Decimal("60100"), Decimal("5.0"))],
        last_update_id=100
    )
    book._save_book_snapshot()
    
    # Добавляем новый уровень 59998
    update = OrderBookUpdate(
        first_update_id=101,
        final_update_id=102,
        bids=[(Decimal("59998"), Decimal("7.0"))],  # Новый уровень!
        asks=[],
        event_time=1234567890000
    )
    book.apply_update(update)
    
    ofi = book.calculate_ofi()
    # Добавили 7 BTC bid → OFI = +7.0
    assert ofi > 0, f"Expected positive OFI, got {ofi}"
    assert abs(ofi - 7.0) < 0.01, f"Expected OFI≈7.0, got {ofi}"


def test_ofi_complex_scenario():
    """WHY: Комплексный сценарий - одновременные изменения bid и ask"""
    book = LocalOrderBook(symbol="BTCUSDT")
    
    book.apply_snapshot(
        bids=[(Decimal("60000"), Decimal("10.0"))],
        asks=[(Decimal("60100"), Decimal("5.0"))],
        last_update_id=100
    )
    book._save_book_snapshot()
    
    # Bid +3 BTC, Ask +2 BTC
    update = OrderBookUpdate(
        first_update_id=101,
        final_update_id=102,
        bids=[(Decimal("60000"), Decimal("13.0"))],  # +3
        asks=[(Decimal("60100"), Decimal("7.0"))],   # +2
        event_time=1234567890000
    )
    book.apply_update(update)
    
    ofi = book.calculate_ofi()
    # OFI = +3 (bid) - (+2) (ask) = +1.0
    assert ofi > 0, f"Expected positive OFI, got {ofi}"
    assert abs(ofi - 1.0) < 0.01, f"Expected OFI≈1.0, got {ofi}"


def test_ofi_sequential_updates():
    """WHY: Проверка что OFI корректно обновляется при последовательных updates"""
    book = LocalOrderBook(symbol="BTCUSDT")
    
    book.apply_snapshot(
        bids=[(Decimal("60000"), Decimal("10.0"))],
        asks=[(Decimal("60100"), Decimal("5.0"))],
        last_update_id=100
    )
    book._save_book_snapshot()
    
    # Update 1: Bid +5
    update1 = OrderBookUpdate(
        first_update_id=101,
        final_update_id=102,
        bids=[(Decimal("60000"), Decimal("15.0"))],
        asks=[],
        event_time=1234567890000
    )
    book.apply_update(update1)
    
    ofi1 = book.calculate_ofi()
    assert abs(ofi1 - 5.0) < 0.01
    
    # КРИТИЧНО: apply_update вызывает _save_book_snapshot ДО изменений
    # Поэтому previous_bid_snapshot содержит СТАРОЕ значение (10.0)
    # А ТЕКУЩЕЕ значение в self.bids = 15.0
    # Это ПРАВИЛЬНО для OFI: мы сравниваем NEW (15.0) vs OLD (10.0)
    assert book.previous_bid_snapshot[Decimal("60000")] == Decimal("10.0"), "Snapshot должен содержать СТАРОЕ значение"
    assert book.bids[Decimal("60000")] == Decimal("15.0"), "Текущий стакан должен содержать НОВОЕ значение"
    
    # Update 2: Ask +3
    update2 = OrderBookUpdate(
        first_update_id=103,
        final_update_id=104,
        bids=[],
        asks=[(Decimal("60100"), Decimal("8.0"))],
        event_time=1234567890001  # Другое время
    )
    book.apply_update(update2)
    
    ofi2 = book.calculate_ofi()
    # OFI = 0 (bid не изменился) - 3 (ask +3) = -3.0
    assert abs(ofi2 + 3.0) < 0.01, f"Expected OFI≈-3.0, got {ofi2}"


# ===========================================================================
# ИНТЕГРАЦИОННЫЕ ТЕСТЫ
# ===========================================================================

def test_ofi_integration_iceberg_scenario():
    """
    WHY: Интеграционный тест - сценарий детекции айсберга через OFI
    
    Сценарий:
    1. Большая сделка на Ask (съедает видимую ликвидность)
    2. Ask НЕ пропадает (рефилл)
    3. OFI должен показать отрицательное значение (добавление ask ликвидности)
    4. Это сигнал Sell Iceberg!
    """
    book = LocalOrderBook(symbol="BTCUSDT")
    
    # Начальное состояние
    book.apply_snapshot(
        bids=[(Decimal("60000"), Decimal("10.0"))],
        asks=[(Decimal("60100"), Decimal("5.0"))],  # Видимо только 5 BTC
        last_update_id=100
    )
    book._save_book_snapshot()
    
    # Сделка съедает 8 BTC, но видно было только 5
    # После сделки объем восстановился до 5 BTC (рефилл из айсберга)
    update = OrderBookUpdate(
        first_update_id=101,
        final_update_id=102,
        bids=[],
        asks=[(Decimal("60100"), Decimal("5.0"))],  # Объем НЕ изменился!
        event_time=1234567890000
    )
    book.apply_update(update)
    
    ofi = book.calculate_ofi()
    
    # OFI = 0 - 0 = 0 (видимый объем не изменился)
    # НО это означает что был рефилл! (добавление скрытой ликвидности)
    # В реальности анализатор должен сравнить volume trade vs visible
    assert abs(ofi) < 0.01  # OFI≈0 при идеальном рефилле


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
