"""
WHY: Тест для динамической глубины OFI расчёта (Gemini Phase 2.2).

Проблема: Hardcoded depth=20 даёт boundary noise для волатильных токенов.
Решение: ofi_depth параметр в AssetConfig.

Для BTC: depth=20 (узкие спреды)
Для ETH: depth=30 (шире спреды)
Для SOL: depth=50 (очень волатильный)
"""
import pytest
from decimal import Decimal
from domain import LocalOrderBook, OrderBookUpdate
from config import BTC_CONFIG, ETH_CONFIG, SOL_CONFIG, AssetConfig


def test_config_has_ofi_depth_field():
    """
    WHY: Проверяем что AssetConfig содержит ofi_depth.
    """
    assert hasattr(BTC_CONFIG, 'ofi_depth'), \
        "FAIL: BTC_CONFIG должен содержать ofi_depth"
    
    assert hasattr(ETH_CONFIG, 'ofi_depth'), \
        "FAIL: ETH_CONFIG должен содержать ofi_depth"
    
    # Проверяем типы
    assert isinstance(BTC_CONFIG.ofi_depth, int), \
        "FAIL: ofi_depth должен быть int"
    
    # Проверяем разумные значения
    assert 10 <= BTC_CONFIG.ofi_depth <= 100, \
        f"FAIL: BTC ofi_depth={BTC_CONFIG.ofi_depth} вне разумного диапазона [10, 100]"


def test_btc_has_lower_depth_than_eth():
    """
    WHY: BTC имеет узкие спреды → меньшая глубина достаточна.
    ETH более волатильный → нужна большая глубина.
    """
    assert BTC_CONFIG.ofi_depth <= ETH_CONFIG.ofi_depth, \
        f"FAIL: BTC depth ({BTC_CONFIG.ofi_depth}) должен быть <= ETH depth ({ETH_CONFIG.ofi_depth})"


def test_sol_has_highest_depth():
    """
    WHY: SOL самый волатильный → максимальная глубина для стабильности.
    """
    if hasattr(SOL_CONFIG, 'ofi_depth'):
        assert SOL_CONFIG.ofi_depth >= ETH_CONFIG.ofi_depth, \
            f"FAIL: SOL depth ({SOL_CONFIG.ofi_depth}) должен быть >= ETH depth ({ETH_CONFIG.ofi_depth})"


def test_calculate_ofi_uses_config_depth():
    """
    WHY: Проверяем что calculate_ofi() использует depth из config.
    
    При вызове без параметра должен браться config.ofi_depth.
    """
    # Создаём custom config с нестандартным depth
    custom_config = AssetConfig(
        symbol="TESTUSDT",
        dust_threshold=Decimal("0.001"),
        price_display_format="{:,.2f}",
        min_hidden_volume=Decimal("0.01"),
        min_iceberg_ratio=Decimal("0.3"),
        gamma_wall_tolerance_pct=Decimal("0.001"),
        static_whale_threshold_usd=10000.0,
        static_minnow_threshold_usd=100.0,
        min_whale_floor_usd=1000.0,
        min_minnow_floor_usd=10.0,
        spoofing_volume_threshold=Decimal("0.1"),
        breach_tolerance_pct=Decimal("0.001"),
        lambda_decay=0.1,
        ofi_depth=5  # ← Нестандартное значение
    )
    
    book = LocalOrderBook(symbol="TESTUSDT", config=custom_config)
    
    # Создаём стакан с 20 уровнями
    bids = [(Decimal(str(100000 - i*10)), Decimal("1.0")) for i in range(20)]
    asks = [(Decimal(str(100010 + i*10)), Decimal("1.0")) for i in range(20)]
    
    book.apply_snapshot(bids, asks, last_update_id=1)
    
    # Update: меняем 10-й уровень (вне depth=5)
    update = OrderBookUpdate(
        bids=[(Decimal("99910"), Decimal("2.0"))],  # 10-й уровень
        asks=[],
        first_update_id=2,
        final_update_id=2
    )
    book.apply_update(update)
    
    # Расчёт OFI без параметра (должен использовать config.ofi_depth=5)
    ofi = book.calculate_ofi()
    
    # Проверяем что изменение 10-го уровня НЕ учтено (вне depth=5)
    # OFI должен быть 0 (изменения вне зоны анализа)
    assert abs(ofi) < 0.1, \
        f"FAIL: OFI={ofi}, ожидали ~0 (изменение на 10-м уровне, но depth=5)"


