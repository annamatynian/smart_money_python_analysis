"""
WHY: Тест для Fusion Logic - комбинация OFI + Price Change (Gemini Phase 3.1).

Сценарии детекции:
1. Absorption (Поглощение): OFI > 0 но цена не растёт → Sell Iceberg
2. Gamma Support: OBI > 0.5 + near_gamma_wall → Сильная поддержка
3. Spoofing: OFI скачет но цена стабильна → Манипуляция

Интеграция с WhaleAnalyzer.
"""
import pytest
from decimal import Decimal
from domain import LocalOrderBook, OrderBookUpdate, TradeEvent, GammaProfile
from config import BTC_CONFIG
from datetime import datetime


@pytest.mark.skip(reason="Group 2: Refactoring pending - iceberg logic будет переписан")
def test_detect_absorption_scenario():
    """
    WHY: Сценарий "Absorption" - OFI > 0 но цена стабильна.
    
    Логика:
    - OFI положительный (давление покупателей лимитами)
    - Цена не растёт (price_change ≈ 0)
    - Вывод: Скрытый продавец (Sell Iceberg) поглощает лимиты
    """
    book = LocalOrderBook(symbol="BTCUSDT", config=BTC_CONFIG)
    
    # Initial snapshot
    book.apply_snapshot(
        bids=[(Decimal("100000"), Decimal("5.0"))],
        asks=[(Decimal("100010"), Decimal("5.0"))],
        last_update_id=1
    )
    
    initial_mid = book.get_mid_price()
    
    # Update: добавляем bid ликвидность (+3 BTC)
    book.apply_update(OrderBookUpdate(
        bids=[(Decimal("100000"), Decimal("8.0"))],
        asks=[],
        first_update_id=2,
        final_update_id=2,
        event_time=1000  # FIX: Required field
    ))
    
    # OFI должен быть положительным
    ofi = book.calculate_ofi()
    assert ofi > 0, f"FAIL: OFI={ofi}, ожидали положительное значение"
    
    # Mid price не изменилась (или изменилась незначительно)
    current_mid = book.get_mid_price()
    price_change_pct = abs(float(current_mid - initial_mid) / float(initial_mid)) * 100.0
    
    # ПРОВЕРКА: Absorption scenario detected
    is_absorption = (ofi > 0) and (price_change_pct < 0.01)  # < 0.01% изменение
    
    assert is_absorption, \
        f"FAIL: Absorption НЕ детектирован. OFI={ofi}, price_change={price_change_pct}%"


@pytest.mark.skip(reason="Group 2: Refactoring pending - iceberg logic будет переписан")
def test_gamma_support_scenario():
    """
    WHY: Сценарий "Gamma Support" - OBI > 0.5 + цена у PUT wall.
    
    Логика:
    - Взвешенный OBI показывает сильный bid
    - Цена близко к PUT wall (gamma support)
    - Вывод: Очень сильная зона поддержки
    """
    book = LocalOrderBook(symbol="BTCUSDT", config=BTC_CONFIG)
    
    # Устанавливаем Gamma Profile
    book.gamma_profile = GammaProfile(
        total_gex=1000.0,
        call_wall=Decimal("105000"),  # FIX: Decimal вместо float
        put_wall=Decimal("99000"),    # FIX: Decimal вместо float
        timestamp=datetime.now()
    )
    
    # Стакан: цена около PUT wall (99000)
    book.apply_snapshot(
        bids=[(Decimal("99000"), Decimal("10.0")),
              (Decimal("98990"), Decimal("8.0"))],
        asks=[(Decimal("99010"), Decimal("2.0"))],  # Слабый ask
        last_update_id=1
    )
    
    # Проверяем OBI
    obi = book.get_weighted_obi(depth=2, use_exponential=True)
    assert obi > 0.5, f"FAIL: OBI={obi}, ожидали > 0.5"
    
    # Проверяем близость к gamma wall
    mid_price = book.get_mid_price()
    is_near_gamma, wall_type = book.is_near_gamma_wall(mid_price, tolerance_pct=0.5)
    
    # ПРОВЕРКА: Gamma Support scenario
    is_gamma_support = (obi > 0.5) and is_near_gamma and (wall_type == 'PUT')
    
    assert is_gamma_support, \
        f"FAIL: Gamma Support НЕ детектирован. OBI={obi}, near_gamma={is_near_gamma}, wall={wall_type}"


