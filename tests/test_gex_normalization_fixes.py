"""
WHY: Тесты для GEMINI FIXES - GEX Normalization + Expiration Decay.

Проверяем:
1. GEX Non-Stationarity fix (нормализация по ADV)
2. Expiration Cliff fix (decay перед экспирацией)
3. Совместная работа обоих механизмов
"""

import pytest
from decimal import Decimal
from datetime import datetime, timedelta, timezone
from domain import GammaProfile, LocalOrderBook, TradeEvent
from analyzers import IcebergAnalyzer
from config import BTC_CONFIG


class TestGEXNormalization:
    """Проверяем, что используется normalized GEX вместо абсолютного."""
    
    def test_normalized_gex_above_threshold_triggers_bonus(self):
        """
        WHY: Проверяем, что normalized GEX > 0.1 (10% от ADV) активирует бонус.
        
        Scenario:
        - total_gex = 50M USD (абсолютное)
        - total_gex_normalized = 0.15 (15% от ADV) → ВЫШЕ порога
        - Ожидаем: confidence увеличен (>= base)
        """
        analyzer = IcebergAnalyzer(BTC_CONFIG)
        book = LocalOrderBook(symbol="BTCUSDT")
        
        # Создаем GammaProfile с НОРМАЛИЗОВАННЫМ GEX
        book.gamma_profile = GammaProfile(
            total_gex=50_000_000,  # Абсолютное значение (не используется)
            total_gex_normalized=0.15,  # 15% от ADV → ВЫШЕ порога 0.1
            call_wall=100000.0,
            put_wall=95000.0,
            timestamp=datetime.now(timezone.utc)
        )
        
        # WHY: Используем base_confidence = 0.5 для консистентности
        base_confidence = 0.5
        price = Decimal("100000")  # На gamma wall (call_wall)
        
        adjusted, is_major = analyzer.adjust_confidence_by_gamma(
            base_confidence=base_confidence,
            gamma_profile=book.gamma_profile,
            price=price,
            is_ask=True,  # Ask iceberg на call_wall
            vpin_score=None,
            cvd_divergence=None
        )
        
        # ASSERTION: Уверенность должна ВЫРАСТИ
        assert adjusted > base_confidence, \
            f"Normalized GEX = 0.15 (>0.1) должно повысить confidence, но {adjusted} <= {base_confidence}"
    
    def test_normalized_gex_below_threshold_no_bonus(self):
        """
        WHY: Normalized GEX < 0.1 НЕ должен давать бонус.
        
        Scenario:
        - total_gex_normalized = 0.05 (5% от ADV) → НИЖЕ порога
        - Ожидаем: confidence не изменен или изменен минимально
        """
        analyzer = IcebergAnalyzer(BTC_CONFIG)
        book = LocalOrderBook(symbol="BTCUSDT")
        
        book.gamma_profile = GammaProfile(
            total_gex=10_000_000,
            total_gex_normalized=0.05,  # 5% от ADV → НИЖЕ порога 0.1
            call_wall=100000.0,
            put_wall=95000.0,
            timestamp=datetime.now(timezone.utc)
        )
        
        # WHY: Используем base_confidence = 0.5 для консистентности
        base_confidence = 0.5
        price = Decimal("100000")  # На gamma wall
        
        adjusted, is_major = analyzer.adjust_confidence_by_gamma(
            base_confidence=base_confidence,
            gamma_profile=book.gamma_profile,
            price=price,
            is_ask=True,
            vpin_score=None,
            cvd_divergence=None
        )
        
        # ASSERTION: Уверенность НЕ должна сильно измениться
        # Допускаем небольшие изменения от других факторов, но не GEX бонус
        assert adjusted <= base_confidence * 1.1, \
            f"Normalized GEX = 0.05 (<0.1) не должно давать большой бонус, но {adjusted} > {base_confidence * 1.1}"


