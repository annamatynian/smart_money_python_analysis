"""
Тесты для расширенной системы обнаружения алгоритмов (TWAP/VWAP/ICEBERG/SWEEP).

WHY: Проверяет способность WhaleAnalyzer различать типы алгоритмов
на основе анализа временных интервалов и размерных паттернов.
"""

import pytest
from decimal import Decimal
from datetime import datetime
from domain import LocalOrderBook, TradeEvent, AlgoDetectionMetrics
from analyzers import WhaleAnalyzer
from config import BTC_CONFIG


def create_test_book(symbol="BTCUSDT") -> LocalOrderBook:
    """Создает пустой LocalOrderBook для тестов"""
    return LocalOrderBook(symbol=symbol, config=BTC_CONFIG)


def create_test_trade(
    price: float,
    quantity: float,
    is_sell: bool,
    event_time_ms: int
) -> TradeEvent:
    """
    Создает тестовую сделку.
    
    Args:
        price: Цена в USD
        quantity: Количество в BTC
        is_sell: True если продажа (is_buyer_maker=True)
        event_time_ms: Timestamp в миллисекундах
    """
    return TradeEvent(
        price=Decimal(str(price)),
        quantity=Decimal(str(quantity)),
        is_buyer_maker=is_sell,
        event_time=event_time_ms
    )


# ===========================================================================
# UNIT ТЕСТЫ - AlgoDetectionMetrics
# ===========================================================================

def test_algo_detection_metrics_creation():
    """Проверка создания AlgoDetectionMetrics"""
    metrics = AlgoDetectionMetrics(
        std_dev_intervals_ms=5.0,
        mean_interval_ms=250.0,
        size_uniformity_score=0.95,
        dominant_size_usd=1000.0,
        directional_ratio=0.92,
        algo_type="ICEBERG",
        confidence=0.93
    )
    
    assert metrics.algo_type == "ICEBERG"
    assert metrics.confidence == 0.93
    assert metrics.size_uniformity_score == 0.95


def test_algo_detection_metrics_defaults():
    """Проверка default значений"""
    metrics = AlgoDetectionMetrics(
        std_dev_intervals_ms=10.0,
        mean_interval_ms=200.0,
        size_uniformity_score=0.6,
        dominant_size_usd=500.0,
        directional_ratio=0.88
    )
    
    # WHY: algo_type и confidence должны быть None/0.0 по умолчанию
    assert metrics.algo_type is None
    assert metrics.confidence == 0.0


# ===========================================================================
# ИНТЕГРАЦИОННЫЕ ТЕСТЫ - WhaleAnalyzer с LocalOrderBook
# ===========================================================================

def test_twap_detection_constant_intervals():
    """
    TWAP: 200 сделок с ПОСТОЯННЫМ интервалом 250ms ± 5ms (CV < 10%)
    
    Expected: algo_type='TWAP', confidence > 0.85
    
    WHY (HYBRID APPROACH):
    - Используем $100-200 сделки (0.002-0.004 BTC) для гарантии minnow
    - Тестируем ПОЛНУЮ логику: статические пороги → динамические пороги
    - Размеры реалистичны для algo (дробление на мелкие части)
    """
    book = create_test_book()
    analyzer = WhaleAnalyzer(BTC_CONFIG)
    
    base_time = 1000000  # Начальный timestamp
    base_price = 50000.0
    
    # Генерируем 200 сделок с интервалом 250ms ± 5ms (очень стабильно)
    for i in range(200):
        # WHY: Малый jitter (±5ms) создает CV < 10% → TWAP
        jitter = (i % 3) * 2 - 2  # -2, 0, +2
        trade_time = base_time + i * 250 + jitter
        
        # HYBRID FIX: 2 значения $99.99-$100 = 0.0019998-0.002 BTC
        # WHY: 20-й перцентиль = $99.99, threshold = max($99.99, $100) = $100
        # Все сделки ($99.99, $100) <= $100 → minnow
        # size_uniformity = 50% < 0.9 → НЕ ICEBERG
        quantity = 0.0019998 if i % 2 == 0 else 0.002  # $99.99 или $100
        
        trade = create_test_trade(
            price=base_price,
            quantity=quantity,
            is_sell=True,    # Все продажи
            event_time_ms=trade_time
        )
        
        category, volume_usd, algo_alert = analyzer.update_stats(book, trade)
    
    # EDGE CASE CHECK: Проверяем что динамические пороги не сломали логику
    whale_thresh, minnow_thresh = analyzer._calculate_dynamic_thresholds(book)
    assert minnow_thresh <= 500.0, f"Dynamic minnow threshold too high: {minnow_thresh} (breaks test assumptions)"
    
    # Проверяем результат
    assert algo_alert != False, f"TWAP должен быть обнаружен (minnow count: {len(book.algo_window)})"
    assert "TWAP" in algo_alert, f"Expected TWAP, got {algo_alert}"
    assert book.last_algo_detection is not None
    assert book.last_algo_detection.algo_type == "TWAP"
    assert book.last_algo_detection.confidence > 0.85


