"""
TEST: VPIN & CVD Confidence Adjustment Integration

WHY: Проверяем что analyze_with_timing() корректирует confidence
на основе VPIN и CVD divergence как описано в документации.

ТЕОРИЯ (документ "Анализ данных смарт-мани"):
- VPIN > 0.7 (токсичный поток) → СНИЖАЕТ confidence
- VPIN < 0.3 (шумный поток) → ПОВЫШАЕТ confidence
- BULLISH divergence на BID → УСИЛИВАЕТ confidence (+25%)
- BEARISH divergence на ASK → УСИЛИВАЕТ confidence (+25%)
- Айсберг ПРОТИВ дивергенции → СНИЖАЕТ confidence (-15%)
"""

import pytest
from decimal import Decimal
from analyzers import IcebergAnalyzer
from config import BTC_CONFIG
from domain import GammaProfile, LocalOrderBook


class TestVPINConfidenceAdjustment:
    """Проверяем VPIN adjustment (Фаза 2)"""
    
    def setup_method(self):
        """Инициализация перед каждым тестом"""
        self.analyzer = IcebergAnalyzer(BTC_CONFIG)
    
    def test_toxic_flow_reduces_confidence(self):
        """
        WHY: Токсичный поток (VPIN > 0.7) СНИЖАЕТ confidence.
        
        Теория: Информированные агрессоры пробивают айсберг.
        Ожидание: base_confidence * toxicity_multiplier < base_confidence
        """
        base_confidence = 0.8
        vpin_score = 0.9  # Экстремально токсичный поток
        
        # Call adjust_confidence_by_gamma
        adjusted, is_major = self.analyzer.adjust_confidence_by_gamma(
            base_confidence=base_confidence,
            gamma_profile=None,  # Не используем GEX в этом тесте
            price=Decimal("50000"),
            is_ask=False,
            vpin_score=vpin_score,
            cvd_divergence=None
        )
        
        # Проверяем что confidence СНИЗИЛСЯ
        assert adjusted < base_confidence, (
            f"VPIN={vpin_score} должен СНИЖАТЬ confidence, "
            f"но получили {adjusted} >= {base_confidence}"
        )
        
        # Проверяем формулу: toxicity_multiplier = 1.0 - (0.9 - 0.7) * 1.5 = 0.7
        expected_multiplier = 1.0 - (vpin_score - 0.7) * 1.5
        expected_multiplier = max(0.55, expected_multiplier)  # Floor at 0.55
        expected = base_confidence * expected_multiplier
        
        assert abs(adjusted - expected) < 0.01, (
            f"Ожидали {expected:.3f}, получили {adjusted:.3f}"
        )
    
    def test_noise_flow_increases_confidence(self):
        """
        WHY: Шумный поток (VPIN < 0.3) ПОВЫШАЕТ confidence.
        
        Теория: Розничные трейдеры не пробьют айсберг.
        Ожидание: adjusted > base_confidence
        """
        base_confidence = 0.6
        vpin_score = 0.1  # Очень шумный поток
        
        adjusted, is_major = self.analyzer.adjust_confidence_by_gamma(
            base_confidence=base_confidence,
            gamma_profile=None,
            price=Decimal("50000"),
            is_ask=False,
            vpin_score=vpin_score,
            cvd_divergence=None
        )
        
        # Проверяем что confidence ВЫРОС
        assert adjusted > base_confidence, (
            f"VPIN={vpin_score} должен ПОВЫШАТЬ confidence, "
            f"но получили {adjusted} <= {base_confidence}"
        )
        
        # Проверяем формулу: noise_multiplier = 1.0 + (0.3 - 0.1) * 0.67 ≈ 1.134
        expected_multiplier = 1.0 + (0.3 - vpin_score) * 0.67
        expected_multiplier = min(1.20, expected_multiplier)  # Cap at 1.20
        expected = base_confidence * expected_multiplier
        
        assert abs(adjusted - expected) < 0.01
    
    def test_neutral_vpin_no_change(self):
        """
        WHY: Нейтральный VPIN (0.3-0.7) НЕ модифицирует confidence.
        """
        base_confidence = 0.7
        vpin_score = 0.5  # Нейтральный
        
        adjusted, _ = self.analyzer.adjust_confidence_by_gamma(
            base_confidence=base_confidence,
            gamma_profile=None,
            price=Decimal("50000"),
            is_ask=False,
            vpin_score=vpin_score,
            cvd_divergence=None
        )
        
        # Проверяем что confidence НЕ изменился
        assert adjusted == base_confidence, (
            f"Нейтральный VPIN не должен менять confidence, "
            f"но получили {adjusted} вместо {base_confidence}"
        )


