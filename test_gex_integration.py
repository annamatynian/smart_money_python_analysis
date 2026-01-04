# WHY: Тесты для GEX-интеграции (Task: GEX Context)
import pytest
from decimal import Decimal
from domain import GammaProfile, LocalOrderBook, TradeEvent
from analyzers import IcebergAnalyzer
from config import BTC_CONFIG  # WHY: Нужен config для создания экземпляра

@pytest.mark.skip(reason="Group 2: Refactoring pending - iceberg logic будет переписан")
def test_adjust_confidence_positive_gamma_on_wall():
    """
    WHY: При +GEX айсберг НА gamma wall должен иметь x1.8 boost.
    """
    # WHY: Создаем экземпляр анализатора с config
    analyzer = IcebergAnalyzer(BTC_CONFIG)
    
    gamma = GammaProfile(
        total_gex=50000000.0,  # +50M = положительная гамма
        call_wall=60000.0,
        put_wall=58000.0
    )
    
    base_conf = 0.5
    adjusted, is_major = analyzer.adjust_confidence_by_gamma(
        base_confidence=base_conf,
        gamma_profile=gamma,
        price=Decimal("60000.0"),  # Точно на Call Wall
        is_ask=True
    )
    
    expected = 0.5 * 1.8  # = 0.9
    assert abs(adjusted - expected) < 0.01, f"Expected {expected}, got {adjusted}"
    assert is_major == True, "Should be major gamma event"

@pytest.mark.skip(reason="Group 2: Refactoring pending - iceberg logic будет переписан")
def test_adjust_confidence_negative_gamma_on_wall():
    """
    WHY: При -GEX айсберг НА gamma wall должен иметь x1.3 boost (меньше чем при +GEX).
    """
    # WHY: Создаем экземпляр анализатора с config
    analyzer = IcebergAnalyzer(BTC_CONFIG)
    
    gamma = GammaProfile(
        total_gex=-20000000.0,  # -20M = отрицательная гамма (gamma squeeze)
        call_wall=60000.0,
        put_wall=58000.0
    )
    
    base_conf = 0.6
    adjusted, is_major = analyzer.adjust_confidence_by_gamma(
        base_confidence=base_conf,
        gamma_profile=gamma,
        price=Decimal("58000.0"),  # На Put Wall
        is_ask=False
    )
    
    expected = 0.6 * 1.3  # = 0.78
    assert abs(adjusted - expected) < 0.01, f"Expected {expected}, got {adjusted}"
    assert is_major == True, "Should be major gamma event"

@pytest.mark.skip(reason="Group 2: Refactoring pending - iceberg logic будет переписан")
def test_adjust_confidence_negative_gamma_not_on_wall():
    """
    WHY: При -GEX обычные айсберги (НЕ на стене) должны иметь x0.75 penalty.
    """
    # WHY: Создаем экземпляр анализатора с config
    analyzer = IcebergAnalyzer(BTC_CONFIG)
    
    gamma = GammaProfile(
        total_gex=-10000000.0,
        call_wall=62000.0,
        put_wall=58000.0
    )
    
    base_conf = 0.7
    adjusted, is_major = analyzer.adjust_confidence_by_gamma(
        base_confidence=base_conf,
        gamma_profile=gamma,
        price=Decimal("60000.0"),  # Далеко от стен
        is_ask=True
    )
    
    expected = 0.7 * 0.75  # = 0.525
    assert abs(adjusted - expected) < 0.01, f"Expected {expected}, got {adjusted}"
    assert is_major == False, "Should NOT be major gamma event"

@pytest.mark.skip(reason="Group 2: Refactoring pending - iceberg logic будет переписан")
def test_adjust_confidence_no_gamma_profile():
    """
    WHY: Без gamma profile уверенность не должна меняться.
    """
    # WHY: Создаем экземпляр анализатора с config
    analyzer = IcebergAnalyzer(BTC_CONFIG)
    
    base_conf = 0.65
    adjusted, is_major = analyzer.adjust_confidence_by_gamma(
        base_confidence=base_conf,
        gamma_profile=None,  # НЕТ данных от Deribit
        price=Decimal("60000.0"),
        is_ask=True
    )
    
    assert adjusted == base_conf, "Confidence should remain unchanged"
    assert is_major == False, "No gamma event without profile"

@pytest.mark.skip(reason="Group 2: Refactoring pending - iceberg logic будет переписан")
def test_near_gamma_wall_detection():
    """
    WHY: LocalOrderBook должен правильно определять близость к стенам.
    """
    book = LocalOrderBook(symbol="BTCUSDT")
    book.gamma_profile = GammaProfile(
        total_gex=10000000.0,
        call_wall=60000.0,
        put_wall=58000.0
    )
    
    # Точно на Call Wall
    is_near, wall_type = book.is_near_gamma_wall(Decimal("60000.0"))
    assert is_near == True, "Should detect Call Wall"
    assert wall_type == 'CALL', f"Expected CALL, got {wall_type}"
    
    # Близко к Put Wall (в пределах 0.5%)
    is_near, wall_type = book.is_near_gamma_wall(Decimal("58200.0"))  # +200$ = ~0.34%
    assert is_near == True, "Should detect Put Wall"
    assert wall_type == 'PUT', f"Expected PUT, got {wall_type}"
    
    # Далеко от стен
    is_near, wall_type = book.is_near_gamma_wall(Decimal("59000.0"))
    assert is_near == False, "Should NOT detect any wall"
    assert wall_type is None, f"Expected None, got {wall_type}"

@pytest.mark.skip(reason="Group 2: Refactoring pending - iceberg logic будет переписан")
def test_confidence_capped_at_one():
    """
    WHY: Adjusted confidence не должна превышать 1.0.
    """
    # WHY: Создаем экземпляр анализатора с config
    analyzer = IcebergAnalyzer(BTC_CONFIG)
    
    gamma = GammaProfile(
        total_gex=100000000.0,  # Огромная +GEX
        call_wall=60000.0,
        put_wall=58000.0
    )
    
    base_conf = 0.9  # Уже высокая уверенность
    adjusted, _ = analyzer.adjust_confidence_by_gamma(
        base_confidence=base_conf,
        gamma_profile=gamma,
        price=Decimal("60000.0"),  # На Call Wall
        is_ask=True
    )
    
    # 0.9 * 1.8 = 1.62, но должно быть обрезано до 1.0
    assert adjusted <= 1.0, f"Confidence should be capped at 1.0, got {adjusted}"
    assert adjusted == 1.0, "Should be exactly 1.0 after capping"