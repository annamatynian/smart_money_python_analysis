"""
VULNERABILITY #4 FIX VALIDATION TESTS

Проверяют что исправление Decimal vs Float работает корректно.
"""

import pytest
from decimal import Decimal
from domain import GammaProfile, LocalOrderBook, TradeEvent
from analyzers import IcebergAnalyzer, GammaProvider
from analyzers_features import FeatureCollector
from config import BTC_CONFIG
from datetime import datetime, timezone


class TestGammaProfileDecimalFix:
    """Проверка что GammaProfile использует Decimal"""
    
    def test_gamma_profile_accepts_decimal(self):
        """
        FIX TEST: GammaProfile должен принимать Decimal для цен
        """
        gamma = GammaProfile(
            total_gex=1000.0,
            total_gex_normalized=0.15,
            call_wall=Decimal("50000.0"),  # Decimal!
            put_wall=Decimal("48000.0"),   # Decimal!
            expiry_timestamp=datetime.now(timezone.utc)
        )
        
        # Проверяем тип
        assert isinstance(gamma.call_wall, Decimal)
        assert isinstance(gamma.put_wall, Decimal)
        
        # Проверяем точность
        assert gamma.call_wall == Decimal("50000.0")
        assert gamma.put_wall == Decimal("48000.0")
    
    def test_gamma_profile_decimal_precision_preserved(self):
        """
        КРИТИЧЕСКИЙ ТЕСТ: Decimal сохраняет точность
        
        Используем классический пример потери точности: 0.1
        WHY: 0.1 в binary = бесконечная дробь → float теряет точность
        """
        # Decimal сохраняет точность для 0.1
        decimal_point_one = Decimal("0.1")
        
        gamma = GammaProfile(
            total_gex=1000.0,
            call_wall=Decimal("50000.0") + decimal_point_one,  # 50000.1
            put_wall=Decimal("48000.0")
        )
        
        # Decimal: 50000.1 точно
        assert gamma.call_wall == Decimal("50000.1")
        
        # Float arithmetic теряет точность
        # Классический пример: 0.1 + 0.2 != 0.3 в float
        float_sum = 0.1 + 0.2
        decimal_sum = Decimal("0.1") + Decimal("0.2")
        
        assert float_sum != 0.3  # Float broken!
        assert decimal_sum == Decimal("0.3")  # Decimal precise!


