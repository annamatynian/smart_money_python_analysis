import asyncio
import asyncpg
from decimal import Decimal
from typing import AsyncGenerator
import sys

# Импорты твоих классов
from infrastructure import IMarketDataSource
from domain import OrderBookUpdate, TradeEvent
from services import TradingEngine
from repository import PostgresRepository

# 1. Фейковая биржа (Mock)
class ScenerioMock(IMarketDataSource):
    async def get_snapshot(self, symbol: str):
        return {'bids': [], 'asks': [], 'lastUpdateId': 0}

    async def listen_updates(self, symbol) -> AsyncGenerator[OrderBookUpdate, None]:
        # Видимый ордер на 1 BTC
        print("🎭 MOCK: Sending OrderBook Update...")
        yield OrderBookUpdate(
            first_update_id=1, final_update_id=2,
            bids=[(Decimal("90000.00"), Decimal("1.0"))],
            asks=[]
        )
        while True: await asyncio.sleep(1)

    async def listen_trades(self, symbol) -> AsyncGenerator[TradeEvent, None]:
        await asyncio.sleep(0.5) 
        # Продажа 10 BTC
        print("🎭 MOCK: Sending Trade (Size: 10.0)...")
        yield TradeEvent(
            price=Decimal("90000.00"),
            quantity=Decimal("10.0"), 
            is_buyer_maker=True,
            event_time=1638747660000
        )
        while True: await asyncio.sleep(1)

# 2. Скрипт проверки
async def run_test():
    # ⚠️ ПРОВЕРЬ ПАРОЛЬ НИЖЕ
    DSN = "postgresql://postgres:Jayaasiri2185@localhost:5432/trading_db"
    
    print("🧪 Starting Database Test...")
    
    # Сброс таблицы
    try:
        sys_conn = await asyncpg.connect(DSN)
        await sys_conn.execute("DROP TABLE IF EXISTS iceberg_training_data")
        await sys_conn.close()
        print("🗑️  Old table dropped.")
    except Exception as e:
        print(f"⚠️ Table drop warning: {e}")
        return

    # Подключение
    try:
        repo = PostgresRepository(DSN)
        await repo.connect()
    except Exception as e:
        print(f"❌ Connection Error: {e}")
        return
    
    # Запуск теста
    mock_infra = ScenerioMock()
    engine = TradingEngine("TEST_TICKER", mock_infra, repository=repo)
    task = asyncio.create_task(engine.run())
    
    print("⏳ Waiting for processing (3s)...")
    await asyncio.sleep(3)
    
    # Проверка БД
    print("\n🔍 Checking Database...")
    try:
        async with repo.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM iceberg_training_data 
                WHERE symbol = 'TEST_TICKER' 
                ORDER BY id DESC LIMIT 1
            """)
            
            if rows:
                row = rows[0]
                print(f"✅ SUCCESS! Data found:")
                print(f"   Price: {row['price']}")
                print(f"   Trade Qty: {row['trade_quantity']} (Exp: 10.0)")
                print(f"   Visible: {row['visible_volume_before']} (Exp: 1.0)")
                print(f"   Added Hidden: {row['added_volume']} (Exp: 9.0)")
                print(f"   GEX Total: {row['total_gex']}")
            else:
                print("❌ FAILURE: Table is empty.")
                
    except Exception as e:
        print(f"❌ Error querying DB: {e}")
    
    task.cancel()
    await repo.close()

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(run_test())