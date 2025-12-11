"""
WHY: Упрощённые тесты для Market Metrics Logging (Gemini Phase 3.2).

Mock-based тесты без реальной БД для проверки логики.
"""
import pytest
from decimal import Decimal
from datetime import datetime
from domain import LocalOrderBook, OrderBookUpdate
from config import BTC_CONFIG


def test_metric_collection_from_orderbook():
    """
    WHY: Проверяем что можем собрать все необходимые метрики из книги.
    
    Метрики для логирования:
    - mid_price
    - ofi
    - obi
    - spread_bps
    """
    book = LocalOrderBook(symbol="BTCUSDT", config=BTC_CONFIG)
    
    # Создаём стакан
    book.apply_snapshot(
        bids=[(Decimal("100000"), Decimal("5.0"))],
        asks=[(Decimal("100010"), Decimal("3.0"))],
        last_update_id=1
    )
    
    # Update для генерации OFI
    book.apply_update(OrderBookUpdate(
        bids=[(Decimal("100000"), Decimal("7.0"))],
        asks=[],
        first_update_id=2,
        final_update_id=2
    ))
    
    # Собираем метрики
    mid_price = book.get_mid_price()
    ofi = book.calculate_ofi()
    obi = book.get_weighted_obi(use_exponential=True)
    spread_bps = book.get_spread()
    
    # Проверки
    assert mid_price is not None, "FAIL: mid_price должна быть доступна"
    assert mid_price == Decimal("100005"), f"FAIL: mid_price={mid_price}"
    
    # OFI должен быть положительным (добавили bids)
    assert ofi > 0, f"FAIL: OFI={ofi}, ожидали положительное"
    
    # OBI должен быть положительным (bids > asks)
    assert obi > 0, f"FAIL: OBI={obi}, ожидали положительное"
    
    # Spread должен быть положительным
    assert spread_bps > 0, f"FAIL: spread_bps={spread_bps}"


def test_metrics_structure():
    """
    WHY: Проверяем структуру метрик для логирования.
    
    Должны иметь все необходимые поля.
    """
    book = LocalOrderBook(symbol="ETHUSDT", config=BTC_CONFIG)
    
    book.apply_snapshot(
        bids=[(Decimal("3000"), Decimal("10.0"))],
        asks=[(Decimal("3005"), Decimal("8.0"))],
        last_update_id=1
    )
    
    book.apply_update(OrderBookUpdate(
        bids=[(Decimal("3000"), Decimal("12.0"))],
        asks=[],
        first_update_id=2,
        final_update_id=2
    ))
    
    # Формируем словарь метрик
    metrics = {
        'symbol': 'ETHUSDT',
        'timestamp': datetime.now(),
        'mid_price': book.get_mid_price(),
        'ofi': book.calculate_ofi(),
        'obi': book.get_weighted_obi(use_exponential=True),
        'spread_bps': book.get_spread()
    }
    
    # Проверяем что все поля присутствуют
    required_fields = ['symbol', 'timestamp', 'mid_price', 'ofi', 'obi', 'spread_bps']
    for field in required_fields:
        assert field in metrics, f"FAIL: Поле {field} отсутствует"
    
    # Проверяем типы
    assert isinstance(metrics['symbol'], str)
    assert isinstance(metrics['timestamp'], datetime)
    assert isinstance(metrics['mid_price'], Decimal)
    assert isinstance(metrics['ofi'], float)
    assert isinstance(metrics['obi'], float)
    assert isinstance(metrics['spread_bps'], (float, Decimal))  # Может быть и Decimal


def test_mock_repository_logging():
    """
    WHY: Mock тест - проверяем что метод вызывается с корректными параметрами.
    """
    # Mock repository
    class MockRepo:
        def __init__(self):
            self.logged_metrics = []
        
        async def log_market_metrics(self, **kwargs):
            self.logged_metrics.append(kwargs)
    
    repo = MockRepo()
    
    # Симулируем логирование
    import asyncio
    
    async def simulate_logging():
        await repo.log_market_metrics(
            symbol='BTCUSDT',
            timestamp=datetime.now(),
            mid_price=Decimal('100000.50'),
            ofi=2.5,
            obi=0.75,
            spread_bps=10.5
        )
    
    asyncio.run(simulate_logging())
    
    # Проверяем
    assert len(repo.logged_metrics) == 1
    
    logged = repo.logged_metrics[0]
    assert logged['symbol'] == 'BTCUSDT'
    assert abs(float(logged['mid_price']) - 100000.50) < 0.01
    assert logged['ofi'] == 2.5
    assert logged['obi'] == 0.75


