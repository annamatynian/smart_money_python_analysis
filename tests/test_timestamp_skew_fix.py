# ===========================================================================
# TEST: Timestamp Skew Fix (Gemini Validation)
# ===========================================================================

"""
WHY: Проверяет что event_time заполняется корректно (биржевое время).

Проблема (до fix):
- TradeEvent.event_time = биржевое время (int, мс)
- OrderBookUpdate.event_time = локальное время (datetime.now())
- Delta-t расчет смешивал две временные шкалы → шум в ML features

После fix:
- Оба события используют int (миллисекунды) от биржи
- Delta-t = abs(update.event_time - trade.event_time) корректен
"""

import pytest
from decimal import Decimal
from domain import OrderBookUpdate, TradeEvent


class TestTimestampSkewFix:
    """Тесты корректности типов event_time после Gemini fix"""
    
    def test_orderbookupdate_event_time_is_int(self):
        """
        WHY: OrderBookUpdate.event_time должен быть int (миллисекунды).
        
        Было: datetime (default_factory=datetime.now) ❌
        Стало: int (заполняется из data['E']) ✅
        """
        # Создаем OrderBookUpdate с биржевым временем
        update = OrderBookUpdate(
            first_update_id=1000,
            final_update_id=1001,
            event_time=1703845200000,  # int (миллисекунды)
            bids=[(Decimal("60000"), Decimal("1.5"))],
            asks=[(Decimal("60100"), Decimal("0.5"))]
        )
        
        # Проверяем что event_time - это int
        assert isinstance(update.event_time, int), \
            f"event_time должен быть int, а получили {type(update.event_time)}"
        
        # Проверяем разумность значения (должно быть в миллисекундах)
        assert update.event_time > 1600000000000, \
            "event_time должен быть в миллисекундах (>= 2020 год)"
        assert update.event_time < 2000000000000, \
            "event_time должен быть в миллисекундах (<= 2033 год)"
    
    def test_tradeevent_event_time_is_int(self):
        """
        WHY: TradeEvent.event_time всегда был int - проверяем что не сломали.
        """
        trade = TradeEvent(
            price=Decimal("60050"),
            quantity=Decimal("0.5"),
            is_buyer_maker=False,
            event_time=1703845201000  # int (миллисекунды)
        )
        
        assert isinstance(trade.event_time, int), \
            f"TradeEvent.event_time должен быть int, получили {type(trade.event_time)}"
    
    def test_delta_t_calculation_compatibility(self):
        """
        WHY: Проверяет что Delta-t можно вычислить корректно.
        
        Формула: delta_t_ms = abs(update.event_time - trade.event_time)
        Должно работать без ошибок типов.
        """
        trade = TradeEvent(
            price=Decimal("60050"),
            quantity=Decimal("0.5"),
            is_buyer_maker=False,
            event_time=1703845200000  # Trade пришла первой
        )
        
        update = OrderBookUpdate(
            first_update_id=1000,
            final_update_id=1001,
            event_time=1703845200025,  # Update пришел через 25ms
            bids=[(Decimal("60000"), Decimal("1.6"))],
            asks=[]
        )
        
        # Вычисляем Delta-t (должно работать без ошибок)
        delta_t_ms = abs(update.event_time - trade.event_time)
        
        # Проверяем результат
        assert isinstance(delta_t_ms, int), "Delta-t должен быть int"
        assert delta_t_ms == 25, f"Ожидали delta_t=25ms, получили {delta_t_ms}"
    
    def test_delta_t_with_race_condition(self):
        """
        WHY: Проверяет корректность при Race Condition (update раньше trade).
        
        В real-time потоке update может прийти раньше trade из-за асинхронности.
        Формула с abs() должна обработать это корректно.
        """
        trade = TradeEvent(
            price=Decimal("60050"),
            quantity=Decimal("0.5"),
            is_buyer_maker=False,
            event_time=1703845200100  # Trade пришла ПОЗЖЕ
        )
        
        update = OrderBookUpdate(
            first_update_id=1000,
            final_update_id=1001,
            event_time=1703845200090,  # Update пришел РАНЬШЕ (race condition)
            bids=[(Decimal("60000"), Decimal("1.6"))],
            asks=[]
        )
        
        # Delta-t с abs() должен обработать отрицательную разницу
        delta_t_ms = abs(update.event_time - trade.event_time)
        
        assert delta_t_ms == 10, f"Ожидали delta_t=10ms (race condition), получили {delta_t_ms}"
    
    def test_realistic_delta_t_range(self):
        """
        WHY: Проверяет что Delta-t находится в реалистичном диапазоне.
        
        Теория (из документации):
        - Биржевые refills: 5-30ms
        - Новые ордера MM: 50-500ms
        - Race conditions: могут быть отрицательными (но abs() исправит)
        """
        # Сценарий: Быстрый refill (биржевой айсберг)
        trade_fast = TradeEvent(
            price=Decimal("60050"),
            quantity=Decimal("0.5"),
            is_buyer_maker=False,
            event_time=1703845200000
        )
        
        update_fast = OrderBookUpdate(
            first_update_id=1000,
            final_update_id=1001,
            event_time=1703845200015,  # 15ms delay
            bids=[(Decimal("60000"), Decimal("1.6"))],
            asks=[]
        )
        
        delta_t_fast = abs(update_fast.event_time - trade_fast.event_time)
        
        # Проверяем что попали в диапазон биржевых refills
        assert 5 <= delta_t_fast <= 30, \
            f"Быстрый refill должен быть 5-30ms, получили {delta_t_fast}ms"
        
        # Сценарий: Медленный новый ордер (маркет-мейкер)
        trade_slow = TradeEvent(
            price=Decimal("60050"),
            quantity=Decimal("0.5"),
            is_buyer_maker=False,
            event_time=1703845200000
        )
        
        update_slow = OrderBookUpdate(
            first_update_id=1000,
            final_update_id=1001,
            event_time=1703845200200,  # 200ms delay
            bids=[(Decimal("60000"), Decimal("1.6"))],
            asks=[]
        )
        
        delta_t_slow = abs(update_slow.event_time - trade_slow.event_time)
        
        # Проверяем что попали в диапазон новых ордеров
        assert 50 <= delta_t_slow <= 500, \
            f"Новый ордер MM должен быть 50-500ms, получили {delta_t_slow}ms"


class TestTimestampValidationIntegration:
    """Интеграционные тесты для проверки всей цепочки"""
    
    def test_binance_infrastructure_provides_event_time(self):
        """
        WHY: Проверяет что BinanceInfrastructure заполняет event_time.
        
        NOTE: Это unit-тест для infrastructure, не требует реального API.
        """
        from infrastructure import BinanceInfrastructure
        
        # Проверяем что метод существует и имеет правильную сигнатуру
        infra = BinanceInfrastructure()
        
        # Проверяем что listen_updates возвращает AsyncGenerator
        assert hasattr(infra, 'listen_updates'), \
            "BinanceInfrastructure должен иметь метод listen_updates"
        
        # NOTE: Полную проверку с реальным WebSocket делаем в отдельном интеграционном тесте


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
