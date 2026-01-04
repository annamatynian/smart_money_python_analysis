"""
WHY: Tests for iceberg reconciliation after snapshot resync (Critical Bug Fix)

Scenario: Network glitch → WebSocket reconnect → snapshot resync
Problem: Old icebergs from pre-resync state remain as "ghosts" 
Solution: reconcile_with_snapshot() marks missing levels as INVALIDATED

Reference: Gemini recommendation 2.2 Gap Detection
"""
import pytest
from decimal import Decimal
from datetime import datetime, timedelta
from domain import LocalOrderBook, IcebergLevel, IcebergStatus
from config import BTC_CONFIG


def test_reconcile_removes_ghost_icebergs():
    """
    WHY: После resync айсберги, отсутствующие в новом снапшоте, должны быть INVALIDATED
    
    Scenario:
    1. Before resync: Iceberg at 60000 (BID)
    2. Network disconnect → iceberg cancelled by trader
    3. After resync: Snapshot has no liquidity at 60000
    4. Expected: Iceberg marked as INVALIDATED (not ACTIVE)
    """
    # Setup
    book = LocalOrderBook(symbol="BTCUSDT", config=BTC_CONFIG)
    
    # Register iceberg BEFORE resync
    book.register_iceberg(
        price=Decimal("60000.00"),
        hidden_vol=Decimal("10.0"),
        is_ask=False,  # BID iceberg
        confidence=0.85
    )
    
    # Verify it's active
    iceberg = book.get_iceberg_at_price(Decimal("60000.00"), is_ask=False)
    assert iceberg is not None
    assert iceberg.status == IcebergStatus.ACTIVE
    
    # RESYNC: New snapshot without this level
    new_snapshot_bids = [
        (Decimal("59900.00"), Decimal("5.0")),  # Different price
        (Decimal("59800.00"), Decimal("3.0"))
    ]
    new_snapshot_asks = [
        (Decimal("60100.00"), Decimal("2.0"))
    ]
    
    # ACT: Reconcile
    book.reconcile_with_snapshot(new_snapshot_bids, new_snapshot_asks)
    
    # ASSERT: Ghost iceberg is INVALIDATED
    iceberg_after = book.get_iceberg_at_price(Decimal("60000.00"), is_ask=False)
    assert iceberg_after is not None, "Iceberg should still exist in registry"
    assert iceberg_after.status == IcebergStatus.CANCELLED, "Should be marked CANCELLED (not in snapshot)"


def test_reconcile_preserves_active_icebergs():
    """
    WHY: Айсберги, которые ЕСТЬ в новом снапшоте, должны остаться ACTIVE
    
    Scenario:
    1. Iceberg at 60000 with 10 BTC hidden volume
    2. Resync → snapshot shows 15 BTC at 60000 (iceberg still there)
    3. Expected: Iceberg remains ACTIVE
    """
    book = LocalOrderBook(symbol="BTCUSDT", config=BTC_CONFIG)
    
    # Register iceberg
    book.register_iceberg(
        price=Decimal("60000.00"),
        hidden_vol=Decimal("10.0"),
        is_ask=False,
        confidence=0.85
    )
    
    # RESYNC: Snapshot HAS this level with significant volume
    new_snapshot_bids = [
        (Decimal("60000.00"), Decimal("15.0")),  # Still there
        (Decimal("59900.00"), Decimal("5.0"))
    ]
    new_snapshot_asks = []
    
    # ACT
    book.reconcile_with_snapshot(new_snapshot_bids, new_snapshot_asks)
    
    # ASSERT: Still active
    iceberg = book.get_iceberg_at_price(Decimal("60000.00"), is_ask=False)
    assert iceberg.status == IcebergStatus.ACTIVE


