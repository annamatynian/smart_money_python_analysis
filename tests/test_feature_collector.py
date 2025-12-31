# ===========================================================================
# ТЕСТЫ: FeatureCollector
# ===========================================================================

"""
WHY: Проверяем что FeatureCollector корректно собирает метрики.

Тестируем:
1. Работу с разными структурами данных (словарь cvd, tracker objects)
2. Безопасность при отсутствии данных (возвращает None)
3. Расчет TWAP и волатильности
4. OFI tracking
"""

import pytest
from datetime import datetime, timezone
from decimal import Decimal
from analyzers_features import FeatureCollector, FeatureSnapshot


# Mock объекты для тестирования
class MockOrderBook:
    def __init__(self):
        self.best_bid = Decimal('60000')
        self.best_ask = Decimal('60050')
        self.bids = {
            Decimal('60000'): 1.0,
            Decimal('59990'): 2.0,
            Decimal('59980'): 1.5,
        }
        self.asks = {
            Decimal('60050'): 1.0,
            Decimal('60060'): 2.0,
            Decimal('60070'): 1.5,
        }
        # WHY: FeatureCollector читает CVD напрямую из whale_cvd
        self.whale_cvd = {
            'whale': 50000.0,
            'dolphin': 10000.0,
            'minnow': -5000.0  # WHY: Используется 'minnow', а не 'fish'
        }
        # FIX (Gemini): Добавляем OFI
        self._ofi_value = 25.0  # Mock value for testing
    
    def get_weighted_obi(self, depth=20, use_exponential=True):
        """WHY: LocalOrderBook имеет этот метод"""
        return 0.35  # Положительный дисбаланс
    
    def calculate_ofi(self):
        """FIX (Gemini): FeatureCollector._get_ofi() вызывает этот метод"""
        return self._ofi_value
    
    def get_best_bid(self):
        """WHY: FeatureCollector._get_current_price() использует этот метод"""
        return (self.best_bid, Decimal('1.0'))  # Returns (price, qty)
    
    def get_best_ask(self):
        """WHY: FeatureCollector._get_current_price() использует этот метод"""
        return (self.best_ask, Decimal('1.0'))  # Returns (price, qty)


class MockDerivativesAnalyzer:
    def __init__(self):
        self._cached_basis = 12.5  # APR
        self._cached_skew = 6.8
        self._last_basis_update = datetime.now(timezone.utc)
        self._last_skew_update = datetime.now(timezone.utc)
    
    def get_cached_basis(self):
        return self._cached_basis
    
    def get_cached_skew(self):
        return self._cached_skew
    
    def interpret_basis_contango(self, basis_apr):
        if basis_apr < 5:
            return 'NORMAL'
        elif basis_apr < 15:
            return 'OPTIMISTIC'
        else:
            return 'OVERHEATED'
    
    def interpret_skew(self, skew, price_rising):
        if skew > 5:
            return 'FEAR'
        elif skew < -5:
            return 'GREED'
        else:
            return 'NEUTRAL'


# ============================================================================
# ТЕСТЫ
# ============================================================================