def test_calculate_ofi_explicit_depth_overrides_config():
    """
    WHY: Проверяем что явный параметр depth переопределяет config.
    
    calculate_ofi(depth=10) должен игнорировать config.ofi_depth.
    """
    book = LocalOrderBook(symbol="BTCUSDT", config=BTC_CONFIG)
    
    # Стакан с 30 уровнями
    bids = [(Decimal(str(100000 - i*10)), Decimal("1.0")) for i in range(30)]
    asks = [(Decimal(str(100010 + i*10)), Decimal("1.0")) for i in range(30)]
    
    book.apply_snapshot(bids, asks, last_update_id=1)
    
    # Update на 25-м уровне
    update = OrderBookUpdate(
        bids=[(Decimal("99760"), Decimal("5.0"))],  # 25-й уровень
        asks=[],
        first_update_id=2,
        final_update_id=2
    )
    book.apply_update(update)
    
    # OFI с явным depth=30 должен учесть изменение
    ofi_deep = book.calculate_ofi(depth=30)
    assert ofi_deep > 0, \
        f"FAIL: OFI с depth=30 должен быть >0, получили {ofi_deep}"
    
    # OFI с depth=10 НЕ должен учесть (25-й уровень вне зоны)
    ofi_shallow = book.calculate_ofi(depth=10)
    assert abs(ofi_shallow) < abs(ofi_deep), \
        f"FAIL: OFI с depth=10 ({ofi_shallow}) должен быть меньше чем с depth=30 ({ofi_deep})"


def test_snapshot_respects_ofi_depth():
    """
    WHY: Проверяем что _save_book_snapshot() сохраняет depth из config.
    
    Если config.ofi_depth=15, должно сохраняться 15 уровней.
    """
    custom_config = AssetConfig(
        symbol="TESTUSDT",
        dust_threshold=Decimal("0.001"),
        price_display_format="{:,.2f}",
        min_hidden_volume=Decimal("0.01"),
        min_iceberg_ratio=Decimal("0.3"),
        gamma_wall_tolerance_pct=Decimal("0.001"),
        static_whale_threshold_usd=10000.0,
        static_minnow_threshold_usd=100.0,
        min_whale_floor_usd=1000.0,
        min_minnow_floor_usd=10.0,
        spoofing_volume_threshold=Decimal("0.1"),
        breach_tolerance_pct=Decimal("0.001"),
        lambda_decay=0.1,
        ofi_depth=15  # ← Кастомная глубина
    )
    
    book = LocalOrderBook(symbol="TESTUSDT", config=custom_config)
    
    # Стакан с 50 уровнями
    bids = [(Decimal(str(100000 - i*10)), Decimal("1.0")) for i in range(50)]
    asks = [(Decimal(str(100010 + i*10)), Decimal("1.0")) for i in range(50)]
    
    book.apply_snapshot(bids, asks, last_update_id=1)
    
    # Проверяем что snapshot содержит ровно 15 уровней
    assert len(book.previous_bid_snapshot) == 15, \
        f"FAIL: Bid snapshot должен содержать 15 уровней, содержит {len(book.previous_bid_snapshot)}"
    assert len(book.previous_ask_snapshot) == 15, \
        f"FAIL: Ask snapshot должен содержать 15 уровней, содержит {len(book.previous_ask_snapshot)}"


def test_different_tokens_use_different_depths():
    """
    WHY: Интеграционный тест - разные токены используют разные depth.
    
    BTC (depth=20) vs ETH (depth=30) должны давать разные OFI на дальних уровнях.
    """
    btc_book = LocalOrderBook(symbol="BTCUSDT", config=BTC_CONFIG)
    eth_book = LocalOrderBook(symbol="ETHUSDT", config=ETH_CONFIG)
    
    # Одинаковая структура стакана (в процентах от mid)
    for book, base_price in [(btc_book, 100000), (eth_book, 3000)]:
        bids = [(Decimal(str(base_price - i*10)), Decimal("1.0")) for i in range(40)]
        asks = [(Decimal(str(base_price + 10 + i*10)), Decimal("1.0")) for i in range(40)]
        book.apply_snapshot(bids, asks, last_update_id=1)
    
    # Update на 25-м уровне для обеих книг
    btc_book.apply_update(OrderBookUpdate(
        bids=[(Decimal("99760"), Decimal("5.0"))],
        asks=[],
        first_update_id=2,
        final_update_id=2
    ))
    
    eth_book.apply_update(OrderBookUpdate(
        bids=[(Decimal("2760"), Decimal("5.0"))],
        asks=[],
        first_update_id=2,
        final_update_id=2
    ))
    
    # Расчёт OFI без параметра (используют свои config.ofi_depth)
    btc_ofi = btc_book.calculate_ofi()
    eth_ofi = eth_book.calculate_ofi()
    
    # Для BTC (depth=20) изменение на 25-м уровне НЕ учтено
    # Для ETH (depth=30) изменение на 25-м уровне УЧТЕНО
    
    # Проверка: ETH OFI должен быть больше по модулю
    assert abs(eth_ofi) > abs(btc_ofi), \
        f"FAIL: ETH OFI ({eth_ofi}) должен быть > BTC OFI ({btc_ofi}) при изменении на 25-м уровне"
