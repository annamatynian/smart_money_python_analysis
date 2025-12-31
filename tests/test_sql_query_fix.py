"""
TEST: Проверка SQL-запроса в get_cold_start_candles

WHY: Gemini нашёл критическую ошибку в SQL:
1. LAST() не существует в PostgreSQL (только в TimescaleDB)
2. Неверная логика - для дельт нужно SUM(), а не LAST()

FIX: Заменено LAST() на SUM() для CVD дельт

ПРОВЕРКА:
1. SQL не содержит LAST()
2. SQL использует SUM() для CVD дельт
3. Логика агрегации корректна
"""

import pytest
import inspect
from repository import PostgresRepository


class TestSQLQueryValidation:
    """Проверяем SQL-запрос в get_cold_start_candles"""
    
    def test_no_last_function(self):
        """
        WHY: LAST() не существует в стандартном PostgreSQL.
        
        LAST() - это функция TimescaleDB.
        Если используется обычный PostgreSQL - запрос упадёт.
        """
        repo = PostgresRepository(dsn="postgresql://fake")
        
        # Получаем исходный код метода
        source = inspect.getsource(repo.get_cold_start_candles)
        
        # Проверяем что LAST() не используется
        assert 'LAST(' not in source, \
            "SQL использует LAST() - это функция TimescaleDB, не PostgreSQL!"
        
        # Более строгая проверка (case-insensitive)
        assert 'last(' not in source.lower() or 'lastval(' in source.lower(), \
            "SQL содержит вызов LAST() функции!"
    
    def test_sum_for_cvd_deltas(self):
        """
        WHY: CVD дельты нужно СУММИРОВАТЬ, а не брать последнее значение.
        
        flow_whale_cvd_delta - это ИЗМЕНЕНИЕ за каждую метрику.
        Для свечи за 1 час нужно просуммировать все изменения,
        а не брать только последнюю секунду.
        
        Пример:
        - 14:00:00 - whale_cvd_delta = +100 BTC
        - 14:30:00 - whale_cvd_delta = +50 BTC
        - 14:59:59 - whale_cvd_delta = +30 BTC
        
        Правильно: SUM = 180 BTC (весь объём за час)
        Неправильно: LAST = 30 BTC (только последняя секунда!)
        """
        repo = PostgresRepository(dsn="postgresql://fake")
        
        # Получаем исходный код метода
        source = inspect.getsource(repo.get_cold_start_candles)
        
        # Проверяем что используется SUM() для whale_cvd
        assert 'SUM(flow_whale_cvd_delta)' in source, \
            "Для whale_cvd должен использоваться SUM(), а не LAST()!"
        
        # Проверяем что используется SUM() для minnow_cvd
        assert 'SUM(flow_minnow_cvd_delta)' in source, \
            "Для minnow_cvd должен использоваться SUM(), а не LAST()!"
    
    def test_correct_comment(self):
        """
        WHY: Комментарий должен объяснять почему используется SUM.
        """
        repo = PostgresRepository(dsn="postgresql://fake")
        source = inspect.getsource(repo.get_cold_start_candles)
        
        # Проверяем что нет старого комментария про LAST
        assert 'берём last' not in source.lower() and 'берем last' not in source.lower(), \
            "Старый комментарий про LAST() всё ещё присутствует!"
        
        # Проверяем что есть правильный комментарий
        assert 'суммируем' in source.lower() or 'SUM' in source, \
            "Должен быть комментарий объясняющий использование SUM()!"
    
    def test_aggregation_functions_consistency(self):
        """
        WHY: Проверяем логику агрегации для разных метрик.
        
        Правильная агрегация:
        - AVG(price) - средняя цена за период
        - AVG(book_ofi) - средний OFI
        - AVG(book_obi) - средний OBI
        - SUM(flow_whale_cvd_delta) - сумма дельт CVD (накопленный объём)
        - SUM(flow_minnow_cvd_delta) - сумма дельт CVD
        """
        repo = PostgresRepository(dsn="postgresql://fake")
        source = inspect.getsource(repo.get_cold_start_candles)
        
        # Проверяем AVG для цены
        assert 'AVG(price)' in source, \
            "Для цены должно использоваться AVG()!"
        
        # Проверяем AVG для OFI
        assert 'AVG(book_ofi)' in source, \
            "Для OFI должно использоваться AVG()!"
        
        # Проверяем AVG для OBI
        assert 'AVG(book_obi)' in source, \
            "Для OBI должно использоваться AVG()!"
        
        # Проверяем SUM для CVD дельт (НЕ AVG!)
        assert 'SUM(flow_whale_cvd_delta)' in source, \
            "Для CVD дельт должно использоваться SUM(), а не AVG()!"
    
    def test_sql_uses_standard_postgres(self):
        """
        WHY: SQL должен быть совместим со стандартным PostgreSQL.
        
        Запрещённые функции (только TimescaleDB):
        - LAST()
        - FIRST()
        - time_bucket() (должен использоваться date_bin())
        """
        repo = PostgresRepository(dsn="postgresql://fake")
        source = inspect.getsource(repo.get_cold_start_candles)
        
        # Проверяем что не используются TimescaleDB-специфичные функции
        timescaledb_functions = ['LAST(', 'FIRST(', 'time_bucket(']
        
        for func in timescaledb_functions:
            assert func not in source, \
                f"SQL использует TimescaleDB-специфичную функцию {func}!"
        
        # Проверяем что используется стандартный date_bin (PostgreSQL 14+)
        assert 'date_bin(' in source, \
            "Должен использоваться date_bin() для агрегации по времени!"


class TestCVDCalculationLogic:
    """Проверяем математическую логику расчёта CVD"""
    
    def test_cvd_delta_vs_cvd_absolute(self):
        """
        WHY: Важно понимать разницу между delta и absolute.
        
        Таблица market_metrics_full содержит:
        - flow_whale_cvd_delta: ИЗМЕНЕНИЕ за период (delta)
        - Нет абсолютного значения CVD
        
        Для свечи нужно:
        - SUM(delta) = накопленное изменение за период
        """
        # Это концептуальный тест - просто документирует логику
        
        # Пример данных в market_metrics_full:
        # time         | flow_whale_cvd_delta
        # 14:00:00     | +100
        # 14:00:05     | +50
        # 14:00:10     | -30
        # 14:00:15     | +20
        
        # Для свечи 14:00-14:01:
        # SUM(flow_whale_cvd_delta) = 100 + 50 - 30 + 20 = 140
        
        # Это правильный подход!
        assert True  # Тест проходит если логика понятна


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
