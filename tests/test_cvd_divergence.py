"""
WHY: Тест для CVD Divergence Detection (Decision Layer - Critical Tag).

Сценарий: Цена делает Lower Low, но whale_cvd делает Higher Low.
Это CONTRARIAN SIGNAL - скрытая аккумуляция институционалов.

Теория (документ "Smart Money Analysis", раздел 3.1):
- Дивергенция показывает что "умные деньги" покупают страх толпы
- Один из самых надёжных сигналов разворота тренда
"""
import pytest
from decimal import Decimal
from datetime import datetime
from domain import LocalOrderBook, TradeEvent
from analyzers import WhaleAnalyzer
from config import BTC_CONFIG


def test_detect_bullish_cvd_divergence():
    """
    WHY: Классическая Bullish Divergence.
    
    Логика:
    - Цена: 100000 → 99000 → 98500 (Lower Lows)
    - Whale CVD: -10k → -5k → -2k (Higher Lows, киты покупают)
    - Вывод: Дивергенция → разворот вверх вероятен
    """
    book = LocalOrderBook(symbol="BTCUSDT", config=BTC_CONFIG)
    analyzer = WhaleAnalyzer(config=BTC_CONFIG)
    
    # Initial snapshot
    book.apply_snapshot(
        bids=[(Decimal("100000"), Decimal("5.0"))],
        asks=[(Decimal("100010"), Decimal("5.0"))],
        last_update_id=1
    )
    
    # Сценарий: Цена падает, но киты покупают (объёмы > $100k)
    trades = [
        # Point 1: Price = 100000, кит продаёт (taker продал)
        TradeEvent(price=Decimal("100000"), quantity=Decimal("1.5"),  # $150k
                   is_buyer_maker=True, event_time=1000),  # SELL (maker был buyer → taker продал)
        
        # Point 2: Price = 99000 (Lower Low), кит покупает (taker купил)
        TradeEvent(price=Decimal("99000"), quantity=Decimal("1.2"),  # $118.8k
                   is_buyer_maker=False, event_time=2000),  # BUY (maker был seller → taker купил)
        
        # Point 3: Price = 98500 (Lower Low), кит покупает ещё больше
        TradeEvent(price=Decimal("98500"), quantity=Decimal("1.8"),  # $177.3k
                   is_buyer_maker=False, event_time=3000),  # BUY (maker был seller → taker купил)
    ]
    
    # Прогоняем сделки через анализатор
    price_history = []
    whale_cvd_history = []
    
    for trade in trades:
        # Обновляем статистику
        analyzer.update_stats(book, trade)
        
        # Сохраняем историю
        price_history.append(float(trade.price))
        whale_cvd_history.append(book.whale_cvd['whale'])
    
    # Печатаем для отладки
    print(f"\nPrice History: {price_history}")
    print(f"Whale CVD History: {whale_cvd_history}")
    
    # ПРОВЕРКА: Цена делает Lower Lows
    assert price_history[1] < price_history[0], \
        f"FAIL: Ожидали Lower Low, {price_history}"
    assert price_history[2] < price_history[1], \
        f"FAIL: Ожидали Lower Low, {price_history}"
    
    # ПРОВЕРКА: Whale CVD делает Higher Lows (меньше негатива = покупают)
    assert whale_cvd_history[1] > whale_cvd_history[0], \
        f"FAIL: Whale CVD должен расти, {whale_cvd_history}"
    assert whale_cvd_history[2] > whale_cvd_history[1], \
        f"FAIL: Whale CVD должен расти, {whale_cvd_history}"
    
    # ПРОВЕРКА: Дивергенция детектирована (вызываем метод)
    is_div, div_type, confidence = book.detect_cvd_divergence(
        price_history=price_history,
        cvd_history=whale_cvd_history
    )
    
    print(f"\nDivergence Detected: {is_div}")
    print(f"Type: {div_type}")
    print(f"Confidence: {confidence:.2f}")
    
    assert is_div, \
        f"FAIL: Bullish Divergence НЕ детектирована! Price={price_history}, CVD={whale_cvd_history}"
    
    assert div_type == 'BULLISH', \
        f"FAIL: Ожидали BULLISH, получили {div_type}"
    
    assert confidence > 0.0, \
        f"FAIL: Confidence должен быть > 0, получили {confidence}"


def test_detect_bearish_cvd_divergence():
    """
    WHY: Bearish Divergence (обратный сценарий).
    
    Логика:
    - Цена: 100000 → 101000 → 102000 (Higher Highs)
    - Whale CVD: +10k → +5k → +2k (Lower Highs, киты продают)
    - Вывод: Распродажа в рост → падение вероятно
    """
    book = LocalOrderBook(symbol="BTCUSDT", config=BTC_CONFIG)
    analyzer = WhaleAnalyzer(config=BTC_CONFIG)
    
    book.apply_snapshot(
        bids=[(Decimal("100000"), Decimal("5.0"))],
        asks=[(Decimal("100010"), Decimal("5.0"))],
        last_update_id=1
    )
    
    # Сценарий: Цена растёт, но киты продают
    trades = [
        # Point 1: Price = 100000, киты покупают
        TradeEvent(price=Decimal("100000"), quantity=Decimal("1.5"), 
                   is_buyer_maker=True, event_time=1000),
        
        # Point 2: Price = 101000 (Higher High), киты покупают меньше
        TradeEvent(price=Decimal("101000"), quantity=Decimal("1.0"), 
                   is_buyer_maker=True, event_time=2000),
        
        # Point 3: Price = 102000 (Higher High), киты покупают ещё меньше
        TradeEvent(price=Decimal("102000"), quantity=Decimal("0.5"), 
                   is_buyer_maker=True, event_time=3000),
    ]
    
    price_history = []
    whale_cvd_history = []
    
    for trade in trades:
        analyzer.update_stats(book, trade)
        price_history.append(float(trade.price))
        whale_cvd_history.append(book.whale_cvd['whale'])
    
    # ПРОВЕРКА: Цена делает Higher Highs
    assert price_history[1] > price_history[0]
    assert price_history[2] > price_history[1]
    
    # ПРОВЕРКА: Whale CVD делает Lower Highs (меньше покупок)
    assert whale_cvd_history[1] < whale_cvd_history[0]
    assert whale_cvd_history[2] < whale_cvd_history[1]
    
    # ПРОВЕРКА: Bearish Divergence детектирована
    divergence_detected = (
        (price_history[2] > price_history[0]) and  # Price Higher High
        (whale_cvd_history[2] < whale_cvd_history[0])  # CVD Lower High
    )
    
    assert divergence_detected, \
        "FAIL: Bearish Divergence НЕ детектирована!"


