"""
Быстрая валидация DataLeakageGuard - проверка основных сценариев

WHY: Проверяем что Guard корректно ловит утечки и пропускает чистые данные
"""
import sys
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

# Добавляем путь к проекту
sys.path.insert(0, r'C:\Users\annam\Documents\DeFi-RAG-Project\smart_money_python_analysis')

from utils_ml import DataLeakageGuard, safe_merge_candles_features

def test_future_leakage_detection():
    """Тест 1: Guard должен ловить фичи из будущего"""
    print("\n🧪 TEST 1: Future Leakage Detection")
    
    bad_data = pd.DataFrame({
        'candle_time': [
            datetime(2024, 1, 1, 14, 0),
            datetime(2024, 1, 1, 15, 0),
        ],
        'snapshot_time': [
            datetime(2024, 1, 1, 14, 0),   # OK
            datetime(2024, 1, 1, 15, 30),  # УТЕЧКА!
        ],
        'target': [100, 110]
    })
    
    guard = DataLeakageGuard(bad_data, 'candle_time', 'target')
    
    try:
        guard.check_timestamp_alignment('snapshot_time')
        print("   ❌ FAILED - Should have caught future leakage!")
        return False
    except ValueError as e:
        if "future timestamps" in str(e):
            print("   ✅ PASSED - Correctly caught future leakage")
            return True
        else:
            print(f"   ❌ FAILED - Wrong error: {e}")
            return False


def test_valid_backward_context():
    """Тест 2: Guard должен пропускать корректный backward контекст"""
    print("\n🧪 TEST 2: Valid Backward Context")
    
    good_data = pd.DataFrame({
        'candle_time': [
            datetime(2024, 1, 1, 14, 0),
            datetime(2024, 1, 1, 15, 0),
        ],
        'snapshot_time': [
            datetime(2024, 1, 1, 13, 59),  # 1 мин назад - OK
            datetime(2024, 1, 1, 14, 59),  # 1 мин назад - OK
        ],
        'target': [100, 110],
        'timeframe': ['1h', '1h'],
        'aggregation_version': ['1.0', '1.0']
    })
    
    guard = DataLeakageGuard(good_data, 'candle_time', 'target')
    
    try:
        guard.check_timestamp_alignment('snapshot_time')
        print("   ✅ PASSED - Backward context allowed")
        return True
    except ValueError as e:
        print(f"   ❌ FAILED - Should not raise error: {e}")
        return False


def test_shift_integrity():
    """Тест 3: Guard должен проверять корректность lag-фичей"""
    print("\n🧪 TEST 3: Shift Integrity Check")
    
    data = pd.DataFrame({
        'candle_time': pd.date_range('2024-01-01', periods=10, freq='1h'),
        'price': [100, 101, 102, 103, 104, 105, 106, 107, 108, 109],
        'target': range(110, 120)
    })
    
    # Правильный сдвиг
    data['price_1h_ago'] = data['price'].shift(1)
    
    guard = DataLeakageGuard(data, 'candle_time', 'target')
    
    try:
        guard.check_shift_integrity()
        
        # Не должно быть ошибок сдвига
        shift_issues = [i for i in guard.issues_found if 'shift' in i.lower() or 'mismatch' in i.lower()]
        
        if len(shift_issues) == 0:
            print("   ✅ PASSED - Shift integrity verified")
            return True
        else:
            print(f"   ❌ FAILED - Found issues: {shift_issues}")
            return False
    except Exception as e:
        print(f"   ❌ FAILED - Unexpected error: {e}")
        return False


def test_timeframe_mixing_detection():
    """Тест 4: Guard должен ловить смешивание таймфреймов"""
    print("\n🧪 TEST 4: Timeframe Mixing Detection")
    
    bad_data = pd.DataFrame({
        'candle_time': [
            datetime(2024, 1, 1, 14, 0),
            datetime(2024, 1, 1, 14, 0),  # Тот же timestamp!
        ],
        'timeframe': ['1h', '4h'],  # ❌ Смешивание!
        'target': [100, 110]
    })
    
    guard = DataLeakageGuard(bad_data, 'candle_time', 'target')
    
    try:
        guard.check_timeframe_consistency()
        print("   ❌ FAILED - Should have caught timeframe mixing!")
        return False
    except ValueError as e:
        if "mixing" in str(e).lower():
            print("   ✅ PASSED - Correctly caught timeframe mixing")
            return True
        else:
            print(f"   ❌ FAILED - Wrong error: {e}")
            return False


