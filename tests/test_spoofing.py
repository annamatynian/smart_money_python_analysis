"""
WHY: Unit-тесты для антиспуфинг функциональности (Task 1.1 & 1.2)

Покрытие:
1. CancellationContext - создание и валидация
2. IcebergLevel.is_significant_for_swing() - фильтр по времени
3. IcebergLevel.get_refill_frequency() - расчет частоты
4. SpoofingAnalyzer - все методы детекции
"""

# WHY: Добавляем родительскую папку в sys.path для импорта модулей
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from decimal import Decimal
from datetime import datetime, timedelta
from domain import IcebergLevel, CancellationContext, IcebergStatus
from analyzers import SpoofingAnalyzer
from config import BTC_CONFIG


# ===========================================================================
# FIXTURES
# ===========================================================================

@pytest.fixture
def analyzer():
    """WHY: GEMINI FIX - SpoofingAnalyzer теперь требует config"""
    return SpoofingAnalyzer(config=BTC_CONFIG)


# ===========================================================================
# ТЕСТЫ CancellationContext
# ===========================================================================

def test_cancellation_context_creation():
    """WHY: Проверка создания контекста с валидными данными"""
    ctx = CancellationContext(
        mid_price_at_cancel=Decimal("100000.50"),
        distance_from_level_pct=Decimal("0.25"),
        price_velocity_5s=Decimal("10.5"),
        moving_towards_level=True,
        volume_executed_pct=Decimal("15.0")
    )
    
    assert ctx.mid_price_at_cancel == Decimal("100000.50")
    assert ctx.moving_towards_level == True
    assert float(ctx.volume_executed_pct) == 15.0


def test_cancellation_context_edge_cases():
    """WHY: Граничные случаи - нулевые значения, отрицательные"""
    ctx = CancellationContext(
        mid_price_at_cancel=Decimal("0"),
        distance_from_level_pct=Decimal("-5.0"),  # Цена НИЖЕ уровня
        price_velocity_5s=Decimal("-100.0"),       # Цена падает
        moving_towards_level=False,
        volume_executed_pct=Decimal("0")           # Ничего не исполнено
    )
    
    assert ctx.mid_price_at_cancel == Decimal("0")
    assert float(ctx.distance_from_level_pct) == -5.0


# ===========================================================================
# ТЕСТЫ IcebergLevel.is_significant_for_swing()
# ===========================================================================

def test_is_significant_young_iceberg():
    """WHY: Молодой айсберг (<5 мин) не значим для свинга"""
    iceberg = IcebergLevel(
        price=Decimal("100000"),
        is_ask=True,
        creation_time=datetime.now() - timedelta(seconds=60)  # 1 минута
    )
    
    assert iceberg.is_significant_for_swing() == False


def test_is_significant_old_iceberg():
    """WHY: Старый айсберг (>5 мин) значим для свинга"""
    iceberg = IcebergLevel(
        price=Decimal("100000"),
        is_ask=True,
        creation_time=datetime.now() - timedelta(seconds=400)  # 6.6 минут
    )
    
    assert iceberg.is_significant_for_swing() == True


def test_is_significant_custom_threshold():
    """WHY: Проверка кастомного порога"""
    iceberg = IcebergLevel(
        price=Decimal("100000"),
        is_ask=True,
        creation_time=datetime.now() - timedelta(seconds=150)  # 2.5 минуты
    )
    
    assert iceberg.is_significant_for_swing(min_lifetime_seconds=120) == True
    assert iceberg.is_significant_for_swing(min_lifetime_seconds=200) == False


# ===========================================================================
# ТЕСТЫ IcebergLevel.get_refill_frequency()
# ===========================================================================

def test_refill_frequency_zero_for_young():
    """WHY: Молодой айсберг (<1 сек) возвращает 0.0"""
    iceberg = IcebergLevel(
        price=Decimal("100000"),
        is_ask=True,
        refill_count=5
    )
    # Айсберг создан только что
    assert iceberg.get_refill_frequency() == 0.0


def test_refill_frequency_calculation():
    """WHY: Проверка корректного расчета частоты"""
    iceberg = IcebergLevel(
        price=Decimal("100000"),
        is_ask=True,
        creation_time=datetime.now() - timedelta(minutes=2),  # 2 минуты назад
        refill_count=10
    )
    
    freq = iceberg.get_refill_frequency()
    # 10 рефиллов / 2 минуты = 5.0 refills/min
    assert 4.9 < freq < 5.1  # Допуск на погрешность времени


def test_refill_frequency_high_activity():
    """WHY: Высокая активность - много рефиллов"""
    iceberg = IcebergLevel(
        price=Decimal("100000"),
        is_ask=True,
        creation_time=datetime.now() - timedelta(minutes=1),  # 1 минута
        refill_count=30
    )
    
    freq = iceberg.get_refill_frequency()
    assert freq > 25.0  # Агрессивный алго


