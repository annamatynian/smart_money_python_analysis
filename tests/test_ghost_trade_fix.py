"""
FIX VULNERABILITY #3: Ghost Trade Issue (Race Condition)

ПРОБЛЕМА:
ReorderingBuffer использует get_all_sorted() который ОЧИЩАЕТ весь буфер сразу.
При высокой скорости событий Trade и Depth могут попасть в РАЗНЫЕ итерации:
- T=0ms: sleep(50ms)
- T=50ms: Trade arrives → processed immediately
- T=75ms: Depth arrives → processed in NEXT iteration (separate batch)
→ Iceberg LOST (events processed separately)

РЕШЕНИЕ:
Использовать временное окно (time window) вместо micro-batching:
- Собирать события в buffer
- pop_ready() возвращает ТОЛЬКО события старше delay_ms
- Гарантирует что Trade и Depth обработаются ВМЕСТЕ

ТЕСТЫ:
1. test_buffer_holds_events_during_window: Буфер НЕ отдает события моложе delay
2. test_buffer_returns_old_events: Буфер отдает события старше delay
3. test_trade_and_depth_processed_together: Trade+Depth с разницей <delay обрабатываются вместе
4. test_priority_ordering_within_window: При равном времени Trade обрабатывается первым
5. test_adaptive_delay_integration: Буфер работает с адаптивным delay
"""

import pytest
import asyncio
import time
from decimal import Decimal
from datetime import datetime, timezone
from infrastructure import ReorderingBuffer
from domain import TradeEvent, OrderBookUpdate


