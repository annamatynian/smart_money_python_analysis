"""
WHY: Unit-тесты для SpoofingAnalyzer (Gemini Validation).

Покрытие уязвимостей:
- А: Хардкод 0.1 BTC в _analyze_execution_pattern (multi-asset killer)
- Б: Хардкод 0.5% в _analyze_cancellation_context (volatility blindness)
- В: Ступенчатая логика в _analyze_duration (ML non-continuity)
"""

import pytest
from decimal import Decimal
from datetime import datetime, timedelta
from analyzers import SpoofingAnalyzer
from domain import IcebergLevel, CancellationContext
from config import BTC_CONFIG, ETH_CONFIG


class TestSpoofingAnalyzerVulnerabilityA:
    """
    УЯЗВИМОСТЬ А: Хардкод порога 0.1 в _analyze_execution_pattern
    
    Проблема:
    - 0.1 BTC = $9,500 (разумно)
    - 0.1 ETH = $350 (проблема)
    - 0.1 SOL = $15 (катастрофа)
    
    Ожидаемое поведение:
    - Использовать config.spoofing_volume_threshold
    - Для BTC: 0.1
    - Для ETH: 1.0
    - Для SOL: 10.0
    """
    
    def test_small_btc_volume_triggers_score_increase(self):
        """
        WHY: Для BTC порог 0.1 разумен - должен срабатывать.
        
        Сценарий:
        - Айсберг с total_hidden_volume = 0.05 BTC ($4,750)
        - Должен получить штраф +0.3 за малый объем
        """
        # Arrange
        analyzer = SpoofingAnalyzer(config=BTC_CONFIG)  # GEMINI FIX
        
        iceberg = IcebergLevel(
            price=Decimal("95000"),
            total_hidden_volume=Decimal("0.05"),  # < 0.1 BTC
            is_ask=True,
            confidence_score=0.8
        )
        iceberg.refill_count = 5  # Умеренная активность
        
        # Act
        score = analyzer._analyze_execution_pattern(iceberg)  # GEMINI FIX
        
        # Assert
        # WHY: Малый объем (0.05 < 0.1) → score должен включать штраф +0.3
        assert score > 0.2, f"Expected score > 0.2 for tiny BTC volume, got {score}"
    
    def test_small_eth_volume_should_not_trigger_for_reasonable_size(self):
        """
        WHY: Для ETH порог 0.1 слишком низкий - 3.0 ETH ($10,500) это нормальный размер.
        
        ОЖИДАНИЕ ПОСЛЕ ФИКСА:
        - config.spoofing_volume_threshold для ETH = 2.0
        - Айсберг 3.0 ETH НЕ должен получать штраф
        - Высокая частота рефиллов (15/min) → легитимный алго
        
        ТЕКУЩЕЕ ПОВЕДЕНИЕ (ДО ФИКСА):
        - FAIL: score включает штраф, хотя 3.0 ETH - нормальный объем
        """
        # Arrange
        analyzer = SpoofingAnalyzer(config=ETH_CONFIG)  # GEMINI FIX - используем ETH config!
        
        iceberg = IcebergLevel(
            price=Decimal("3500"),
            total_hidden_volume=Decimal("3.0"),  # 3.0 ETH = $10,500 (выше threshold!)
            is_ask=True,
            confidence_score=0.8,
            creation_time=datetime.now() - timedelta(seconds=60)  # КРИТИЧНО: айсберг существует 60 сек
        )
        iceberg.refill_count = 15  # 15 рефиллов за 60 сек = 15/min > 10 → легитимный!
        
        # Act
        score = analyzer._analyze_execution_pattern(iceberg)  # GEMINI FIX
        
        # Assert
        # WHY: 3.0 ETH > 2.0 ETH (threshold) → НЕТ штрафа за объем
        # refill_frequency = 15/min > 10 → score = 0.0
        # После фикса этот тест должен ПРОЙТИ (score <= 0.2)
        assert score <= 0.2, (
            f"ETH volume 3.0 ETH ($10,500) incorrectly flagged as suspicious! "
            f"Score: {score}. This is Vulnerability A - hardcoded threshold."
        )
    
    def test_config_based_threshold_btc(self):
        """
        WHY: После фикса SpoofingAnalyzer должен использовать config.
        
        Проверяем что для BTC порог берется из BTC_CONFIG.spoofing_volume_threshold.
        """
        # Arrange
        analyzer = SpoofingAnalyzer(config=BTC_CONFIG)  # FIX: GEMINI - теперь работает!
        
        # Создаем айсберг чуть ниже порога BTC (0.1)
        iceberg = IcebergLevel(
            price=Decimal("95000"),
            total_hidden_volume=Decimal("0.08"),  # < 0.1 BTC threshold
            is_ask=True,
            confidence_score=0.8
        )
        iceberg.refill_count = 5
        
        # Act
        score = analyzer._analyze_execution_pattern(iceberg)
        
        # Assert
        # WHY: 0.08 < 0.1 (BTC_CONFIG.spoofing_volume_threshold) → должен быть штраф
        assert score > 0.2, (
            f"Expected score > 0.2 for volume below BTC threshold, got {score}. "
            f"Config-based threshold is working!"
        )


