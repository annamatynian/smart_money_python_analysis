import asyncio
from infrastructure import BinanceInfrastructure, DeribitInfrastructure
from repository import PostgresRepository
from services import TradingEngine
import colorama 
colorama.init() 

async def main():
    # 1. Настройка
    symbol = "BTCUSDT"
    print(f"🔥 Запуск нового движка для {symbol}...")
    
    # 2. Инициализация
    # Создаем инфраструктуру (подключение к Binance)
    infra = BinanceInfrastructure()
    deribit = DeribitInfrastructure()

    # --- БАЗА ДАННЫХ (ОТКЛЮЧЕНО ДЛЯ ТЕСТИРОВАНИЯ) ---
    # dsn = "postgresql://postgres:Jayaasiri2185@localhost:5432/trading_db"
    # repo = PostgresRepository(dsn)
    # await repo.connect()
    # await repo.run_migrations()  # Применяем миграции (lifecycle + features)
    repo = None  # Запускаем без БД
    # -------------------
    
    # Создаем Мозг (TradingEngine), который связывает Стакан, Аналитику и Данные
    engine = TradingEngine(symbol, infra, deribit_infra=deribit, repository=repo)
    
    # 3. Запуск
    try:
        await engine.run()
    except KeyboardInterrupt:
        print("\n🛑 Остановка бота пользователем.")
    except Exception as e:
        print(f"\n❌ Критическая ошибка: {e}")

if __name__ == "__main__":
    try:
        # Запуск асинхронного цикла (Windows/Linux)
        asyncio.run(main())
    except KeyboardInterrupt:
        pass