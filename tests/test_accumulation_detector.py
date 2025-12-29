"""
WHY: Тесты для детектора накопления/дистрибуции (AccumulationDetector).

Теория (документ "Smart Money Analysis", раздел 3.2):
- Накопление = Whale CVD растет, пока цена падает (BULLISH divergence)
- Дистрибуция = Whale CVD падает, пока цена растет (BEARISH divergence)
- Корреляция с айсберг-зонами усиливает сигнал

Task: Context (Multi-Timeframe & Accumulation) - Gemini Phase 3.2
"""

import pytest
from decimal import Decimal
from datetime import datetime, timedelta
from domain import LocalOrderBook, IcebergLevel, TradeEvent
from analyzers import AccumulationDetector
from config import BTC_CONFIG


def test_detect_bullish_divergence_1h():
    """
    WHY: Проверяем детекцию БЫЧЬЕЙ дивергенции на 1H таймфрейме.
    
    Сценарий:
    - Цена падает: 95000 → 94000 (Lower Low)
    - Whale CVD растет: 1000 → 2000 (Higher Low)
    - Результат: BULLISH накопление
    """
    book = LocalOrderBook(symbol="BTCUSDT", config=BTC_CONFIG)
    detector = AccumulationDetector(book)
    
    # WHY: Инициализируем стакан (чтобы get_mid_price() работал)
    book.apply_snapshot(
        bids=[(Decimal("93700"), Decimal("1.0")), (Decimal("93600"), Decimal("2.0"))],
        asks=[(Decimal("93900"), Decimal("1.0")), (Decimal("94000"), Decimal("2.0"))],
        last_update_id=1
    )
    
    # Симулируем данные с накоплением
    base_time = datetime.now() - timedelta(hours=5)
    scenarios = [
        # (hours, price, whale_cvd, minnow_cvd)
        (0, Decimal("95000"), 1000.0, 500.0),   # Старт
        (1, Decimal("94500"), 1200.0, 400.0),   # Цена ↓, Whale ↑, Minnow ↓
        (2, Decimal("94000"), 1500.0, 300.0),   # Цена ↓, Whale ↑, Minnow ↓
        (3, Decimal("94200"), 1800.0, 250.0),   # Отскок, Whale ↑, Minnow ↓
        (4, Decimal("93800"), 2000.0, 200.0),   # Lower Low + Higher Whale + Lower Minnow!
    ]
    
    for hours, price, whale_cvd, minnow_cvd in scenarios:
        timestamp = base_time + timedelta(hours=hours)
        book.historical_memory.update_history(timestamp, whale_cvd, minnow_cvd, price)
    
    # Детектируем
    result = detector.detect_accumulation(timeframe='1h')
    
    # ПРОВЕРКА: Обнаружена дивергенция
    assert result is not None, "FAIL: Должна быть обнаружена дивергенция"
    
    assert result['type'] == 'BULLISH', \
        f"FAIL: Должна быть BULLISH дивергенция, получили {result['type']}"
    
    assert result['timeframe'] == '1h', \
        f"FAIL: Таймфрейм должен быть 1h, получили {result['timeframe']}"
    
    assert result['confidence'] >= 0.5, \
        f"FAIL: Confidence должна быть >=0.5, получили {result['confidence']}"
    
    # === НОВЫЕ ПОЛЯ: Wyckoff Pattern ===
    assert 'wyckoff_pattern' in result, "FAIL: Должно быть поле wyckoff_pattern"
    assert result['wyckoff_pattern'] in ['SPRING', 'ACCUMULATION'], \
        f"FAIL: Паттерн должен быть SPRING или ACCUMULATION, получили {result['wyckoff_pattern']}"
    
    assert 'absorption_detected' in result, "FAIL: Должно быть поле absorption_detected"
    assert 'obi_confirms' in result, "FAIL: Должно быть поле obi_confirms"