@pytest.mark.asyncio
async def test_feature_collector_with_all_dependencies():
    """
    WHY: Проверяем полный сбор метрик когда все зависимости доступны.
    """
    # Arrange
    book = MockOrderBook()
    derivatives = MockDerivativesAnalyzer()
    
    collector = FeatureCollector(
        order_book=book,
        flow_analyzer=None,  # WHY: CVD читается из order_book
        derivatives_analyzer=derivatives
    )
    
    # WHY: Устанавливаем кешированные значения вручную
    collector.cached_basis = 12.5
    collector.cached_skew = 6.8
    
    # Добавляем историю цен для TWAP
    for i in range(10):
        collector.update_price(60000 + i * 10)
    
    # Act - FIRST call (initializes state, returns 0)
    snapshot1 = collector.capture_snapshot()
    
    # WHY: Изменяем CVD для второго вызова (симулируем реальную торговлю)
    book.whale_cvd['whale'] = 60000.0  # +10000 delta
    book.whale_cvd['dolphin'] = 12000.0  # +2000 delta
    book.whale_cvd['minnow'] = -6000.0  # -1000 delta
    
    # Act - SECOND call (returns actual delta)
    snapshot2 = collector.capture_snapshot()
    
    # Assert - Use SECOND snapshot (has deltas)
    assert snapshot2 is not None
    assert isinstance(snapshot2, FeatureSnapshot)
    
    # Orderbook метрики
    assert snapshot2.spread_bps is not None
    assert snapshot2.spread_bps > 0
    assert snapshot2.depth_ratio is not None
    assert snapshot2.obi_value == 0.35
    assert snapshot2.ofi_value is not None  # OFI должен обновиться
    
    # Flow метрики (теперь это ДЕЛЬТЫ!)
    # WHY: FeatureCollector возвращает delta CVD для stationarity
    # First call: initialized state (returned 0)
    # Second call: delta = current - last
    assert snapshot2.whale_cvd == 10000.0  # 60000 - 50000
    assert snapshot2.dolphin_cvd == 2000.0  # 12000 - 10000
    assert snapshot2.fish_cvd == -1000.0   # -6000 - (-5000)
    assert snapshot2.total_cvd == 11000.0  # 10000 + 2000 + (-1000)
    
    # Derivatives метрики
    assert snapshot2.futures_basis_apr == 12.5
    assert snapshot2.basis_state == 'CONTANGO'  # WHY: basis_apr=12.5 → CONTANGO (10-20 range)
    assert snapshot2.options_skew == 6.8
    assert snapshot2.skew_state == 'FEAR'
    
    # Price метрики
    assert snapshot2.current_price is not None
    assert snapshot2.twap_5m is not None
    assert snapshot2.price_vs_twap_pct is not None


@pytest.mark.asyncio
async def test_feature_collector_tolerates_missing_dependencies():
    """
    WHY: Проверяем что отсутствие зависимостей не ломает систему.
    """
    # Arrange - только book, без derivatives
    book = MockOrderBook()
    collector = FeatureCollector(
        order_book=book,
        flow_analyzer=None,
        derivatives_analyzer=None  # WHY: Нет derivatives
    )
    
    # Act - FIRST call (initializes state)
    snapshot1 = collector.capture_snapshot()
    
    # WHY: Изменяем CVD для второго вызова
    book.whale_cvd['whale'] = 55000.0
    book.whale_cvd['dolphin'] = 11000.0
    book.whale_cvd['minnow'] = -5500.0
    
    # Act - SECOND call (returns deltas)
    snapshot2 = collector.capture_snapshot()
    
    # Assert - Use SECOND snapshot
    assert snapshot2 is not None
    
    # Orderbook метрики должны быть
    assert snapshot2.spread_bps is not None
    assert snapshot2.depth_ratio is not None
    assert snapshot2.obi_value == 0.35  # WHY: MockOrderBook.get_weighted_obi() возвращает 0.35
    
    # Flow метрики (ДЕЛЬТЫ!)
    assert snapshot2.whale_cvd == 5000.0  # 55000 - 50000
    assert snapshot2.fish_cvd == -500.0   # -5500 - (-5000)
    assert snapshot2.dolphin_cvd == 1000.0  # 11000 - 10000
    assert snapshot2.total_cvd == 5500.0  # 5000 + (-500) + 1000
    
    # Derivatives метрики должны быть None
    assert snapshot2.futures_basis_apr is None
    assert snapshot2.basis_state is None


@pytest.mark.asyncio
async def test_ofi_calculation():
    """
    WHY: Проверяем что OFI читается из book.calculate_ofi().
    
    FIX (Gemini Validation): OFI теперь берется напрямую из LocalOrderBook,
    а не через update_ofi().
    """
    # Arrange
    book = MockOrderBook()
    book._ofi_value = 42.0  # Устанавливаем mock значение
    collector = FeatureCollector(order_book=book)
    
    # Act
    collector.capture_snapshot()  # Initialize state
    snapshot = collector.capture_snapshot()
    
    # Assert - OFI читается из book
    assert snapshot.ofi_value == 42.0


@pytest.mark.asyncio
async def test_twap_calculation():
    """
    WHY: Проверяем расчет TWAP.
    """
    # Arrange
    collector = FeatureCollector(order_book=MockOrderBook())
    
    # Добавляем цены: 60000, 60010, 60020, 60030
    prices = [60000, 60010, 60020, 60030]
    for p in prices:
        collector.update_price(p)
    
    # Act - Two calls for delta
    collector.capture_snapshot()  # Initialize state
    snapshot = collector.capture_snapshot()  # Get delta
    
    # Assert
    expected_twap = sum(prices) / len(prices)  # 60015
    assert snapshot.twap_5m == expected_twap
    
    # Current price должна быть mid price
    assert snapshot.current_price == 60025.0  # (60000 + 60050) / 2


