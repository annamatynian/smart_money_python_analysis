"""
Тесты для DataLeakageGuard - проверка защиты от утечек данных

WHY: Критично убедиться, что Guard надежно ловит все типы утечек.
Если эти тесты зеленые - можно доверять валидации датасета.
"""
import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from utils_ml import DataLeakageGuard, safe_merge_candles_features


class TestTimestampAlignment:
    """Тесты проверки временной целостности (CHECK 1)"""
    
    def test_catches_future_leakage(self):
        """Guard должен ловить фичи из будущего"""
        # Создаем плохие данные (snapshot_time ПОСЛЕ candle_time)
        bad_data = pd.DataFrame({
            'candle_time': [
                datetime(2024, 1, 1, 14, 0),
                datetime(2024, 1, 1, 15, 0),
            ],
            'snapshot_time': [
                datetime(2024, 1, 1, 14, 0),   # OK
                datetime(2024, 1, 1, 15, 30),  # УТЕЧКА! (30 минут в будущем)
            ],
            'target': [100, 110]
        })
        
        guard = DataLeakageGuard(bad_data, 'candle_time', 'target')
        
        with pytest.raises(ValueError, match="future timestamps"):
            guard.check_timestamp_alignment('snapshot_time')
    
    def test_allows_valid_backward_context(self):
        """Guard должен пропускать корректный backward контекст"""
        # Хорошие данные (snapshot_time РАНЬШЕ candle_time)
        good_data = pd.DataFrame({
            'candle_time': [
                datetime(2024, 1, 1, 14, 0),
                datetime(2024, 1, 1, 15, 0),
            ],
            'snapshot_time': [
                datetime(2024, 1, 1, 13, 59),  # 1 минута назад - OK
                datetime(2024, 1, 1, 14, 59),  # 1 минута назад - OK
            ],
            'target': [100, 110]
        })
        
        guard = DataLeakageGuard(good_data, 'candle_time', 'target')
        guard.check_timestamp_alignment('snapshot_time')  # Не должно упасть
    
    def test_allows_exact_time_match(self):
        """Guard должен пропускать точное совпадение времени (граничный случай)"""
        # Граничный случай: snapshot_time == candle_time (OK, не из будущего)
        edge_case = pd.DataFrame({
            'candle_time': [datetime(2024, 1, 1, 14, 0)],
            'snapshot_time': [datetime(2024, 1, 1, 14, 0)],  # Ровно в момент свечи
            'target': [100]
        })
        
        guard = DataLeakageGuard(edge_case, 'candle_time', 'target')
        guard.check_timestamp_alignment('snapshot_time')  # Должно пройти


class TestCorrelationSpike:
    """Тесты проверки подозрительных корреляций (CHECK 2)"""
    
    def test_detects_perfect_correlation(self):
        """Guard должен предупреждать о почти идеальной корреляции"""
        # Создаем фичу, которая почти == target (99.9% корреляция)
        data = pd.DataFrame({
            'candle_time': pd.date_range('2024-01-01', periods=100, freq='1h'),
            'target': np.random.randn(100) * 10 + 100,
            'normal_feature': np.random.randn(100) * 5 + 50,
        })
        data['leaky_feature'] = data['target'] * 0.999 + np.random.randn(100) * 0.01  # Почти копия
        
        guard = DataLeakageGuard(data, 'candle_time', 'target')
        
        # check_target_correlation_spike не падает, но добавляет в issues_found
        guard.check_target_correlation_spike(threshold=0.95)
        
        assert len(guard.issues_found) > 0  # Должно быть предупреждение
        assert any('leaky_feature' in issue for issue in guard.issues_found)
    
    def test_allows_normal_correlations(self):
        """Guard должен пропускать нормальные корреляции"""
        data = pd.DataFrame({
            'candle_time': pd.date_range('2024-01-01', periods=100, freq='1h'),
            'target': np.random.randn(100) * 10 + 100,
            'feature1': np.random.randn(100) * 5 + 50,  # Случайная корреляция
            'feature2': np.random.randn(100) * 3 + 30,
        })
        
        guard = DataLeakageGuard(data, 'candle_time', 'target')
        guard.check_target_correlation_spike(threshold=0.95)
        
        # Не должно быть предупреждений (случайная корреляция < 0.95)
        corr_issues = [i for i in guard.issues_found if 'correlation' in i.lower()]
        assert len(corr_issues) == 0