# ===========================================================================
# ТЕСТЫ SpoofingAnalyzer._analyze_duration()
# ===========================================================================

def test_analyze_duration_instant_spoofing(analyzer):
    """WHY: Айсберг <5 сек = гарантированно спуфинг"""
    iceberg = IcebergLevel(
        price=Decimal("100000"),
        is_ask=True,
        creation_time=datetime.now() - timedelta(seconds=2)
    )
    
    score = analyzer._analyze_duration(iceberg)
    # FIX: Теперь используем smooth function (не step)
    assert score > 0.8  # Очень короткий lifetime


def test_analyze_duration_hft(analyzer):
    """WHY: Айсберг 30 сек = вероятно HFT"""
    iceberg = IcebergLevel(
        price=Decimal("100000"),
        is_ask=True,
        creation_time=datetime.now() - timedelta(seconds=30)
    )
    
    score = analyzer._analyze_duration(iceberg)
    # FIX: Smooth function → 1/(1+0.1*30) = 0.25
    assert 0.2 < score < 0.3


def test_analyze_duration_swing_level(analyzer):
    """WHY: Айсберг >5 мин = реальный уровень"""
    iceberg = IcebergLevel(
        price=Decimal("100000"),
        is_ask=True,
        creation_time=datetime.now() - timedelta(minutes=10)
    )
    
    score = analyzer._analyze_duration(iceberg)
    # FIX: 10 мин = 600 сек → 1/(1+0.1*600) = 0.016
    assert score < 0.05


# ===========================================================================
# ТЕСТЫ SpoofingAnalyzer._analyze_cancellation_context()
# ===========================================================================

def test_analyze_cancellation_no_context(analyzer):
    """WHY: Нет контекста отмены = score 0.0"""
    iceberg = IcebergLevel(
        price=Decimal("100000"),
        is_ask=True,
        cancellation_context=None
    )
    
    score = analyzer._analyze_cancellation_context(
        iceberg, 
        Decimal("100000"), 
        []
    )
    assert score == 0.0


def test_analyze_cancellation_classic_spoofing(analyzer):
    """WHY: Классический спуфинг - отмена при приближении цены"""
    ctx = CancellationContext(
        mid_price_at_cancel=Decimal("99950"),  # Близко к уровню
        distance_from_level_pct=Decimal("0.05"),  # 0.05% = очень близко
        price_velocity_5s=Decimal("50.0"),  # Двигалась К уровню
        moving_towards_level=True,          # ДА - двигалась К уровню!
        volume_executed_pct=Decimal("5.0")  # Всего 5% исполнено
    )
    
    iceberg = IcebergLevel(
        price=Decimal("100000"),
        is_ask=True,
        cancellation_context=ctx
    )
    
    score = analyzer._analyze_cancellation_context(
        iceberg,
        Decimal("99950"),
        []
    )
    
    # moving_towards = +0.6, distance < 0.5% = +0.3, executed < 10% = +0.1
    # Total = 1.0 (с учетом погрешности float)
    assert abs(score - 1.0) < 0.01  # Допуск на float precision


def test_analyze_cancellation_legitimate(analyzer):
    """WHY: Легитимная отмена - цена уходила ОТ уровня"""
    ctx = CancellationContext(
        mid_price_at_cancel=Decimal("98000"),  # Далеко от уровня
        distance_from_level_pct=Decimal("2.0"),  # 2% = далеко
        price_velocity_5s=Decimal("-100.0"),  # Падала (от ASK уровня)
        moving_towards_level=False,  # НЕТ - уходила от уровня
        volume_executed_pct=Decimal("80.0")  # 80% исполнено
    )
    
    iceberg = IcebergLevel(
        price=Decimal("100000"),
        is_ask=True,
        cancellation_context=ctx
    )
    
    score = analyzer._analyze_cancellation_context(
        iceberg,
        Decimal("98000"),
        []
    )
    
    # Ничего не сработало = 0.0
    assert score == 0.0


# ===========================================================================
# ТЕСТЫ SpoofingAnalyzer._analyze_execution_pattern()
# ===========================================================================

def test_analyze_execution_high_frequency(analyzer):
    """WHY: Высокая частота рефиллов = легитимный алго"""
    iceberg = IcebergLevel(
        price=Decimal("100000"),
        is_ask=True,
        creation_time=datetime.now() - timedelta(minutes=1),
        refill_count=15,  # 15 refills/min
        total_hidden_volume=Decimal("5.0")
    )
    
    score = analyzer._analyze_execution_pattern(iceberg)
    assert score == 0.0