def test_safe_merge_backward_only():
    """Тест 5: safe_merge должен брать только backward контекст"""
    print("\n🧪 TEST 5: Safe Merge (Backward Only)")
    
    candles = pd.DataFrame({
        'candle_time': [datetime(2024, 1, 1, 15, 0)],
        'close': [100]
    })
    
    features = pd.DataFrame({
        'snapshot_time': [
            datetime(2024, 1, 1, 14, 50),  # 10 мин до
            datetime(2024, 1, 1, 15, 10),  # 10 мин после (не должен взять!)
        ],
        'obi': [0.5, 0.9]
    })
    
    merged = safe_merge_candles_features(candles, features)
    
    # Должен взять 14:50, а не 15:10
    if merged.iloc[0]['snapshot_time'] == datetime(2024, 1, 1, 14, 50):
        if merged.iloc[0]['obi'] == 0.5:  # Не 0.9!
            print("   ✅ PASSED - Merge took backward context only")
            return True
        else:
            print(f"   ❌ FAILED - Wrong OBI value: {merged.iloc[0]['obi']}")
            return False
    else:
        print(f"   ❌ FAILED - Wrong timestamp: {merged.iloc[0]['snapshot_time']}")
        return False


def test_clean_dataset_passes():
    """Тест 6: Чистый датасет должен пройти все проверки"""
    print("\n🧪 TEST 6: Clean Dataset (Full Check)")
    
    clean_data = pd.DataFrame({
        'candle_time': pd.date_range('2024-01-01', periods=50, freq='1h'),
        'snapshot_time': pd.date_range('2024-01-01', periods=50, freq='1h') - timedelta(minutes=1),
        'timeframe': ['1h'] * 50,
        'aggregation_version': ['1.0'] * 50,
        'price': np.random.randn(50) * 10 + 100,
        'target': np.random.randn(50) * 5 + 50,
    })
    
    # Добавляем правильный lag
    clean_data['price_1h_ago'] = clean_data['price'].shift(1)
    
    guard = DataLeakageGuard(clean_data, 'candle_time', 'target')
    
    try:
        guard.check_all()
        
        # Проверяем, что критических ошибок нет
        critical_issues = [i for i in guard.issues_found if 'CRITICAL' in i]
        
        if len(critical_issues) == 0:
            print("   ✅ PASSED - Clean dataset validated")
            return True
        else:
            print(f"   ❌ FAILED - Found critical issues: {critical_issues}")
            return False
    except ValueError as e:
        print(f"   ❌ FAILED - Should not raise error: {e}")
        return False


def main():
    """Запуск всех тестов"""
    print("=" * 70)
    print("🛡️  DATA LEAKAGE GUARD - VALIDATION SUITE")
    print("=" * 70)
    
    tests = [
        test_future_leakage_detection,
        test_valid_backward_context,
        test_shift_integrity,
        test_timeframe_mixing_detection,
        test_safe_merge_backward_only,
        test_clean_dataset_passes
    ]
    
    results = []
    for test_func in tests:
        try:
            result = test_func()
            results.append(result)
        except Exception as e:
            print(f"   ❌ CRASHED - {e}")
            import traceback
            traceback.print_exc()
            results.append(False)
    
    # Итоговый отчет
    passed = sum(results)
    total = len(results)
    
    print("\n" + "=" * 70)
    print(f"📊 FINAL RESULTS: {passed}/{total} tests passed")
    
    if passed == total:
        print("✅ ALL TESTS PASSED - Guard is working correctly!")
    else:
        print(f"❌ {total - passed} tests failed - review output above")
    
    print("=" * 70)
    
    return passed == total


if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)
