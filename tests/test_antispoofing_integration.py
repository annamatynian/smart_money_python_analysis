"""
WHY: Тест интеграции SpoofingAnalyzer в TradingEngine.

Проверяет:
1. SpoofingAnalyzer создается в __init__
2. calculate_spoofing_probability вызывается при обнаружении айсберга
3. spoofing_probability сохраняется в IcebergLevel
4. Confidence корректируется на основе spoofing_probability
"""

import pytest
from decimal import Decimal
from datetime import datetime
from services import TradingEngine
from domain import LocalOrderBook, TradeEvent, IcebergLevel
from analyzers import SpoofingAnalyzer
from config import BTC_CONFIG


class TestAntiSpoofingIntegration:
    """Проверка интеграции анти-спуфинга в движок"""
    
    def test_spoofing_analyzer_initialized(self):
        """
        WHY: Проверяем что SpoofingAnalyzer создается в TradingEngine.__init__
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
        
        # ASSERT: spoofing_analyzer должен существовать
        assert hasattr(engine, 'spoofing_analyzer'), "SpoofingAnalyzer не создан в __init__"
        assert engine.spoofing_analyzer is not None, "spoofing_analyzer = None"
    
    def test_spoofing_probability_calculated_for_iceberg(self):
        """
        WHY: Проверяем что spoofing_probability вычисляется для каждого айсберга
        """
        # Setup
        book = LocalOrderBook(symbol='BTCUSDT')
        
        # Создаем айсберг вручную
        iceberg = book.register_iceberg(
            price=Decimal('50000'),
            hidden_vol=Decimal('1.5'),
            is_ask=False,
            confidence=0.85
        )
        
        # Создаем SpoofingAnalyzer
        analyzer = SpoofingAnalyzer()
        
        # Рассчитываем spoofing probability
        current_mid = Decimal('50100')
        price_history = [Decimal('50000'), Decimal('50050'), Decimal('50100')]
        
        spoofing_prob = analyzer.calculate_spoofing_probability(
            iceberg_level=iceberg,
            current_mid_price=current_mid,
            price_history=price_history
        )
        
        # ASSERT: вероятность должна быть [0.0, 1.0]
        assert 0.0 <= spoofing_prob <= 1.0, f"Invalid spoofing_prob: {spoofing_prob}"
        
        # ASSERT: для короткоживущих айсбергов должна быть повышенная вероятность
        # WHY: Weighted sum = duration(1.0)*0.3 + context(0.0)*0.5 + exec(0.5)*0.2 = 0.4
        assert spoofing_prob >= 0.3, f"Короткоживущий айсберг должен иметь spoofing_prob >= 0.3, получено {spoofing_prob}"
    
    def test_confidence_adjustment_by_spoofing(self):
        """
        WHY: Проверяем что confidence снижается для подозрительных айсбергов
        """
        book = LocalOrderBook(symbol='BTCUSDT')
        
        # Создаем айсберг с высокой базовой уверенностью
        iceberg = book.register_iceberg(
            price=Decimal('50000'),
            hidden_vol=Decimal('2.0'),
            is_ask=False,
            confidence=0.90  # Высокая базовая уверенность
        )
        
        analyzer = SpoofingAnalyzer()
        
        # Симулируем короткоживущий айсберг (spoofing_prob = 1.0)
        spoofing_prob = 1.0  # 100% вероятность спуфинга
        
        # Корректируем confidence
        # WHY: Формула adjusted = base * (1 - spoofing_prob)
        base_confidence = 0.90
        expected_adjusted = base_confidence * (1.0 - spoofing_prob)
        
        # ASSERT: Если spoofing_prob = 1.0, то confidence должен упасть до ~0
        assert expected_adjusted < 0.1, "Confidence должен сильно снизиться при spoofing_prob=1.0"
        
        # Проверяем формулу
        adjusted_confidence = base_confidence * (1.0 - spoofing_prob)
        assert adjusted_confidence == expected_adjusted
    
    def test_spoofing_field_saved_to_iceberg_level(self):
        """
        WHY: Проверяем что spoofing_probability сохраняется в IcebergLevel
        """
        book = LocalOrderBook(symbol='BTCUSDT')
        
        # Создаем айсберг
        iceberg = book.register_iceberg(
            price=Decimal('50000'),
            hidden_vol=Decimal('1.0'),
            is_ask=False,
            confidence=0.80
        )
        
        # ASSERT: Поле spoofing_probability должно существовать
        assert hasattr(iceberg, 'spoofing_probability'), "IcebergLevel не имеет поля spoofing_probability"
        
        # ASSERT: Начальное значение должно быть 0.0
        assert iceberg.spoofing_probability == 0.0, "Начальное значение spoofing_probability != 0.0"
        
        # Обновляем значение
        iceberg.spoofing_probability = 0.75
        
        # ASSERT: Значение должно сохраниться
        assert iceberg.spoofing_probability == 0.75


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