def test_twap_no_false_positive():
    """
    Обычная торговля (хаотичные интервалы) НЕ должна триггерить TWAP
    
    Expected: algo_alert = False или не TWAP
    """
    book = create_test_book()
    analyzer = WhaleAnalyzer(BTC_CONFIG)
    
    base_time = 1000000
    base_price = 50000.0
    
    # Генерируем 200 сделок с ХАОТИЧНЫМИ интервалами (50-500ms)
    import random
    random.seed(42)
    
    current_time = base_time
    for i in range(200):
        # WHY: Большой разброс → CV > 50% → НЕ алгоритм
        interval = random.randint(50, 500)
        current_time += interval
        
        trade = create_test_trade(
            price=base_price,
            quantity=0.001,
            is_sell=True,
            event_time_ms=current_time
        )
        
        category, volume_usd, algo_alert = analyzer.update_stats(book, trade)
    
    # Проверяем что TWAP НЕ обнаружен
    if algo_alert:
        assert "TWAP" not in algo_alert, f"False positive: {algo_alert}"


def test_vwap_detection_variable_intervals():
    """
    VWAP: 200 сделок с ПЕРЕМЕННЫМИ интервалами (CV 20-50%)
    
    Expected: algo_type='VWAP', confidence > 0.7
    
    WHY (HYBRID APPROACH):
    - Используем $100 сделки для гарантии minnow
    - Увеличиваем амплитуду волны до 0-200ms для CV ~30%
    - КРИТИЧНО: mean_interval должен быть > 50ms чтобы не попасть в SWEEP!
    """
    book = create_test_book()
    analyzer = WhaleAnalyzer(BTC_CONFIG)
    
    base_time = 1000000
    base_price = 50000.0
    
    # Генерируем 200 сделок с СИЛЬНОЙ волновой вариацией
    current_time = base_time
    for i in range(200):
        # КРИТИЧНО: Увеличиваем волну до 0-200ms для CV ~30%
        # WHY: wave 0-200ms при base=200ms даёт CV ≈ 30%
        # mean = (200 + 400) / 2 = 300ms > 50ms → не SWEEP!
        wave = int(200 * (i % 10) / 10)  # 0-200ms волна
        interval = 200 + wave  # 200-400ms с сильными волнами
        current_time += interval
        
        # HYBRID FIX: $99.99-$100
        quantity = 0.0019998 if i % 2 == 0 else 0.002
        
        trade = create_test_trade(
            price=base_price,
            quantity=quantity,
            is_sell=True,
            event_time_ms=current_time
        )
        
        category, volume_usd, algo_alert = analyzer.update_stats(book, trade)
    
    # EDGE CASE CHECK
    whale_thresh, minnow_thresh = analyzer._calculate_dynamic_thresholds(book)
    assert minnow_thresh <= 500.0, f"Dynamic minnow threshold too high: {minnow_thresh}"
    
    # Проверяем результат
    assert algo_alert != False, "VWAP должен быть обнаружен"
    assert "VWAP" in algo_alert, f"Expected VWAP, got {algo_alert} (mean: {book.last_algo_detection.mean_interval_ms if book.last_algo_detection else 'N/A'}ms, metrics: {book.last_algo_detection})"
    assert book.last_algo_detection is not None
    assert book.last_algo_detection.algo_type == "VWAP"
    assert book.last_algo_detection.confidence > 0.7