def test_reconcile_invalidates_zero_volume_levels():
    """
    WHY: Если в снапшоте уровень есть, но объем ничтожно мал (< dust_threshold),
         это значит айсберг истощился или отменен
    
    Scenario:
    1. Iceberg at 60000 (10 BTC hidden)
    2. Resync → snapshot shows 0.0001 BTC at 60000 (dust < 0.001 threshold)
    3. Expected: INVALIDATED (volume too low to be real iceberg)
    """
    book = LocalOrderBook(symbol="BTCUSDT", config=BTC_CONFIG)
    
    book.register_iceberg(
        price=Decimal("60000.00"),
        hidden_vol=Decimal("10.0"),
        is_ask=True,  # ASK iceberg
        confidence=0.90
    )
    
    # RESYNC: Price exists but volume is dust (BELOW threshold)
    new_snapshot_bids = []
    new_snapshot_asks = [
        (Decimal("60000.00"), Decimal("0.0001"))  # Dust volume (< 0.001 BTC_CONFIG threshold)
    ]
    
    # ACT
    book.reconcile_with_snapshot(new_snapshot_bids, new_snapshot_asks)
    
    # ASSERT
    iceberg = book.get_iceberg_at_price(Decimal("60000.00"), is_ask=True)
    assert iceberg.status == IcebergStatus.CANCELLED, "Dust volume should invalidate iceberg"


def test_reconcile_handles_empty_snapshot():
    """
    WHY: Edge case - полностью пустой снапшот (биржа вернула пустые массивы)
    
    Scenario:
    1. Multiple icebergs registered
    2. Resync → snapshot is empty (exchange API error or extreme low liquidity)
    3. Expected: ALL icebergs INVALIDATED
    """
    book = LocalOrderBook(symbol="BTCUSDT", config=BTC_CONFIG)
    
    # Register multiple icebergs
    book.register_iceberg(Decimal("60000.00"), Decimal("5.0"), is_ask=False, confidence=0.8)
    book.register_iceberg(Decimal("60100.00"), Decimal("3.0"), is_ask=True, confidence=0.75)
    
    # RESYNC: Empty snapshot
    book.reconcile_with_snapshot(bids=[], asks=[])
    
    # ASSERT: All invalidated
    for level in book.active_icebergs.values():
        assert level.status == IcebergStatus.CANCELLED


def test_reconcile_only_affects_icebergs_not_orderbook():
    """
    WHY: Reconciliation только помечает айсберги, не трогая основной стакан
    
    Scenario:
    1. Iceberg registered
    2. Order book has liquidity at multiple levels
    3. Reconcile called
    4. Expected: Order book state unchanged, only iceberg registry affected
    """
    book = LocalOrderBook(symbol="BTCUSDT", config=BTC_CONFIG)
    
    # Initialize book with snapshot FIRST (to avoid GapDetectedError)
    book.apply_snapshot(
        bids=[(Decimal("60000.00"), Decimal("10.0"))],
        asks=[(Decimal("60100.00"), Decimal("5.0"))],
        last_update_id=99
    )
    
    # Then apply update
    from domain import OrderBookUpdate
    update = OrderBookUpdate(
        bids=[(Decimal("60000.00"), Decimal("10.0"))],
        asks=[(Decimal("60100.00"), Decimal("5.0"))],
        first_update_id=100,
        final_update_id=100,
        event_time=1000  # FIX: Required field
    )
    book.apply_update(update)
    
    # Register iceberg
    book.register_iceberg(Decimal("59000.00"), Decimal("2.0"), is_ask=False, confidence=0.7)
    
    # Store original book state
    original_bid_volume = book.bids[Decimal("60000.00")]
    
    # RESYNC: Snapshot without iceberg level but with order book data
    book.reconcile_with_snapshot(
        bids=[(Decimal("60000.00"), Decimal("10.0"))],  # Same as before
        asks=[(Decimal("60100.00"), Decimal("5.0"))]
    )
    
    # ASSERT: Order book unchanged
    assert book.bids[Decimal("60000.00")] == original_bid_volume
    
    # ASSERT: Iceberg invalidated
    iceberg = book.get_iceberg_at_price(Decimal("59000.00"), is_ask=False)
    assert iceberg.status == IcebergStatus.CANCELLED