class TestReorderingBufferTimeWindow:
    """Тесты временного окна ReorderingBuffer"""
    
    def test_buffer_holds_events_during_window(self):
        """
        КРИТИЧЕСКИЙ ТЕСТ: Буфер НЕ должен отдавать события моложе delay.
        
        Сценарий:
        - delay = 50ms
        - Добавляем событие T=now
        - Вызываем pop_ready() сразу (now+1ms)
        - ОЖИДАНИЕ: Пустой список (событие еще "не созрело")
        """
        buffer = ReorderingBuffer(delay_ms=50)
        
        # Создаем Trade событие с текущим временем
        now_ms = int(time.time() * 1000)
        trade = TradeEvent(
            price=Decimal("50000"),
            quantity=Decimal("1.0"),
            is_buyer_maker=True,
            event_time=now_ms
        )
        
        # Добавляем в буфер
        buffer.add(trade, event_time=now_ms, priority=0)
        
        # Сразу вызываем pop_ready() (событие моложе 50ms)
        ready = buffer.pop_ready()
        
        # ОЖИДАНИЕ: Пустой список (событие слишком свежее)
        assert len(ready) == 0, "Buffer should NOT return events younger than delay_ms"
    
    def test_buffer_returns_old_events(self):
        """
        Буфер ДОЛЖЕН отдавать события старше delay.
        
        Сценарий:
        - delay = 50ms
        - Добавляем событие T=now-100ms (старое)
        - Вызываем pop_ready()
        - ОЖИДАНИЕ: Событие возвращается (старше 50ms)
        """
        buffer = ReorderingBuffer(delay_ms=50)
        
        # Создаем "старое" событие (100ms назад)
        old_time_ms = int((time.time() - 0.1) * 1000)  # 100ms ago
        trade = TradeEvent(
            price=Decimal("50000"),
            quantity=Decimal("1.0"),
            is_buyer_maker=True,
            event_time=old_time_ms
        )
        
        buffer.add(trade, event_time=old_time_ms, priority=0)
        
        # Вызываем pop_ready()
        ready = buffer.pop_ready()
        
        # ОЖИДАНИЕ: Событие возвращено (старше delay)
        assert len(ready) == 1, "Buffer SHOULD return events older than delay_ms"
        assert ready[0] == trade
    
    def test_trade_and_depth_processed_together(self):
        """
        ГЛАВНЫЙ ТЕСТ: Trade и Depth с разницей <delay обрабатываются ВМЕСТЕ.
        
        Сценарий Ghost Trade:
        - T=0ms: Trade arrives
        - T=30ms: Depth arrives (связанное с Trade событие)
        - delay = 50ms
        
        ТЕКУЩЕЕ ПОВЕДЕНИЕ (BAD):
        - get_all_sorted() очищает буфер сразу
        - Trade обрабатывается отдельно
        - Depth обрабатывается в следующей итерации
        → Айсберг теряется
        
        ПРАВИЛЬНОЕ ПОВЕДЕНИЕ:
        - pop_ready() на T=60ms возвращает ОБА события
        - Trade обрабатывается первым (priority=0)
        - Depth обрабатывается вторым (priority=1)
        → Айсберг детектируется
        """
        buffer = ReorderingBuffer(delay_ms=50)
        
        # T=0: Trade arrives
        base_time_ms = int((time.time() - 0.1) * 1000)  # 100ms ago (old event)
        trade = TradeEvent(
            price=Decimal("50000"),
            quantity=Decimal("5.0"),
            is_buyer_maker=False,  # Aggressive buy
            event_time=base_time_ms
        )
        
        # T+30ms: Depth arrives (iceberg refill!)
        depth = OrderBookUpdate(
            bids=[(Decimal("49999"), Decimal("10"))],
            asks=[(Decimal("50000"), Decimal("10"))],  # Volume restored!
            first_update_id=1000,
            final_update_id=1001,
            event_time=base_time_ms + 30  # 30ms later
        )
        
        # Добавляем события
        buffer.add(trade, event_time=base_time_ms, priority=0)  # Trade priority
        buffer.add(depth, event_time=base_time_ms + 30, priority=1)  # Depth priority
        
        # Вызываем pop_ready() (оба события старше 50ms)
        ready = buffer.pop_ready()
        
        # ОЖИДАНИЕ: ОБА события возвращены ВМЕСТЕ
        assert len(ready) == 2, "Both Trade and Depth should be returned together"
        
        # Проверяем порядок: Trade ПЕРВЫМ (priority=0)
        assert isinstance(ready[0], TradeEvent), "Trade should be processed FIRST"
        assert isinstance(ready[1], OrderBookUpdate), "Depth should be processed SECOND"
    
    def test_priority_ordering_within_window(self):
        """
        При ОДИНАКОВОМ времени события сортируются по приоритету.
        
        Сценарий:
        - Trade и Depth с ОДИНАКОВЫМ event_time (rare but possible)
        - Trade priority=0, Depth priority=1
        - ОЖИДАНИЕ: Trade обрабатывается ПЕРВЫМ
        """
        buffer = ReorderingBuffer(delay_ms=50)
        
        # Одинаковое время
        same_time_ms = int((time.time() - 0.1) * 1000)
        
        trade = TradeEvent(
            price=Decimal("50000"),
            quantity=Decimal("1.0"),
            is_buyer_maker=True,
            event_time=same_time_ms
        )
        
        depth = OrderBookUpdate(
            bids=[(Decimal("49999"), Decimal("10"))],
            asks=[(Decimal("50000"), Decimal("10"))],
            first_update_id=1000,
            final_update_id=1001,
            event_time=same_time_ms  # Same time!
        )
        
        # Добавляем в ОБРАТНОМ порядке (сначала Depth, потом Trade)
        buffer.add(depth, event_time=same_time_ms, priority=1)
        buffer.add(trade, event_time=same_time_ms, priority=0)
        
        ready = buffer.pop_ready()
        
        # ОЖИДАНИЕ: Trade ПЕРВЫМ (несмотря на порядок добавления)
        assert len(ready) == 2
        assert isinstance(ready[0], TradeEvent), "Trade should be FIRST (higher priority)"
        assert isinstance(ready[1], OrderBookUpdate), "Depth should be SECOND"
    
    def test_adaptive_delay_integration(self):
        """
        Буфер должен работать с динамическим delay (адаптивная задержка).
        
        Сценарий:
        - Создаем buffer с delay=50ms
        - Меняем delay_sec на 0.1 (100ms)
        - События старше 100ms должны возвращаться
        - События моложе 100ms НЕ должны возвращаться
        """
        buffer = ReorderingBuffer(delay_ms=50)
        
        # Меняем delay динамически (симуляция adaptive delay)
        buffer.delay_sec = 0.1  # 100ms
        
        # Событие 150ms назад (старше 100ms)
        old_event_ms = int((time.time() - 0.15) * 1000)
        old_trade = TradeEvent(
            price=Decimal("50000"),
            quantity=Decimal("1.0"),
            is_buyer_maker=True,
            event_time=old_event_ms
        )
        
        # Событие 80ms назад (моложе 100ms)
        recent_event_ms = int((time.time() - 0.08) * 1000)
        recent_trade = TradeEvent(
            price=Decimal("50001"),
            quantity=Decimal("2.0"),
            is_buyer_maker=False,
            event_time=recent_event_ms
        )
        
        buffer.add(old_trade, event_time=old_event_ms, priority=0)
        buffer.add(recent_trade, event_time=recent_event_ms, priority=0)
        
        ready = buffer.pop_ready()
        
        # ОЖИДАНИЕ: Только старое событие
        assert len(ready) == 1, "Only events older than adaptive delay should be returned"
        assert ready[0] == old_trade, "Should return the 150ms old event"
        
        # Проверяем что новое событие осталось в буфере
        assert len(buffer.buffer) == 1, "Recent event should remain in buffer"