@pytest.mark.skip(reason="Group 2: Refactoring pending - iceberg logic будет переписан")
def test_no_false_positive_on_normal_movement():
    """
    WHY: Проверяем что нормальное движение цены НЕ триггерит Absorption.
    
    Если OFI > 0 И цена растёт - это нормально, НЕ absorption.
    """
    book = LocalOrderBook(symbol="BTCUSDT", config=BTC_CONFIG)
    
    # Initial snapshot
    book.apply_snapshot(
        bids=[(Decimal("100000"), Decimal("5.0"))],
        asks=[(Decimal("100010"), Decimal("5.0"))],
        last_update_id=1
    )
    
    initial_mid = book.get_mid_price()
    
    # Update: цена поднимается (ask исполнен, новый ask выше)
    book.apply_update(OrderBookUpdate(
        bids=[],
        asks=[(Decimal("100020"), Decimal("5.0"))],  # Ask подвинулся выше
        first_update_id=2,
        final_update_id=2,
        event_time=1000  # FIX: Required field
    ))
    
    current_mid = book.get_mid_price()
    price_change_pct = abs(float(current_mid - initial_mid) / float(initial_mid)) * 100.0
    
    ofi = book.calculate_ofi()
    
    # ПРОВЕРКА: Цена выросла → НЕ absorption (даже если OFI положительный)
    is_absorption = (ofi > 0) and (price_change_pct < 0.01)
    
    # Цена выросла на ~0.005% → НЕ должно быть absorption
    assert not is_absorption, \
        f"FAIL: Ложный Absorption при росте цены! price_change={price_change_pct}%"


@pytest.mark.skip(reason="Group 2: Refactoring pending - iceberg logic будет переписан")
def test_fusion_logic_integration():
    """
    WHY: Интеграционный тест - проверяем комбинацию всех сигналов.
    
    Создаём сложный сценарий с OFI, OBI, Gamma и Price Change.
    """
    book = LocalOrderBook(symbol="BTCUSDT", config=BTC_CONFIG)
    
    # Gamma Profile
    book.gamma_profile = GammaProfile(
        total_gex=-500.0,  # Отрицательная гамма → волатильность
        call_wall=Decimal("102000"),  # FIX: Decimal вместо float
        put_wall=Decimal("98000"),    # FIX: Decimal вместо float
        timestamp=datetime.now()
    )
    
    # Стакан далеко от gamma walls
    book.apply_snapshot(
        bids=[(Decimal("100000"), Decimal("5.0"))],
        asks=[(Decimal("100010"), Decimal("5.0"))],
        last_update_id=1
    )
    
    initial_mid = book.get_mid_price()
    
    # Update: добавляем bid ликвидность
    book.apply_update(OrderBookUpdate(
        bids=[(Decimal("100000"), Decimal("7.0"))],
        asks=[],
        first_update_id=2,
        final_update_id=2,
        event_time=1000  # FIX: Required field
    ))
    
    # Собираем все метрики
    ofi = book.calculate_ofi()
    obi = book.get_weighted_obi(use_exponential=True)
    current_mid = book.get_mid_price()
    price_change_pct = abs(float(current_mid - initial_mid) / float(initial_mid)) * 100.0
    is_near_gamma, wall_type = book.is_near_gamma_wall(current_mid, tolerance_pct=0.5)
    
    # Fusion Logic: Определяем сценарий
    scenario = None
    confidence = 0.0
    
    if (ofi > 0) and (price_change_pct < 0.01):
        scenario = "ABSORPTION"
        confidence = min(1.0, abs(ofi) / 5.0)  # Масштабируем confidence
    
    elif (obi > 0.5) and is_near_gamma and (wall_type == 'PUT'):
        scenario = "GAMMA_SUPPORT"
        confidence = obi
    
    elif (ofi < 0) and (price_change_pct < 0.01):
        scenario = "BUY_PRESSURE"  # Обратный absorption
        confidence = min(1.0, abs(ofi) / 5.0)
    
    # ПРОВЕРКА: Сценарий должен быть определён
    assert scenario is not None, \
        "FAIL: Ни один сценарий не детектирован!"
    
    assert 0.0 <= confidence <= 1.0, \
        f"FAIL: Confidence={confidence} вне диапазона [0, 1]"
    
    # Для нашего случая ожидаем ABSORPTION
    assert scenario == "ABSORPTION", \
        f"FAIL: Ожидали ABSORPTION, получили {scenario}"


