"""
WHY: Тесты для кластеризации айсбергов в Price Zones.

Теория (документ "Smart Money Analysis", раздел 3.2):
- Айсберги на близких уровнях (<0.2% разницы) = единая зона поддержки/сопротивления
- Зона с 3+ айсбергами = "сильная зона" (институциональный интерес)
- Используется для свинг-трейдинга: вход у зон, стоп за зонами

Task: Context (Multi-Timeframe & Accumulation) - Gemini Phase 3.2
"""

import pytest
from decimal import Decimal
from datetime import datetime
from domain import LocalOrderBook, IcebergLevel, IcebergStatus, PriceZone
from config import BTC_CONFIG


def test_cluster_icebergs_into_single_zone():
    """
    WHY: Айсберги на уровнях 95000, 95050, 95100 (разница <0.2%) → одна зона.
    
    Логика:
    - 3 BID айсберга с разницей ~0.05% между уровнями
    - Должны объединиться в одну PriceZone
    - Zone.center_price ≈ 95050 (средняя)
    - Zone.total_volume = сумма всех айсбергов
    """
    book = LocalOrderBook(symbol="BTCUSDT", config=BTC_CONFIG)
    
    # Создаём 3 айсберга на близких уровнях (BID - поддержка)
    book.register_iceberg(
        price=Decimal("95000"),
        hidden_vol=Decimal("10.0"),  # 10 BTC
        is_ask=False,
        confidence=0.8
    )
    
    book.register_iceberg(
        price=Decimal("95050"),
        hidden_vol=Decimal("8.0"),
        is_ask=False,
        confidence=0.9
    )
    
    book.register_iceberg(
        price=Decimal("95100"),
        hidden_vol=Decimal("12.0"),
        is_ask=False,
        confidence=0.85
    )
    
    # Вызываем кластеризацию
    zones = book.cluster_icebergs_to_zones(tolerance_pct=0.002)  # 0.2%
    
    # ПРОВЕРКА: Должна быть 1 зона (все айсберги близко)
    assert len(zones) == 1, \
        f"FAIL: Ожидали 1 зону, получили {len(zones)}"
    
    zone = zones[0]
    
    # ПРОВЕРКА: Центр зоны ≈ средняя цена (95050)
    expected_center = (95000 + 95050 + 95100) / 3
    assert abs(float(zone.center_price) - expected_center) < 10, \
        f"FAIL: Центр зоны {zone.center_price}, ожидали ~{expected_center}"
    
    # ПРОВЕРКА: Общий объём = сумма айсбергов
    assert zone.total_volume == Decimal("30.0"), \
        f"FAIL: Объём зоны {zone.total_volume}, ожидали 30.0"
    
    # ПРОВЕРКА: 3 уровня в зоне
    assert zone.iceberg_count == 3, \
        f"FAIL: Количество уровней {zone.iceberg_count}, ожидали 3"
    
    # ПРОВЕРКА: Это BID зона (поддержка)
    assert not zone.is_ask, \
        "FAIL: Зона должна быть BID (поддержка)"


def test_separate_zones_for_distant_icebergs():
    """
    WHY: Айсберги с разницей >0.2% должны быть в разных зонах.
    
    Логика:
    - Айсберг 1: 95000 (BID)
    - Айсберг 2: 96000 (BID) — разница ~1.05% → отдельная зона
    """
    book = LocalOrderBook(symbol="BTCUSDT", config=BTC_CONFIG)
    
    book.register_iceberg(
        price=Decimal("95000"),
        hidden_vol=Decimal("10.0"),
        is_ask=False,
        confidence=0.8
    )
    
    book.register_iceberg(
        price=Decimal("96000"),  # Далеко (1.05%)
        hidden_vol=Decimal("8.0"),
        is_ask=False,
        confidence=0.9
    )
    
    zones = book.cluster_icebergs_to_zones(tolerance_pct=0.002)  # 0.2%
    
    # ПРОВЕРКА: Должно быть 2 зоны
    assert len(zones) == 2, \
        f"FAIL: Ожидали 2 зоны, получили {len(zones)}"