def test_detect_bearish_divergence_4h():
    """
    WHY: Проверяем детекцию МЕДВЕЖЬЕЙ дивергенции на 4H таймфрейме.
    
    Сценарий:
    - Цена растет: 95000 → 96000 (Higher High)
    - Whale CVD падает: 2000 → 1000 (Lower High)
    - Результат: BEARISH дистрибуция
    """
    book = LocalOrderBook(symbol="BTCUSDT", config=BTC_CONFIG)
    detector = AccumulationDetector(book)
    
    # Симулируем данные с дистрибуцией
    base_time = datetime.now() - timedelta(hours=20)
    scenarios = [
        # (hours, price, whale_cvd, minnow_cvd)
        (0, Decimal("95000"), 2000.0, 200.0),    # Старт
        (4, Decimal("95500"), 1800.0, 300.0),    # Цена ↑, Whale ↓, Minnow ↑
        (8, Decimal("96000"), 1500.0, 400.0),    # Цена ↑, Whale ↓, Minnow ↑
        (12, Decimal("95800"), 1300.0, 450.0),   # Откат, Whale ↓, Minnow ↑
        (16, Decimal("96200"), 1000.0, 500.0),   # Higher High + Lower Whale + Higher Minnow!
    ]
    
    for hours, price, whale_cvd, minnow_cvd in scenarios:
        timestamp = base_time + timedelta(hours=hours)
        book.historical_memory.update_history(timestamp, whale_cvd, minnow_cvd, price)
    
    result = detector.detect_accumulation(timeframe='4h')
    
    # ПРОВЕРКА: Обнаружена МЕДВЕЖЬЯ дивергенция
    assert result is not None
    assert result['type'] == 'BEARISH', \
        f"FAIL: Должна быть BEARISH дивергенция, получили {result['type']}"
    
    assert result['timeframe'] == '4h'
    
    # === НОВЫЕ ПОЛЯ ===
    assert result['wyckoff_pattern'] in ['UPTHRUST', 'DISTRIBUTION'], \
        f"FAIL: Паттерн должен быть UPTHRUST или DISTRIBUTION, получили {result['wyckoff_pattern']}"


def test_no_divergence_when_aligned():
    """
    WHY: Проверяем что дивергенция НЕ детектируется когда цена и CVD движутся вместе.
    
    Сценарий:
    - Цена растет: 95000 → 96000
    - Whale CVD растет: 1000 → 2000
    - Результат: Нет дивергенции
    """
    book = LocalOrderBook(symbol="BTCUSDT", config=BTC_CONFIG)
    detector = AccumulationDetector(book)
    
    base_time = datetime.now() - timedelta(hours=3)
    scenarios = [
        # WHY: Both price and CVD rising (no divergence)
        (0, Decimal("95000"), 1000.0, 500.0),
        (1, Decimal("95500"), 1500.0, 600.0),
        (2, Decimal("96000"), 2000.0, 700.0),
    ]
    
    for hours, price, whale_cvd, minnow_cvd in scenarios:
        timestamp = base_time + timedelta(hours=hours)
        book.historical_memory.update_history(timestamp, whale_cvd, minnow_cvd, price)
    
    result = detector.detect_accumulation(timeframe='1h')
    
    # ПРОВЕРКА: Дивергенции НЕТ
    assert result is None, \
        "FAIL: Не должно быть дивергенции когда цена и CVD движутся вместе"


