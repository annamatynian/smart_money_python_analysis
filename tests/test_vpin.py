"""
WHY: TDD для VPIN (Volume-Synchronized Probability of Informed Trading)

Теория: VPIN измеряет "токсичность" потока - вероятность того что
агрессоры (market orders) обладают информационным преимуществом.

Источник: Easley-O'Hara (2012), документ "Flow Toxicity" в проекте.
"""

import pytest
from decimal import Decimal
from datetime import datetime
from domain import VolumeBucket, TradeEvent


class TestVolumeBucket:
    """
    WHY: Базовый building block для VPIN.
    
    VolumeBucket накапливает объем покупок/продаж до достижения
    фиксированного размера корзины (например 10 BTC).
    """
    
    def test_bucket_creation(self):
        """WHY: Проверка инициализации пустой корзины"""
        bucket = VolumeBucket(
            bucket_size=Decimal("10.0"),
            symbol="BTCUSDT"
        )
        
        assert bucket.buy_volume == Decimal("0")
        assert bucket.sell_volume == Decimal("0")
        assert bucket.is_complete is False
        assert bucket.bucket_size == Decimal("10.0")
    
    def test_bucket_add_buy_trade(self):
        """WHY: Добавление покупки (taker купил у maker)"""
        bucket = VolumeBucket(bucket_size=Decimal("10.0"), symbol="BTCUSDT")
        
        trade = TradeEvent(
            price=Decimal("95000"),
            quantity=Decimal("3.5"),
            is_buyer_maker=False,  # taker купил
            event_time=int(datetime.now().timestamp() * 1000)
        )
        
        bucket.add_trade(trade)
        
        assert bucket.buy_volume == Decimal("3.5")
        assert bucket.sell_volume == Decimal("0")
        assert bucket.is_complete is False
    
    def test_bucket_add_sell_trade(self):
        """WHY: Добавление продажи (taker продал maker'у)"""
        bucket = VolumeBucket(bucket_size=Decimal("10.0"), symbol="BTCUSDT")
        
        trade = TradeEvent(
            price=Decimal("95000"),
            quantity=Decimal("2.0"),
            is_buyer_maker=True,  # taker продал
            event_time=int(datetime.now().timestamp() * 1000)
        )
        
        bucket.add_trade(trade)
        
        assert bucket.buy_volume == Decimal("0")
        assert bucket.sell_volume == Decimal("2.0")
        assert bucket.is_complete is False
    
    def test_bucket_completion(self):
        """WHY: Корзина закрывается при достижении bucket_size"""
        bucket = VolumeBucket(bucket_size=Decimal("10.0"), symbol="BTCUSDT")
        
        # Добавляем 6 BTC покупок
        trade1 = TradeEvent(
            price=Decimal("95000"),
            quantity=Decimal("6.0"),
            is_buyer_maker=False,
            event_time=int(datetime.now().timestamp() * 1000)
        )
        bucket.add_trade(trade1)
        
        # Добавляем 4 BTC продаж -> Total = 10 BTC
        trade2 = TradeEvent(
            price=Decimal("95000"),
            quantity=Decimal("4.0"),
            is_buyer_maker=True,
            event_time=int(datetime.now().timestamp() * 1000)
        )
        bucket.add_trade(trade2)
        
        assert bucket.is_complete is True
        assert bucket.total_volume() == Decimal("10.0")
    
    def test_bucket_overflow_protection(self):
        """WHY: Не допускаем переполнения корзины"""
        bucket = VolumeBucket(bucket_size=Decimal("10.0"), symbol="BTCUSDT")
        
        # Наполняем до 8 BTC
        trade1 = TradeEvent(
            price=Decimal("95000"),
            quantity=Decimal("8.0"),
            is_buyer_maker=False,
            event_time=int(datetime.now().timestamp() * 1000)
        )
        bucket.add_trade(trade1)
        
        # Пытаемся добавить 5 BTC -> должно добавиться только 2 BTC
        trade2 = TradeEvent(
            price=Decimal("95000"),
            quantity=Decimal("5.0"),
            is_buyer_maker=True,
            event_time=int(datetime.now().timestamp() * 1000)
        )
        
        overflow = bucket.add_trade(trade2)
        
        assert bucket.is_complete is True
        assert bucket.sell_volume == Decimal("2.0")  # Только 2 BTC влезло
        assert overflow == Decimal("3.0")  # 3 BTC перелив
    
    def test_bucket_imbalance_calculation(self):
        """WHY: |Buy - Sell| для VPIN формулы"""
        bucket = VolumeBucket(bucket_size=Decimal("10.0"), symbol="BTCUSDT")
        
        trade1 = TradeEvent(
            price=Decimal("95000"),
            quantity=Decimal("7.0"),
            is_buyer_maker=False,  # 7 BTC покупок
            event_time=int(datetime.now().timestamp() * 1000)
        )
        bucket.add_trade(trade1)
        
        trade2 = TradeEvent(
            price=Decimal("95000"),
            quantity=Decimal("3.0"),
            is_buyer_maker=True,  # 3 BTC продаж
            event_time=int(datetime.now().timestamp() * 1000)
        )
        bucket.add_trade(trade2)
        
        imbalance = bucket.calculate_imbalance()
        
        assert imbalance == Decimal("4.0")  # |7 - 3| = 4
        assert bucket.is_complete is True


class TestVPINCalculation:
    """
    WHY: Тесты для самого VPIN индекса.
    
    Формула: VPIN = Σ|Buy_i - Sell_i| / (n * bucket_size)
    где n = количество корзин в окне (обычно 50)
    """
    
    def test_vpin_with_balanced_flow(self):
        """WHY: При сбалансированном потоке VPIN → 0"""
        # TODO: Реализуется в FlowToxicityAnalyzer
        # Здесь просто проверим что bucket API позволяет собрать данные
        
        buckets = []
        for _ in range(5):
            bucket = VolumeBucket(bucket_size=Decimal("10.0"), symbol="BTCUSDT")
            
            # 5 BTC покупок, 5 BTC продаж = баланс
            bucket.add_trade(TradeEvent(
                price=Decimal("95000"),
                quantity=Decimal("5.0"),
                is_buyer_maker=False,
                event_time=int(datetime.now().timestamp() * 1000)
            ))
            bucket.add_trade(TradeEvent(
                price=Decimal("95000"),
                quantity=Decimal("5.0"),
                is_buyer_maker=True,
                event_time=int(datetime.now().timestamp() * 1000)
            ))
            
            buckets.append(bucket)
        
        # Все корзины должны быть сбалансированы
        for bucket in buckets:
            assert bucket.calculate_imbalance() == Decimal("0")
    
    def test_vpin_with_toxic_flow(self):
        """WHY: При одностороннем потоке VPIN → 1.0"""
        # TODO: Аналогично - проверяем building blocks
        
        bucket = VolumeBucket(bucket_size=Decimal("10.0"), symbol="BTCUSDT")
        
        # Только покупки (агрессивные тейкеры)
        bucket.add_trade(TradeEvent(
            price=Decimal("95000"),
            quantity=Decimal("10.0"),
            is_buyer_maker=False,
            event_time=int(datetime.now().timestamp() * 1000)
        ))
        
        # Дисбаланс = 10 BTC (максимальная токсичность)
        assert bucket.calculate_imbalance() == Decimal("10.0")
        assert bucket.is_complete is True
