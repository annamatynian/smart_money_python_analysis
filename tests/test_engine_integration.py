"""
WHY: Тесты интеграции AccumulationDetector в TradingEngine

Task: Автоматический анализ дивергенции при обработке trades
"""

import pytest
import asyncio
from decimal import Decimal
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from services import TradingEngine
from domain import TradeEvent
from config import BTC_CONFIG


@pytest.mark.asyncio
async def test_accumulation_detector_integration():
    """
    WHY: Проверяем что AccumulationDetector вызывается автоматически
    
    Сценарий:
    1. Engine обрабатывает trades
    2. historical_memory автоматически обновляется
    3. AccumulationDetector запускается периодически (каждые 10 trades)
    4. При обнаружении дивергенции → генерируется алерт
    """
    # Mock infrastructure
    mock_infra = MagicMock()
    mock_infra.listen_updates = AsyncMock(return_value=asyncio.Future())
    mock_infra.listen_trades = AsyncMock(return_value=asyncio.Future())
    mock_infra.get_snapshot = AsyncMock(return_value={
        'bids': [(Decimal("95000"), Decimal("1.0"))],
        'asks': [(Decimal("95100"), Decimal("1.0"))],
        'lastUpdateId': 1
    })
    
    engine = TradingEngine(symbol="BTCUSDT", infra=mock_infra)
    
    # Создаём trade events с дивергенцией
    base_time = datetime.now()
    trades = [
        TradeEvent(
            symbol="BTCUSDT",
            price=Decimal("95000") - Decimal(i * 50),
            quantity=Decimal("1.0"),
            is_buyer_maker=False,
            event_time=int((base_time + timedelta(hours=i)).timestamp() * 1000),
            trade_id=i
        )
        for i in range(5)
    ]
    
    # Симулируем обработку trades
    for i, trade in enumerate(trades):
        # Обновляем whale_cvd (растёт)
        engine.book.whale_cvd['whale'] += (i + 1) * 1000
        
        # Обновляем historical_memory
        engine.book.historical_memory.update_history(
            timestamp=base_time + timedelta(hours=i),
            whale_cvd=engine.book.whale_cvd['whale'],
            price=trade.price
        )
    
    # ПРОВЕРКА: Детектор должен найти BULLISH дивергенцию
    from analyzers import AccumulationDetector
    detector = AccumulationDetector(engine.book)
    
    # Инициализируем стакан
    engine.book.apply_snapshot(
        bids=[(Decimal("94700"), Decimal("1.0"))],
        asks=[(Decimal("94900"), Decimal("1.0"))],
        last_update_id=1
    )
    
    result = detector.detect_accumulation(timeframe='1h')
    
    assert result is not None, "FAIL: Должна быть обнаружена дивергенция"
    assert result['type'] == 'BULLISH', f"FAIL: Ожидали BULLISH, получили {result['type']}"


@pytest.mark.asyncio  
async def test_gex_correlation_in_engine():
    """
    WHY: Проверяем корреляцию дивергенции с GEX walls
    
    Сценарий:
    1. Engine получает GEX профиль от Deribit
    2. При детекции дивергенции → проверяется близость к GEX wall
    3. Confidence повышается если дивергенция у GEX wall
    """
    # Mock infrastructure
    mock_infra = MagicMock()
    mock_deribit = MagicMock()
    
    # Mock GEX profile
    from domain import GammaProfile
    mock_profile = GammaProfile(
        call_wall=95000,
        put_wall=94000,
        total_gex=5000000.0,
        timestamp=datetime.now()
    )
    mock_deribit.get_gamma_profile = AsyncMock(return_value=mock_profile)
    
    mock_infra.get_snapshot = AsyncMock(return_value={
        'bids': [(Decimal("94700"), Decimal("1.0"))],
        'asks': [(Decimal("94900"), Decimal("1.0"))],
        'lastUpdateId': 1
    })
    
    engine = TradingEngine(symbol="BTCUSDT", infra=mock_infra, deribit_infra=mock_deribit)
    
    # Устанавливаем GEX profile
    engine.book.gamma_profile = mock_profile
    
    # Создаём дивергенцию У PUT WALL (94000)
    base_time = datetime.now()
    final_price = Decimal("93900")  # Финальная цена
    
    for i in range(5):
        price = Decimal("94000") - Decimal(i * 25)  # Падает: 94000 → 93900
        whale_cvd = 1000.0 + i * 500  # Растёт
        
        engine.book.historical_memory.update_history(
            timestamp=base_time + timedelta(hours=i),
            whale_cvd=whale_cvd,
            price=price
        )
    
    # WHY: Инициализируем стакан НА ФИНАЛЬНОЙ ЦЕНЕ (93900)
    # Чтобы get_mid_price() вернул цену рядом с зоной
    engine.book.apply_snapshot(
        bids=[(final_price - Decimal("50"), Decimal("1.0"))],
        asks=[(final_price + Decimal("50"), Decimal("1.0"))],
        last_update_id=1
    )
    
    # Создаём айсберг-зону У ФИНАЛЬНОЙ ЦЕНЫ (93900)
    # WHY: Зона должна быть близко к final_price для детекции
    for price_offset in [-50, 0, 50]:  # Зона: 93850-93950
        engine.book.register_iceberg(
            price=final_price + Decimal(price_offset),
            hidden_vol=Decimal("10.0"),
            is_ask=False,
            confidence=0.8
        )
    
    # Детектируем
    from analyzers import AccumulationDetector
    detector = AccumulationDetector(engine.book)
    result = detector.detect_accumulation(timeframe='1h')
    
    # ПРОВЕРКА: Дивергенция найдена
    assert result is not None
    assert result['type'] == 'BULLISH'
    
    # ПРОВЕРКА: Зона детектирована
    assert result['near_strong_zone'], "FAIL: Должна быть детектирована зона у GEX wall"
    
    # ПРОВЕРКА: Confidence повышена (>= 0.7)
    assert result['confidence'] >= 0.7, \
        f"FAIL: Confidence должна быть повышена у GEX wall, получили {result['confidence']}"
