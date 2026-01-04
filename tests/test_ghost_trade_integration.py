"""
INTEGRATION TEST: Vulnerability #3 Fix in Production Context

Проверяет что исправление Ghost Trade Issue работает в реальной интеграции
services.py + infrastructure.py + domain.py
"""

import pytest
import asyncio
from decimal import Decimal
from datetime import datetime, timezone
import time
from infrastructure import ReorderingBuffer
from domain import TradeEvent, OrderBookUpdate


class TestGhostTradeProductionIntegration:
    """Интеграционные тесты для production сценариев"""
    
    @pytest.mark.asyncio
    async def test_production_workflow_with_pop_ready(self):
        """
        Симуляция реального production workflow services.py:
        
        1. sleep(delay_sec)
        2. Добавляем Trade и Depth в buffer
        3. Вызываем pop_ready()
        4. Обрабатываем события
        5. Повторяем цикл
        
        ОЖИДАНИЕ: Trade и Depth обрабатываются ВМЕСТЕ
        """
        buffer = ReorderingBuffer(delay_ms=50)
        
        # === ITERATION 1 ===
        # T=0: Sleep
        await asyncio.sleep(0.05)  # 50ms delay
        
        # T=50ms: Добавляем старые события (эмуляция "уже пришедших")
        old_time = time.time() - 0.2  # 200ms ago
        trade_time_ms = int(old_time * 1000)
        depth_time_ms = trade_time_ms + 30  # +30ms
        
        trade = TradeEvent(
            price=Decimal("50000"),
            quantity=Decimal("5.0"),
            is_buyer_maker=False,
            event_time=trade_time_ms
        )
        
        depth = OrderBookUpdate(
            bids=[(Decimal("49999"), Decimal("10"))],
            asks=[(Decimal("50000"), Decimal("10"))],  # Refilled!
            first_update_id=1000,
            final_update_id=1001,
            event_time=depth_time_ms
        )
        
        buffer.add(trade, event_time=trade_time_ms, priority=0)
        buffer.add(depth, event_time=depth_time_ms, priority=1)
        
        # Вызываем pop_ready() (как в services.py)
        sorted_events = buffer.pop_ready()
        
        # КРИТИЧЕСКАЯ ПРОВЕРКА
        assert len(sorted_events) == 2, "Both events must be returned"
        assert isinstance(sorted_events[0], TradeEvent)
        assert isinstance(sorted_events[1], OrderBookUpdate)
        
        # === ITERATION 2 ===
        # Buffer должен быть пустым
        await asyncio.sleep(0.05)
        sorted_events2 = buffer.pop_ready()
        assert len(sorted_events2) == 0, "Buffer should be empty"
    
    @pytest.mark.asyncio
    async def test_fresh_events_remain_in_buffer(self):
        """
        КРИТИЧЕСКИЙ ТЕСТ: Свежие события НЕ должны обрабатываться сразу.
        
        Сценарий:
        - delay = 50ms
        - Добавляем событие T=now (свежее)
        - pop_ready() должен вернуть ПУСТОЙ список
        - Событие остается в buffer
        - Через 60ms pop_ready() должен вернуть событие
        """
        buffer = ReorderingBuffer(delay_ms=50)
        
        # Добавляем СВЕЖЕЕ событие
        now_ms = int(time.time() * 1000)
        fresh_trade = TradeEvent(
            price=Decimal("50000"),
            quantity=Decimal("1.0"),
            is_buyer_maker=True,
            event_time=now_ms
        )
        
        buffer.add(fresh_trade, event_time=now_ms, priority=0)
        
        # Сразу вызываем pop_ready()
        ready = buffer.pop_ready()
        assert len(ready) == 0, "Fresh event should NOT be returned"
        
        # Проверяем что событие осталось в buffer
        assert len(buffer.buffer) == 1, "Event should remain in buffer"
        
        # Ждем 60ms (больше delay)
        await asyncio.sleep(0.06)
        
        # Теперь событие должно вернуться
        ready2 = buffer.pop_ready()
        assert len(ready2) == 1, "Old event should be returned now"
        assert ready2[0] == fresh_trade
    
    @pytest.mark.asyncio
    async def test_adaptive_delay_changes_filtering(self):
        """
        Проверка что изменение delay_sec динамически влияет на фильтрацию.
        
        Симуляция Adaptive Delay из services.py:
        - Начинаем с delay=50ms
        - Меняем на delay=100ms (сеть замедлилась)
        - Проверяем что фильтрация изменилась
        """
        buffer = ReorderingBuffer(delay_ms=50)
        
        # Событие 80ms назад
        old_time_80ms = int((time.time() - 0.08) * 1000)
        trade_80 = TradeEvent(
            price=Decimal("50000"),
            quantity=Decimal("1.0"),
            is_buyer_maker=True,
            event_time=old_time_80ms
        )
        
        buffer.add(trade_80, event_time=old_time_80ms, priority=0)
        
        # С delay=50ms событие 80ms назад должно вернуться
        ready = buffer.pop_ready()
        assert len(ready) == 1, "80ms old event should be ready with 50ms delay"
        
        # Добавляем еще одно событие 80ms назад
        old_time_80ms_2 = int((time.time() - 0.08) * 1000)
        trade_80_2 = TradeEvent(
            price=Decimal("50001"),
            quantity=Decimal("2.0"),
            is_buyer_maker=False,
            event_time=old_time_80ms_2
        )
        
        buffer.add(trade_80_2, event_time=old_time_80ms_2, priority=0)
        
        # Меняем delay на 100ms (Adaptive Delay увеличился)
        buffer.delay_sec = 0.1
        
        # Теперь событие 80ms назад НЕ должно вернуться (моложе 100ms)
        ready2 = buffer.pop_ready()
        assert len(ready2) == 0, "80ms old event should NOT be ready with 100ms delay"
        
        # Ждем еще 30ms (итого 110ms)
        await asyncio.sleep(0.03)
        
        # Теперь должно вернуться
        ready3 = buffer.pop_ready()
        assert len(ready3) == 1, "Now event is older than 100ms"


class TestBackwardCompatibility:
    """Проверка что get_all_sorted() еще работает (deprecated)"""
    
    def test_get_all_sorted_still_works(self):
        """
        get_all_sorted() может использоваться в legacy коде.
        Проверяем что он не сломан.
        """
        buffer = ReorderingBuffer(delay_ms=50)
        
        # Добавляем события
        old_time = int((time.time() - 0.1) * 1000)
        
        trade = TradeEvent(
            price=Decimal("50000"),
            quantity=Decimal("1.0"),
            is_buyer_maker=True,
            event_time=old_time
        )
        
        depth = OrderBookUpdate(
            bids=[(Decimal("49999"), Decimal("10"))],
            asks=[(Decimal("50000"), Decimal("10"))],
            first_update_id=1000,
            final_update_id=1001,
            event_time=old_time + 20
        )
        
        buffer.add(trade, event_time=old_time, priority=0)
        buffer.add(depth, event_time=old_time + 20, priority=1)
        
        # get_all_sorted() должен вернуть ВСЁ и очистить buffer
        all_events = buffer.get_all_sorted()
        
        assert len(all_events) == 2
        assert isinstance(all_events[0], TradeEvent)
        assert isinstance(all_events[1], OrderBookUpdate)
        assert len(buffer.buffer) == 0, "Buffer should be empty"
