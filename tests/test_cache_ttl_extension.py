"""
WHY: Тесты для GEMINI FIX - Cache TTL Extension.

Проверяем:
1. TTL для basis увеличен с 5 мин до 30 мин
2. TTL для skew увеличен с 5 мин до 30 мин
3. Возврат None только после 30 минут
"""

import pytest
from datetime import datetime, timedelta
from analyzers_derivatives import DerivativesAnalyzer


class TestCacheTTLExtension:
    """Проверяем, что TTL для макро-данных увеличен до 30 минут."""
    
    def test_basis_fresh_within_30_min(self):
        """
        WHY: Basis кеш должен быть валидным в течение 30 минут.
        
        Scenario:
        - Обновляем basis кеш
        - Проверяем через 29 минут → должен вернуть значение
        """
        analyzer = DerivativesAnalyzer()
        
        # Обновляем кеш
        analyzer.update_basis_cache(basis_apr=15.5)
        
        # Симулируем прошедшие 29 минут (1740 секунд)
        analyzer._last_basis_update = datetime.now() - timedelta(seconds=1740)
        
        # Запрашиваем кеш
        cached = analyzer.get_cached_basis()
        
        # ASSERTION: Должен вернуть значение (не None)
        assert cached is not None, \
            "Basis кеш должен быть валидным в течение 30 минут"
        
        assert cached == 15.5, \
            f"Cached basis должен быть 15.5, но получили {cached}"
    
    def test_basis_stale_after_30_min(self):
        """
        WHY: После 30 минут basis кеш должен устареть (вернуть None).
        
        Scenario:
        - Обновляем basis кеш
        - Проверяем через 31 минуту → должен вернуть None
        """
        analyzer = DerivativesAnalyzer()
        
        # Обновляем кеш
        analyzer.update_basis_cache(basis_apr=15.5)
        
        # Симулируем прошедшие 31 минута (1860 секунд)
        analyzer._last_basis_update = datetime.now() - timedelta(seconds=1860)
        
        # Запрашиваем кеш
        cached = analyzer.get_cached_basis()
        
        # ASSERTION: Должен вернуть None (устарел)
        assert cached is None, \
            "Basis кеш после 30 минут должен вернуть None (stale)"
    
    def test_basis_exact_30_min_boundary(self):
        """
        WHY: Проверяем граничный случай - чуть меньше 30 минут.
        Используем 1799s вместо 1800s для учёта времени выполнения.
        """
        analyzer = DerivativesAnalyzer()
        
        analyzer.update_basis_cache(basis_apr=20.0)
        
        # Чуть меньше 30 минут (1799 секунд)
        # WHY: Используем 1799 вместо 1800, чтобы учесть время выполнения теста
        analyzer._last_basis_update = datetime.now() - timedelta(seconds=1799)
        
        cached = analyzer.get_cached_basis()
        
        # ASSERTION: При 1799s (меньше 1800s) кеш должен быть валидным
        # Условие: age > 1800, значит 1799 НЕ > 1800 → валидный
        assert cached is not None, \
            "При 1799s (меньше 30 мин) кеш должен быть валидным"
        
        assert cached == 20.0
    
    def test_skew_fresh_within_30_min(self):
        """
        WHY: Skew кеш должен быть валидным в течение 30 минут.
        """
        analyzer = DerivativesAnalyzer()
        
        # Обновляем кеш
        analyzer.update_skew_cache(skew=7.5)
        
        # Симулируем прошедшие 29 минут
        analyzer._last_skew_update = datetime.now() - timedelta(seconds=1740)
        
        # Запрашиваем кеш
        cached = analyzer.get_cached_skew()
        
        # ASSERTION: Должен вернуть значение
        assert cached is not None, \
            "Skew кеш должен быть валидным в течение 30 минут"
        
        assert cached == 7.5, \
            f"Cached skew должен быть 7.5, но получили {cached}"
    
    def test_skew_stale_after_30_min(self):
        """
        WHY: После 30 минут skew кеш должен устареть.
        """
        analyzer = DerivativesAnalyzer()
        
        analyzer.update_skew_cache(skew=7.5)
        
        # Симулируем прошедшие 31 минута
        analyzer._last_skew_update = datetime.now() - timedelta(seconds=1860)
        
        cached = analyzer.get_cached_skew()
        
        # ASSERTION: Должен вернуть None
        assert cached is None, \
            "Skew кеш после 30 минут должен вернуть None (stale)"
    
    def test_multiple_updates_reset_timer(self):
        """
        WHY: Повторные обновления кеша должны сбрасывать таймер.
        
        Scenario:
        - Первое обновление
        - Ждем 20 минут
        - Второе обновление (сбрасывает таймер)
        - Ждем еще 20 минут (в сумме 40 от первого, но 20 от второго)
        - Кеш должен быть валидным (т.к. второе обновление сбросило таймер)
        """
        analyzer = DerivativesAnalyzer()
        
        # Первое обновление (40 минут назад)
        analyzer.update_basis_cache(basis_apr=10.0)
        analyzer._last_basis_update = datetime.now() - timedelta(seconds=2400)  # 40 мин
        
        # Второе обновление (20 минут назад) - СБРАСЫВАЕТ таймер
        analyzer.update_basis_cache(basis_apr=12.0)
        analyzer._last_basis_update = datetime.now() - timedelta(seconds=1200)  # 20 мин
        
        # Запрашиваем кеш
        cached = analyzer.get_cached_basis()
        
        # ASSERTION: Должен вернуть НОВОЕ значение (12.0), т.к. таймер сброшен
        assert cached is not None, \
            "После повторного обновления таймер должен сброситься"
        
        assert cached == 12.0, \
            f"После обновления должно быть новое значение 12.0, но получили {cached}"
    
    def test_old_ttl_would_have_failed(self):
        """
        WHY: Доказываем, что старый TTL (300s) был недостаточен.
        
        Scenario:
        - Кеш обновлен 10 минут назад (600s)
        - Старый TTL (300s): вернул бы None ❌
        - Новый TTL (1800s): должен вернуть значение ✅
        """
        analyzer = DerivativesAnalyzer()
        
        analyzer.update_basis_cache(basis_apr=18.0)
        
        # 10 минут назад (600 секунд)
        analyzer._last_basis_update = datetime.now() - timedelta(seconds=600)
        
        cached = analyzer.get_cached_basis()
        
        # ASSERTION: С новым TTL (1800s) должно работать
        assert cached is not None, \
            "С новым TTL (30 мин) кеш 10-минутной давности должен быть валидным"
        
        assert cached == 18.0
        
        # ДОКАЗАТЕЛЬСТВО: Если бы использовали старый TTL (300s)
        # 600 > 300 → вернули бы None
        # Это демонстрирует, что старый TTL был слишком строгим для макро-данных