def test_separate_zones_for_bid_and_ask():
    """
    WHY: BID и ASK айсберги всегда в разных зонах (даже если близко по цене).
    
    Логика:
    - BID айсберг: 95000 (поддержка)
    - ASK айсберг: 95050 (сопротивление)
    - Должны быть в разных зонах (разные стороны рынка)
    """
    book = LocalOrderBook(symbol="BTCUSDT", config=BTC_CONFIG)
    
    book.register_iceberg(
        price=Decimal("95000"),
        hidden_vol=Decimal("10.0"),
        is_ask=False,  # BID
        confidence=0.8
    )
    
    book.register_iceberg(
        price=Decimal("95050"),
        hidden_vol=Decimal("8.0"),
        is_ask=True,  # ASK
        confidence=0.9
    )
    
    zones = book.cluster_icebergs_to_zones(tolerance_pct=0.002)  # 0.2%
    
    # ПРОВЕРКА: Должно быть 2 зоны (разные стороны)
    assert len(zones) == 2, \
        f"FAIL: Ожидали 2 зоны (BID и ASK отдельно), получили {len(zones)}"
    
    # ПРОВЕРКА: Одна зона BID, одна ASK
    bid_zones = [z for z in zones if not z.is_ask]
    ask_zones = [z for z in zones if z.is_ask]
    
    assert len(bid_zones) == 1, "FAIL: Должна быть 1 BID зона"
    assert len(ask_zones) == 1, "FAIL: Должна быть 1 ASK зона"


def test_strong_zone_detection():
    """
    WHY: Зона с 3+ айсбергами = "сильная зона" (институциональный интерес).
    
    Логика:
    - 3+ айсберга в зоне → is_strong = True
    - <3 айсберга → is_strong = False
    """
    book = LocalOrderBook(symbol="BTCUSDT", config=BTC_CONFIG)
    
    # Сильная зона: 3 айсберга
    for price in [95000, 95050, 95100]:
        book.register_iceberg(
            price=Decimal(str(price)),
            hidden_vol=Decimal("10.0"),
            is_ask=False,
            confidence=0.8
        )
    
    # Слабая зона: 2 айсберга (далеко от первой группы)
    for price in [96500, 96550]:
        book.register_iceberg(
            price=Decimal(str(price)),
            hidden_vol=Decimal("5.0"),
            is_ask=False,
            confidence=0.7
        )
    
    zones = book.cluster_icebergs_to_zones(tolerance_pct=0.002)  # 0.2%
    
    # ПРОВЕРКА: Должно быть 2 зоны
    assert len(zones) == 2
    
    # Находим сильную и слабую зоны
    strong_zone = next((z for z in zones if z.iceberg_count >= 3), None)
    weak_zone = next((z for z in zones if z.iceberg_count < 3), None)
    
    assert strong_zone is not None, "FAIL: Сильная зона не найдена"
    assert weak_zone is not None, "FAIL: Слабая зона не найдена"
    
    # ПРОВЕРКА: Метод is_strong()
    assert strong_zone.is_strong(), \
        f"FAIL: Зона с {strong_zone.iceberg_count} айсбергами должна быть сильной"
    
    assert not weak_zone.is_strong(), \
        f"FAIL: Зона с {weak_zone.iceberg_count} айсбергами должна быть слабой"


def test_ignore_cancelled_icebergs_in_clustering():
    """
    WHY: Отменённые айсберги не должны участвовать в кластеризации.
    
    Логика:
    - 2 активных айсберга + 1 отменённый
    - Зона должна содержать только 2 активных
    """
    book = LocalOrderBook(symbol="BTCUSDT", config=BTC_CONFIG)
    
    # Активные
    book.register_iceberg(
        price=Decimal("95000"),
        hidden_vol=Decimal("10.0"),
        is_ask=False,
        confidence=0.8
    )
    
    book.register_iceberg(
        price=Decimal("95050"),
        hidden_vol=Decimal("8.0"),
        is_ask=False,
        confidence=0.9
    )
    
    # Отменённый айсберг
    cancelled_level = book.register_iceberg(
        price=Decimal("95100"),
        hidden_vol=Decimal("5.0"),
        is_ask=False,
        confidence=0.7
    )
    cancelled_level.status = IcebergStatus.CANCELLED
    
    zones = book.cluster_icebergs_to_zones(tolerance_pct=0.002)  # 0.2%
    
    # ПРОВЕРКА: Зона содержит только 2 активных айсберга
    assert len(zones) == 1
    zone = zones[0]
    
    assert zone.iceberg_count == 2, \
        f"FAIL: Зона должна содержать 2 айсберга (без отменённого), получили {zone.iceberg_count}"
    
    assert zone.total_volume == Decimal("18.0"), \
        f"FAIL: Объём должен быть 18.0 (без отменённого), получили {zone.total_volume}"