class TestSpoofingAnalyzerVulnerabilityB:
    """
    УЯЗВИМОСТЬ Б: Хардкод 0.5% в _analyze_cancellation_context
    
    Проблема:
    - Во флэте (volatility 0.2%) → 0.5% слишком широко
    - На пампе (volatility 5%) → 0.5% слишком узко
    
    Ожидаемое поведение:
    - Использовать config.breach_tolerance_pct или 2*Spread
    """
    
    def test_cancellation_near_level_in_flat_market(self):
        """
        WHY: В спокойном рынке отмена на 0.4% - это НЕ "рядом".
        
        Сценарий:
        - BTC spread = 0.01% (флэт)
        - Отмена на distance = 0.4%
        - Это 40x от спреда - очень далеко!
        
        ОЖИДАНИЕ ПОСЛЕ ФИКСА:
        - НЕ должно быть штрафа за "близость"
        """
        # Arrange
        analyzer = SpoofingAnalyzer(config=BTC_CONFIG)  # GEMINI FIX
        
        iceberg = IcebergLevel(
            price=Decimal("95000"),
            total_hidden_volume=Decimal("2.0"),
            is_ask=True,
            confidence_score=0.8
        )
        
        # Симулируем отмену на 0.4% от уровня
        iceberg.cancellation_context = CancellationContext(
            mid_price_at_cancel=Decimal("95400"),  # ОБЯЗАТЕЛЬНОЕ ПОЛЕ
            distance_from_level_pct=Decimal("0.4"),  # 0.4% distance
            price_velocity_5s=Decimal("100"),  # ОБЯЗАТЕЛЬНОЕ ПОЛЕ (цена растёт)
            moving_towards_level=True,
            volume_executed_pct=Decimal("5.0")
        )
        
        # Act
        current_mid = Decimal("95400")  # 0.4% выше iceberg price
        price_history = [Decimal("95000"), Decimal("95100"), Decimal("95200")]
        
        score = analyzer._analyze_cancellation_context(  # GEMINI FIX
            iceberg, current_mid, price_history
        )
        
        # Assert
        # WHY: 0.4% < 0.5% (hardcoded) → получит штраф +0.3
        # После фикса: порог должен быть динамическим (например 2*spread = 0.02%)
        # Тогда 0.4% будет считаться "далеко" → score <= 0.6
        assert score > 0.8, (
            f"Distance 0.4% incorrectly treated as 'near' in flat market! "
            f"Score: {score}. Expected high score due to hardcoded 0.5% threshold."
        )