class TestCVDConfidenceAdjustment:
    """Проверяем CVD Divergence adjustment (Фаза 3)"""
    
    def setup_method(self):
        self.analyzer = IcebergAnalyzer(BTC_CONFIG)
    
    def test_bullish_divergence_on_bid_increases_confidence(self):
        """
        WHY: BULLISH divergence (накопление) на BID УСИЛИВАЕТ confidence.
        
        Теория: Киты поглощают панические продажи на поддержке.
        Ожидание: adjusted = base * (1 + div_confidence * 0.25)
        """
        base_confidence = 0.6
        div_confidence = 0.8  # Сильная дивергенция
        
        # CVD divergence в формате tuple (is_div, div_type, confidence)
        cvd_divergence = (True, 'BULLISH', div_confidence)
        
        adjusted, is_major = self.analyzer.adjust_confidence_by_gamma(
            base_confidence=base_confidence,
            gamma_profile=None,
            price=Decimal("50000"),
            is_ask=False,  # BID iceberg (поддержка)
            vpin_score=None,
            cvd_divergence=cvd_divergence
        )
        
        # Проверяем что confidence ВЫРОС
        assert adjusted > base_confidence
        
        # Проверяем формулу: multiplier = 1.0 + (0.8 * 0.25) = 1.2
        expected_multiplier = 1.0 + (div_confidence * 0.25)
        expected = base_confidence * expected_multiplier
        
        assert abs(adjusted - expected) < 0.01
        
        # Проверяем что это major event
        assert is_major is True, "CVD divergence должна быть major event"
    
    def test_bearish_divergence_on_ask_increases_confidence(self):
        """
        WHY: BEARISH divergence (дистрибуция) на ASK УСИЛИВАЕТ confidence.
        
        Теория: Киты разгружаются на сопротивлении.
        """
        base_confidence = 0.7
        div_confidence = 0.9
        
        cvd_divergence = (True, 'BEARISH', div_confidence)
        
        adjusted, is_major = self.analyzer.adjust_confidence_by_gamma(
            base_confidence=base_confidence,
            gamma_profile=None,
            price=Decimal("50000"),
            is_ask=True,  # ASK iceberg (сопротивление)
            vpin_score=None,
            cvd_divergence=cvd_divergence
        )
        
        # Проверяем рост
        assert adjusted > base_confidence
        
        # Формула: 1.0 + (0.9 * 0.25) = 1.225
        expected = base_confidence * (1.0 + div_confidence * 0.25)
        assert abs(adjusted - expected) < 0.01
        assert is_major is True
    
    def test_conflicting_divergence_reduces_confidence(self):
        """
        WHY: Айсберг ПРОТИВ дивергенции СНИЖАЕТ confidence.
        
        Пример: BULLISH divergence но айсберг на ASK = противоречие.
        Ожидание: adjusted = base * (1 - div_confidence * 0.15)
        """
        base_confidence = 0.8
        div_confidence = 0.7
        
        # BULLISH divergence
        cvd_divergence = (True, 'BULLISH', div_confidence)
        
        adjusted, is_major = self.analyzer.adjust_confidence_by_gamma(
            base_confidence=base_confidence,
            gamma_profile=None,
            price=Decimal("50000"),
            is_ask=True,  # ASK iceberg - ПРОТИВОРЕЧИЕ!
            vpin_score=None,
            cvd_divergence=cvd_divergence
        )
        
        # Проверяем СНИЖЕНИЕ
        assert adjusted < base_confidence, (
            "Conflicting divergence должна СНИЖАТЬ confidence"
        )
        
        # Формула: 1.0 - (0.7 * 0.15) = 0.895
        expected = base_confidence * (1.0 - div_confidence * 0.15)
        assert abs(adjusted - expected) < 0.01
        
        # НЕ major event (противоречие)
        assert is_major is False
    
    def test_weak_divergence_ignored(self):
        """
        WHY: Слабая дивергенция (confidence < 0.5) игнорируется.
        """
        base_confidence = 0.7
        
        # Слабая дивергенция
        cvd_divergence = (True, 'BULLISH', 0.4)  # < 0.5
        
        adjusted, _ = self.analyzer.adjust_confidence_by_gamma(
            base_confidence=base_confidence,
            gamma_profile=None,
            price=Decimal("50000"),
            is_ask=False,
            vpin_score=None,
            cvd_divergence=cvd_divergence
        )
        
        # Проверяем что confidence НЕ изменился
        assert adjusted == base_confidence


