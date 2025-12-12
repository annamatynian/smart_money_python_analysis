"""
WHY: Тесты для системы исторической памяти (Multi-Timeframe Context).

Теория (документ "Smart Money Analysis", раздел 3.2):
- Свинг-трейдинг требует контекста на нескольких таймфреймах
- CVD дивергенция (whale CVD ↑ while price ↓) = накопление
- Работает на 1H/4H/1D/1W таймфреймах

Task: Context (Multi-Timeframe & Accumulation) - Gemini Phase 3.2
"""

import pytest
from decimal import Decimal
from datetime import datetime, timedelta
from domain import HistoricalMemory, LocalOrderBook
from config import BTC_CONFIG


def test_historical_memory_initialization():
    """
    WHY: Проверяем что HistoricalMemory инициализируется с правильными таймфреймами.
    
    Логика:
    - 4 таймфрейма: 1H, 4H, 1D, 1W
    - Каждый таймфрейм имеет deque для CVD и price
    - Правильные maxlen для каждого таймфрейма
    """
    memory = HistoricalMemory()
    
    # ПРОВЕРКА: Все deque созданы
    assert hasattr(memory, 'cvd_history_1h')
    assert hasattr(memory, 'cvd_history_4h')
    assert hasattr(memory, 'cvd_history_1d')
    assert hasattr(memory, 'cvd_history_1w')
    
    assert hasattr(memory, 'price_history_1h')
    assert hasattr(memory, 'price_history_4h')
    assert hasattr(memory, 'price_history_1d')
    assert hasattr(memory, 'price_history_1w')
    
    # ПРОВЕРКА: Правильные maxlen
    assert memory.cvd_history_1h.maxlen == 60, \
        f"FAIL: 1H должен хранить 60 часов, получили {memory.cvd_history_1h.maxlen}"
    
    assert memory.cvd_history_4h.maxlen == 168, \
        f"FAIL: 4H должен хранить 168 записей (4 недели), получили {memory.cvd_history_4h.maxlen}"
    
    assert memory.cvd_history_1d.maxlen == 30, \
        f"FAIL: 1D должен хранить 30 дней, получили {memory.cvd_history_1d.maxlen}"
    
    assert memory.cvd_history_1w.maxlen == 52, \
        f"FAIL: 1W должен хранить 52 недели, получили {memory.cvd_history_1w.maxlen}"


def test_update_history_1h_timeframe():
    """
    WHY: Проверяем что update_history() корректно добавляет данные в 1H таймфрейм.
    
    Логика:
    - Добавляем точку данных (timestamp, whale_cvd, price)
    - Проверяем что данные попали в 1H deque
    - Проверяем что старые данные НЕ попали в 4H/1D/1W (еще рано)
    """
    memory = HistoricalMemory()
    now = datetime.now()
    
    # Добавляем данные
    memory.update_history(
        timestamp=now,
        whale_cvd=1500.0,
        price=Decimal("95000")
    )
    
    # ПРОВЕРКА: Данные в 1H
    assert len(memory.cvd_history_1h) == 1
    assert len(memory.price_history_1h) == 1
    
    cvd_entry = memory.cvd_history_1h[0]
    price_entry = memory.price_history_1h[0]
    
    assert cvd_entry[0] == now
    assert cvd_entry[1] == 1500.0
    assert price_entry[0] == now
    assert price_entry[1] == Decimal("95000")
    
    # ПРОВЕРКА: Пока НЕТ данных в остальных таймфреймах (не прошел час)
    assert len(memory.cvd_history_4h) == 0
    assert len(memory.cvd_history_1d) == 0
    assert len(memory.cvd_history_1w) == 0