class TestGhostTradeScenario:
    """Интеграционный тест: полный сценарий Ghost Trade"""
    
    @pytest.mark.asyncio
    async def test_full_ghost_trade_scenario(self):
        """
        Симуляция реального HFT сценария с race condition.
        
        Timeline:
        T=0ms:    sleep(50ms) starts
        T=10ms:   Trade arrives → added to buffer
        T=35ms:   Depth arrives → added to buffer
        T=50ms:   pop_ready() called
        
        ОЖИДАНИЕ: ОБА события возвращены ВМЕСТЕ
        """
        buffer = ReorderingBuffer(delay_ms=50)
        
        # Базовое время (старое, чтобы события были "ready")
        base_time = time.time() - 0.1  # 100ms ago
        
        # T=0: Trade
        trade_time_ms = int(base_time * 1000)
        trade = TradeEvent(
            price=Decimal("50000"),
            quantity=Decimal("5.0"),
            is_buyer_maker=False,
            event_time=trade_time_ms
        )
        
        # T+35ms: Depth (iceberg refill)
        depth_time_ms = trade_time_ms + 35
        depth = OrderBookUpdate(
            bids=[(Decimal("49999"), Decimal("10"))],
            asks=[(Decimal("50000"), Decimal("10"))],
            first_update_id=1000,
            final_update_id=1001,
            event_time=depth_time_ms
        )
        
        # Симулируем асинхронное прибытие
        buffer.add(trade, event_time=trade_time_ms, priority=0)
        await asyncio.sleep(0.035)  # 35ms delay
        buffer.add(depth, event_time=depth_time_ms, priority=1)
        
        # Вызываем pop_ready() (оба события старше 50ms от base_time)
        ready = buffer.pop_ready()
        
        # КРИТИЧЕСКАЯ ПРОВЕРКА
        assert len(ready) == 2, "Ghost Trade scenario: Both events MUST be returned together"
        assert isinstance(ready[0], TradeEvent)
        assert isinstance(ready[1], OrderBookUpdate)
        
        # Проверяем что buffer очищен от этих событий
        ready2 = buffer.pop_ready()
        assert len(ready2) == 0, "Buffer should be empty after pop_ready()"