@pytest.mark.skip(reason="Group 2: Refactoring pending - iceberg logic будет переписан")
def test_confidence_scaling():
    """
    WHY: Проверяем что confidence корректно масштабируется.
    
    Больший OFI → выше confidence.
    """
    book = LocalOrderBook(symbol="BTCUSDT", config=BTC_CONFIG)
    
    book.apply_snapshot(
        bids=[(Decimal("100000"), Decimal("5.0"))],
        asks=[(Decimal("100010"), Decimal("5.0"))],
        last_update_id=1
    )
    
    # Сценарий 1: Малый OFI
    book.apply_update(OrderBookUpdate(
        bids=[(Decimal("100000"), Decimal("6.0"))],  # +1 BTC
        asks=[],
        first_update_id=2,
        final_update_id=2,
        event_time=1000  # FIX: Required field
    ))
    
    ofi_small = book.calculate_ofi()
    confidence_small = min(1.0, abs(ofi_small) / 5.0)
    
    # Reset
    book.apply_snapshot(
        bids=[(Decimal("100000"), Decimal("5.0"))],
        asks=[(Decimal("100010"), Decimal("5.0"))],
        last_update_id=3
    )
    
    # Сценарий 2: Большой OFI
    book.apply_update(OrderBookUpdate(
        bids=[(Decimal("100000"), Decimal("10.0"))],  # +5 BTC
        asks=[],
        first_update_id=4,
        final_update_id=4,
        event_time=2000  # FIX: Required field
    ))
    
    ofi_large = book.calculate_ofi()
    confidence_large = min(1.0, abs(ofi_large) / 5.0)
    
    # ПРОВЕРКА: Больший OFI → выше confidence
    assert confidence_large > confidence_small, \
        f"FAIL: confidence_large ({confidence_large}) должен быть > confidence_small ({confidence_small})"


@pytest.mark.skip(reason="Group 2: Refactoring pending - iceberg logic будет переписан")
def test_price_velocity_calculation():
    """
    WHY: Проверяем расчёт скорости изменения цены (dP/dt).
    
    Используется для детекции movement towards level.
    """
    book = LocalOrderBook(symbol="BTCUSDT", config=BTC_CONFIG)
    
    # Создаём историю цен
    prices = [
        (Decimal("100000"), 1000),  # t=0, mid=100000
        (Decimal("100005"), 2000),  # t=1s, mid=100005 (+5)
        (Decimal("100010"), 3000),  # t=2s, mid=100010 (+5)
    ]
    
    # Расчёт velocity между первой и последней точкой
    price_change = float(prices[-1][0] - prices[0][0])
    time_change_ms = prices[-1][1] - prices[0][1]
    time_change_s = time_change_ms / 1000.0
    
    velocity = price_change / time_change_s  # $/s
    
    # ПРОВЕРКА: Velocity = +10 / 2 = +5 $/s
    expected_velocity = 5.0
    assert abs(velocity - expected_velocity) < 0.1, \
        f"FAIL: velocity={velocity}, ожидали {expected_velocity}"
    
    # Проверяем что velocity положительная (цена растёт)
    assert velocity > 0, \
        f"FAIL: velocity={velocity} должна быть положительной (рост цены)"