class TestExpirationDecay:
    """Проверяем decay влияния GEX перед экспирацией опционов."""
    
    def test_decay_at_2_hours_before_expiry(self):
        """
        WHY: За 2 часа до экспирации decay_factor = 1.0 (полное влияние).
        """
        analyzer = IcebergAnalyzer(BTC_CONFIG)
        book = LocalOrderBook(symbol="BTCUSDT")
        
        # Экспирация через 2 часа
        expiry = datetime.now(timezone.utc) + timedelta(hours=2)
        
        book.gamma_profile = GammaProfile(
            total_gex=100_000_000,
            total_gex_normalized=0.2,  # Выше порога
            call_wall=100000.0,
            put_wall=95000.0,
            timestamp=datetime.now(timezone.utc),
            expiry_timestamp=expiry
        )
        
        # WHY: Используем base_confidence = 0.5, чтобы после x1.8 бонуса не превысить cap (1.0)
        # 0.5 * 1.8 = 0.9 (< 1.0, не обрежется)
        base_confidence = 0.5
        price = Decimal("100000")
        
        adjusted_2h, _ = analyzer.adjust_confidence_by_gamma(
            base_confidence=base_confidence,
            gamma_profile=book.gamma_profile,
            price=price,
            is_ask=True,
            vpin_score=None,
            cvd_divergence=None
        )
        
        # Ожидаем: За 2 часа = полный бонус (decay_factor = 1.0)
        # Максимальный бонус для +GEX на wall = x1.8
        # 0.5 * 1.8 = 0.9
        expected_approx = base_confidence * 1.8  # 0.9
        
        assert adjusted_2h >= expected_approx * 0.95, \
            f"За 2 часа до экспирации должен быть почти полный бонус (~0.9), но {adjusted_2h} < {expected_approx * 0.95}"
    
    def test_decay_at_1_hour_before_expiry(self):
        """
        WHY: За 1 час до экспирации decay_factor = 0.5 (50% влияния).
        """
        analyzer = IcebergAnalyzer(BTC_CONFIG)
        book = LocalOrderBook(symbol="BTCUSDT")
        
        # Экспирация через 1 час
        expiry = datetime.now(timezone.utc) + timedelta(hours=1)
        
        book.gamma_profile = GammaProfile(
            total_gex=100_000_000,
            total_gex_normalized=0.2,
            call_wall=100000.0,
            put_wall=95000.0,
            timestamp=datetime.now(timezone.utc),
            expiry_timestamp=expiry
        )
        
        # WHY: Используем base_confidence = 0.5 для избежания cap
        base_confidence = 0.5
        price = Decimal("100000")
        
        adjusted_1h, _ = analyzer.adjust_confidence_by_gamma(
            base_confidence=base_confidence,
            gamma_profile=book.gamma_profile,
            price=price,
            is_ask=True,
            vpin_score=None,
            cvd_divergence=None
        )
        
        # Ожидаем: За 1 час = 50% бонуса (decay_factor = 0.5)
        # Бонус = 0.8 * 0.5 = 0.4
        # Итого: confidence * (1.0 + 0.4) = 0.5 * 1.4 = 0.7
        expected_approx = base_confidence * 1.4  # 0.7
        
        # Должно быть меньше полного бонуса (0.9) но больше базового (0.5)
        assert adjusted_1h < base_confidence * 1.8 * 0.95, \
            f"За 1 час до экспирации бонус должен уменьшиться (decay=0.5), но {adjusted_1h} >= {base_confidence * 1.8 * 0.95}"
        
        assert adjusted_1h > base_confidence * 1.2, \
            f"За 1 час до экспирации все еще должен быть бонус (~0.7), но {adjusted_1h} <= {base_confidence * 1.2}"
    
    def test_decay_at_30_min_before_expiry(self):
        """
        WHY: За 30 минут до экспирации decay_factor = 0.25 (25% влияния).
        """
        analyzer = IcebergAnalyzer(BTC_CONFIG)
        book = LocalOrderBook(symbol="BTCUSDT")
        
        # Экспирация через 30 минут
        expiry = datetime.now(timezone.utc) + timedelta(minutes=30)
        
        book.gamma_profile = GammaProfile(
            total_gex=100_000_000,
            total_gex_normalized=0.2,
            call_wall=100000.0,
            put_wall=95000.0,
            timestamp=datetime.now(timezone.utc),
            expiry_timestamp=expiry
        )
        
        # WHY: Используем base_confidence = 0.5
        base_confidence = 0.5
        
        adjusted_30m, _ = analyzer.adjust_confidence_by_gamma(
            base_confidence=base_confidence,
            gamma_profile=book.gamma_profile,
            price=Decimal("100000"),
            is_ask=True,
            vpin_score=None,
            cvd_divergence=None
        )
        
        # Ожидаем: За 30 мин = 25% бонуса (decay_factor = 0.25)
        # Бонус = 0.8 * 0.25 = 0.2
        # Итого: confidence * (1.0 + 0.2) = 0.5 * 1.2 = 0.6
        expected_approx = base_confidence * 1.2  # 0.6
        
        assert adjusted_30m <= base_confidence * 1.35, \
            f"За 30 мин до экспирации бонус должен быть минимальным (~0.6), но {adjusted_30m} > {base_confidence * 1.35}"
        
        assert adjusted_30m >= base_confidence * 1.05, \
            f"За 30 мин все еще должен быть небольшой бонус, но {adjusted_30m} < {base_confidence * 1.05}"
    
    def test_no_decay_if_no_expiry_timestamp(self):
        """
        WHY: Если expiry_timestamp = None, decay не применяется.
        """
        analyzer = IcebergAnalyzer(BTC_CONFIG)
        book = LocalOrderBook(symbol="BTCUSDT")
        
        book.gamma_profile = GammaProfile(
            total_gex=100_000_000,
            total_gex_normalized=0.2,
            call_wall=100000.0,
            put_wall=95000.0,
            timestamp=datetime.now(timezone.utc),
            expiry_timestamp=None  # НЕТ expiry
        )
        
        # WHY: Используем base_confidence = 0.5
        base_confidence = 0.5
        
        adjusted, _ = analyzer.adjust_confidence_by_gamma(
            base_confidence=base_confidence,
            gamma_profile=book.gamma_profile,
            price=Decimal("100000"),
            is_ask=True,
            vpin_score=None,
            cvd_divergence=None
        )
        
        # Ожидаем: Полный бонус без decay (x1.8)
        # 0.5 * 1.8 = 0.9
        assert adjusted >= base_confidence * 1.7, \
            f"Без expiry_timestamp должен быть полный бонус (~0.9), но {adjusted} < {base_confidence * 1.7}"