def test_analyze_execution_low_frequency(analyzer):
    """WHY: Низкая частота + малый объем = спуфинг"""
    iceberg = IcebergLevel(
        price=Decimal("100000"),
        is_ask=True,
        creation_time=datetime.now() - timedelta(minutes=5),
        refill_count=2,  # 0.4 refills/min - очень мало!
        total_hidden_volume=Decimal("0.05")  # Крошечный объем
    )
    
    score = analyzer._analyze_execution_pattern(iceberg)
    assert score > 0.5  # Должен быть подозрителен


# ===========================================================================
# ИНТЕГРАЦИОННЫЙ ТЕСТ calculate_spoofing_probability
# ===========================================================================

def test_calculate_spoofing_probability_clear_spoof(analyzer):
    """WHY: Очевидный спуфинг - все признаки указывают на манипуляцию"""
    ctx = CancellationContext(
        mid_price_at_cancel=Decimal("99990"),
        distance_from_level_pct=Decimal("0.01"),
        price_velocity_5s=Decimal("20.0"),
        moving_towards_level=True,
        volume_executed_pct=Decimal("2.0")
    )
    
    iceberg = IcebergLevel(
        price=Decimal("100000"),
        is_ask=True,
        creation_time=datetime.now() - timedelta(seconds=3),  # 3 сек
        cancellation_context=ctx,
        refill_count=1,
        total_hidden_volume=Decimal("0.08")
    )
    
    prob = analyzer.calculate_spoofing_probability(
        iceberg,
        Decimal("99990"),
        []
    )
    
    # FIX: Smooth duration → ниже scores
    # Duration: ~0.77 (30%) + Cancellation: 1.0 (50%) + Execution: ~0.8 (20%)
    assert prob >= 0.7  # Снижаем с учетом smooth function


def test_calculate_spoofing_probability_legitimate(analyzer):
    """WHY: Легитимный уровень - долгий, активный, не отменен"""
    iceberg = IcebergLevel(
        price=Decimal("100000"),
        is_ask=True,
        creation_time=datetime.now() - timedelta(minutes=10),  # 10 мин
        cancellation_context=None,  # Еще активен
        refill_count=50,  # Много рефиллов
        total_hidden_volume=Decimal("10.0")  # Большой объем
    )
    
    prob = analyzer.calculate_spoofing_probability(
        iceberg,
        Decimal("100000"),
        []
    )
    
    # Duration: ~0.016 (30%) + Cancellation: 0.0 (50%) + Execution: 0.0 (20%)
    # = 0.005
    assert prob < 0.1


def test_edge_case_short_lived_but_heavily_executed(analyzer):
    """
    WHY: Спорный случай - айсберг жил 10 сек (подозрительно), 
    НО исполнился на 40% (это реальные деньги, не спуфинг)
    
    Ожидание: Высокий volume_executed_pct должен снизить score
    """
    ctx = CancellationContext(
        mid_price_at_cancel=Decimal("99980"),
        distance_from_level_pct=Decimal("0.02"),  # Близко
        price_velocity_5s=Decimal("15.0"),
        moving_towards_level=True,  # Двигалась К
        volume_executed_pct=Decimal("40.0")  # 40% ИСПОЛНЕНО!
    )
    
    iceberg = IcebergLevel(
        price=Decimal("100000"),
        is_ask=True,
        creation_time=datetime.now() - timedelta(seconds=10),  # Короткий
        cancellation_context=ctx,
        refill_count=5,
        total_hidden_volume=Decimal("2.0")
    )
    
    prob = analyzer.calculate_spoofing_probability(
        iceberg,
        Decimal("99980"),
        []
    )
    
    # FIX: Smooth duration → 10 сек = 0.5
    # Duration: ~0.5, Cancellation: ~0.6, Execution: ~0.3
    # НО volume_executed=40% должен снизить Cancellation score!
    assert prob < 0.6, f"Expected prob < 0.6 for 40% executed, got {prob}"


def test_refill_frequency_stable_calculation():
    """
    WHY: Фиксация flaky теста - используем фиксированное время
    вместо datetime.now() для стабильных расчетов
    """
    import unittest.mock
    
    # Создаем айсберг с фиксированным временем создания
    fixed_time = datetime(2025, 1, 1, 12, 0, 0)
    
    iceberg = IcebergLevel(
        price=Decimal("100000"),
        is_ask=True,
        creation_time=fixed_time,
        refill_count=10
    )
    
    # Мокаем "текущее время" = +2 минуты
    with unittest.mock.patch('domain.datetime') as mock_datetime:
        mock_datetime.now.return_value = fixed_time + timedelta(minutes=2)
        
        freq = iceberg.get_refill_frequency()
        
        # 10 рефиллов / 2 минуты = 5.0 рефиллов/мин (точно!)
        assert freq == 5.0, f"Expected 5.0, got {freq}"