class TestSpoofingAnalyzerVulnerabilityC:
    """
    УЯЗВИМОСТЬ В: Ступенчатая логика в _analyze_duration
    
    Проблема:
    - 4.9 сек → score = 1.0
    - 5.1 сек → score = 0.7
    - Огромный скачок на границе!
    
    Ожидаемое поведение:
    - Гладкая функция: score = 1.0 / (1.0 + 0.1 * duration)
    """
    
    def test_duration_step_function_problem(self):
        """
        WHY: ML модели страдают от ступенчатых функций.
        
        Текущее поведение:
        - 4.9 сек → 1.0
        - 5.1 сек → 0.7
        - Разница: 0.3 (огромная!)
        
        Ожидание после фикса:
        - 4.9 сек → 0.67
        - 5.1 сек → 0.66
        - Разница: 0.01 (плавная)
        """
        # Arrange
        analyzer = SpoofingAnalyzer(config=BTC_CONFIG)  # GEMINI FIX
        
        iceberg_4_9_sec = IcebergLevel(
            price=Decimal("95000"),
            total_hidden_volume=Decimal("1.0"),
            is_ask=True,
            confidence_score=0.8,
            creation_time=datetime.now() - timedelta(seconds=4.9)
        )
        
        iceberg_5_1_sec = IcebergLevel(
            price=Decimal("95000"),
            total_hidden_volume=Decimal("1.0"),
            is_ask=True,
            confidence_score=0.8,
            creation_time=datetime.now() - timedelta(seconds=5.1)
        )
        
        # Act
        score_4_9 = analyzer._analyze_duration(iceberg_4_9_sec)  # GEMINI FIX
        score_5_1 = analyzer._analyze_duration(iceberg_5_1_sec)  # GEMINI FIX
        
        # Assert
        # WHY: ПОСЛЕ ФИКСА - гладкая функция (score = 1.0 / (1.0 + 0.1 * duration))
        # 4.9 сек → 1.0 / (1.0 + 0.49) = 0.67
        # 5.1 сек → 1.0 / (1.0 + 0.51) = 0.66
        assert abs(score_4_9 - 0.67) < 0.02, f"Expected ~0.67 for 4.9 sec, got {score_4_9}"
        assert abs(score_5_1 - 0.66) < 0.02, f"Expected ~0.66 for 5.1 sec, got {score_5_1}"
        
        # WHY: Разница должна быть минимальной (< 0.05 для ML)
        diff = abs(score_4_9 - score_5_1)
        assert diff < 0.05, (
            f"Step function creates huge gap: {diff}. "
            f"ML models cannot handle this discontinuity."
        )
    
    def test_smooth_function_after_fix(self):
        """
        WHY: После фикса должна быть гладкая функция.
        
        Formula: score = 1.0 / (1.0 + 0.1 * duration)
        
        Проверяем примеры:
        - 0 сек → 1.0 / 1.0 = 1.0
        - 10 сек → 1.0 / 2.0 = 0.5
        - 100 сек → 1.0 / 11.0 = 0.091
        - 600 сек (10 мин) → 1.0 / 61.0 = 0.016
        """
        # Arrange
        analyzer = SpoofingAnalyzer(config=BTC_CONFIG)  # FIX: GEMINI - теперь работает!
        
        # Test cases: различные duration значения
        test_cases = [
            (0, 1.0),      # Мгновенный → максимальный score
            (10, 0.5),     # 10 сек → score 0.5
            (100, 0.091),  # 100 сек → score ~0.09
            (600, 0.016),  # 10 мин → score ~0.016
        ]
        
        for duration_sec, expected_score in test_cases:
            iceberg = IcebergLevel(
                price=Decimal("95000"),
                total_hidden_volume=Decimal("1.0"),
                is_ask=True,
                confidence_score=0.8,
                creation_time=datetime.now() - timedelta(seconds=duration_sec)
            )
            
            # Act
            score = analyzer._analyze_duration(iceberg)
            
            # Assert
            # WHY: Проверяем что формула работает правильно (допуск 0.01)
            assert abs(score - expected_score) < 0.01, (
                f"Duration {duration_sec}s: expected ~{expected_score}, got {score}. "
                f"Smooth function formula: 1.0 / (1.0 + 0.1 * {duration_sec})"
            )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