class TestCombinedGEXFixes:
    """Проверяем совместную работу нормализации и decay."""
    
    def test_low_normalized_gex_with_imminent_expiry(self):
        """
        WHY: Если GEX слабый (normalized < 0.1) И экспирация близко → нет бонуса.
        """
        analyzer = IcebergAnalyzer(BTC_CONFIG)
        book = LocalOrderBook(symbol="BTCUSDT")
        
        # Слабый GEX + экспирация через 30 минут
        expiry = datetime.now(timezone.utc) + timedelta(minutes=30)
        
        book.gamma_profile = GammaProfile(
            total_gex=5_000_000,
            total_gex_normalized=0.05,  # НИЖЕ порога
            call_wall=100000.0,
            put_wall=95000.0,
            timestamp=datetime.now(timezone.utc),
            expiry_timestamp=expiry
        )
        
        base_confidence = 0.7
        
        adjusted, _ = analyzer.adjust_confidence_by_gamma(
            base_confidence=base_confidence,
            gamma_profile=book.gamma_profile,
            price=Decimal("100000"),
            is_ask=True,
            vpin_score=None,
            cvd_divergence=None
        )
        
        # Ожидаем: Практически нет изменений (GEX незначимый)
        assert abs(adjusted - base_confidence) < 0.1, \
            f"Слабый GEX + близкая экспирация не должны давать бонус, но adjusted={adjusted}, base={base_confidence}"
    
    def test_strong_normalized_gex_far_from_expiry(self):
        """
        WHY: Если GEX сильный (normalized > 0.1) И экспирация далеко → максимальный бонус.
        """
        analyzer = IcebergAnalyzer(BTC_CONFIG)
        book = LocalOrderBook(symbol="BTCUSDT")
        
        # Сильный GEX + экспирация через неделю
        expiry = datetime.now(timezone.utc) + timedelta(days=7)
        
        book.gamma_profile = GammaProfile(
            total_gex=200_000_000,
            total_gex_normalized=0.3,  # ВЫШЕ порога
            call_wall=100000.0,
            put_wall=95000.0,
            timestamp=datetime.now(timezone.utc),
            expiry_timestamp=expiry
        )
        
        # WHY: Используем base_confidence = 0.5
        base_confidence = 0.5
        
        adjusted, is_major = analyzer.adjust_confidence_by_gamma(
            base_confidence=base_confidence,
            gamma_profile=book.gamma_profile,
            price=Decimal("100000"),
            is_ask=True,
            vpin_score=None,
            cvd_divergence=None
        )
        
        # Ожидаем: Максимальный бонус x1.8 (decay_factor = 1.0)
        # 0.5 * 1.8 = 0.9
        assert adjusted >= base_confidence * 1.7, \
            f"Сильный GEX далеко от экспирации должен давать максимальный бонус (~0.9), но {adjusted} < {base_confidence * 1.7}"
        
        assert is_major is True, \
            "Gamma wall event с сильным GEX должен быть is_major=True"
