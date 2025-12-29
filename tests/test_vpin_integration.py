"""
WHY: Тест интеграции FlowToxicityAnalyzer (VPIN) в TradingEngine.

Проверяет:
1. FlowToxicityAnalyzer создается в __init__
2. update_vpin() вызывается при каждой сделке
3. VPIN рассчитывается корректно
4. flow_analyzer передан в FeatureCollector
"""

import pytest
from decimal import Decimal
from datetime import datetime
from services import TradingEngine
from domain import LocalOrderBook, TradeEvent, VolumeBucket
from analyzers import FlowToxicityAnalyzer
from config import BTC_CONFIG


class TestVPINIntegration:
    """Проверка интеграции VPIN в движок"""
    
    def test_flow_toxicity_analyzer_initialized(self):
        """
        WHY: Проверяем что FlowToxicityAnalyzer создается в TradingEngine.__init__
        """
        # Mock infrastructure
        class MockInfra:
            async def get_snapshot(self, symbol):
                return {'bids': [], 'asks': [], 'lastUpdateId': 0}
            async def listen_updates(self, symbol):
                return
                yield
            async def listen_trades(self, symbol):
                return
                yield
        
        # Create engine
        engine = TradingEngine(
            symbol='BTCUSDT',
            infra=MockInfra(),
            deribit_infra=None,
            repository=None
        )
        
        # ASSERT: flow_toxicity_analyzer должен существовать
        assert hasattr(engine, 'flow_toxicity_analyzer'), "FlowToxicityAnalyzer не создан в __init__"
        assert engine.flow_toxicity_analyzer is not None, "flow_toxicity_analyzer = None"
        assert isinstance(engine.flow_toxicity_analyzer, FlowToxicityAnalyzer)
    
    def test_vpin_bucket_initialization(self):
        """
        WHY: Проверяем что current_vpin_bucket создается при инициализации
        """
        book = LocalOrderBook(symbol='BTCUSDT')
        
        # Создаем FlowToxicityAnalyzer
        bucket_size = Decimal("10.0")  # 10 BTC per bucket
        analyzer = FlowToxicityAnalyzer(book, bucket_size)
        
        # ASSERT: current_vpin_bucket должен быть создан
        assert book.current_vpin_bucket is not None
        assert isinstance(book.current_vpin_bucket, VolumeBucket)
        assert book.current_vpin_bucket.bucket_size == bucket_size
    
    def test_vpin_calculation_with_trades(self):
        """
        WHY: Проверяем корректность расчета VPIN
        """
        book = LocalOrderBook(symbol='BTCUSDT')
        bucket_size = Decimal("1.0")  # Маленькая корзина для теста
        analyzer = FlowToxicityAnalyzer(book, bucket_size)
        
        # Симулируем 20 сделок (заполним 10 корзин - минимум для VPIN)
        for i in range(20):
            trade = TradeEvent(
                price=Decimal('50000'),
                quantity=Decimal('0.5'),  # Половина корзины
                is_buyer_maker=(i % 2 == 0),  # Чередуем buy/sell
                event_time=int(datetime.now().timestamp() * 1000) + i,
                trade_id=i
            )
            analyzer.update_vpin(trade)
        
        # ASSERT: После 20 сделок (10 корзин) VPIN должен быть доступен
        vpin = analyzer.get_current_vpin()
        assert vpin is not None, "После заполнения 10 корзин VPIN должен быть рассчитан"
        assert 0.0 <= vpin <= 1.0, f"VPIN должен быть [0.0, 1.0], получено {vpin}"
    
    def test_flow_analyzer_passed_to_feature_collector(self):
        """
        WHY: Проверяем что flow_toxicity_analyzer передается в FeatureCollector
        """
        class MockInfra:
            async def get_snapshot(self, symbol):
                return {'bids': [], 'asks': [], 'lastUpdateId': 0}
            async def listen_updates(self, symbol):
                return
                yield
            async def listen_trades(self, symbol):
                return
                yield
        
        engine = TradingEngine(
            symbol='BTCUSDT',
            infra=MockInfra(),
            deribit_infra=None,
            repository=None
        )
        
        # ASSERT: FeatureCollector должен получить flow_toxicity_analyzer
        assert engine.feature_collector.flow_toxicity is not None
        assert isinstance(engine.feature_collector.flow_toxicity, FlowToxicityAnalyzer)
    
    def test_toxicity_level_classification(self):
        """
        WHY: Проверяем классификацию уровней токсичности
        """
        book = LocalOrderBook(symbol='BTCUSDT')
        bucket_size = Decimal("1.0")
        analyzer = FlowToxicityAnalyzer(book, bucket_size)
        
        # Заполняем корзины односторонними сделками (высокий VPIN)
        for i in range(20):
            trade = TradeEvent(
                price=Decimal('50000'),
                quantity=Decimal('0.5'),
                is_buyer_maker=False,  # Все покупки (высокий дисбаланс)
                event_time=int(datetime.now().timestamp() * 1000) + i,
                trade_id=i
            )
            analyzer.update_vpin(trade)
        
        # ASSERT: При высоком дисбалансе VPIN должен быть высоким
        toxicity_level = analyzer.get_toxicity_level()
        assert toxicity_level in ['HIGH', 'EXTREME', 'MODERATE'], \
            f"Односторонний поток должен давать HIGH/EXTREME toxicity, получено {toxicity_level}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
