# ===========================================================================
# TESTS: Feature Collector - Smart Money Trends (Step 2)
# ===========================================================================

"""
WHY: Тестируем заполнение макро-контекста в FeatureSnapshot.

Проверяем:
1. _calculate_trend() корректно вычисляет изменение CVD
2. capture_snapshot() заполняет whale_cvd_trend_1w/1m/6m
3. Обработка граничных случаев (мало данных, пустая история)
"""

import pytest
from datetime import datetime, timedelta
from collections import deque
from decimal import Decimal

from domain import LocalOrderBook, HistoricalMemory
from analyzers_features import FeatureCollector


class TestCalculateTrend:
    """Тесты для метода _calculate_trend"""
    
    def test_calculate_trend_basic(self):
        """
        WHY: Базовый тест расчета тренда.
        
        Scenario: 7 дней CVD истории, запрашиваем тренд за 7 баров
        Expected: current - oldest = тренд
        """
        # Arrange
        collector = FeatureCollector(order_book=None)
        
        # История CVD (симулируем 7 дней)
        # Формат: [(timestamp, cvd_value), ...]
        history = deque(maxlen=10)
        base_time = datetime(2025, 1, 1)
        
        for i in range(7):
            timestamp = base_time + timedelta(days=i)
            cvd_value = 1000.0 + (i * 100.0)  # Растёт на 100 каждый день
            history.append((timestamp, cvd_value))
        
        # Act
        trend = collector._calculate_trend(history, bars_count=7)
        
        # Assert
        # WHY: Ожидаем 1600 - 1000 = 600 (6 дней роста по 100)
        assert trend == pytest.approx(600.0, abs=1.0)
    
    def test_calculate_trend_partial_data(self):
        """
        WHY: Тест когда запрашиваем больше баров чем есть в истории.
        
        Scenario: История 3 бара, запрашиваем 7
        Expected: Берём сколько есть (3)
        """
        # Arrange
        collector = FeatureCollector(order_book=None)
        history = deque(maxlen=10)
        
        # Только 3 точки данных
        history.append((datetime(2025, 1, 1), 1000.0))
        history.append((datetime(2025, 1, 2), 1100.0))
        history.append((datetime(2025, 1, 3), 1200.0))
        
        # Act
        trend = collector._calculate_trend(history, bars_count=7)
        
        # Assert
        # WHY: Берём всё что есть: 1200 - 1000 = 200
        assert trend == pytest.approx(200.0, abs=1.0)
    
    def test_calculate_trend_empty_history(self):
        """
        WHY: Граничный случай - пустая история.
        
        Expected: None (нет данных)
        """
        # Arrange
        collector = FeatureCollector(order_book=None)
        history = deque(maxlen=10)
        
        # Act
        trend = collector._calculate_trend(history, bars_count=7)
        
        # Assert
        assert trend is None
    
    def test_calculate_trend_single_point(self):
        """
        WHY: Граничный случай - только одна точка.
        
        Expected: None (нужно минимум 2 для тренда)
        """
        # Arrange
        collector = FeatureCollector(order_book=None)
        history = deque(maxlen=10)
        history.append((datetime(2025, 1, 1), 1000.0))
        
        # Act
        trend = collector._calculate_trend(history, bars_count=7)
        
        # Assert
        assert trend is None
    
    def test_calculate_trend_negative_trend(self):
        """
        WHY: Тест падающего тренда (киты продают).
        
        Scenario: CVD падает каждый день
        Expected: Отрицательное значение
        """
        # Arrange
        collector = FeatureCollector(order_book=None)
        history = deque(maxlen=10)
        
        # CVD падает на 50 каждый день
        for i in range(7):
            timestamp = datetime(2025, 1, 1) + timedelta(days=i)
            cvd_value = 1000.0 - (i * 50.0)
            history.append((timestamp, cvd_value))
        
        # Act
        trend = collector._calculate_trend(history, bars_count=7)
        
        # Assert
        # WHY: 700 - 1000 = -300
        assert trend == pytest.approx(-300.0, abs=1.0)


