"""
Тесты для DerivativesAnalyzer - Clean Architecture (математика без IO)

WHY: Проверяем что математика derivatives вынесена из infrastructure в analyzer.
Изолированные тесты без моков - чистые функции.
"""

import pytest
from decimal import Decimal
from analyzers_derivatives import DerivativesAnalyzer


class TestAnnualizedBasis:
    """Тесты расчёта аннуализированного базиса"""
    
    def test_basic_contango(self):
        """
        WHY: Базовый случай - Futures > Spot (Contango)
        
        Example: Spot $60,000, Futures $60,500, DTE=30 дней
        Basis = (60500 - 60000) / 60000 = 0.00833
        Annualized = 0.00833 * (365/30) * 100 = 10.1% APR
        """
        analyzer = DerivativesAnalyzer()
        
        spot = Decimal('60000')
        futures = Decimal('60500')
        dte = 30
        
        result = analyzer.calculate_annualized_basis(spot, futures, dte)
        
        # Ожидаем ~10.1% (с точностью 0.1%)
        assert 10.0 <= result <= 10.2
    
    def test_backwardation(self):
        """
        WHY: Futures < Spot (Backwardation) - отрицательный базис
        
        Example: Spot $60,000, Futures $59,500, DTE=30 дней
        Basis = (59500 - 60000) / 60000 = -0.00833
        Annualized = -0.00833 * (365/30) * 100 = -10.1% APR
        """
        analyzer = DerivativesAnalyzer()
        
        spot = Decimal('60000')
        futures = Decimal('59500')
        dte = 30
        
        result = analyzer.calculate_annualized_basis(spot, futures, dte)
        
        # Ожидаем ~-10.1% (с точностью 0.1%)
        assert -10.2 <= result <= -10.0
    
    def test_perpetual_futures(self):
        """
        WHY: Perpetual futures (DTE=1) - упрощённый расчёт
        
        Example: Spot $60,000, Futures $60,050, DTE=1
        Basis = (60050 - 60000) / 60000 = 0.000833
        Annualized = 0.000833 * (365/1) * 100 = 30.4% APR
        
        NOTE: Для perpetual это мгновенная премия, не реальный APR
        """
        analyzer = DerivativesAnalyzer()
        
        spot = Decimal('60000')
        futures = Decimal('60050')
        dte = 1  # Perpetual
        
        result = analyzer.calculate_annualized_basis(spot, futures, dte)
        
        # Ожидаем ~30.4% (с точностью 0.5%)
        assert 30.0 <= result <= 31.0
    
    def test_zero_basis(self):
        """
        WHY: Futures = Spot (нет премии)
        
        Example: Spot $60,000, Futures $60,000
        Basis = 0, Annualized = 0%
        """
        analyzer = DerivativesAnalyzer()
        
        spot = Decimal('60000')
        futures = Decimal('60000')
        dte = 30
        
        result = analyzer.calculate_annualized_basis(spot, futures, dte)
        
        assert result == 0.0
    
    def test_invalid_inputs(self):
        """
        WHY: Проверка валидации входных данных
        
        Должны выбрасываться исключения при:
        - spot <= 0
        - futures <= 0
        - dte <= 0
        """
        analyzer = DerivativesAnalyzer()
        
        # Отрицательная цена спота
        with pytest.raises(ValueError):
            analyzer.calculate_annualized_basis(
                Decimal('-100'), Decimal('100'), 30
            )
        
        # Нулевая цена фьючерса
        with pytest.raises(ValueError):
            analyzer.calculate_annualized_basis(
                Decimal('100'), Decimal('0'), 30
            )
        
        # Нулевой DTE
        with pytest.raises(ValueError):
            analyzer.calculate_annualized_basis(
                Decimal('100'), Decimal('100'), 0
            )


class TestOptionsSkew:
    """Тесты расчёта Options Skew"""
    
    def test_put_fear_premium(self):
        """
        WHY: Базовый случай - Put IV > Call IV (страх падения)
        
        Example: Put IV 65%, Call IV 58%
        Skew = 65 - 58 = +7.0% (путы дороже)
        """
        analyzer = DerivativesAnalyzer()
        
        put_iv_25d = 65.0
        call_iv_25d = 58.0
        
        result = analyzer.calculate_options_skew(put_iv_25d, call_iv_25d)
        
        assert result == 7.0
    
    def test_call_premium(self):
        """
        WHY: Call IV > Put IV (ожидание роста)
        
        Example: Put IV 55%, Call IV 60%
        Skew = 55 - 60 = -5.0% (коллы дороже)
        """
        analyzer = DerivativesAnalyzer()
        
        put_iv_25d = 55.0
        call_iv_25d = 60.0
        
        result = analyzer.calculate_options_skew(put_iv_25d, call_iv_25d)
        
        assert result == -5.0
    
    def test_neutral_skew(self):
        """
        WHY: Put IV = Call IV (нет премии)
        
        Example: Put IV 60%, Call IV 60%
        Skew = 0%
        """
        analyzer = DerivativesAnalyzer()
        
        put_iv_25d = 60.0
        call_iv_25d = 60.0
        
        result = analyzer.calculate_options_skew(put_iv_25d, call_iv_25d)
        
        assert result == 0.0
    
    def test_invalid_inputs(self):
        """
        WHY: Проверка валидации - IV не может быть отрицательным
        """
        analyzer = DerivativesAnalyzer()
        
        # Отрицательный Put IV
        with pytest.raises(ValueError):
            analyzer.calculate_options_skew(-10.0, 60.0)
        
        # Отрицательный Call IV
        with pytest.raises(ValueError):
            analyzer.calculate_options_skew(60.0, -10.0)


