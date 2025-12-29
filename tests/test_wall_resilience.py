"""
WHY: Тесты для Wall Resilience + Passive/Aggressive Separation.

Проверяет разделение "стены" и "удара" в микроструктуре рынка.
"""

import pytest
from decimal import Decimal
from datetime import datetime, timedelta
from domain import LocalOrderBook, IcebergLevel


class TestWallResilience:
    """
    WHY: Тестируем Wall Resilience метрику для определения силы айсберга.
    """
    
    def test_strong_wall_fast_refill(self):
        """
        WHY: Быстрое восстановление (<50ms) = STRONG wall.
        
        Scenario: Нативный айсберг на бирже восстанавливается за 30ms
        """
        iceberg = IcebergLevel(
            price=Decimal("100000"),
            is_ask=False,
            average_refill_delay_ms=30.0  # Быстрое восстановление
        )
        
        resilience = iceberg.calculate_wall_resilience()
        assert resilience == 'STRONG', "Быстрое восстановление должно давать STRONG"
    
    def test_moderate_wall_algo_refill(self):
        """
        WHY: Среднее восстановление (50-200ms) = MODERATE wall.
        
        Scenario: Алгоритмическое пополнение за 100ms
        """
        iceberg = IcebergLevel(
            price=Decimal("100000"),
            is_ask=False,
            average_refill_delay_ms=100.0
        )
        
        resilience = iceberg.calculate_wall_resilience()
        assert resilience == 'MODERATE'
    
    def test_weak_wall_slow_algo(self):
        """
        WHY: Медленное восстановление (200-500ms) = WEAK wall.
        """
        iceberg = IcebergLevel(
            price=Decimal("100000"),
            is_ask=False,
            average_refill_delay_ms=300.0
        )
        
        resilience = iceberg.calculate_wall_resilience()
        assert resilience == 'WEAK'
    
    def test_exhausted_wall_very_slow(self):
        """
        WHY: Очень медленное восстановление (>500ms) = EXHAUSTED wall.
        
        Scenario: Стена истощена, восстанавливается за 700ms
        """
        iceberg = IcebergLevel(
            price=Decimal("100000"),
            is_ask=False,
            average_refill_delay_ms=700.0
        )
        
        resilience = iceberg.calculate_wall_resilience()
        assert resilience == 'EXHAUSTED'
    
    def test_no_refill_data_returns_none(self):
        """
        WHY: Если нет данных о пополнениях → None.
        """
        iceberg = IcebergLevel(
            price=Decimal("100000"),
            is_ask=False,
            average_refill_delay_ms=None  # Нет данных
        )
        
        resilience = iceberg.calculate_wall_resilience()
        assert resilience is None


class TestPassiveAggressiveSeparation:
    """
    WHY: Тестируем разделение whale CVD на passive/aggressive.
    """
    
    def test_passive_accumulation_tracked(self):
        """
        WHY: Passive (киты стоят айсбергом) → whale_passive_accumulation_1h.
        
        Scenario:
        - Киты выставляют айсберг на BID
        - is_passive=True
        - CVD должен попасть в whale_passive_accumulation_1h
        """
        book = LocalOrderBook(symbol='BTCUSDT')
        
        now = datetime.now()
        book.historical_memory.update_history(
            timestamp=now,
            whale_cvd=10000.0,
            minnow_cvd=-5000.0,
            price=Decimal("100000"),
            is_passive=True  # PASSIVE accumulation
        )
        
        # Проверяем что данные попали в passive
        assert len(book.historical_memory.whale_passive_accumulation_1h) == 1
        assert book.historical_memory.whale_passive_accumulation_1h[0][1] == 10000.0
        
        # Aggressive должен быть пустым
        assert len(book.historical_memory.whale_aggressive_entry_1h) == 0
    
    def test_aggressive_entry_tracked(self):
        """
        WHY: Aggressive (киты бьют по рынку) → whale_aggressive_entry_1h.
        
        Scenario:
        - Киты агрессивно покупают market orders
        - is_passive=False
        - CVD должен попасть в whale_aggressive_entry_1h
        """
        book = LocalOrderBook(symbol='BTCUSDT')
        
        now = datetime.now()
        book.historical_memory.update_history(
            timestamp=now,
            whale_cvd=10000.0,
            minnow_cvd=-5000.0,
            price=Decimal("100000"),
            is_passive=False  # AGGRESSIVE entry
        )
        
        # Проверяем что данные попали в aggressive
        assert len(book.historical_memory.whale_aggressive_entry_1h) == 1
        assert book.historical_memory.whale_aggressive_entry_1h[0][1] == 10000.0
        
        # Passive должен быть пустым
        assert len(book.historical_memory.whale_passive_accumulation_1h) == 0
    
    def test_mixed_passive_and_aggressive(self):
        """
        WHY: Микс passive и aggressive корректно разделяется.
        
        Scenario:
        - 3 passive события (киты стоят айсбергами)
        - 2 aggressive события (киты атакуют)
        - Должны быть в разных deque
        """
        book = LocalOrderBook(symbol='BTCUSDT')
        
        now = datetime.now()
        
        # 3 passive события
        for i in range(3):
            book.historical_memory.update_history(
                timestamp=now + timedelta(hours=i),
                whale_cvd=10000.0 + i * 1000,
                minnow_cvd=-5000.0,
                price=Decimal("100000"),
                is_passive=True
            )
        
        # 2 aggressive события
        for i in range(2):
            book.historical_memory.update_history(
                timestamp=now + timedelta(hours=3 + i),
                whale_cvd=15000.0 + i * 1000,
                minnow_cvd=-3000.0,
                price=Decimal("100500"),
                is_passive=False
            )
        
        # Проверяем разделение
        assert len(book.historical_memory.whale_passive_accumulation_1h) == 3
        assert len(book.historical_memory.whale_aggressive_entry_1h) == 2
        
        # Проверяем что обычная история содержит ВСЕ события (5)
        assert len(book.historical_memory.cvd_history_1h) == 5
    
    def test_passive_default_parameter(self):
        """
        WHY: По умолчанию is_passive=True (backward compatibility).
        
        Scenario: Вызываем update_history без is_passive → должен быть passive
        """
        book = LocalOrderBook(symbol='BTCUSDT')
        
        now = datetime.now()
        book.historical_memory.update_history(
            timestamp=now,
            whale_cvd=10000.0,
            minnow_cvd=-5000.0,
            price=Decimal("100000")
            # is_passive не передан → default=True
        )
        
        # Должно попасть в passive (default behavior)
        assert len(book.historical_memory.whale_passive_accumulation_1h) == 1
        assert len(book.historical_memory.whale_aggressive_entry_1h) == 0


