"""
TEST: Проверка что в repository.py нет дублирующихся методов

WHY: Gemini нашёл критическую ошибку - два метода с одинаковым именем
get_aggregated_smart_candles. Второй перезаписывал первый.

FIX: Второй метод переименован в get_cold_start_candles()

ПРОВЕРКА:
1. Оба метода существуют
2. Сигнатуры различаются
3. Методы не перезаписывают друг друга
"""

import pytest
import inspect
from repository import PostgresRepository


class TestRepositoryMethods:
    """Проверяем что методы не дублируются"""
    
    def test_both_methods_exist(self):
        """
        WHY: Проверяем что ОБА метода существуют.
        
        - get_aggregated_smart_candles (для RAG)
        - get_cold_start_candles (для Cold Start)
        """
        repo = PostgresRepository(dsn="postgresql://fake")
        
        # Проверяем что оба метода есть
        assert hasattr(repo, 'get_aggregated_smart_candles'), \
            "Метод get_aggregated_smart_candles не найден!"
        
        assert hasattr(repo, 'get_cold_start_candles'), \
            "Метод get_cold_start_candles не найден!"
    
    def test_method_signatures_differ(self):
        """
        WHY: Проверяем что сигнатуры методов различаются.
        
        get_aggregated_smart_candles: (symbol, start_time, end_time, timeframe_minutes)
        get_cold_start_candles: (symbol, timeframe, limit)
        """
        repo = PostgresRepository(dsn="postgresql://fake")
        
        # Получаем сигнатуры методов
        sig_rag = inspect.signature(repo.get_aggregated_smart_candles)
        sig_cold = inspect.signature(repo.get_cold_start_candles)
        
        # Проверяем параметры get_aggregated_smart_candles
        params_rag = list(sig_rag.parameters.keys())
        assert 'symbol' in params_rag
        assert 'start_time' in params_rag
        assert 'end_time' in params_rag
        assert 'timeframe_minutes' in params_rag
        
        # Проверяем параметры get_cold_start_candles
        params_cold = list(sig_cold.parameters.keys())
        assert 'symbol' in params_cold
        assert 'timeframe' in params_cold
        assert 'limit' in params_cold
        
        # Проверяем что параметры РАЗНЫЕ
        assert 'start_time' not in params_cold, \
            "get_cold_start_candles не должен иметь start_time!"
        
        assert 'limit' not in params_rag, \
            "get_aggregated_smart_candles не должен иметь limit!"
    
    def test_methods_are_different_objects(self):
        """
        WHY: Проверяем что это РАЗНЫЕ методы (не перезапись).
        
        В Python если второй метод перезаписывает первый,
        то у них будет одинаковый __code__ объект.
        """
        repo = PostgresRepository(dsn="postgresql://fake")
        
        method_rag = repo.get_aggregated_smart_candles
        method_cold = repo.get_cold_start_candles
        
        # Проверяем что это разные методы
        assert method_rag.__code__ is not method_cold.__code__, \
            "Методы имеют одинаковый код! Возможно второй перезаписывает первый."
        
        # Проверяем что у них разные номера строк
        assert method_rag.__code__.co_firstlineno != method_cold.__code__.co_firstlineno, \
            "Методы имеют одинаковый номер строки!"
    
    def test_docstrings_explain_difference(self):
        """
        WHY: Проверяем что в docstring объяснена разница.
        """
        repo = PostgresRepository(dsn="postgresql://fake")
        
        # Проверяем что у get_cold_start_candles есть упоминание get_aggregated_smart_candles
        docstring_cold = repo.get_cold_start_candles.__doc__
        
        assert docstring_cold is not None, \
            "У get_cold_start_candles нет docstring!"
        
        # Проверяем что в docstring упомянута разница
        assert 'get_aggregated_smart_candles' in docstring_cold or 'ОТЛИЧИЕ' in docstring_cold, \
            "Docstring должен объяснять отличие от get_aggregated_smart_candles!"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
