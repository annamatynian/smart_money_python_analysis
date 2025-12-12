"""
WHY: Тесты для Adaptive Delay механизма (Task: Gemini Phase 2.1)

Проверяем:
1. LatencyMonitor корректно записывает задержки
2. Адаптивная задержка вычисляется по формуле T_GU = μ_RTT + μ_proc + k·σ_jit
3. Границы min/max защищают от аномалий
4. Динамическое обновление delay в TradingEngine
"""

import pytest
from infrastructure import LatencyMonitor
import statistics


def test_latency_monitor_initialization():
    """
    WHY: Проверяем что LatencyMonitor инициализируется с правильными параметрами.
    """
    monitor = LatencyMonitor(window_size=100, k=3.0, base_processing_ms=10.0)
    
    assert monitor.window_size == 100
    assert monitor.k == 3.0
    assert monitor.base_processing_ms == 10.0
    assert monitor.min_delay_ms == 10.0
    assert monitor.max_delay_ms == 500.0
    assert len(monitor.latencies) == 0


def test_record_latency_basic():
    """
    WHY: Проверяем что задержки корректно записываются.
    """
    monitor = LatencyMonitor(window_size=10)
    
    # Симулируем 5 событий с разными задержками
    base_time = 1000000  # Базовое время в мс
    
    for i in range(5):
        event_time_ms = base_time + (i * 100)
        arrival_time_ms = event_time_ms + 50 + (i * 5)  # Задержка 50-70ms
        monitor.record_latency(event_time_ms, arrival_time_ms)
    
    assert len(monitor.latencies) == 5
    assert all(50 <= lat <= 70 for lat in monitor.latencies)


def test_adaptive_delay_formula():
    """
    WHY: Проверяем что формула T_GU = μ_RTT + μ_proc + k·σ_jit работает корректно.
    
    Формула из документации (cite: строка 1.2 документа):
    T_GU(t) = μ_RTT(t) + μ_proc(t) + k · σ_jit(t)
    """
    monitor = LatencyMonitor(window_size=100, k=3.0, base_processing_ms=10.0)
    
    # Добавляем 20 измерений с известными параметрами
    # RTT = 30ms со стандартным отклонением ~5ms
    latencies = [30, 32, 28, 35, 29, 31, 33, 27, 30, 34,
                 29, 31, 30, 32, 28, 30, 31, 29, 33, 30]
    
    base_time = 1000000
    for i, lat in enumerate(latencies):
        event_time_ms = base_time + (i * 100)
        arrival_time_ms = event_time_ms + lat
        monitor.record_latency(event_time_ms, arrival_time_ms)
    
    # Вычисляем ожидаемые значения
    mean_rtt = statistics.mean(latencies)
    stdev_jit = statistics.stdev(latencies)
    expected_delay = mean_rtt + 10.0 + (3.0 * stdev_jit)
    
    adaptive_delay = monitor.get_adaptive_delay()
    
    # Проверяем что результат близок к ожидаемому (±1ms погрешность)
    assert abs(adaptive_delay - expected_delay) < 1.0
    
    # Проверяем что формула соблюдается
    stats = monitor.get_stats()
    assert abs(stats['mean_rtt'] - mean_rtt) < 0.1
    assert abs(stats['stdev_jitter'] - stdev_jit) < 0.1


def test_min_max_boundaries():
    """
    WHY: Проверяем что границы min_delay_ms и max_delay_ms защищают от аномалий.
    """
    monitor = LatencyMonitor(window_size=20, k=3.0, base_processing_ms=10.0)
    
    # Тест 1: Очень маленькие задержки (должны ограничиться min=10ms)
    base_time = 1000000
    for i in range(15):
        event_time_ms = base_time + (i * 100)
        arrival_time_ms = event_time_ms + 1  # Всего 1ms задержка
        monitor.record_latency(event_time_ms, arrival_time_ms)
    
    adaptive_delay = monitor.get_adaptive_delay()
    assert adaptive_delay >= 10.0, "Delay должен быть >= min_delay_ms"
    
    # Тест 2: Очень большие задержки (должны ограничиться max=500ms)
    monitor2 = LatencyMonitor(window_size=20, k=10.0, base_processing_ms=100.0)
    
    for i in range(15):
        event_time_ms = base_time + (i * 100)
        arrival_time_ms = event_time_ms + 200  # 200ms задержка
        monitor2.record_latency(event_time_ms, arrival_time_ms)
    
    adaptive_delay2 = monitor2.get_adaptive_delay()
    assert adaptive_delay2 <= 500.0, "Delay должен быть <= max_delay_ms"


def test_insufficient_data_returns_default():
    """
    WHY: Проверяем что при недостаточном количестве данных возвращается дефолт 50ms.
    """
    monitor = LatencyMonitor(window_size=100)
    
    # Добавляем только 5 измерений (меньше порога 10)
    base_time = 1000000
    for i in range(5):
        event_time_ms = base_time + (i * 100)
        arrival_time_ms = event_time_ms + 30
        monitor.record_latency(event_time_ms, arrival_time_ms)
    
    adaptive_delay = monitor.get_adaptive_delay()
    assert adaptive_delay == 50.0, "При недостаточных данных должен возвращаться default 50ms"


