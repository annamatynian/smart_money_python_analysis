"""
=== INTEGRATION TEST: GEX Normalization + Expiration Decay ===

WHY: Проверяем полную интеграцию всех GEMINI FIX изменений:
1. domain.py: GammaProfile.get_next_options_expiry()
2. infrastructure.py: get_average_daily_volume()
3. analyzers_derivatives.py: calculate_gex() с новыми полями
"""

import pytest
import asyncio
from datetime import datetime, timezone, timedelta
from analyzers_derivatives import DerivativesAnalyzer
from infrastructure import get_average_daily_volume
from domain import GammaProfile


class TestGEXIntegration:
    """
    WHY: Полная проверка интеграции GEMINI FIX.
    """
    
    def test_gamma_profile_expiry_calculation(self):
        """
        WHY: Проверяем что GammaProfile.get_next_options_expiry() работает корректно.
        """
        expiry = GammaProfile.get_next_options_expiry()
        
        now = datetime.now(timezone.utc)
        
        # ASSERTION 1: Expiry всегда в будущем
        assert expiry > now, f"Expiry должен быть в будущем, но {expiry} <= {now}"
        
        # ASSERTION 2: Expiry это пятница
        assert expiry.weekday() == 4, f"Expiry должен быть пятница (4), но weekday={expiry.weekday()}"
        
        # ASSERTION 3: Expiry в 08:00 UTC
        assert expiry.hour == 8, f"Expiry должен быть 08:00, но hour={expiry.hour}"
        assert expiry.minute == 0, f"Expiry должен быть 08:00:00, но minute={expiry.minute}"
        
        # ASSERTION 4: Expiry в пределах 7 дней
        days_ahead = (expiry - now).total_seconds() / (60 * 60 * 24)
        assert 0 < days_ahead <= 7, f"Expiry должен быть в пределах 7 дней, но days_ahead={days_ahead}"
    
    @pytest.mark.asyncio
    async def test_adv_function_integration(self):
        """
        WHY: Проверяем что get_average_daily_volume() работает (или возвращает None если нет сети).
        """
        # Пытаемся получить ADV для BTC (ОБЯЗАТЕЛЬНО указываем symbol)
        adv = await get_average_daily_volume(symbol="BTCUSDT", days=20, exchange="binance")
        
        # ASSERTION: Либо float > 0, либо None (если нет сети/ошибка)
        if adv is not None:
            assert isinstance(adv, float), f"ADV должен быть float, но type={type(adv)}"
            assert adv > 0, f"ADV должен быть положительным, но adv={adv}"
            
            # Разумный диапазон для BTC (100M - 100B USD)
            assert 100_000_000 < adv < 100_000_000_000, \
                f"ADV для BTC вне разумного диапазона: {adv}"
        else:
            # Если None - это нормально (нет сети), пропускаем
            pytest.skip("Нет доступа к Binance API (ожидаемо в CI/CD)")
    
    def test_calculate_gex_with_normalization(self):
        """
        WHY: Проверяем что calculate_gex() корректно заполняет новые поля.
        """
        from config import BTC_CONFIG
        analyzer = DerivativesAnalyzer(config=BTC_CONFIG)
        
        # Mock данные
        strikes = [90000, 95000, 100000, 105000]
        types = ['C', 'C', 'P', 'P']
        expiry_years = [0.08] * 4  # ~30 дней
        ivs = [0.65, 0.70, 0.75, 0.80]
        oi = [1000, 1500, 2000, 1200]
        spot = 98000.0
        
        # Mock ADV (2B USD)
        mock_adv = 2_000_000_000.0
        
        # Вызываем calculate_gex с новыми параметрами
        profile = analyzer.calculate_gex(
            strikes=strikes,
            types=types,
            expiry_years=expiry_years,
            ivs=ivs,
            open_interest=oi,
            underlying_price=spot,
            avg_daily_volume=mock_adv  # Symbol берется из analyzer.config.symbol
        )
        
        # ASSERTION 1: GammaProfile создан
        assert profile is not None, "calculate_gex() должен вернуть GammaProfile"
        assert isinstance(profile, GammaProfile)
        
        # ASSERTION 2: total_gex_normalized заполнен
        assert profile.total_gex_normalized is not None, \
            "total_gex_normalized должен быть заполнен когда передан avg_daily_volume"
        
        assert isinstance(profile.total_gex_normalized, float)
        
        # ASSERTION 3: Нормализованное значение в разумных пределах (0.0001 - 10.0)
        # GEX обычно составляет малую долю от ADV
        assert 0.0 < abs(profile.total_gex_normalized) < 10.0, \
            f"total_gex_normalized вне диапазона: {profile.total_gex_normalized}"
        
        # ASSERTION 4: expiry_timestamp заполнен
        assert profile.expiry_timestamp is not None, \
            "expiry_timestamp должен быть заполнен"
        
        assert isinstance(profile.expiry_timestamp, datetime)
        
        # ASSERTION 5: expiry в будущем
        now = datetime.now(timezone.utc)
        assert profile.expiry_timestamp > now, \
            f"expiry_timestamp должен быть в будущем: {profile.expiry_timestamp}"
    
    def test_calculate_gex_without_adv(self):
        """
        WHY: Проверяем backward compatibility - если ADV не передан, 
        total_gex_normalized должен быть None.
        """
        analyzer = DerivativesAnalyzer()
        
        # Mock данные (без avg_daily_volume)
        strikes = [95000, 100000]
        types = ['C', 'P']
        expiry_years = [0.08, 0.08]
        ivs = [0.70, 0.75]
        oi = [1000, 1500]
        spot = 98000.0
        
        # Вызываем БЕЗ avg_daily_volume
        profile = analyzer.calculate_gex(
            strikes=strikes,
            types=types,
            expiry_years=expiry_years,
            ivs=ivs,
            open_interest=oi,
            underlying_price=spot
            # avg_daily_volume НЕ передан
        )
        
        # ASSERTION 1: GammaProfile создан
        assert profile is not None
        
        # ASSERTION 2: total_gex_normalized = None (т.к. ADV не передан)
        assert profile.total_gex_normalized is None, \
            "total_gex_normalized должен быть None когда avg_daily_volume не передан"
        
        # ASSERTION 3: expiry_timestamp всё равно заполнен
        assert profile.expiry_timestamp is not None, \
            "expiry_timestamp должен всегда заполняться"
        
        # ASSERTION 4: Старые поля работают как раньше
        assert isinstance(profile.total_gex, float)
        assert isinstance(profile.call_wall, float)
        assert isinstance(profile.put_wall, float)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