def test_iceberg_algo_detection_fixed_size():
    """
    Iceberg Algo: 200 сделок ОДИНАКОВОГО размера
    
    Expected: algo_type='ICEBERG', size_uniformity > 0.9
    
    WHY (HYBRID APPROACH):
    - Используем 0.003 BTC = $150 (ФИКСИРОВАННЫЙ)
    - Проверяем что size_uniformity имеет ПРИОРИТЕТ над timing в классификаторе
    """
    book = create_test_book()
    analyzer = WhaleAnalyzer(BTC_CONFIG)
    
    base_time = 1000000
    base_price = 50000.0
    # HYBRID FIX: 0.002 BTC * $50k = $100 → гарантированно minnow
    fixed_quantity = 0.002  # ФИКСИРОВАННЫЙ display_qty
    
    # Генерируем 200 сделок АБСОЛЮТНО ОДИНАКОВОГО размера
    for i in range(200):
        trade_time = base_time + i * 200  # Интервал 200ms
        
        trade = create_test_trade(
            price=base_price,
            quantity=fixed_quantity,  # КРИТИЧНО: Одинаковый размер
            is_sell=True,
            event_time_ms=trade_time
        )
        
        category, volume_usd, algo_alert = analyzer.update_stats(book, trade)
    
    # EDGE CASE CHECK
    whale_thresh, minnow_thresh = analyzer._calculate_dynamic_thresholds(book)
    assert minnow_thresh <= 500.0, f"Dynamic minnow threshold too high: {minnow_thresh}"
    
    # Проверяем что все сделки классифицированы как minnow
    assert category == 'minnow', f"Expected minnow, got {category} (volume: {volume_usd})"
    
    # Проверяем результат
    assert algo_alert != False, f"ICEBERG должен быть обнаружен (last category: {category})"
    assert "ICEBERG" in algo_alert, f"Expected ICEBERG, got {algo_alert}"
    assert book.last_algo_detection is not None
    assert book.last_algo_detection.algo_type == "ICEBERG"
    assert book.last_algo_detection.size_uniformity_score > 0.9


def test_sweep_algo_detection():
    """
    Sweep Algo: 200 сделок с ОЧЕНЬ короткими интервалами (<50ms)
    
    Expected: algo_type='SWEEP', mean_interval < 50ms
    
    WHY (HYBRID APPROACH):
    - Используем $100-180 сделки
    - Интервалы 10-22ms (mean ~16ms) для гарантии SWEEP
    - КРИТИЧНО: CV должен быть > 10% чтобы не попасть в TWAP
    """
    book = create_test_book()
    analyzer = WhaleAnalyzer(BTC_CONFIG)
    
    base_time = 1000000
    base_price = 50000.0
    
    # Генерируем 200 сделок с ОЧЕНЬ короткими интервалами
    current_time = base_time
    for i in range(200):
        # КРИТИЧНО: mean должен быть < 25ms для SWEEP
        # WHY: SWEEP имеет НИЗШИЙ приоритет, проверяется ПОСЛЕ TWAP/VWAP
        # Нужно избежать CV < 10% (иначе TWAP), но mean < 50ms
        interval = 10 + (i % 7) * 2  # 10-22ms → mean ~16ms, CV ~25%
        current_time += interval
        
        # HYBRID FIX: $99.99-$100
        quantity = 0.0019998 if i % 2 == 0 else 0.002
        
        trade = create_test_trade(
            price=base_price,
            quantity=quantity,
            is_sell=True,
            event_time_ms=current_time
        )
        
        category, volume_usd, algo_alert = analyzer.update_stats(book, trade)
    
    # EDGE CASE CHECK
    whale_thresh, minnow_thresh = analyzer._calculate_dynamic_thresholds(book)
    assert minnow_thresh <= 500.0, f"Dynamic minnow threshold too high: {minnow_thresh}"
    
    # Проверяем результат
    assert algo_alert != False, "SWEEP должен быть обнаружен"
    assert "SWEEP" in algo_alert, f"Expected SWEEP, got {algo_alert} (mean: {book.last_algo_detection.mean_interval_ms if book.last_algo_detection else 'N/A'}ms)"
    assert book.last_algo_detection is not None
    assert book.last_algo_detection.algo_type == "SWEEP"
    assert book.last_algo_detection.mean_interval_ms < 50.0


def test_algo_detection_mixed_directions():
    """
    200 сделок 50/50 buy/sell НЕ должны триггерить алгоритм
    
    Expected: algo_alert = False (directional_ratio < 0.85)
    
    WHY: Проверяем что КРИТЕРИЙ 0 (directional_ratio >= 0.85) работает
    """
    book = create_test_book()
    analyzer = WhaleAnalyzer(BTC_CONFIG)
    
    base_time = 1000000
    base_price = 50000.0
    
    # Генерируем 200 сделок, чередуя buy/sell
    for i in range(200):
        trade_time = base_time + i * 250
        
        # HYBRID FIX: Используем $100 сделки
        trade = create_test_trade(
            price=base_price,
            quantity=0.002,  # $100
            is_sell=(i % 2 == 0),  # 50/50 распределение
            event_time_ms=trade_time
        )
        
        category, volume_usd, algo_alert = analyzer.update_stats(book, trade)
    
    # Проверяем что алгоритм НЕ обнаружен
    assert algo_alert == False, f"Should not detect algo with mixed directions, got {algo_alert}"