class TestCaptureSnapshotWithMemory:
    """Тесты интеграции HistoricalMemory в capture_snapshot"""
    
    @pytest.fixture
    def setup_memory(self):
        """
        WHY: Создаёт HistoricalMemory с тестовыми данными.
        
        Симулируем 6 месяцев CVD истории.
        """
        memory = HistoricalMemory()
        base_time = datetime(2024, 7, 1)
        
        # 1. Заполняем 1D историю (180 дней)
        for i in range(180):
            timestamp = base_time + timedelta(days=i)
            whale_cvd = 10000.0 + (i * 100.0)  # Растёт на 100 каждый день
            minnow_cvd = -5000.0 - (i * 50.0)  # Падает (паника)
            price = Decimal("60000") + Decimal(i * 10)
            
            memory.update_history(timestamp, whale_cvd, minnow_cvd, price)
        
        return memory
    
    def test_capture_snapshot_fills_trends(self, setup_memory):
        """
        WHY: Основной тест - проверяем что все поля трендов заполняются.
        
        Expected: whale_cvd_trend_1w, _1m, _6m должны быть float
        """
        # Arrange
        book = LocalOrderBook(symbol="BTCUSDT")
        collector = FeatureCollector(order_book=book)
        memory = setup_memory
        
        # Act
        snapshot = collector.capture_snapshot(historical_memory=memory)
        
        # Assert
        # WHY: Проверяем что поля НЕ None
        assert snapshot.whale_cvd_trend_1w is not None
        assert snapshot.whale_cvd_trend_1m is not None
        assert snapshot.whale_cvd_trend_3m is not None  # КВАРТАЛ
        assert snapshot.whale_cvd_trend_6m is not None
        
        # WHY: Проверяем разумные значения (растущий тренд)
        assert snapshot.whale_cvd_trend_1w > 0  # 7 дней роста
        assert snapshot.whale_cvd_trend_1m > 0  # 30 дней роста
        assert snapshot.whale_cvd_trend_3m > 0  # 90 дней роста (КВАРТАЛ)
        assert snapshot.whale_cvd_trend_6m > 0  # 180 дней роста
        
        # WHY: 6м тренд должен быть больше чем 3м > 1м > 1w (больше времени = больше изменение)
        assert snapshot.whale_cvd_trend_6m > snapshot.whale_cvd_trend_3m
        assert snapshot.whale_cvd_trend_3m > snapshot.whale_cvd_trend_1m
        assert snapshot.whale_cvd_trend_1m > snapshot.whale_cvd_trend_1w
    
    def test_capture_snapshot_without_memory(self):
        """
        WHY: Проверяем что без memory поля остаются None.
        
        Expected: Все trend поля = None
        """
        # Arrange
        book = LocalOrderBook(symbol="BTCUSDT")
        collector = FeatureCollector(order_book=book)
        
        # Act (БЕЗ historical_memory)
        snapshot = collector.capture_snapshot()
        
        # Assert
        assert snapshot.whale_cvd_trend_1w is None
        assert snapshot.whale_cvd_trend_1m is None
        assert snapshot.whale_cvd_trend_3m is None  # КВАРТАЛ
        assert snapshot.whale_cvd_trend_6m is None
    
    def test_capture_snapshot_partial_memory(self):
        """
        WHY: Тест когда есть только недавняя история (< 6 месяцев).
        
        Scenario: Только 30 дней истории
        Expected: 1w и 1m заполнены, 6m берёт всё что есть
        """
        # Arrange
        book = LocalOrderBook(symbol="BTCUSDT")
        collector = FeatureCollector(order_book=book)
        memory = HistoricalMemory()
        
        # Заполняем только 30 дней
        base_time = datetime(2025, 1, 1)
        for i in range(30):
            timestamp = base_time + timedelta(days=i)
            whale_cvd = 5000.0 + (i * 50.0)
            memory.update_history(timestamp, whale_cvd, 0.0, Decimal("60000"))
        
        # Act
        snapshot = collector.capture_snapshot(historical_memory=memory)
        
        # Assert
        # WHY: 1w работает (есть 7 дней)
        assert snapshot.whale_cvd_trend_1w is not None
        
        # WHY: 1m работает (есть 30 дней)
        assert snapshot.whale_cvd_trend_1m is not None
        
        # WHY: 6m берёт всё что есть (30 дней)
        assert snapshot.whale_cvd_trend_6m is not None
        
        # WHY: 6m и 1m должны быть примерно равны (оба берут ~30 дней)
        assert abs(snapshot.whale_cvd_trend_6m - snapshot.whale_cvd_trend_1m) < 100


# === RUN TESTS ===
if __name__ == "__main__":
    pytest.main([__file__, "-v"])
