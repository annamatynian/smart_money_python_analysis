from decimal import Decimal

spot = Decimal('60000')
futures = Decimal('60500')

basis = float((futures - spot) / spot)
basis_pct = basis * 100

print(f"\n📊 Basis Test:")
print(f"  Spot: ${spot}")
print(f"  Futures: ${futures}")
print(f"  Basis (decimal): {basis:.6f}")
print(f"  Basis (percent): {basis_pct:.4f}%")

assert 0.8 < basis_pct < 0.9, f"Expected 0.833%, got {basis_pct:.4f}%"
print("  ✅ TEST PASSED!")