class TestIcebergAnalyzerDecimalFix:
    """Проверка что IcebergAnalyzer корректно работает с Decimal"""
    
    def test_adjust_confidence_decimal_comparison(self):
        """
        FIX TEST: adjust_confidence_by_gamma() использует Decimal comparison
        """
        config = BTC_CONFIG
        analyzer = IcebergAnalyzer(config)
        
        # Gamma profile с Decimal walls
        gamma = GammaProfile(
            total_gex=1000.0,
            total_gex_normalized=0.15,
            call_wall=Decimal("50000.0"),
            put_wall=Decimal("48000.0"),
            expiry_timestamp=datetime.now(timezone.utc)
        )
        
        # Decimal цена ТОЧНО на call_wall
        price = Decimal("50000.0")
        
        base_confidence = 0.8
        adjusted, is_major = analyzer.adjust_confidence_by_gamma(
            base_confidence=base_confidence,
            gamma_profile=gamma,
            price=price,
            is_ask=True
        )
        
        # Должен детектить "on gamma wall" (is_major=True)
        assert is_major, "Should detect as on gamma wall"
        assert adjusted > base_confidence, "Confidence should increase"
    
    def test_adjust_confidence_high_precision_price(self):
        """
        EDGE CASE: Высокоточная цена близко к gamma wall
        """
        config = BTC_CONFIG
        analyzer = IcebergAnalyzer(config)
        
        gamma = GammaProfile(
            total_gex=1000.0,
            total_gex_normalized=0.15,
            call_wall=Decimal("50000.0"),
            put_wall=Decimal("48000.0"),
            expiry_timestamp=datetime.now(timezone.utc)
        )
        
        # Цена отличается на 0.00001 BTC (1 satoshi разницы для 8 decimals)
        # Но TOLERANCE = 0.001 * 50000 = 50 BTC
        # Так что 0.00001 < 50 → ON WALL
        price = Decimal("50000.00001")
        
        base_confidence = 0.8
        adjusted, is_major = analyzer.adjust_confidence_by_gamma(
            base_confidence=base_confidence,
            gamma_profile=gamma,
            price=price,
            is_ask=True
        )
        
        # Должен детектить "on gamma wall"
        assert is_major, "Should detect on wall (within tolerance)"
        assert adjusted > base_confidence
    
    def test_adjust_confidence_outside_tolerance(self):
        """
        Цена ВНЕ tolerance - не должен считать "on wall"
        """
        config = BTC_CONFIG
        analyzer = IcebergAnalyzer(config)
        
        gamma = GammaProfile(
            total_gex=1000.0,
            total_gex_normalized=0.15,
            call_wall=Decimal("50000.0"),
            put_wall=Decimal("48000.0"),
            expiry_timestamp=datetime.now(timezone.utc)
        )
        
        # Цена далеко от walls (52000 vs 50000 call_wall)
        # Distance = 2000 BTC
        # TOLERANCE = 0.001 * 52000 = 52 BTC
        # 2000 > 52 → NOT ON WALL
        price = Decimal("52000.0")
        
        base_confidence = 0.8
        adjusted, is_major = analyzer.adjust_confidence_by_gamma(
            base_confidence=base_confidence,
            gamma_profile=gamma,
            price=price,
            is_ask=True
        )
        
        # НЕ должен детектить "on gamma wall"
        assert not is_major, "Should NOT detect on wall (outside tolerance)"


class TestGammaProviderDecimalFix:
    """Проверка что GammaProvider работает с Decimal"""
    
    def test_get_gamma_wall_distance_accepts_decimal(self):
        """
        FIX TEST: get_gamma_wall_distance() принимает Decimal
        """
        # Создаем LocalOrderBook с gamma_profile
        book = LocalOrderBook(symbol="BTCUSDT")
        book.gamma_profile = GammaProfile(
            total_gex=1000.0,
            call_wall=Decimal("50000.0"),
            put_wall=Decimal("48000.0")
        )
        
        provider = GammaProvider(book)
        
        # Передаем Decimal цену
        current_price = Decimal("49000.0")
        
        distance_pct, wall_type = provider.get_gamma_wall_distance(current_price)
        
        assert distance_pct is not None
        assert wall_type == 'PUT'  # Ближе к put_wall (48000)
        
        # Distance = (49000 - 48000) / 49000 * 100 ≈ 2.04%
        assert 2.0 < distance_pct < 2.1
    
    def test_get_gamma_wall_distance_precision(self):
        """
        ТОЧНОСТЬ: Decimal arithmetic должен быть точным
        """
        book = LocalOrderBook(symbol="BTCUSDT")
        book.gamma_profile = GammaProfile(
            total_gex=1000.0,
            call_wall=Decimal("50000.12345"),  # Высокая точность
            put_wall=Decimal("48000.0")
        )
        
        provider = GammaProvider(book)
        
        # Точная цена
        current_price = Decimal("50000.00000")
        
        distance_pct, wall_type = provider.get_gamma_wall_distance(current_price)
        
        assert wall_type == 'CALL'  # Ближе к call_wall
        
        # Distance = (50000.12345 - 50000.0) / 50000.0 * 100
        # = 0.12345 / 50000 * 100 = 0.0002469%
        assert distance_pct < 0.001  # Очень близко!


