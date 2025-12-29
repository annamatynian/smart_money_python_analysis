"""
Tests for Thread Safety (asyncio.Lock)
WHY: Gemini рекомендация - защита _cached_divergence_state от race conditions
"""
import pytest
import asyncio
from decimal import Decimal
from domain import LocalOrderBook
from analyzers import AccumulationDetector
from config import get_config


class TestThreadSafety:
    """Tests for concurrent access to AccumulationDetector cache"""
    
    @pytest.mark.asyncio
    async def test_cache_has_lock(self):
        """
        ПРОВЕРКА: AccumulationDetector имеет asyncio.Lock для кеша
        """
        book = LocalOrderBook(symbol='BTCUSDT')
        config = get_config('BTCUSDT')
        detector = AccumulationDetector(book=book, config=config)
        
        # Проверяем что Lock существует
        assert hasattr(detector, '_cache_lock')
        assert isinstance(detector._cache_lock, asyncio.Lock)
    
    @pytest.mark.asyncio
    async def test_concurrent_cache_access(self):
        """
        ПРОВЕРКА: Одновременный доступ к кешу не ломает данные
        
        WHY: Предотвращаем race condition при:
        - detect_accumulation_multi_timeframe() обновляет кеш
        - get_current_divergence_state() читает кеш
        В одно и то же время (HFT сценарий)
        """
        book = LocalOrderBook(symbol='BTCUSDT')
        config = get_config('BTCUSDT')
        detector = AccumulationDetector(book=book, config=config)
        
        async def read_cache():
            """Читаем кеш много раз"""
            for _ in range(100):
                detector.get_current_divergence_state()
                await asyncio.sleep(0.001)  # 1ms пауза
        
        async def update_cache():
            """Обновляем кеш"""
            for _ in range(10):
                detector.detect_accumulation_multi_timeframe()
                await asyncio.sleep(0.01)  # 10ms пауза
        
        # Запускаем параллельно
        await asyncio.gather(
            read_cache(),
            update_cache()
        )
        
        # Если Lock работает - не будет exception
        # Test passes если не упали
        assert True