def test_correlation_with_iceberg_zones():
    """
    WHY: Проверяем корреляцию дивергенции с айсберг-зонами.
    
    Логика:
    - BULLISH дивергенция + сильная BID зона → высокая вероятность отбоя
    - BEARISH дивергенция + сильная ASK зона → высокая вероятность разворота
    - Дивергенция без зон → средняя вероятность
    """
    book = LocalOrderBook(symbol="BTCUSDT", config=BTC_CONFIG)
    detector = AccumulationDetector(book)
    
    # WHY: Инициализируем стакан (чтобы get_mid_price() работал)
    book.apply_snapshot(
        bids=[(Decimal("93700"), Decimal("1.0")), (Decimal("93600"), Decimal("2.0"))],
        asks=[(Decimal("93900"), Decimal("1.0")), (Decimal("94000"), Decimal("2.0"))],
        last_update_id=1
    )
    
    # 1. Создаём BULLISH дивергенцию
    base_time = datetime.now() - timedelta(hours=5)
    scenarios = [
        (0, Decimal("95000"), 1000.0, 500.0),
        (2, Decimal("94500"), 1500.0, 400.0),
        (4, Decimal("94000"), 2000.0, 300.0),  # Lower Low price, Higher Whale, Lower Minnow
    ]
    
    for hours, price, whale_cvd, minnow_cvd in scenarios:
        timestamp = base_time + timedelta(hours=hours)
        book.historical_memory.update_history(timestamp, whale_cvd, minnow_cvd, price)
    
    # 2. Создаём сильную BID зону ОКОЛО текущей цены (93800)
    # WHY: Зона должна быть близко к финальной цене (93800)
    for price in [93750, 93800, 93850]:  # Зона вокруг 93800
        book.register_iceberg(
            price=Decimal(str(price)),
            hidden_vol=Decimal("10.0"),
            is_ask=False,
            confidence=0.8
        )
    
    # Детектируем с корреляцией
    result = detector.detect_accumulation(timeframe='1h')
    
    # ПРОВЕРКА: Дивергенция найдена
    assert result is not None
    assert result['type'] == 'BULLISH'
    
    # ПРОВЕРКА: Корреляция с зоной усиливает confidence
    assert result['near_strong_zone'], \
        "FAIL: Должна быть детектирована близость к сильной зоне"
    
    assert result['confidence'] >= 0.7, \
        f"FAIL: Confidence с зоной должна быть >=0.7, получили {result['confidence']}"
    
    # === ПРОВЕРКА: Если рядом сильная зона → высокий шанс SPRING ===
    # WHY: Зона + Absorption + OBI → может быть SPRING
    assert result['wyckoff_pattern'] in ['SPRING', 'ACCUMULATION'], \
        f"FAIL: Паттерн должен быть SPRING или ACCUMULATION, получили {result['wyckoff_pattern']}"


def test_multi_timeframe_analysis():
    """
    WHY: Проверяем анализ на нескольких таймфреймах одновременно.
    
    Логика:
    - Дивергенция на старшем таймфрейме (4H/1D) важнее чем на 1H
    - Дивергенция на нескольких таймфреймах → очень высокая confidence
    """
    book = LocalOrderBook(symbol="BTCUSDT", config=BTC_CONFIG)
    detector = AccumulationDetector(book)
    
    # Создаём дивергенцию на 1H и 4H одновременно
    base_time = datetime.now() - timedelta(hours=20)
    
    for i in range(20):
        timestamp = base_time + timedelta(hours=i)
        # Цена падает
        price = Decimal("95000") - Decimal(i * 50)
        # Whale CVD растет
        whale_cvd = 1000.0 + i * 100
        # Minnow CVD падает (паника)
        minnow_cvd = 500.0 - i * 10
        
        book.historical_memory.update_history(timestamp, whale_cvd, minnow_cvd, price)
    
    # Анализируем все таймфреймы
    results = detector.detect_accumulation_multi_timeframe()
    
    # ПРОВЕРКА: Дивергенция на нескольких таймфреймах
    assert '1h' in results, "FAIL: Должна быть дивергенция на 1H"
    assert '4h' in results, "FAIL: Должна быть дивергенция на 4H"
    
    # ПРОВЕРКА: Все BULLISH
    for tf, result in results.items():
        assert result['type'] == 'BULLISH', \
            f"FAIL: {tf} должен быть BULLISH"


def test_insufficient_data():
    """
    WHY: Проверяем поведение при недостатке данных.
    
    Логика:
    - Нужно минимум 3 точки для дивергенции
    - Если данных мало → None
    """
    book = LocalOrderBook(symbol="BTCUSDT", config=BTC_CONFIG)
    detector = AccumulationDetector(book)
    
    # Добавляем только 2 точки (недостаточно)
    base_time = datetime.now()
    book.historical_memory.update_history(base_time, 1000.0, 500.0, Decimal("95000"))
    book.historical_memory.update_history(
        base_time + timedelta(hours=1), 
        1200.0,
        450.0,
        Decimal("94000")
    )
    
    result = detector.detect_accumulation(timeframe='1h')
    
    # ПРОВЕРКА: Нет результата (недостаточно данных)
    assert result is None, \
        "FAIL: Не должно быть результата при недостатке данных"