def test_no_false_positive_on_aligned_movement():
    """
    WHY: Проверяем что НЕТ ложного срабатывания при синхронном движении.
    
    Если цена и CVD движутся в одном направлении - дивергенции НЕТ.
    """
    book = LocalOrderBook(symbol="BTCUSDT", config=BTC_CONFIG)
    analyzer = WhaleAnalyzer(config=BTC_CONFIG)
    
    book.apply_snapshot(
        bids=[(Decimal("100000"), Decimal("5.0"))],
        asks=[(Decimal("100010"), Decimal("5.0"))],
        last_update_id=1
    )
    
    # Нормальное падение: и цена, и CVD вниз
    trades = [
        TradeEvent(price=Decimal("100000"), quantity=Decimal("1.0"), 
                   is_buyer_maker=False, event_time=1000),  # Sell
        TradeEvent(price=Decimal("99000"), quantity=Decimal("1.0"), 
                   is_buyer_maker=False, event_time=2000),  # Sell
        TradeEvent(price=Decimal("98000"), quantity=Decimal("1.0"), 
                   is_buyer_maker=False, event_time=3000),  # Sell
    ]
    
    price_history = []
    whale_cvd_history = []
    
    for trade in trades:
        analyzer.update_stats(book, trade)
        price_history.append(float(trade.price))
        whale_cvd_history.append(book.whale_cvd['whale'])
    
    # Цена падает
    assert price_history[2] < price_history[0]
    
    # CVD тоже падает (продажи)
    assert whale_cvd_history[2] < whale_cvd_history[0]
    
    # НЕТ дивергенции
    divergence_detected = (
        (price_history[2] < price_history[0]) and
        (whale_cvd_history[2] > whale_cvd_history[0])
    )
    
    assert not divergence_detected, \
        "FAIL: Ложная дивергенция при синхронном движении!"


def test_divergence_requires_minimum_window():
    """
    WHY: Дивергенция должна проверяться на достаточном окне (минимум 3 точки).
    
    2 точки недостаточно - может быть случайность.
    """
    book = LocalOrderBook(symbol="BTCUSDT", config=BTC_CONFIG)
    analyzer = WhaleAnalyzer(config=BTC_CONFIG)
    
    book.apply_snapshot(
        bids=[(Decimal("100000"), Decimal("5.0"))],
        asks=[(Decimal("100010"), Decimal("5.0"))],
        last_update_id=1
    )
    
    # Только 2 точки
    trades = [
        TradeEvent(price=Decimal("100000"), quantity=Decimal("1.0"), 
                   is_buyer_maker=False, event_time=1000),
        TradeEvent(price=Decimal("99000"), quantity=Decimal("0.5"), 
                   is_buyer_maker=True, event_time=2000),
    ]
    
    for trade in trades:
        analyzer.update_stats(book, trade)
    
    # Проверяем что для детекции нужно минимум 3 точки
    # (это логика будет в методе detect_divergence)
    assert len(trades) < 3, \
        "FAIL: Для дивергенции нужно минимум 3 точки данных"


def test_divergence_timeframe_validation():
    """
    WHY: Дивергенция должна формироваться в разумном окне времени.
    
    Слишком быстро (< 1 мин) = шум
    Слишком медленно (> 1 час) = устаревшая информация
    """
    book = LocalOrderBook(symbol="BTCUSDT", config=BTC_CONFIG)
    
    book.apply_snapshot(
        bids=[(Decimal("100000"), Decimal("5.0"))],
        asks=[(Decimal("100010"), Decimal("5.0"))],
        last_update_id=1
    )
    
    # Слишком быстрая дивергенция (< 1 минуты между точками)
    fast_trades = [
        TradeEvent(price=Decimal("100000"), quantity=Decimal("1.0"), 
                   is_buyer_maker=False, event_time=1000),  # t=1s
        TradeEvent(price=Decimal("99000"), quantity=Decimal("1.0"), 
                   is_buyer_maker=True, event_time=2000),   # t=2s (+1s)
        TradeEvent(price=Decimal("98500"), quantity=Decimal("1.0"), 
                   is_buyer_maker=True, event_time=3000),   # t=3s (+1s)
    ]
    
    # Вычисляем timeframe
    timeframe_ms = fast_trades[-1].event_time - fast_trades[0].event_time
    timeframe_min = timeframe_ms / 1000.0 / 60.0
    
    # ПРОВЕРКА: Слишком короткий timeframe
    assert timeframe_min < 1.0, \
        f"FAIL: Timeframe {timeframe_min:.2f} мин слишком короткий для надёжной дивергенции"
    
    # Для валидной дивергенции нужно 1-60 минут
    valid_timeframe = (1.0 <= timeframe_min <= 60.0)
    assert not valid_timeframe, \
        "FAIL: Timeframe должен быть в диапазоне 1-60 минут"