def test_downsample_to_higher_timeframes():
    """
    WHY: Проверяем что данные правильно агрегируются в старшие таймфреймы.
    
    Логика:
    - Добавляем данные каждый час (61 точка = 61 час)
    - После 4 часов → должна появиться 1 точка в 4H
    - После 24 часов → должна появиться 1 точка в 1D
    - После 168 часов (7 дней) → должна появиться 1 точка в 1W
    """
    memory = HistoricalMemory()
    base_time = datetime.now() - timedelta(hours=200)  # Начинаем 200 часов назад
    
    # Добавляем по 1 точке каждый час (200 точек)
    for i in range(200):
        timestamp = base_time + timedelta(hours=i)
        whale_cvd = 1000.0 + i * 10  # CVD растет
        price = Decimal("95000") + Decimal(i * 5)  # Цена растет
        
        memory.update_history(timestamp, whale_cvd, price)
    
    # ПРОВЕРКА: 1H хранит только последние 60 часов
    assert len(memory.cvd_history_1h) == 60, \
        f"FAIL: 1H должен содержать 60 точек, получили {len(memory.cvd_history_1h)}"
    
    # ПРОВЕРКА: 4H должен содержать ~49 точек (200 часов / 4 = 50, минус 1 на инициализацию)
    expected_4h = (200 // 4) - 1
    assert len(memory.cvd_history_4h) == expected_4h, \
        f"FAIL: 4H должен содержать {expected_4h} точек, получили {len(memory.cvd_history_4h)}"
    
    # ПРОВЕРКА: 1D должен содержать ~8 точек (200 часов: 24h, 48h, 72h, 96h, 120h, 144h, 168h, 192h)
    expected_1d = 200 // 24
    assert len(memory.cvd_history_1d) == expected_1d, \
        f"FAIL: 1D должен содержать {expected_1d} точек, получили {len(memory.cvd_history_1d)}"
    
    # ПРОВЕРКА: 1W должен содержать ~1 точку (200 часов: точка на 168h)
    expected_1w = 200 // 168
    assert len(memory.cvd_history_1w) == expected_1w, \
        f"FAIL: 1W должен содержать {expected_1w} точку, получили {len(memory.cvd_history_1w)}"


def test_cvd_divergence_detection():
    """
    WHY: Проверяем детекцию накопления (CVD divergence).
    
    Логика:
    - Цена падает: 95000 → 94000 (Lower Low)
    - Whale CVD растет: 1000 → 2000 (Higher Low)
    - Это БЫЧЬЯ дивергенция (накопление) → сигнал на покупку
    """
    memory = HistoricalMemory()
    base_time = datetime.now() - timedelta(hours=10)
    
    # Сценарий накопления
    data_points = [
        (0, 95000, 1000),   # Старт
        (2, 94500, 1200),   # Цена ↓, CVD ↑
        (4, 94000, 1500),   # Цена ↓, CVD ↑
        (6, 94200, 1800),   # Цена ↑, CVD ↑
        (8, 93800, 2000),   # Цена Lower Low, CVD Higher Low!
    ]
    
    for hours_offset, price, cvd in data_points:
        timestamp = base_time + timedelta(hours=hours_offset)
        memory.update_history(timestamp, float(cvd), Decimal(str(price)))
    
    # Проверяем дивергенцию
    is_divergence, div_type = memory.detect_cvd_divergence(timeframe='1h')
    
    # ПРОВЕРКА: Дивергенция обнаружена
    assert is_divergence, \
        "FAIL: Должна быть обнаружена CVD дивергенция (накопление)"
    
    # ПРОВЕРКА: Это БЫЧЬЯ дивергенция
    assert div_type == 'BULLISH', \
        f"FAIL: Должна быть BULLISH дивергенция, получили {div_type}"


def test_no_divergence_when_aligned():
    """
    WHY: Проверяем что дивергенция НЕ детектируется когда цена и CVD движутся вместе.
    
    Логика:
    - Цена растет: 95000 → 96000
    - Whale CVD растет: 1000 → 2000
    - Нет дивергенции (нормальное движение)
    """
    memory = HistoricalMemory()
    base_time = datetime.now() - timedelta(hours=6)
    
    # Сценарий без дивергенции
    data_points = [
        (0, 95000, 1000),   # Старт
        (2, 95500, 1500),   # Оба растут
        (4, 96000, 2000),   # Оба растут
    ]
    
    for hours_offset, price, cvd in data_points:
        timestamp = base_time + timedelta(hours=hours_offset)
        memory.update_history(timestamp, float(cvd), Decimal(str(price)))
    
    is_divergence, div_type = memory.detect_cvd_divergence(timeframe='1h')
    
    # ПРОВЕРКА: Дивергенции НЕТ
    assert not is_divergence, \
        "FAIL: Не должно быть дивергенции когда цена и CVD движутся вместе"


def test_integration_with_localorderbook():
    """
    WHY: Проверяем интеграцию HistoricalMemory с LocalOrderBook.
    
    Логика:
    - LocalOrderBook имеет поле historical_memory
    - При обработке сделок вызывается update_history()
    - История накапливается автоматически
    """
    book = LocalOrderBook(symbol="BTCUSDT", config=BTC_CONFIG)
    
    # ПРОВЕРКА: У книги есть historical_memory
    assert hasattr(book, 'historical_memory'), \
        "FAIL: LocalOrderBook должен иметь поле historical_memory"
    
    assert isinstance(book.historical_memory, HistoricalMemory), \
        "FAIL: historical_memory должен быть экземпляром HistoricalMemory"
    
    # ПРОВЕРКА: Изначально история пуста
    assert len(book.historical_memory.cvd_history_1h) == 0