def test_anomaly_filtering():
    """
    WHY: Проверяем что аномально большие задержки (>5 секунд) отфильтровываются.
    
    Это защита от рассинхронизации часов между клиентом и биржей.
    """
    monitor = LatencyMonitor(window_size=20)
    
    base_time = 1000000
    
    # Добавляем нормальные задержки
    for i in range(10):
        event_time_ms = base_time + (i * 100)
        arrival_time_ms = event_time_ms + 50
        monitor.record_latency(event_time_ms, arrival_time_ms)
    
    # Пытаемся добавить аномалию (10 секунд)
    monitor.record_latency(base_time + 1000, base_time + 11000)
    
    # Аномалия должна быть отфильтрована
    assert len(monitor.latencies) == 10, "Аномальные значения должны игнорироваться"
    assert all(lat < 5000 for lat in monitor.latencies)


def test_window_size_limit():
    """
    WHY: Проверяем что окно ограничено размером window_size.
    """
    monitor = LatencyMonitor(window_size=10)
    
    base_time = 1000000
    
    # Добавляем 20 измерений (больше чем window_size=10)
    for i in range(20):
        event_time_ms = base_time + (i * 100)
        arrival_time_ms = event_time_ms + 50
        monitor.record_latency(event_time_ms, arrival_time_ms)
    
    # Окно должно содержать только последние 10
    assert len(monitor.latencies) == 10


def test_stats_output_format():
    """
    WHY: Проверяем что get_stats() возвращает корректный формат данных.
    """
    monitor = LatencyMonitor(window_size=20)
    
    base_time = 1000000
    for i in range(15):
        event_time_ms = base_time + (i * 100)
        arrival_time_ms = event_time_ms + 40 + (i % 5)  # 40-44ms
        monitor.record_latency(event_time_ms, arrival_time_ms)
    
    stats = monitor.get_stats()
    
    # Проверяем структуру
    assert 'mean_rtt' in stats
    assert 'stdev_jitter' in stats
    assert 'adaptive_delay' in stats
    assert 'sample_size' in stats
    
    # Проверяем типы
    assert isinstance(stats['mean_rtt'], (int, float))
    assert isinstance(stats['stdev_jitter'], (int, float))
    assert isinstance(stats['adaptive_delay'], (int, float))
    assert isinstance(stats['sample_size'], int)
    
    # Проверяем значения
    assert stats['sample_size'] == 15
    assert 40 <= stats['mean_rtt'] <= 44


def test_high_volatility_scenario():
    """
    WHY: Проверяем поведение при высокой волатильности (большой джиттер).
    
    Во время новостей или ликвидаций задержки могут сильно варьироваться.
    Адаптивная задержка должна увеличиваться для защиты от race conditions.
    """
    monitor = LatencyMonitor(window_size=30, k=3.0, base_processing_ms=10.0)
    
    base_time = 1000000
    
    # Симулируем высокую волатильность: задержки от 20ms до 150ms
    volatile_latencies = [20, 150, 30, 140, 25, 130, 35, 120, 40, 110,
                          50, 100, 60, 90, 70, 80, 75, 85, 65, 95]
    
    for i, lat in enumerate(volatile_latencies):
        event_time_ms = base_time + (i * 100)
        arrival_time_ms = event_time_ms + lat
        monitor.record_latency(event_time_ms, arrival_time_ms)
    
    adaptive_delay = monitor.get_adaptive_delay()
    mean_rtt = statistics.mean(volatile_latencies)
    
    # При высоком джиттере delay должен быть значительно больше среднего RTT
    assert adaptive_delay > mean_rtt + 30, "При высоком джиттере delay должен увеличиваться"


def test_stable_network_scenario():
    """
    WHY: Проверяем поведение при стабильной сети (малый джиттер).
    
    В нормальных условиях задержки стабильны, и adaptive delay должен быть близок к минимуму.
    """
    monitor = LatencyMonitor(window_size=30, k=3.0, base_processing_ms=10.0)
    
    base_time = 1000000
    
    # Симулируем стабильную сеть: задержки 25±2ms
    stable_latencies = [25, 26, 24, 25, 27, 25, 24, 26, 25, 25,
                        24, 26, 25, 24, 25, 26, 25, 24, 26, 25]
    
    for i, lat in enumerate(stable_latencies):
        event_time_ms = base_time + (i * 100)
        arrival_time_ms = event_time_ms + lat
        monitor.record_latency(event_time_ms, arrival_time_ms)
    
    adaptive_delay = monitor.get_adaptive_delay()
    stdev_jit = statistics.stdev(stable_latencies)
    
    # При малом джиттере delay должен быть близок к μ_RTT + μ_proc
    expected_min = 25 + 10  # mean_rtt + base_processing
    assert abs(adaptive_delay - expected_min) < 10, "При стабильной сети delay должен быть минимальным"