class TestCacheEdgeCases:
    """Дополнительные edge cases для кеширования."""
    
    def test_empty_cache_returns_none(self):
        """
        WHY: Если кеш пустой (никогда не обновлялся), должен вернуть None.
        """
        analyzer = DerivativesAnalyzer()
        
        # НЕ обновляем кеш (пустой)
        cached_basis = analyzer.get_cached_basis()
        cached_skew = analyzer.get_cached_skew()
        
        assert cached_basis is None, "Пустой basis кеш должен вернуть None"
        assert cached_skew is None, "Пустой skew кеш должен вернуть None"
    
    def test_zero_values_are_cached(self):
        """
        WHY: Значения 0.0 должны корректно кешироваться (не путать с None).
        """
        analyzer = DerivativesAnalyzer()
        
        # Обновляем кеш с нулевыми значениями
        analyzer.update_basis_cache(basis_apr=0.0)
        analyzer.update_skew_cache(skew=0.0)
        
        cached_basis = analyzer.get_cached_basis()
        cached_skew = analyzer.get_cached_skew()
        
        # ASSERTION: Должны вернуть 0.0, а НЕ None
        assert cached_basis == 0.0, "Basis=0.0 должен кешироваться"
        assert cached_skew == 0.0, "Skew=0.0 должен кешироваться"
    
    def test_negative_values_are_cached(self):
        """
        WHY: Отрицательные значения (backwardation, negative skew) валидны.
        """
        analyzer = DerivativesAnalyzer()
        
        analyzer.update_basis_cache(basis_apr=-5.0)  # Backwardation
        analyzer.update_skew_cache(skew=-10.0)  # Calls дороже puts
        
        cached_basis = analyzer.get_cached_basis()
        cached_skew = analyzer.get_cached_skew()
        
        assert cached_basis == -5.0, "Отрицательный basis должен кешироваться"
        assert cached_skew == -10.0, "Отрицательный skew должен кешироваться"