@pytest.mark.asyncio
async def test_price_vs_twap():
    """
    WHY: Проверяем расчет отклонения от TWAP.
    
    Если current = 60100, TWAP = 60000:
    deviation = (60100 - 60000) / 60000 * 100 = 0.167%
    """
    # Arrange
    book = MockOrderBook()
    book.best_bid = Decimal('60100')
    book.best_ask = Decimal('60100')
    
    collector = FeatureCollector(order_book=book)
    
    # TWAP = 60000
    for _ in range(5):
        collector.update_price(60000)
    
    # Act - Two calls for delta
    collector.capture_snapshot()  # Initialize state
    snapshot = collector.capture_snapshot()  # Get delta
    
    # Assert
    # Current = 60100, TWAP = 60000
    # Deviation = (60100 - 60000) / 60000 * 100 = 0.1667%
    assert snapshot.current_price == 60100
    assert snapshot.twap_5m == 60000
    assert abs(snapshot.price_vs_twap_pct - 0.1667) < 0.01


@pytest.mark.asyncio
async def test_spread_calculation():
    """
    WHY: Проверяем расчет спреда в basis points.
    
    Если bid = 60000, ask = 60050:
    mid = 60025, spread = 50
    spread_bps = (50 / 60025) * 10000 = 8.33 bps
    """
    # Arrange
    book = MockOrderBook()
    collector = FeatureCollector(order_book=book)
    
    # Act - Two calls for delta
    collector.capture_snapshot()  # Initialize state
    snapshot = collector.capture_snapshot()  # Get delta
    
    # Assert
    # spread = 60050 - 60000 = 50
    # mid = 60025
    # bps = (50 / 60025) * 10000 = 8.329
    assert snapshot.spread_bps is not None
    assert abs(snapshot.spread_bps - 8.329) < 0.01


@pytest.mark.asyncio
async def test_depth_ratio():
    """
    WHY: Проверяем расчет соотношения bid/ask глубины.
    
    Bid depth = 1.0 + 2.0 + 1.5 = 4.5
    Ask depth = 1.0 + 2.0 + 1.5 = 4.5
    Ratio = 4.5 / 4.5 = 1.0 (баланс)
    """
    # Arrange
    book = MockOrderBook()
    collector = FeatureCollector(order_book=book)
    
    # Act
    snapshot = collector.capture_snapshot()
    
    # Assert
    assert snapshot.depth_ratio == 1.0


@pytest.mark.asyncio
async def test_total_cvd_calculation():
    """
    WHY: Проверяем суммирование CVD.
    
    Whale = 50000
    Dolphin = 10000
    Fish = -5000
    Total = 55000
    """
    # Arrange
    book = MockOrderBook()  # WHY: CVD читается из order_book.whale_cvd
    collector = FeatureCollector(order_book=book)
    
    # Act - Two calls for delta
    book = MockOrderBook()  # WHY: CVD читается из order_book.whale_cvd
    collector = FeatureCollector(order_book=book)
    
    collector.capture_snapshot()  # Initialize state
    
    # Изменяем CVD
    book.whale_cvd['whale'] = 60000.0
    book.whale_cvd['dolphin'] = 12000.0
    book.whale_cvd['minnow'] = -6000.0
    
    snapshot = collector.capture_snapshot()  # Get delta (= actual delta)
    
    # Assert - Second call delta
    # Whale: 10000, Dolphin: 2000, Fish: -1000
    # Total: 10000 + 2000 + (-1000) = 11000
    assert snapshot.total_cvd == 11000.0


@pytest.mark.asyncio
async def test_empty_price_history():
    """
    WHY: Проверяем что пустая история не ломает расчет TWAP.
    """
    # Arrange
    collector = FeatureCollector(order_book=MockOrderBook())
    # Не добавляем цены
    
    # Act
    snapshot = collector.capture_snapshot()
    
    # Assert
    assert snapshot.twap_5m is None
    assert snapshot.volatility_1h is None
    assert snapshot.price_vs_twap_pct is None