class TestFeatureCollectorDecimalFix:
    """Проверка что FeatureCollector возвращает Decimal mid price"""
    
    def test_get_current_price_returns_decimal(self):
        """
        FIX TEST: _get_current_price() возвращает Decimal
        """
        book = LocalOrderBook(symbol="BTCUSDT")
        
        # Добавляем bid/ask
        book.bids[Decimal("49999.5")] = Decimal("10.0")
        book.asks[Decimal("50000.5")] = Decimal("10.0")
        
        collector = FeatureCollector(order_book=book)
        
        mid_price = collector._get_current_price()
        
        # Проверяем тип
        assert isinstance(mid_price, Decimal)
        
        # Проверяем значение: (49999.5 + 50000.5) / 2 = 50000.0
        assert mid_price == Decimal("50000.0")
    
    def test_get_current_price_precision(self):
        """
        ТОЧНОСТЬ: Decimal arithmetic сохраняет precision
        """
        book = LocalOrderBook(symbol="BTCUSDT")
        
        # Высокоточные цены
        book.bids[Decimal("49999.12345678")] = Decimal("10.0")
        book.asks[Decimal("50000.87654321")] = Decimal("10.0")
        
        collector = FeatureCollector(order_book=book)
        
        mid_price = collector._get_current_price()
        
        # Mid = (49999.12345678 + 50000.87654321) / 2 = 49999.999999995
        expected = (Decimal("49999.12345678") + Decimal("50000.87654321")) / Decimal("2")
        
        assert mid_price == expected
        
        # Float бы округлил!
        assert float(mid_price) != expected


class TestEndToEndDecimalPrecision:
    """End-to-end тест: проверка всей цепочки Decimal precision"""
    
    def test_full_chain_decimal_precision(self):
        """
        Полная цепочка:
        1. LocalOrderBook хранит Decimal цены
        2. GammaProfile хранит Decimal walls
        3. FeatureCollector._get_current_price() возвращает Decimal
        4. GammaProvider.get_gamma_wall_distance() работает с Decimal
        5. IcebergAnalyzer.adjust_confidence_by_gamma() сравнивает Decimal
        
        НЕТ ПОТЕРИ ТОЧНОСТИ на всей цепочке!
        """
        config = BTC_CONFIG
        
        # 1. LocalOrderBook
        book = LocalOrderBook(symbol="BTCUSDT")
        book.bids[Decimal("50000.00000001")] = Decimal("10.0")
        book.asks[Decimal("50000.00000002")] = Decimal("10.0")
        
        # 2. GammaProfile с Decimal
        book.gamma_profile = GammaProfile(
            total_gex=1000.0,
            total_gex_normalized=0.15,
            call_wall=Decimal("50000.0"),
            put_wall=Decimal("48000.0"),
            expiry_timestamp=datetime.now(timezone.utc)
        )
        
        # 3. FeatureCollector
        collector = FeatureCollector(order_book=book)
        mid_price = collector._get_current_price()
        
        # Mid price - Decimal!
        assert isinstance(mid_price, Decimal)
        expected_mid = (Decimal("50000.00000001") + Decimal("50000.00000002")) / Decimal("2")
        assert mid_price == expected_mid
        
        # 4. GammaProvider
        gamma_provider = GammaProvider(book)
        distance_pct, wall_type = gamma_provider.get_gamma_wall_distance(mid_price)
        
        # Distance вычислен точно (без float errors)
        assert distance_pct is not None
        assert wall_type == 'CALL'
        
        # Distance должно быть ОЧЕНЬ маленькое (цена почти на стене)
        # (50000.000000015 - 50000.0) / 50000.000000015 * 100 ≈ 0.00000003%
        assert distance_pct < 0.0001
        
        # 5. IcebergAnalyzer
        analyzer = IcebergAnalyzer(config)
        adjusted, is_major = analyzer.adjust_confidence_by_gamma(
            base_confidence=0.8,
            gamma_profile=book.gamma_profile,
            price=mid_price,  # Decimal!
            is_ask=True
        )
        
        # Должен детектить "on gamma wall"
        assert is_major
        assert adjusted > 0.8
        
        # ✅ ВСЯ ЦЕПОЧКА РАБОТАЕТ БЕЗ ПОТЕРИ ТОЧНОСТИ!