def test_algo_detection_insufficient_data():
    """
    Менее 200 сделок - недостаточно данных
    
    Expected: algo_alert = False
    
    WHY: Проверяем что порог len(algo_window) >= 200 работает
    """
    book = create_test_book()
    analyzer = WhaleAnalyzer(BTC_CONFIG)
    
    base_time = 1000000
    base_price = 50000.0
    
    # Генерируем только 100 сделок (недостаточно)
    for i in range(100):
        trade_time = base_time + i * 250
        
        # HYBRID FIX: $100 сделки
        trade = create_test_trade(
            price=base_price,
            quantity=0.002,
            is_sell=True,
            event_time_ms=trade_time
        )
        
        category, volume_usd, algo_alert = analyzer.update_stats(book, trade)
    
    # Проверяем что алгоритм НЕ обнаружен (недостаточно данных)
    assert algo_alert == False, "Should not detect algo with <200 trades"


def test_algo_detection_cleanup_old_trades():
    """
    Старые сделки (>60 секунд) должны удаляться из окна
    
    Expected: algo_window содержит только свежие сделки
    
    WHY (HYBRID APPROACH):
    - Проверяем синхронизацию cleanup для всех 3 deque
    - Используем $100 сделки
    - Проверяем что ТОЛЬКО новая сделка остается (cutoff работает)
    """
    book = create_test_book()
    analyzer = WhaleAnalyzer(BTC_CONFIG)
    
    base_time = 1000000
    base_price = 50000.0
    
    # Генерируем 100 сделок в первой минуте
    for i in range(100):
        trade_time = base_time + i * 200  # 200ms интервал
        
        # HYBRID FIX: $100 сделки
        trade = create_test_trade(
            price=base_price,
            quantity=0.002,
            is_sell=True,
            event_time_ms=trade_time
        )
        
        analyzer.update_stats(book, trade)
    
    # Проверяем что сделки сохранены
    # WHY: algo_window добавляет ТОЛЬКО minnow сделки
    # Первая сделка может быть dolphin (до накопления 100 для динамических порогов)
    num_trades = len(book.algo_window)
    assert num_trades >= 99, f"Expected ~100 trades (at least 99), got {num_trades}"
    
    # КРИТИЧНО: Проверяем синхронизацию всех 3 deque
    # WHY: algo_interval_history имеет на 1 меньше (first minnow trade не создает interval)
    expected_intervals = num_trades - 1
    assert len(book.algo_interval_history) == expected_intervals, f"Expected {expected_intervals} intervals, got {len(book.algo_interval_history)}"
    assert len(book.algo_size_pattern) == num_trades, f"Expected {num_trades} sizes, got {len(book.algo_size_pattern)}"
    
    # КРИТИЧНО: Добавляем сделку через 61 секунду от ПОСЛЕДНЕЙ сделки!
    # WHY: Cleanup срабатывает КАЖДЫЙ РАЗ, поэтому нужно считать от последней
    last_trade_time = base_time + 99 * 200  # Последняя сделка @ 1019800ms
    future_time = last_trade_time + 61000  # +61 секунд от последней
    
    future_trade = create_test_trade(
        price=base_price,
        quantity=0.002,
        is_sell=True,
        event_time_ms=future_time
    )
    
    analyzer.update_stats(book, future_trade)
    
    # Проверяем что старые сделки удалены
    # WHY: Cutoff = future_time - 60000
    # Все сделки с timestamp < cutoff должны быть удалены
    # Остается только новая сделка @ future_time
    assert len(book.algo_window) == 1, f"Old trades not cleaned up: {len(book.algo_window)} trades remain (expected 1)"
    
    # КРИТИЧНО: Проверяем что ВСЕ deque синхронизированы
    assert len(book.algo_interval_history) == 0, f"Intervals not cleaned: {len(book.algo_interval_history)} (expected 0)"
    assert len(book.algo_size_pattern) == 1, f"Sizes not cleaned: {len(book.algo_size_pattern)} (expected 1)"


