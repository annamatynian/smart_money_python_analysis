import unittest
from decimal import Decimal
# Исправленный импорт: используем актуальные классы из domain.py
from domain import LocalOrderBook, TradeEvent, IcebergDetectionResult
from analyzers import IcebergAnalyzer
from config import BTC_CONFIG

class TestIcebergRobustness(unittest.TestCase):
    
    def setUp(self):
        # WHY: Используем новую архитектуру с IcebergAnalyzer
        self.book = LocalOrderBook(symbol="BTCUSDT")
        self.analyzer = IcebergAnalyzer(BTC_CONFIG)
        
        # Имитируем стакан: цена 60000, объем 10.0
        self.book.bids = {Decimal("60000"): Decimal("10.0")}
        self.book.asks = {}  # Инициализируем пустой ask

    def test_normal_trade_no_iceberg(self):
        """Тест 1: Обычная сделка, объемы совпадают идеально"""
        trade = TradeEvent(
            price=Decimal("60000"), 
            quantity=Decimal("2.0"), 
            is_buyer_maker=True, 
            event_time=1000
        )
        
        # WHY: Используем новый API - analyzer.analyze()
        visible_before = Decimal("10.0")
        result = self.analyzer.analyze(self.book, trade, visible_before)
        
        # Обновляем стакан: 10.0 - 2.0 = 8.0
        self.book.bids[Decimal("60000")] = Decimal("8.0")
        
        # Проверяем: нет скрытого объема (2.0 <= 10.0)
        self.assertIsNone(result, "Обычная сделка не должна детектироваться как айсберг")

    def test_liquidity_added_during_trade(self):
        """Тест 2: Кто-то добавил ликвидность (стакан вырос вопреки продаже)"""
        trade = TradeEvent(
            price=Decimal("60000"), 
            quantity=Decimal("2.0"), 
            is_buyer_maker=True, 
            event_time=1000
        )
        
        # Было 10.0, продали 2.0, но кто-то добавил 5.0 → стало 13.0
        # Это НЕ айсберг, так как visible_after > visible_before
        visible_before = Decimal("10.0")
        result = self.analyzer.analyze(self.book, trade, visible_before)
        
        # WHY: Сделка 2.0 меньше visible_before 10.0 → нет айсберга
        self.assertIsNone(result, "Рост стакана не должен считаться скрытым айсбергом")

    def test_hidden_volume_detection(self):
        """Тест 3: Детекция скрытого объема"""
        # WHY: Случай, когда trade.quantity > visible_before
        trade = TradeEvent(
            price=Decimal("60000"),
            quantity=Decimal("15.0"),  # Больше чем visible (10.0)
            is_buyer_maker=True,
            event_time=1000
        )
        
        visible_before = Decimal("10.0")
        result = self.analyzer.analyze(self.book, trade, visible_before)
        
        # Проверяем: должен обнаружить hidden_volume = 15.0 - 10.0 = 5.0
        self.assertIsNotNone(result, "Должен обнаружить айсберг")
        self.assertEqual(result.detected_hidden_volume, Decimal("5.0"), 
                        "Скрытый объем должен быть 5.0")

if __name__ == '__main__':
    unittest.main()