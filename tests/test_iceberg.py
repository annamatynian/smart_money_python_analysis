import asyncio
from decimal import Decimal
from typing import AsyncGenerator, Dict, Any

# Импортируем ваши классы (убедитесь, что имена файлов совпадают с импортами)
# Если ваши файлы называются domain.py, infrastructure.py - исправьте тут
from domain import OrderBookUpdate, TradeEvent
from infrastructure import IMarketDataSource
from services import TradingEngine

class IcebergScenarioMock(IMarketDataSource):
    """
    Сценарный Мок.
    Генерирует строго заданную последовательность для проверки математики айсберга.
    """
    
    async def get_snapshot(self, symbol: str) -> Dict[str, Any]:
        print("🎭 [TEST] 1. Отправляем снапшот: Bid 60000 c объемом 10 BTC")
        await asyncio.sleep(0.1)
        return {
            'bids': [(Decimal("60000.00"), Decimal("10.0"))], # Здесь стоит плита
            'asks': [(Decimal("60100.00"), Decimal("5.0"))],
            'lastUpdateId': 100
        }

    async def listen_updates(self, symbol: str) -> AsyncGenerator[OrderBookUpdate, None]:
        """Эмулируем поведение биржи после сделки"""
        
        # Ждем, пока движок инициализируется (он ждет 2 сек буферизации)
        await asyncio.sleep(3) 
        
        print(f"🎭 [TEST] 3. Отправляем Depth Update: Объем упал с 10.0 до 9.0 (изменение всего 1 BTC)")
        yield OrderBookUpdate(
            first_update_id=101,
            final_update_id=102,
            # Биржа говорит: осталось 9 BTC.
            # Хотя продали 5 BTC. Значит 4 BTC было подложено.
            bids=[(Decimal("60000.00"), Decimal("9.0"))], 
            asks=[]
        )
        
        # Держим соединение открытым
        while True:
            await asyncio.sleep(1)

    async def listen_trades(self, symbol: str) -> AsyncGenerator[TradeEvent, None]:
        """Эмулируем продажу в бид"""
        
        # Ждем инициализации + чуть меньше, чем depth update, 
        # чтобы сделка пришла чуть раньше (или попала в обработку вместе)
        await asyncio.sleep(2.5)
        
        print(f"🎭 [TEST] 2. Отправляем TRADE: Продажа 5.0 BTC по 60000")
        yield TradeEvent(
            price=Decimal("60000.00"),
            quantity=Decimal("5.0"),     # Агрессор продал 5 монет
            is_buyer_maker=True,         # True = Maker (Bid) покупал, значит Taker продавал
            event_time=1638747660000
        )

async def main():
    print("--- ЗАПУСК ТЕСТА НА АЙСБЕРГ ---\n")
    
    # 1. Создаем инфраструктуру с нашим сценарием
    mock_infra = IcebergScenarioMock()
    
    # 2. Создаем движок
    engine = TradingEngine("BTCUSDT", mock_infra)
    
    # 3. Запускаем (с таймаутом, чтобы тест не висел вечно)
    try:
        await asyncio.wait_for(engine.run(), timeout=6.0)
    except asyncio.TimeoutError:
        print("\n--- ТЕСТ ЗАВЕРШЕН (Timeout) ---")

if __name__ == "__main__":
    asyncio.run(main())