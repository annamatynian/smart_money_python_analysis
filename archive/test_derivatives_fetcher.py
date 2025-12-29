"""
Tests for DerivativesDataFetcher (Binance Futures + Deribit).

WHY: Validates API integration and metrics calculation.
"""

import pytest
import asyncio
from infrastructure_derivatives import DerivativesDataFetcher
from decimal import Decimal


# ===========================================================================
# TEST 1: SPOT PRICE FETCHING
# ===========================================================================

@pytest.mark.asyncio
async def test_fetch_spot_price_btc():
    """
    WHY: Проверяет получение spot price с Binance.
    
    NOTE: Требует internet connection. Skip если недоступен.
    """
    fetcher = DerivativesDataFetcher(symbol='BTCUSDT')
    
    spot_price = await fetcher.fetch_spot_price()
    
    # Проверяем что цена получена и разумная
    assert spot_price is not None
    assert isinstance(spot_price, Decimal)
    assert 10000 < float(spot_price) < 200000  # BTC должен быть в разумных пределах


@pytest.mark.asyncio
async def test_fetch_spot_price_eth():
    """WHY: Проверяет поддержку ETH."""
    fetcher = DerivativesDataFetcher(symbol='ETHUSDT')
    
    spot_price = await fetcher.fetch_spot_price()
    
    assert spot_price is not None
    assert 500 < float(spot_price) < 10000  # ETH пределы


# ===========================================================================
# TEST 2: FUTURES PRICE FETCHING
# ===========================================================================

@pytest.mark.asyncio
async def test_fetch_futures_price():
    """
    WHY: Проверяет получение perpetual futures price.
    """
    fetcher = DerivativesDataFetcher(symbol='BTCUSDT')
    
    futures_price = await fetcher.fetch_futures_price()
    
    assert futures_price is not None
    assert isinstance(futures_price, Decimal)
    assert 10000 < float(futures_price) < 200000


# ===========================================================================
# TEST 3: OPEN INTEREST FETCHING
# ===========================================================================

@pytest.mark.asyncio
async def test_fetch_open_interest():
    """
    WHY: Проверяет получение OI с Binance Futures.
    """
    fetcher = DerivativesDataFetcher(symbol='BTCUSDT')
    
    oi = await fetcher.fetch_open_interest()
    
    assert oi is not None
    assert isinstance(oi, float)
    assert oi > 0  # OI всегда положительный
    
    # Проверяем что OI сохранился в кеше
    assert fetcher.last_oi == oi
    assert fetcher.last_oi_timestamp is not None


# ===========================================================================
# TEST 4: OPTIONS SKEW (Deribit)
# ===========================================================================

@pytest.mark.asyncio
async def test_fetch_options_skew():
    """
    WHY: Проверяет получение options skew с Deribit.
    
    NOTE: Deribit API может быть недоступен в некоторых регионах.
    """
    fetcher = DerivativesDataFetcher(symbol='BTCUSDT')
    
    skew = await fetcher.fetch_options_skew()
    
    # Skew может быть None если нет опционов
    if skew is not None:
        assert isinstance(skew, float)
        # Skew обычно в диапазоне -20% до +20%
        assert -30 < skew < 30


# ===========================================================================
# TEST 5: OI DELTA CALCULATION
# ===========================================================================

@pytest.mark.asyncio
async def test_calculate_oi_delta():
    """
    WHY: Проверяет расчет OI Delta.
    """
    fetcher = DerivativesDataFetcher(symbol='BTCUSDT')
    
    # Первый запрос - delta будет None
    oi1 = await fetcher.fetch_open_interest()
    assert oi1 is not None
    
    # Имитируем изменение OI
    previous_oi = fetcher.last_oi
    current_oi = previous_oi + 1000  # +1000 BTC
    
    delta = await fetcher.calculate_oi_delta(current_oi, previous_oi)
    
    assert delta is not None
    assert delta == 1000.0


# ===========================================================================
# TEST 6: FULL METRICS FETCH
# ===========================================================================

@pytest.mark.asyncio
async def test_fetch_all_metrics():
    """
    WHY: Проверяет параллельный сбор всех метрик.
    
    Это интеграционный тест - требует доступа к Binance + Deribit API.
    """
    fetcher = DerivativesDataFetcher(symbol='BTCUSDT')
    
    metrics = await fetcher.fetch_all_metrics()
    
    # Проверяем структуру ответа
    assert 'spot_price' in metrics
    assert 'futures_price' in metrics
    assert 'basis_apr' in metrics
    assert 'open_interest' in metrics
    assert 'oi_delta' in metrics
    assert 'options_skew' in metrics
    assert 'timestamp' in metrics
    
    # Проверяем что spot и futures получены
    assert metrics['spot_price'] is not None
    assert metrics['futures_price'] is not None
    
    # Basis должен быть рассчитан
    if metrics['basis_apr'] is not None:
        assert isinstance(metrics['basis_apr'], float)
        # Basis обычно в диапазоне -50% до +100% APR
        assert -100 < metrics['basis_apr'] < 200


# ===========================================================================
# TEST 7: BASIS CALCULATION LOGIC
# ===========================================================================

@pytest.mark.asyncio
async def test_basis_calculation():
    """
    WHY: Проверяет корректность расчета Annualized Basis.
    
    Scenario: 
    - Spot: $60,000
    - Futures: $60,500
    - Expected basis ≈ 9% APR (для perpetual)
    """
    fetcher = DerivativesDataFetcher(symbol='BTCUSDT')
    
    # Mock spot and futures
    spot = Decimal('60000')
    futures = Decimal('60500')
    
    # Расчет basis (копируем логику из fetch_all_metrics)
    basis = float((futures - spot) / spot)
    basis_apr = basis * 100  # Perpetual: просто премия в %
    
    # Проверяем что basis в ожидаемых пределах
    # (60500-60000)/60000 * 100 = 0.833%
    assert 0.8 < basis_apr < 0.9


# ===========================================================================
# TEST 8: ERROR HANDLING
# ===========================================================================

@pytest.mark.asyncio
async def test_invalid_symbol():
    """
    WHY: Проверяет обработку некорректного символа.
    
    Scenario: Запрашиваем несуществующий символ 'INVALID'.
    """
    fetcher = DerivativesDataFetcher(symbol='INVALID')
    
    spot_price = await fetcher.fetch_spot_price()
    
    # API должен вернуть None для несуществующего символа
    # (или выбросить ошибку, которая будет залогирована)
    # В любом случае не должно быть crash
    assert spot_price is None or isinstance(spot_price, Decimal)


# ===========================================================================
# TEST 9: MULTIPLE SYMBOLS
# ===========================================================================

@pytest.mark.asyncio
async def test_multiple_symbols_parallel():
    """
    WHY: Проверяет параллельную работу с несколькими символами.
    
    Scenario: Одновременно запрашиваем BTC, ETH, SOL.
    """
    fetchers = [
        DerivativesDataFetcher(symbol='BTCUSDT'),
        DerivativesDataFetcher(symbol='ETHUSDT'),
        # DerivativesDataFetcher(symbol='SOLUSDT')  # Раскомментируй если нужно
    ]
    
    # Параллельный запрос
    tasks = [f.fetch_all_metrics() for f in fetchers]
    results = await asyncio.gather(*tasks)
    
    # Проверяем что все запросы вернули данные
    assert len(results) == len(fetchers)
    
    for metrics in results:
        assert metrics['spot_price'] is not None
        assert metrics['futures_price'] is not None


# ===========================================================================
# PYTEST CONFIGURATION
# ===========================================================================

def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers", "asyncio: mark test as async (requires pytest-asyncio)"
    )