def test_algo_detection_gap_recovery():
    """
    Gap Recovery: Бот торговал, замолчал на 2 минуты, потом снова начал
    
    Expected: После gap система корректно восстанавливается без крашей
    
    WHY (EDGE CASE):
    - Проверяем что старые сделки удаляются по timeout
    - algo_window становится пустым после gap
    - Новые сделки корректно начинают накапливаться
    - НЕТ division by zero или других ошибок при малом количестве данных
    """
    book = create_test_book()
    analyzer = WhaleAnalyzer(BTC_CONFIG)
    
    base_time = 1000000
    base_price = 50000.0
    
    # Фаза 1: Генерируем 100 сделок (активная торговля)
    for i in range(100):
        trade_time = base_time + i * 200  # 200ms интервал
        
        trade = create_test_trade(
            price=base_price,
            quantity=0.002,  # $100
            is_sell=True,
            event_time_ms=trade_time
        )
        
        analyzer.update_stats(book, trade)
    
    # Проверяем что данные накоплены
    assert len(book.algo_window) >= 99, f"Expected ~100 trades, got {len(book.algo_window)}"
    
    # Фаза 2: GAP - 2 минуты тишины (120 секунд)
    # WHY: Все старые сделки должны удалиться (cutoff = 60 сек)
    gap_time = base_time + 99 * 200 + 120000  # +2 минуты от последней сделки
    
    gap_trade = create_test_trade(
        price=base_price,
        quantity=0.002,
        is_sell=True,
        event_time_ms=gap_time
    )
    
    # КРИТИЧНО: Эта операция НЕ должна крашить систему
    category, volume_usd, algo_alert = analyzer.update_stats(book, gap_trade)
    
    # Проверяем recovery: окно очищено, осталась только новая сделка
    assert len(book.algo_window) == 1, f"Expected 1 trade after gap, got {len(book.algo_window)}"
    assert len(book.algo_interval_history) == 0, f"Expected 0 intervals after gap, got {len(book.algo_interval_history)}"
    assert len(book.algo_size_pattern) == 1, f"Expected 1 size after gap, got {len(book.algo_size_pattern)}"
    
    # Проверяем что алгоритм НЕ обнаружен (недостаточно данных после gap)
    assert algo_alert == False, f"Should not detect algo after gap with only 1 trade, got {algo_alert}"
    
    # Фаза 3: Возобновление торговли - добавляем еще 199 сделок
    current_time = gap_time
    category_counts = {'whale': 0, 'dolphin': 0, 'minnow': 0}  # DEBUG: счетчик категорий
    algo_detected = False  # КРИТИЧНО: сохраняем факт детекции
    
    for i in range(199):
        current_time += 250  # TWAP-подобный паттерн
        
        trade = create_test_trade(
            price=base_price,
            quantity=0.002,
            is_sell=True,
            event_time_ms=current_time
        )
        
        category, volume_usd, algo_alert = analyzer.update_stats(book, trade)
        category_counts[category] += 1  # DEBUG: считаем категории
        
        # КРИТИЧНО: Сохраняем факт детекции (может произойти на 200-й сделке)
        if algo_alert != False:
            algo_detected = algo_alert
            print(f"\n[ALGO DETECTED] At trade {i+2}: {algo_alert}")  # +2 потому что gap_trade = 1
        
        # DEBUG: Выводим пороги каждые 50 сделок
        if (i + 1) % 50 == 0:
            whale_t, minnow_t = analyzer._calculate_dynamic_thresholds(book)
            print(f"\n[DEBUG] After {i+1} trades: whale_thresh=${whale_t:.2f}, minnow_thresh=${minnow_t:.2f}")
            print(f"[DEBUG] Categories: {category_counts}")
            print(f"[DEBUG] algo_window size: {len(book.algo_window)}")
    
    # DEBUG: Финальная статистика
    print(f"\n[DEBUG] Final categories: {category_counts}")
    whale_thresh, minnow_thresh = analyzer._calculate_dynamic_thresholds(book)
    print(f"[DEBUG] Final thresholds: whale=${whale_thresh:.2f}, minnow=${minnow_thresh:.2f}")
    print(f"[DEBUG] Trade size: ${base_price * 0.002:.2f}")
    print(f"[DEBUG] Algo detected: {algo_detected}")
    
    # Проверяем что система восстановилась
    # WHY: После gap system восстанавливается и может детектировать алгоритм
    # Когда алгоритм детектируется, algo_window очищается!
    # Поэтому проверяем либо algo_window заполнен, либо алгоритм был обнаружен
    actual_trades = len(book.algo_window)
    
    if algo_detected:
        # SUCCESS: Алгоритм был обнаружен и окно очищено
        print(f"\n[SUCCESS] Recovery successful: Algorithm detected: {algo_detected}")
        # WHY: Может быть либо TWAP (равномерные интервалы), либо ICEBERG (одинаковые размеры)
        # Оба варианта правильны для данного паттерна (постоянные интервалы + фиксированный размер)
        assert algo_detected in ["SELL_TWAP", "SELL_ICEBERG"], f"Expected SELL_TWAP or SELL_ICEBERG, got {algo_detected}"
    else:
        # Алгоритм не обнаружен - проверяем что накоплено достаточно данных
        assert actual_trades >= 190, f"Expected >=190 trades after recovery, got {actual_trades}"