class TestShiftIntegrity:
    """Тесты проверки корректности lag-фичей (CHECK 3)"""
    
    def test_detects_missing_shift(self):
        """Guard должен ловить lag-колонку без сдвига"""
        data = pd.DataFrame({
            'candle_time': pd.date_range('2024-01-01', periods=10, freq='1h'),
            'price': [100, 101, 102, 103, 104, 105, 106, 107, 108, 109],
            'target': [110, 111, 112, 113, 114, 115, 116, 117, 118, 119]
        })
        
        # Создаем "lag" колонку БЕЗ сдвига (копия оригинала)
        data['price_1h_ago'] = data['price']  # ❌ Должно быть .shift(1)!
        
        guard = DataLeakageGuard(data, 'candle_time', 'target')
        guard.check_shift_integrity()
        
        # Должно найти проблему
        assert any('price_1h_ago' in issue for issue in guard.issues_found)
    
    def test_validates_correct_shift(self):
        """Guard должен пропускать корректно сдвинутые lag-фичи"""
        data = pd.DataFrame({
            'candle_time': pd.date_range('2024-01-01', periods=10, freq='1h'),
            'price': [100, 101, 102, 103, 104, 105, 106, 107, 108, 109],
            'target': [110, 111, 112, 113, 114, 115, 116, 117, 118, 119]
        })
        
        # Правильный сдвиг
        data['price_1h_ago'] = data['price'].shift(1)
        
        guard = DataLeakageGuard(data, 'candle_time', 'target')
        guard.check_shift_integrity()
        
        # Не должно быть ошибок
        shift_issues = [i for i in guard.issues_found if 'shift' in i.lower()]
        assert len(shift_issues) == 0
    
    def test_detects_missing_nan_at_start(self):
        """Guard должен замечать отсутствие NaN в начале lag-колонки"""
        data = pd.DataFrame({
            'candle_time': pd.date_range('2024-01-01', periods=5, freq='1h'),
            'price': [100, 101, 102, 103, 104],
            'target': [110, 111, 112, 113, 114]
        })
        
        # "Lag" колонка без NaN в начале (забыли сдвиг)
        data['price_1h_ago'] = [100, 100, 101, 102, 103]  # ❌ Первый элемент должен быть NaN
        
        guard = DataLeakageGuard(data, 'candle_time', 'target')
        guard.check_shift_integrity()
        
        # Должно найти проблему
        assert any('price_1h_ago' in issue for issue in guard.issues_found)


class TestTimeframeConsistency:
    """Тесты проверки смешивания таймфреймов (CHECK 4)"""
    
    def test_detects_timeframe_mixing(self):
        """Guard должен ловить смешивание 1H и 4H данных"""
        # Плохие данные: одна свеча с разными таймфреймами
        bad_data = pd.DataFrame({
            'candle_time': [
                datetime(2024, 1, 1, 14, 0),
                datetime(2024, 1, 1, 14, 0),  # Тот же timestamp!
            ],
            'timeframe': ['1h', '4h'],  # ❌ Смешивание!
            'target': [100, 110]
        })
        
        guard = DataLeakageGuard(bad_data, 'candle_time', 'target')
        
        with pytest.raises(ValueError, match="Timeframe mixing"):
            guard.check_timeframe_consistency()
    
    def test_allows_consistent_timeframes(self):
        """Guard должен пропускать данные с одним таймфреймом"""
        good_data = pd.DataFrame({
            'candle_time': pd.date_range('2024-01-01', periods=10, freq='1h'),
            'timeframe': ['1h'] * 10,  # Все 1H
            'target': range(100, 110)
        })
        
        guard = DataLeakageGuard(good_data, 'candle_time', 'target')
        guard.check_timeframe_consistency()  # Не должно упасть


class TestAggregationVersion:
    """Тесты проверки версии агрегации (CHECK 5)"""
    
    def test_detects_mixed_versions(self):
        """Guard должен ловить смешивание версий 0.9 и 1.0"""
        bad_data = pd.DataFrame({
            'candle_time': pd.date_range('2024-01-01', periods=10, freq='1h'),
            'aggregation_version': ['1.0'] * 5 + ['0.9'] * 5,  # ❌ Смешивание!
            'target': range(100, 110)
        })
        
        guard = DataLeakageGuard(bad_data, 'candle_time', 'target')
        
        with pytest.raises(ValueError, match="Mixed aggregation versions"):
            guard.check_aggregation_version()
    
    def test_allows_consistent_version(self):
        """Guard должен пропускать данные с одной версией"""
        good_data = pd.DataFrame({
            'candle_time': pd.date_range('2024-01-01', periods=10, freq='1h'),
            'aggregation_version': ['1.0'] * 10,
            'target': range(100, 110)
        })
        
        guard = DataLeakageGuard(good_data, 'candle_time', 'target')
        guard.check_aggregation_version()  # Не должно упасть
    
    def test_warns_on_old_version(self):
        """Guard должен предупреждать об использовании старой версии"""
        old_data = pd.DataFrame({
            'candle_time': pd.date_range('2024-01-01', periods=5, freq='1h'),
            'aggregation_version': ['0.9'] * 5,  # Старая версия
            'target': range(100, 105)
        })
        
        guard = DataLeakageGuard(old_data, 'candle_time', 'target')
        guard.check_aggregation_version(expected_version='1.0')
        
        # Должно добавить предупреждение в issues_found
        assert any('Old version' in issue for issue in guard.issues_found)


