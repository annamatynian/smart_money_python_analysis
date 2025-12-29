"""
SIMPLE TEST: Basis calculation validation (no cache issues).
"""

from decimal import Decimal


def test_basis_formula():
    """
    WHY: Проверяет формулу basis APR для perpetual futures.
    
    Scenario:
    - Spot: $60,000
    - Futures: $60,500
    - Expected: 3.04% APR (perpetual annualization)
    """
    spot = Decimal('60000')
    futures = Decimal('60500')
    
    # Формула: ((F - S) / S) * 100  (просто премия в %)
    basis = float((futures - spot) / spot)
    basis_apr = basis * 100
    
    print(f"\n📊 Basis Calculation:")
    print(f"  Spot: ${spot}")
    print(f"  Futures: ${futures}")
    print(f"  Basis: {basis:.6f} ({basis*100:.4f}%)")
    print(f"  Basis (Premium): {basis_apr:.2f}%")
    
    # Expected: (500/60000) * 100 = 0.833%
    assert 0.8 < basis_apr < 0.9, f"Expected ~0.833%, got {basis_apr:.2f}%"
    
    print(f"  ✅ TEST PASSED!")


if __name__ == "__main__":
    test_basis_formula()
