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
    
    def get_weighted_obi(self, depth=20, use_exponential=True):
        """WHY: LocalOrderBook имеет этот метод"""
        return 0.35  # Положительный дисбаланс


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
    
    # Обновляем OFI
    collector.update_ofi(bid_depth=100, ask_depth=80)
    collector.update_ofi(bid_depth=120, ask_depth=75)
    
    # Act
    snapshot = await collector.capture_snapshot()
    
    # Assert
    assert snapshot is not None
    assert isinstance(snapshot, FeatureSnapshot)
    
    # Orderbook метрики
    assert snapshot.spread_bps is not None
    assert snapshot.spread_bps > 0
    assert snapshot.depth_ratio is not None
    assert snapshot.obi_value == 0.35
    assert snapshot.ofi_value is not None  # OFI должен обновиться
    
    # Flow метрики (из словаря cvd)
    assert snapshot.whale_cvd == 50000.0
    assert snapshot.dolphin_cvd == 10000.0
    assert snapshot.fish_cvd == -5000.0
    assert snapshot.total_cvd == 55000.0
    
    # Derivatives метрики
    assert snapshot.futures_basis_apr == 12.5
    assert snapshot.basis_state == 'CONTANGO'  # WHY: basis_apr=12.5 → CONTANGO (10-20 range)
    assert snapshot.options_skew == 6.8
    assert snapshot.skew_state == 'FEAR'
    
    # Price метрики
    assert snapshot.current_price is not None
    assert snapshot.twap_5m is not None
    assert snapshot.price_vs_twap_pct is not None


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
    
    # Act
    snapshot = await collector.capture_snapshot()
    
    # Assert
    assert snapshot is not None
    
    # Orderbook метрики должны быть
    assert snapshot.spread_bps is not None
    assert snapshot.depth_ratio is not None
    assert snapshot.obi_value == 0.35  # WHY: MockOrderBook.get_weighted_obi() возвращает 0.35
    
    # Flow метрики должны быть (читаются из order_book)
    assert snapshot.whale_cvd == 50000.0
    assert snapshot.fish_cvd == -5000.0
    assert snapshot.dolphin_cvd == 10000.0
    assert snapshot.total_cvd == 55000.0
    
    # Derivatives метрики должны быть None
    assert snapshot.futures_basis_apr is None
    assert snapshot.basis_state is None


@pytest.mark.asyncio
async def test_ofi_calculation():
    """
    WHY: Проверяем расчет Order Flow Imbalance.
    
    Теория: OFI = ΔBid - ΔAsk
    Если bid увеличился на 20, а ask уменьшился на 5:
    OFI = 20 - (-5) = 25 (давление покупателей)
    """
    # Arrange
    collector = FeatureCollector(order_book=MockOrderBook())
    
    # Act
    collector.update_ofi(bid_depth=100, ask_depth=80)  # Начальное состояние
    collector.update_ofi(bid_depth=120, ask_depth=75)  # Bid +20, Ask -5
    
    snapshot = await collector.capture_snapshot()
    
    # Assert
    # ΔBid = 120 - 100 = 20
    # ΔAsk = 75 - 80 = -5
    # OFI = 20 - (-5) = 25
    assert snapshot.ofi_value == 25


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
    
    # Act
    snapshot = await collector.capture_snapshot()
    
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
    
    # Act
    snapshot = await collector.capture_snapshot()
    
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
    
    # Act
    snapshot = await collector.capture_snapshot()
    
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
    snapshot = await collector.capture_snapshot()
    
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
    
    # Act
    snapshot = await collector.capture_snapshot()
    
    # Assert
    assert snapshot.total_cvd == 55000.0


@pytest.mark.asyncio
async def test_empty_price_history():
    """
    WHY: Проверяем что пустая история не ломает расчет TWAP.
    """
    # Arrange
    collector = FeatureCollector(order_book=MockOrderBook())
    # Не добавляем цены
    
    # Act
    snapshot = await collector.capture_snapshot()
    
    # Assert
    assert snapshot.twap_5m is None
    assert snapshot.volatility_1h is None
    assert snapshot.price_vs_twap_pct is None
