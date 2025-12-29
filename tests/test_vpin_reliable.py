"""
Tests for VPIN Reliable Check
WHY: Gemini рекомендация - активировать _is_vpin_reliable() в основном цикле
"""
import pytest
from decimal import Decimal
from datetime import datetime
from domain import LocalOrderBook, TradeEvent, VolumeBucket
from analyzers import FlowToxicityAnalyzer
from config import get_config


class TestVPINReliableCheck:
    """Tests for VPIN reliability filter"""
    
    def test_vpin_analyzer_has_reliable_check(self):
        """
        ПРОВЕРКА: FlowToxicityAnalyzer имеет метод _is_vpin_reliable()
        
        WHY: Фильтрует "флэтовые" сигналы где VPIN шумный
        """
        book = LocalOrderBook(symbol='BTCUSDT')
        config = get_config('BTCUSDT')
        analyzer = FlowToxicityAnalyzer(book=book, bucket_size=Decimal('10'))
        
        # Проверяем что метод существует
        assert hasattr(analyzer, '_is_vpin_reliable')
        assert callable(analyzer._is_vpin_reliable)
    
    def test_vpin_unreliable_with_few_buckets(self):
        """
        ПРОВЕРКА: VPIN unreliable если корзин < 10
        
        WHY: Документ "Анализ смарт-мани" - минимум 10 корзин для валидности
        """
        book = LocalOrderBook(symbol='BTCUSDT')
        analyzer = FlowToxicityAnalyzer(book=book, bucket_size=Decimal('10'))
        
        # Добавляем только 5 корзин
        for i in range(5):
            bucket = VolumeBucket(bucket_size=Decimal('10'), symbol='BTCUSDT')
            bucket.buy_volume = Decimal('6')
            bucket.sell_volume = Decimal('4')
            bucket.is_complete = True
            book.vpin_buckets.append(bucket)
        
        # VPIN unreliable
        assert not analyzer._is_vpin_reliable()
    
    def test_vpin_reliable_with_sufficient_buckets(self):
        """
        ПРОВЕРКА: VPIN reliable если корзин >= 10 и объёмы не нулевые
        """
        book = LocalOrderBook(symbol='BTCUSDT')
        analyzer = FlowToxicityAnalyzer(book=book, bucket_size=Decimal('10'))
        
        # Добавляем 15 корзин с реальным объёмом
        for i in range(15):
            bucket = VolumeBucket(bucket_size=Decimal('10'), symbol='BTCUSDT')
            bucket.buy_volume = Decimal('6')
            bucket.sell_volume = Decimal('4')
            bucket.is_complete = True
            book.vpin_buckets.append(bucket)
        
        # VPIN reliable
        assert analyzer._is_vpin_reliable()
    
    def test_vpin_unreliable_with_flat_market(self):
        """
        ПРОВЕРКА: VPIN unreliable в "флэте" (Buy ≈ Sell во всех корзинах)
        
        WHY: Высокий VPIN в флэте = шум, не токсичность
        """
        book = LocalOrderBook(symbol='BTCUSDT')
        analyzer = FlowToxicityAnalyzer(book=book, bucket_size=Decimal('10'))
        
        # Добавляем 15 корзин с ИДЕАЛЬНЫМ балансом (Buy = Sell)
        for i in range(15):
            bucket = VolumeBucket(bucket_size=Decimal('10'), symbol='BTCUSDT')
            bucket.buy_volume = Decimal('5.0')  # Точно 50%
            bucket.sell_volume = Decimal('5.0')  # Точно 50%
            bucket.is_complete = True
            book.vpin_buckets.append(bucket)
        
        # VPIN unreliable (нет дисбаланса = нет информации)
        assert not analyzer._is_vpin_reliable()
    
    def test_update_vpin_returns_none_if_unreliable(self):
        """
        ПРОВЕРКА: update_vpin() возвращает None если VPIN unreliable
        
        WHY: Предотвращает использование шумных сигналов в торговых решениях
        """
        book = LocalOrderBook(symbol='BTCUSDT')
        analyzer = FlowToxicityAnalyzer(book=book, bucket_size=Decimal('10'))
        
        # Добавляем только 3 корзины (unreliable)
        for i in range(3):
            bucket = VolumeBucket(bucket_size=Decimal('10'), symbol='BTCUSDT')
            bucket.buy_volume = Decimal('6')
            bucket.sell_volume = Decimal('4')
            bucket.is_complete = True
            book.vpin_buckets.append(bucket)
        
        # Создаём trade
        trade = TradeEvent(
            price=Decimal('60000'),
            quantity=Decimal('0.5'),
            is_buyer_maker=False,
            event_time=1000
        )
        
        # update_vpin() должен вернуть None
        vpin = analyzer.update_vpin(trade)
        assert vpin is None