class TestOIDelta:
    """Тесты расчёта OI Delta"""
    
    def test_positive_delta(self):
        """
        WHY: OI растёт (новые позиции открываются)
        
        Example: OI_start 50,000, OI_end 52,000
        Delta = +2,000
        """
        analyzer = DerivativesAnalyzer()
        
        oi_start = 50000.0
        oi_end = 52000.0
        
        delta, magnitude = analyzer.calculate_oi_delta(oi_start, oi_end)
        
        assert delta == 2000.0
        assert magnitude in ['MAJOR', 'MODERATE', 'MINOR']  # Зависит от volume
    
    def test_negative_delta(self):
        """
        WHY: OI падает (позиции закрываются)
        
        Example: OI_start 50,000, OI_end 48,000
        Delta = -2,000
        """
        analyzer = DerivativesAnalyzer()
        
        oi_start = 50000.0
        oi_end = 48000.0
        
        delta, magnitude = analyzer.calculate_oi_delta(oi_start, oi_end)
        
        assert delta == -2000.0
        assert magnitude in ['MAJOR', 'MODERATE', 'MINOR']
    
    def test_zero_delta(self):
        """
        WHY: OI не изменился
        
        Example: OI_start 50,000, OI_end 50,000
        Delta = 0
        """
        analyzer = DerivativesAnalyzer()
        
        oi_start = 50000.0
        oi_end = 50000.0
        
        delta, magnitude = analyzer.calculate_oi_delta(oi_start, oi_end)
        
        assert delta == 0.0
    
    def test_magnitude_classification_with_volume(self):
        """
        WHY: Классификация MAJOR/MODERATE/MINOR с учётом объёма
        
        Теория (документ "Анализ умных денег"):
        - MAJOR: Delta > 5% от volume
        - MODERATE: 1-5% от volume
        - MINOR: <1% от volume
        """
        analyzer = DerivativesAnalyzer()
        
        volume = 100000.0
        
        # MAJOR: Delta = 6,000 (6% от volume)
        delta_major, mag_major = analyzer.calculate_oi_delta(
            50000.0, 56000.0, volume
        )
        assert mag_major == 'MAJOR'
        
        # MODERATE: Delta = 3,000 (3% от volume)
        delta_mod, mag_mod = analyzer.calculate_oi_delta(
            50000.0, 53000.0, volume
        )
        assert mag_mod == 'MODERATE'
        
        # MINOR: Delta = 500 (0.5% от volume)
        delta_min, mag_min = analyzer.calculate_oi_delta(
            50000.0, 50500.0, volume
        )
        assert mag_min == 'MINOR'


class TestIntegration:
    """Интеграционные тесты - комбинация метрик"""
    
    def test_full_derivatives_snapshot(self):
        """
        WHY: Полный снимок derivatives метрик (как в FeatureCollector)
        
        Сценарий: Перегрев рынка
        - Basis APR = 25% (контанго, перегрев)
        - Skew = +8% (страх падения)
        - OI Delta = +5,000 (новые позиции)
        """
        analyzer = DerivativesAnalyzer()
        
        # 1. Basis
        spot = Decimal('60000')
        futures = Decimal('64000')  # Большая премия
        basis = analyzer.calculate_annualized_basis(spot, futures, days_to_expiry=30)
        
        # 2. Skew
        skew = analyzer.calculate_options_skew(put_iv_25d=68.0, call_iv_25d=60.0)
        
        # 3. OI Delta
        oi_delta, oi_mag = analyzer.calculate_oi_delta(50000.0, 55000.0, 100000.0)
        
        # Assertions
        assert basis > 20.0  # Перегрев
        assert skew > 5.0    # Страх
        assert oi_delta > 0  # Новые позиции
        assert oi_mag == 'MODERATE'  # 5% от volume
        
        # Интерпретация: Рынок перегрет, но институционалы боятся (покупают путы)
        # Вероятный сигнал: Скорая коррекция