class TestCombinedVPINandCVD:
    """Проверяем комбинированный эффект VPIN + CVD + GEX"""
    
    def setup_method(self):
        self.analyzer = IcebergAnalyzer(BTC_CONFIG)
    
    def test_all_factors_combine(self):
        """
        WHY: Все 3 фазы применяются последовательно.
        
        Сценарий:
        - GEX: Positive + on Call Wall → base * 1.8
        - VPIN: 0.9 (toxic) → result * 0.55
        - CVD: BEARISH on ASK → result * 1.225
        
        Финальная формула:
        final = min(1.0, base * 1.8 * 0.55 * 1.225)
        """
        base_confidence = 0.5
        
        # Создаём GammaProfile
        gamma_profile = GammaProfile(
            call_wall=50000.0,
            put_wall=48000.0,
            total_gex=1000000.0  # Positive GEX
        )
        
        # VPIN токсичный
        vpin_score = 0.9
        
        # CVD BEARISH на ASK
        cvd_divergence = (True, 'BEARISH', 0.9)
        
        adjusted, is_major = self.analyzer.adjust_confidence_by_gamma(
            base_confidence=base_confidence,
            gamma_profile=gamma_profile,
            price=Decimal("50000"),  # Точно на Call Wall
            is_ask=True,  # ASK iceberg
            vpin_score=vpin_score,
            cvd_divergence=cvd_divergence
        )
        
        # Проверяем что это major event (GEX + CVD)
        assert is_major is True
        
        # Вычисляем ожидаемый результат
        # Фаза 1 (GEX): base * 1.8 = 0.9
        after_gex = base_confidence * 1.8
        
        # Фаза 2 (VPIN): 0.9 * toxicity_multiplier
        toxicity_multiplier = 1.0 - (vpin_score - 0.7) * 1.5
        toxicity_multiplier = max(0.55, toxicity_multiplier)  # = 0.55
        after_vpin = after_gex * toxicity_multiplier
        
        # Фаза 3 (CVD): after_vpin * (1 + 0.9 * 0.25)
        cvd_multiplier = 1.0 + (0.9 * 0.25)  # = 1.225
        expected = after_vpin * cvd_multiplier
        
        # Обрезка до [0.0, 1.0]
        expected = min(1.0, expected)
        
        assert abs(adjusted - expected) < 0.01, (
            f"Ожидали {expected:.3f}, получили {adjusted:.3f}"
        )
    
    def test_optimal_conditions_for_spring_pattern(self):
        """
        WHY: Идеальные условия для Wyckoff SPRING pattern.
        
        Условия:
        - Положительная GEX на Put Wall (поддержка)
        - Низкий VPIN (0.2) = шумный поток
        - BULLISH CVD divergence на BID
        
        Ожидание: Очень высокая confidence (близко к 1.0)
        """
        base_confidence = 0.7
        
        # GEX: Positive + Put Wall
        gamma_profile = GammaProfile(
            call_wall=52000.0,
            put_wall=48000.0,
            total_gex=500000.0  # Positive
        )
        
        # VPIN: Низкий (шумный поток)
        vpin_score = 0.2
        
        # CVD: BULLISH на BID
        cvd_divergence = (True, 'BULLISH', 0.85)
        
        adjusted, is_major = self.analyzer.adjust_confidence_by_gamma(
            base_confidence=base_confidence,
            gamma_profile=gamma_profile,
            price=Decimal("48000"),  # На Put Wall
            is_ask=False,  # BID (поддержка)
            vpin_score=vpin_score,
            cvd_divergence=cvd_divergence
        )
        
        # Проверяем что confidence ОЧЕНЬ высокая
        assert adjusted > 0.9, (
            f"SPRING pattern должен давать высокую confidence, "
            f"получили {adjusted}"
        )
        
        assert is_major is True
    
    def test_worst_case_reduces_to_minimum(self):
        """
        WHY: Худший сценарий - все факторы против.
        
        Условия:
        - Отрицательная GEX вне gamma wall
        - Высокий VPIN (токсичный поток)
        - Conflicting CVD divergence
        
        Ожидание: Confidence сильно снижена
        """
        base_confidence = 0.8
        
        # GEX: Negative (не на wall)
        gamma_profile = GammaProfile(
            call_wall=52000.0,
            put_wall=48000.0,
            total_gex=-500000.0  # Negative GEX
        )
        
        # VPIN: Токсичный
        vpin_score = 0.95
        
        # CVD: Conflicting (BULLISH но на ASK)
        cvd_divergence = (True, 'BULLISH', 0.8)
        
        adjusted, _ = self.analyzer.adjust_confidence_by_gamma(
            base_confidence=base_confidence,
            gamma_profile=gamma_profile,
            price=Decimal("50000"),  # Не на wall
            is_ask=True,  # Conflicting
            vpin_score=vpin_score,
            cvd_divergence=cvd_divergence
        )
        
        # Проверяем что confidence СИЛЬНО снижена
        assert adjusted < 0.5, (
            f"Worst case должен давать низкую confidence, "
            f"получили {adjusted}"
        )


