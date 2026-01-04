"""
VULNERABILITY #4 FIX: DerivativesAnalyzer Decimal Conversion Test
Проверка что calculate_gex() конвертирует float → Decimal для call/put walls.
"""

import pytest
from decimal import Decimal
from analyzers_derivatives import DerivativesAnalyzer


class TestDerivativesDecimalFix:
    """Проверка конвертации float → Decimal в calculate_gex()"""
    
    def test_calculate_gex_returns_decimal_walls(self):
        """
        FIX TEST: calculate_gex() должен возвращать Decimal для call/put_wall
        
        WHY: Страйки приходят как float из Deribit API, но GammaProfile требует Decimal
        """
        analyzer = DerivativesAnalyzer()
        
        # Входные данные (float, как от Deribit)
        strikes = [90000.0, 95000.0, 100000.0]
        types = ['C', 'C', 'C']
        expiry_years = [0.08, 0.08, 0.08]
        ivs = [0.65, 0.70, 0.75]
        oi = [1000.0, 1500.0, 2000.0]
        spot = 98000.0
        
        gamma_profile = analyzer.calculate_gex(
            strikes=strikes,
            types=types,
            expiry_years=expiry_years,
            ivs=ivs,
            open_interest=oi,
            underlying_price=spot
        )
        
        # Проверяем тип
        assert isinstance(gamma_profile.call_wall, Decimal), "call_wall должен быть Decimal"
        assert isinstance(gamma_profile.put_wall, Decimal), "put_wall должен быть Decimal"
    
    def test_calculate_gex_preserves_precision(self):
        """
        ТОЧНОСТЬ: Decimal сохраняет точность страйков
        
        WHY: float → str → Decimal сохраняет оригинальное значение
        """
        analyzer = DerivativesAnalyzer()
        
        # Высокоточные страйки (как может быть в SOL или низколиквидных токенах)
        strikes = [100.123456789, 105.987654321]
        types = ['C', 'P']
        expiry_years = [0.08, 0.08]
        ivs = [0.65, 0.70]
        oi = [1000.0, 2000.0]
        spot = 102.5
        
        gamma_profile = analyzer.calculate_gex(
            strikes=strikes,
            types=types,
            expiry_years=expiry_years,
            ivs=ivs,
            open_interest=oi,
            underlying_price=spot
        )
        
        # call_wall должен быть 100.123456789 (точно!)
        # put_wall должен быть 105.987654321 (точно!)
        
        # Decimal сохраняет точность
        assert gamma_profile.call_wall == Decimal("100.123456789")
        assert gamma_profile.put_wall == Decimal("105.987654321")
    
    def test_calculate_gex_handles_none_walls(self):
        """
        EDGE CASE: Если нет call/put strikes, должно вернуть Decimal("0")
        """
        analyzer = DerivativesAnalyzer()
        
        # Только Put опционы (нет Calls)
        strikes = [90000.0, 95000.0]
        types = ['P', 'P']
        expiry_years = [0.08, 0.08]
        ivs = [0.65, 0.70]
        oi = [1000.0, 1500.0]
        spot = 98000.0
        
        gamma_profile = analyzer.calculate_gex(
            strikes=strikes,
            types=types,
            expiry_years=expiry_years,
            ivs=ivs,
            open_interest=oi,
            underlying_price=spot
        )
        
        # call_wall должен быть Decimal("0") (нет Call страйков)
        assert gamma_profile.call_wall == Decimal("0")
        
        # put_wall должен быть Decimal (один из страйков)
        assert isinstance(gamma_profile.put_wall, Decimal)
        assert gamma_profile.put_wall > 0