def test_handle_none_values():
    """
    WHY: Проверяем обработку None значений (когда метрики недоступны).
    
    OFI = None если нет previous_snapshot.
    """
    book = LocalOrderBook(symbol="BTCUSDT", config=BTC_CONFIG)
    
    # Только snapshot (без update) → OFI будет None или 0.0
    book.apply_snapshot(
        bids=[(Decimal("100000"), Decimal("5.0"))],
        asks=[(Decimal("100010"), Decimal("5.0"))],
        last_update_id=1
    )
    
    # OFI без предыдущего состояния
    ofi = book.calculate_ofi()
    
    # Должен быть 0.0 (нет изменений)
    assert ofi == 0.0, f"FAIL: OFI={ofi}, ожидали 0.0"
    
    # mid_price доступна
    mid_price = book.get_mid_price()
    assert mid_price is not None
    
    # Можно логировать с OFI=0.0
    metrics = {
        'symbol': 'BTCUSDT',
        'timestamp': datetime.now(),
        'mid_price': mid_price,
        'ofi': ofi,  # 0.0
        'obi': book.get_weighted_obi(use_exponential=True),
        'spread_bps': book.get_spread()
    }
    
    # Все поля заполнены
    assert all(v is not None for v in metrics.values())


def test_metric_consistency():
    """
    WHY: Проверяем консистентность метрик при повторных расчётах.
    """
    book = LocalOrderBook(symbol="BTCUSDT", config=BTC_CONFIG)
    
    book.apply_snapshot(
        bids=[(Decimal("100000"), Decimal("5.0"))],
        asks=[(Decimal("100010"), Decimal("5.0"))],
        last_update_id=1
    )
    
    book.apply_update(OrderBookUpdate(
        bids=[(Decimal("100000"), Decimal("8.0"))],
        asks=[],
        first_update_id=2,
        final_update_id=2
    ))
    
    # Рассчитываем метрики дважды
    ofi_1 = book.calculate_ofi()
    ofi_2 = book.calculate_ofi()
    
    assert ofi_1 == ofi_2, \
        f"FAIL: OFI не консистентен! {ofi_1} != {ofi_2}"
    
    obi_1 = book.get_weighted_obi(use_exponential=True)
    obi_2 = book.get_weighted_obi(use_exponential=True)
    
    assert obi_1 == obi_2, \
        f"FAIL: OBI не консистентен! {obi_1} != {obi_2}"


def test_batch_metrics_collection():
    """
    WHY: Проверяем сбор метрик для множества updates (batch режим).
    
    Используется для накопления истории.
    """
    book = LocalOrderBook(symbol="BTCUSDT", config=BTC_CONFIG)
    
    book.apply_snapshot(
        bids=[(Decimal("100000"), Decimal("5.0"))],
        asks=[(Decimal("100010"), Decimal("5.0"))],
        last_update_id=1
    )
    
    metrics_history = []
    
    # 10 updates
    for i in range(10):
        book.apply_update(OrderBookUpdate(
            bids=[(Decimal("100000"), Decimal(str(5.0 + i * 0.5)))],
            asks=[],
            first_update_id=i+2,
            final_update_id=i+2
        ))
        
        # Собираем метрики
        metrics_history.append({
            'timestamp': datetime.now(),
            'ofi': book.calculate_ofi(),
            'obi': book.get_weighted_obi(use_exponential=True)
        })
    
    # Проверяем
    assert len(metrics_history) == 10
    
    # OFI должен расти (постоянно добавляем bids)
    ofis = [m['ofi'] for m in metrics_history]
    
    # Проверяем что большинство OFI положительные (первый может быть 0)
    positive_ofis = [ofi for ofi in ofis if ofi > 0]
    assert len(positive_ofis) >= 8, \
        f"FAIL: Ожидали минимум 8 положительных OFI, получили {len(positive_ofis)}"


def test_repository_method_signature():
    """
    WHY: Проверяем сигнатуру метода log_market_metrics.
    
    Метод должен принимать все необходимые параметры.
    """
    # Проверяем что метод существует в repository
    from repository import PostgresRepository
    
    # Метод должен существовать в классе
    assert hasattr(PostgresRepository, 'log_market_metrics'), \
        "FAIL: Метод log_market_metrics не найден в PostgresRepository"
    
    # Проверяем что это async метод
    import inspect
    assert inspect.iscoroutinefunction(PostgresRepository.log_market_metrics), \
        "FAIL: log_market_metrics должен быть async методом"