class TestEdgeCases:
    """Тестируем граничные случаи"""
    
    def setup_method(self):
        self.analyzer = IcebergAnalyzer(BTC_CONFIG)
    
    def test_no_vpin_no_cvd_no_gex(self):
        """
        WHY: Если нет данных - confidence не меняется.
        """
        base_confidence = 0.75
        
        adjusted, is_major = self.analyzer.adjust_confidence_by_gamma(
            base_confidence=base_confidence,
            gamma_profile=None,
            price=Decimal("50000"),
            is_ask=False,
            vpin_score=None,
            cvd_divergence=None
        )
        
        assert adjusted == base_confidence
        assert is_major is False
    
    def test_confidence_capped_at_1_0(self):
        """
        WHY: Confidence никогда не превышает 1.0.
        """
        base_confidence = 0.9
        
        # Супер-позитивные факторы
        gamma_profile = GammaProfile(
            call_wall=50000.0,
            put_wall=48000.0,
            total_gex=1000000.0
        )
        
        adjusted, _ = self.analyzer.adjust_confidence_by_gamma(
            base_confidence=base_confidence,
            gamma_profile=gamma_profile,
            price=Decimal("50000"),  # На Call Wall
            is_ask=True,
            vpin_score=0.1,  # Шумный поток
            cvd_divergence=(True, 'BEARISH', 0.95)
        )
        
        # Проверяем cap
        assert adjusted <= 1.0
        assert adjusted == 1.0  # Должно быть ровно 1.0
    
    def test_confidence_floored_at_0_0(self):
        """
        WHY: Confidence никогда не опускается ниже 0.0.
        """
        base_confidence = 0.2
        
        # Негативные факторы
        gamma_profile = GammaProfile(
            call_wall=52000.0,
            put_wall=48000.0,
            total_gex=-1000000.0
        )
        
        adjusted, _ = self.analyzer.adjust_confidence_by_gamma(
            base_confidence=base_confidence,
            gamma_profile=gamma_profile,
            price=Decimal("50000"),  # Не на wall
            is_ask=False,
            vpin_score=1.0,  # Экстремальный токсичный поток
            cvd_divergence=(True, 'BEARISH', 0.9)  # Conflicting
        )
        
        # Проверяем floor
        assert adjusted >= 0.0


if __name__ == "__main__":
    # Запуск тестов
    pytest.main([__file__, "-v", "--tb=short"])