class TestWallResilienceIntegration:
    """
    WHY: Интеграционные тесты для Wall Resilience + Passive/Aggressive.
    """
    
    def test_strong_passive_wall_scenario(self):
        """
        WHY: Полный сценарий: STRONG wall + passive accumulation.
        
        Scenario:
        1. Киты выставляют STRONG айсберг (быстрое восстановление <50ms)
        2. Используют passive accumulation (стоят айсбергом)
        3. Система должна классифицировать как "железобетонная стена"
        """
        book = LocalOrderBook(symbol='BTCUSDT')
        
        # 1. Создаём STRONG айсберг
        iceberg = IcebergLevel(
            price=Decimal("100000"),
            is_ask=False,
            average_refill_delay_ms=30.0  # STRONG
        )
        
        # 2. Добавляем passive accumulation
        now = datetime.now()
        book.historical_memory.update_history(
            timestamp=now,
            whale_cvd=50000.0,  # Большое накопление
            minnow_cvd=-10000.0,  # Minnows продают (паника)
            price=Decimal("100000"),
            is_passive=True
        )
        
        # 3. Проверяем классификацию
        resilience = iceberg.calculate_wall_resilience()
        assert resilience == 'STRONG'
        
        # 4. Проверяем passive tracking
        assert len(book.historical_memory.whale_passive_accumulation_1h) == 1
        assert book.historical_memory.whale_passive_accumulation_1h[0][1] == 50000.0
    
    def test_exhausted_aggressive_wall_scenario(self):
        """
        WHY: Полный сценарий: EXHAUSTED wall + aggressive entry.
        
        Scenario:
        1. Айсберг медленно восстанавливается (>500ms) → EXHAUSTED
        2. Киты агрессивно входят market orders
        3. Это признак завершения накопления (переход к Markup фазе)
        """
        book = LocalOrderBook(symbol='BTCUSDT')
        
        # 1. Создаём EXHAUSTED айсберг
        iceberg = IcebergLevel(
            price=Decimal("100000"),
            is_ask=True,  # ASK (сопротивление)
            average_refill_delay_ms=600.0  # EXHAUSTED
        )
        
        # 2. Добавляем aggressive entry
        now = datetime.now()
        book.historical_memory.update_history(
            timestamp=now,
            whale_cvd=-30000.0,  # Киты продают агрессивно
            minnow_cvd=15000.0,  # Minnows покупают (жадность)
            price=Decimal("101000"),
            is_passive=False  # AGGRESSIVE
        )
        
        # 3. Проверяем классификацию
        resilience = iceberg.calculate_wall_resilience()
        assert resilience == 'EXHAUSTED'
        
        # 4. Проверяем aggressive tracking
        assert len(book.historical_memory.whale_aggressive_entry_1h) == 1
        assert book.historical_memory.whale_aggressive_entry_1h[0][1] == -30000.0