class TestSafeMerge:
    """Тесты безопасного merge (helper функция)"""
    
    def test_safe_merge_prevents_future_leakage(self):
        """safe_merge_candles_features должен брать только backward контекст"""
        # Свечи
        candles = pd.DataFrame({
            'candle_time': [
                datetime(2024, 1, 1, 14, 0),
                datetime(2024, 1, 1, 15, 0),
                datetime(2024, 1, 1, 16, 0),
            ],
            'close': [100, 110, 120]
        })
        
        # Фичи (контекст)
        features = pd.DataFrame({
            'snapshot_time': [
                datetime(2024, 1, 1, 13, 55),  # До 14:00
                datetime(2024, 1, 1, 14, 55),  # До 15:00
                datetime(2024, 1, 1, 16, 30),  # ПОСЛЕ 16:00 (не должен попасть)
            ],
            'obi': [0.5, 0.6, 0.7]
        })
        
        merged = safe_merge_candles_features(candles, features)
        
        # Проверяем, что последняя свеча (16:00) НЕ получила контекст из 16:30
        last_row = merged[merged['candle_time'] == datetime(2024, 1, 1, 16, 0)].iloc[0]
        assert last_row['snapshot_time'] == datetime(2024, 1, 1, 14, 55)  # Взял предыдущий
    
    def test_safe_merge_backward_only(self):
        """Проверяем, что merge_asof действительно берет только backward"""
        candles = pd.DataFrame({
            'candle_time': [datetime(2024, 1, 1, 15, 0)],
            'close': [100]
        })
        
        features = pd.DataFrame({
            'snapshot_time': [
                datetime(2024, 1, 1, 14, 50),  # 10 минут до
                datetime(2024, 1, 1, 15, 10),  # 10 минут после (не должен взять!)
            ],
            'obi': [0.5, 0.9]
        })
        
        merged = safe_merge_candles_features(candles, features)
        
        # Должен взять 14:50, а не 15:10
        assert merged.iloc[0]['snapshot_time'] == datetime(2024, 1, 1, 14, 50)
        assert merged.iloc[0]['obi'] == 0.5  # Не 0.9!


class TestFullAudit:
    """Интеграционные тесты полной проверки (check_all)"""
    
    def test_clean_dataset_passes_all_checks(self):
        """Полностью чистый датасет должен пройти все проверки"""
        clean_data = pd.DataFrame({
            'candle_time': pd.date_range('2024-01-01', periods=100, freq='1h'),
            'snapshot_time': pd.date_range('2024-01-01', periods=100, freq='1h') - timedelta(minutes=1),
            'timeframe': ['1h'] * 100,
            'aggregation_version': ['1.0'] * 100,
            'price': np.random.randn(100) * 10 + 100,
            'target': np.random.randn(100) * 5 + 50,
        })
        
        # Добавляем правильный lag
        clean_data['price_1h_ago'] = clean_data['price'].shift(1)
        
        guard = DataLeakageGuard(clean_data, 'candle_time', 'target')
        guard.check_all()  # Не должно упасть
        
        # Проверяем, что критических ошибок нет
        critical_issues = [i for i in guard.issues_found if 'CRITICAL' in i]
        assert len(critical_issues) == 0
    
    def test_dirty_dataset_fails_audit(self):
        """Датасет с утечкой должен упасть на check_all"""
        dirty_data = pd.DataFrame({
            'candle_time': [datetime(2024, 1, 1, 14, 0)],
            'snapshot_time': [datetime(2024, 1, 1, 14, 30)],  # ❌ Из будущего!
            'timeframe': ['1h'],
            'aggregation_version': ['1.0'],
            'target': [100]
        })
        
        guard = DataLeakageGuard(dirty_data, 'candle_time', 'target')
        
        with pytest.raises(ValueError):
            guard.check_all()


# =============================================================================
# PYTEST МАРКЕРЫ для удобного запуска
# =============================================================================

# Запуск только быстрых тестов:
# pytest tests/test_data_leakage_guard.py -v -m "not slow"

# Запуск всех тестов:
# pytest tests/test_data_leakage_guard.py -v
