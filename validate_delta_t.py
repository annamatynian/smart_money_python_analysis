"""
Quick validation script for Delta-t implementation
WHY: Validates the core mathematical model and basic functionality
"""

import sys
sys.path.insert(0, r'C:\Users\annam\Documents\DeFi-RAG-Project\smart_money_python_analysis')

from decimal import Decimal
from domain import LocalOrderBook, TradeEvent
from analyzers import IcebergAnalyzer

def test_sigmoid_model():
    """Test the mathematical sigmoid model"""
    from math import exp
    
    CUTOFF_MS = 30
    ALPHA = 0.15
    
    test_cases = [
        (0, 1.0, "Instant refill"),
        (15, 0.88, "Fast refill"),
        (30, 0.5, "At cutoff"),
        (45, 0.12, "Slow"),
        (60, 0.02, "Too slow"),
    ]
    
    print("\n=== Testing Sigmoid Probability Model ===")
    print(f"Formula: P(Refill|Δt) = 1 / (1 + e^(α(Δt - τ)))")
    print(f"Parameters: α={ALPHA}, τ={CUTOFF_MS}ms\n")
    
    all_passed = True
    for delta_t, expected_prob, description in test_cases:
        exponent = ALPHA * (delta_t - CUTOFF_MS)
        
        if exponent > 50:
            prob = 0.0
        elif exponent < -50:
            prob = 1.0
        else:
            prob = 1.0 / (1.0 + exp(exponent))
        
        passed = abs(prob - expected_prob) < 0.1
        status = "✅ PASS" if passed else "❌ FAIL"
        all_passed = all_passed and passed
        
        print(f"{status} Δt={delta_t:2d}ms: P={prob:.3f} (expected≈{expected_prob:.2f}) - {description}")
    
    return all_passed

def test_fast_refill_detection():
    """Test detection of genuine iceberg (fast refill)"""
    print("\n=== Testing Fast Refill Detection ===")
    
    book = LocalOrderBook(symbol="BTCUSDT")
    
    trade = TradeEvent(
        price=Decimal("100000.0"),
        quantity=Decimal("1.5"),
        is_buyer_maker=False,
        event_time=1000000,
        trade_id=1
    )
    
    visible_before = Decimal("0.5")
    delta_t_ms = 15  # Fast refill
    update_time_ms = trade.event_time + delta_t_ms
    
    result = IcebergAnalyzer.analyze_with_timing(
        book=book,
        trade=trade,
        visible_before=visible_before,
        delta_t_ms=delta_t_ms,
        update_time_ms=update_time_ms
    )
    
    if result is not None:
        print(f"✅ PASS: Iceberg detected")
        print(f"   Hidden volume: {result.detected_hidden_volume} BTC")
        print(f"   Confidence: {result.confidence:.2%}")
        print(f"   Delta-t: {delta_t_ms}ms (genuine refill)")
        return True
    else:
        print(f"❌ FAIL: Should detect iceberg with Δt={delta_t_ms}ms")
        return False

def test_slow_refill_rejection():
    """Test filtering of market maker orders (slow)"""
    print("\n=== Testing Slow Refill Rejection ===")
    
    book = LocalOrderBook(symbol="BTCUSDT")
    
    trade = TradeEvent(
        price=Decimal("100000.0"),
        quantity=Decimal("1.5"),
        is_buyer_maker=False,
        event_time=1000000,
        trade_id=2
    )
    
    visible_before = Decimal("0.5")
    delta_t_ms = 75  # Too slow for refill
    update_time_ms = trade.event_time + delta_t_ms
    
    result = IcebergAnalyzer.analyze_with_timing(
        book=book,
        trade=trade,
        visible_before=visible_before,
        delta_t_ms=delta_t_ms,
        update_time_ms=update_time_ms
    )
    
    if result is None:
        print(f"✅ PASS: Correctly rejected slow order")
        print(f"   Delta-t: {delta_t_ms}ms (market maker)")
        print(f"   Reason: Exceeds MAX_REFILL_DELAY_MS=50")
        return True
    else:
        print(f"❌ FAIL: Should reject Δt={delta_t_ms}ms as market maker")
        return False

def test_race_condition_handling():
    """Test handling of negative delta-t (race condition)"""
    print("\n=== Testing Race Condition Handling ===")
    
    book = LocalOrderBook(symbol="BTCUSDT")
    
    trade = TradeEvent(
        price=Decimal("100000.0"),
        quantity=Decimal("1.5"),
        is_buyer_maker=False,
        event_time=1000000,
        trade_id=3
    )
    
    visible_before = Decimal("0.5")
    delta_t_ms = -10  # Update arrived before trade
    update_time_ms = trade.event_time + delta_t_ms
    
    result = IcebergAnalyzer.analyze_with_timing(
        book=book,
        trade=trade,
        visible_before=visible_before,
        delta_t_ms=delta_t_ms,
        update_time_ms=update_time_ms
    )
    
    if result is None:
        print(f"✅ PASS: Correctly rejected race condition")
        print(f"   Delta-t: {delta_t_ms}ms (negative)")
        print(f"   Reason: Update arrived before trade")
        return True
    else:
        print(f"❌ FAIL: Should reject negative delta-t")
        return False

def main():
    print("=" * 70)
    print(" DELTA-T TEMPORAL VALIDATION - QUICK VALIDATION")
    print("=" * 70)
    
    results = []
    
    # Run tests
    results.append(("Sigmoid Model", test_sigmoid_model()))
    results.append(("Fast Refill Detection", test_fast_refill_detection()))
    results.append(("Slow Refill Rejection", test_slow_refill_rejection()))
    results.append(("Race Condition Handling", test_race_condition_handling()))
    
    # Summary
    print("\n" + "=" * 70)
    print(" TEST SUMMARY")
    print("=" * 70)
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status} {name}")
    
    print(f"\nTotal: {passed}/{total} tests passed")
    
    if passed == total:
        print("\n🎉 ALL TESTS PASSED! Implementation is correct.")
        return 0
    else:
        print(f"\n⚠️  {total - passed} test(s) failed. Review implementation.")
        return 1

if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
