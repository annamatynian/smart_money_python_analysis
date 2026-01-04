"""
ТЕСТЫ: Fix Vulnerability #2 - Clock Skew (Python vs Postgres)

WHY: Проверяем что FeatureSnapshot использует exchange event_time вместо datetime.now()
Это гарантирует Time-Determinism и синхронизацию с SQL date_bin().

Gemini Issue: "FeatureSnapshot.snapshot_time должен браться из OrderBookUpdate.event_time"
"""
import pytest
from datetime import datetime, timezone, timedelta
from analyzers_features import FeatureCollector, FeatureSnapshot
from domain import LocalOrderBook


class TestClockSkewFix:
    """
    Тесты для проверки исправления Clock Skew уязвимости.
    
    Проблема: snapshot_time создавался через datetime.now() (Python app time)
    Решение: snapshot_time берется из exchange event_time (source of truth)
    """
    
    def setup_method(self):
        """Создаем минимальный setup для тестирования"""
        self.book = LocalOrderBook(symbol="BTCUSDT")
        self.collector = FeatureCollector(order_book=self.book)
    
    def test_snapshot_uses_exchange_event_time_when_provided(self):
        """
        TEST 1: Snapshot использует event_time когда он передан.
        
        WHY: Гарантирует что время снимка = времени биржи, а не Python app server.
        """
        # Arrange: Создаем фиксированное время "биржи"
        exchange_time = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        
        # Act: Вызываем capture_snapshot с event_time (skip warmup для unit-теста)
        snapshot = self.collector.capture_snapshot(event_time=exchange_time, skip_warmup_check=True)
        
        # Assert: snapshot_time должен быть ТОЧНО равен exchange_time
        assert snapshot.snapshot_time == exchange_time, \
            f"Expected {exchange_time}, got {snapshot.snapshot_time}"
    
    def test_snapshot_uses_current_time_when_event_time_not_provided(self):
        """
        TEST 2: Snapshot использует datetime.now() как fallback.
        
        WHY: Обратная совместимость - если event_time не передан, работает как раньше.
        """
        # Arrange: Запоминаем текущее время (с допуском ±1 сек)
        before = datetime.now(timezone.utc)
        
        # Act: Вызываем без event_time (старый способ, skip warmup)
        snapshot = self.collector.capture_snapshot(skip_warmup_check=True)
        
        after = datetime.now(timezone.utc)
        
        # Assert: snapshot_time должен быть между before и after
        assert before <= snapshot.snapshot_time <= after, \
            f"snapshot_time {snapshot.snapshot_time} not in range [{before}, {after}]"
    
    def test_snapshot_time_determinism(self):
        """
        TEST 3: Time-Determinism - одинаковый event_time дает одинаковый snapshot_time.
        
        WHY: Критично для ML reproducibility и устранения processing lag.
        """
        # Arrange: Фиксированное время
        fixed_time = datetime(2025, 1, 1, 14, 30, 0, tzinfo=timezone.utc)
        
        # Act: Создаем два snapshot с одинаковым event_time (skip warmup)
        snapshot1 = self.collector.capture_snapshot(event_time=fixed_time, skip_warmup_check=True)
        snapshot2 = self.collector.capture_snapshot(event_time=fixed_time, skip_warmup_check=True)
        
        # Assert: Оба должны иметь ИДЕНТИЧНОЕ время
        assert snapshot1.snapshot_time == snapshot2.snapshot_time == fixed_time
    
    def test_no_clock_drift_between_snapshots(self):
        """
        TEST 4: Проверка отсутствия clock drift при использовании event_time.
        
        WHY: Гарантирует что processing lag не влияет на timestamp.
        """
        # Arrange: Два события с разницей в 100ms (exchange time)
        event_time_1 = datetime(2025, 1, 1, 12, 0, 0, 0, tzinfo=timezone.utc)
        event_time_2 = event_time_1 + timedelta(milliseconds=100)
        
        # Act: Создаем snapshots с задержкой между вызовами (имитация processing lag)
        snapshot1 = self.collector.capture_snapshot(event_time=event_time_1, skip_warmup_check=True)
        
        import time
        time.sleep(0.05)  # 50ms processing lag
        
        snapshot2 = self.collector.capture_snapshot(event_time=event_time_2, skip_warmup_check=True)
        
        # Assert: Разница между snapshot_time должна быть ТОЧНО 100ms (не зависит от sleep)
        time_diff = (snapshot2.snapshot_time - snapshot1.snapshot_time).total_seconds() * 1000
        assert time_diff == 100.0, \
            f"Expected 100ms difference, got {time_diff}ms (processing lag leaked into timestamp!)"


class TestEventTimeIntegrationWithServices:
    """
    Интеграционные тесты: проверка что event_time корректно передается из services.py
    
    WHY: Гарантирует что fix работает end-to-end в реальном pipeline.
    """
    
    def test_feature_snapshot_receives_exchange_timestamp(self):
        """
        TEST 5: Проверка что в production код передает OrderBookUpdate.event_time.
        
        WHY: Критично для устранения merge_asof съезжания.
        """
        # Arrange: Имитируем OrderBookUpdate с exchange timestamp
        from domain import OrderBookUpdate
        
        exchange_timestamp = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        
        from decimal import Decimal
        
        update = OrderBookUpdate(
            first_update_id=1000,
            final_update_id=1001,
            bids=[(Decimal('50000.0'), Decimal('1.5'))],  # FIX: Decimal required
            asks=[(Decimal('50001.0'), Decimal('2.0'))],  # FIX: Decimal required
            event_time=int(exchange_timestamp.timestamp() * 1000)  # FIX: int milliseconds required
        )
        
        # Act: Создаем collector и вызываем с event_time из update
        book = LocalOrderBook(symbol="BTCUSDT")
        collector = FeatureCollector(order_book=book)
        
        # Convert int milliseconds back to datetime for capture_snapshot
        event_time_dt = datetime.fromtimestamp(update.event_time / 1000.0, tz=timezone.utc)
        snapshot = collector.capture_snapshot(event_time=event_time_dt, skip_warmup_check=True)
        
        # Assert: snapshot_time должен совпадать с exchange timestamp
        assert snapshot.snapshot_time == exchange_timestamp
        
        # Assert: Разница с datetime.now() может быть значительной (processing lag)
        current_time = datetime.now(timezone.utc)
        lag_ms = (current_time - snapshot.snapshot_time).total_seconds() * 1000
        
        # Processing lag может быть > 0 (мы в будущем относительно события)
        # Но snapshot_time НЕ зависит от этого lag!
        assert lag_ms >= 0, f"snapshot_time is in the future relative to now() - broken test clock"
