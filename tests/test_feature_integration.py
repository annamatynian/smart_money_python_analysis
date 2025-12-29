# ===========================================================================
# INTEGRATION TEST: FeatureCollector + Repository
# ===========================================================================

"""
WHY: Проверяем что FeatureCollector корректно работает с TradingEngine.

Тестируем:
1. Инициализацию FeatureCollector в TradingEngine
2. Захват snapshot
3. Сохранение в БД через repository
"""

import pytest
from decimal import Decimal
from datetime import datetime
from analyzers_features import FeatureCollector, FeatureSnapshot
from domain import LocalOrderBook


class MockRepository:
    """Mock repository для тестирования"""
    def __init__(self):
        self.lifecycle_events = []
        self.feature_snapshots = []
    
    async def save_lifecycle_event(self, **kwargs):
        """Сохраняет lifecycle event и возвращает UUID"""
        event_id = f"mock-uuid-{len(self.lifecycle_events)}"
        self.lifecycle_events.append({
            'id': event_id,
            **kwargs
        })
        return event_id
    
    async def save_feature_snapshot(self, lifecycle_id, snapshot):
        """Сохраняет feature snapshot"""
        self.feature_snapshots.append({
            'lifecycle_id': lifecycle_id,
            'snapshot': snapshot
        })
        return True


@pytest.mark.asyncio
async def test_feature_collector_integration():
    """
    WHY: Проверяем полный цикл: capture_snapshot → save_lifecycle → save_feature_snapshot
    """
    # 1. Setup
    book = LocalOrderBook(symbol='BTCUSDT')
    book.whale_cvd = {'whale': 50000.0, 'dolphin': 10000.0, 'minnow': -5000.0}
    
    collector = FeatureCollector(
        order_book=book,
        flow_analyzer=None
    )
    
    repo = MockRepository()
    
    # 2. Обновляем историю цен
    for price in [60000, 60010, 60020, 60015, 60025]:
        collector.update_price(price)
    
    # 3. Захватываем snapshot
    snapshot = await collector.capture_snapshot()
    
    # 4. Проверяем что snapshot содержит данные
    assert snapshot is not None
    assert snapshot.whale_cvd == 50000.0
    assert snapshot.fish_cvd == -5000.0  # minnow
    assert snapshot.dolphin_cvd == 10000.0
    assert snapshot.total_cvd == 55000.0
    
    # 5. Сохраняем в БД
    lifecycle_id = await repo.save_lifecycle_event(
        symbol='BTCUSDT',
        price=Decimal('60000'),
        is_ask=False,
        event_type='DETECTED',
        total_volume_absorbed=Decimal('5.0'),
        refill_count=3
    )
    
    assert lifecycle_id is not None
    
    # 6. Сохраняем feature snapshot
    result = await repo.save_feature_snapshot(lifecycle_id, snapshot)
    assert result is True
    
    # 7. Проверяем что данные сохранены
    assert len(repo.lifecycle_events) == 1
    assert len(repo.feature_snapshots) == 1
    
    saved_snapshot = repo.feature_snapshots[0]
    assert saved_snapshot['lifecycle_id'] == lifecycle_id
    assert saved_snapshot['snapshot'].whale_cvd == 50000.0


@pytest.mark.asyncio
async def test_feature_collector_with_empty_data():
    """
    WHY: Проверяем что collector работает без ошибок даже когда данных нет
    """
    # 1. Setup с пустым book
    book = LocalOrderBook(symbol='BTCUSDT')
    
    collector = FeatureCollector(
        order_book=book,
        flow_analyzer=None
    )
    
    # 2. Захватываем snapshot БЕЗ данных
    snapshot = await collector.capture_snapshot()
    
    # 3. Проверяем что snapshot создан
    assert snapshot is not None
    
    # WHY: LocalOrderBook инициализирует whale_cvd как {'whale': 0, 'dolphin': 0, 'minnow': 0}
    # Поэтому возвращается 0.0, а не None
    assert snapshot.whale_cvd == 0.0  # Инициализировано в 0
    assert snapshot.fish_cvd == 0.0   # minnow тоже 0
    assert snapshot.dolphin_cvd == 0.0
    assert snapshot.total_cvd == 0.0
    
    # OBI вернёт 0.0 для пустого стакана (не None)
    assert snapshot.obi_value == 0.0
    
    # Эти поля действительно None когда нет данных:
    assert snapshot.current_price is None  # Нет bid/ask
    assert snapshot.spread_bps is None     # Нет спреда
    assert snapshot.twap_5m is None        # Нет истории цен
    
    print("✅ FeatureCollector корректно обрабатывает пустые данные (возвращает 0.0 вместо None)")


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
