import asyncio
import os
import random
from datetime import datetime, timedelta
from decimal import Decimal
from dotenv import load_dotenv

# Импортируем наши модули
from repository import PostgresRepository
from advisor_hybrid import HybridAdvisorService

# Загружаем ключи
load_dotenv()

# DSN твоей базы данных (проверь пароль и хост!)
DB_DSN = "postgresql://postgres:Jayaasiri2185@localhost:5432/trading_db"

async def generate_synthetic_data(repo: PostgresRepository, symbol: str, start_time: datetime, count: int = 60):
    """
    Генерирует 60 минут данных (1 час), имитируя ВАЙКОФФ НАКОПЛЕНИЕ.
    Сценарий:
    - Цена: Флэт (стоит в диапазоне 95000-95100).
    - Киты: Агрессивно покупают (+10 BTC каждую минуту).
    - Толпа: Продает в панике (-2 BTC каждую минуту).
    - Деривативы: Страх высокий (Skew > 5), Базис низкий.
    """
    print(f"🛠 Генерируем синтетические данные для {symbol}...")
    
    async with repo.pool.acquire() as conn:
        # Очистка старых данных для чистоты эксперимента
        await conn.execute("DELETE FROM market_metrics_full WHERE symbol = $1", symbol)
        
        current_time = start_time
        base_price = 95000.0
        
        for i in range(count):
            # 1. Цена почти не меняется (Флэт)
            # Добавляем немного шума, чтобы не было скучно
            noise = random.uniform(-20, 20)
            price = base_price + noise
            
            # 2. Киты покупают (CVD растет)
            whale_delta = 10.0 + random.uniform(-2, 5) # +10..15 BTC
            
            # 3. Толпа продает (Panic selling)
            minnow_delta = -2.0 + random.uniform(-1, 0) # -2..-3 BTC
            
            # 4. OFI положительный (лимитные покупки держат цену)
            ofi = 5.0 + random.uniform(0, 2)
            
            # 5. Skew высокий (Толпа боится падения, покупает Путы)
            skew = 6.0 + random.uniform(0, 1) # > 5% = Fear
            
            # Вставка в БД
            await repo.log_full_metric({
                'timestamp': current_time,
                'symbol': symbol,
                'price': price,
                'spread_bps': 2.5,
                'ofi': ofi,
                'obi': 0.8, # Стакан плотный на бидах
                'whale_cvd': whale_delta,
                'minnow_cvd': minnow_delta,
                'basis': 2.0, # Низкий базис
                'skew': skew,
                'oi_delta': 100.0 # ОИ растет (набор позиций)
            })
            
            current_time += timedelta(minutes=1) # Шаг 1 минута

    print("✅ Данные загружены в SQL.")

async def run_test():
    repo = PostgresRepository(dsn=DB_DSN)
    advisor = HybridAdvisorService(db_dsn=DB_DSN)
    
    await repo.connect()
    
    # 1. Параметры теста
    symbol = "TEST_BTC"
    start_dt = datetime(2025, 1, 1, 12, 0, 0) # Произвольная дата в прошлом
    end_dt = start_dt + timedelta(hours=1)    # 1 час данных
    
    # 2. Создаем ситуацию в базе
    await generate_synthetic_data(repo, symbol, start_dt, count=60)
    
    # 3. Спрашиваем Агента
    print("\n🕵️‍♂️ ЗАПУСК АГЕНТА: Анализ синтетической ситуации...\n")
    
    question = "Посмотри на этот час. Что делают киты? Стоит ли мне открывать Лонг?"
    
    response = await advisor.ask_about_history(
        question=question,
        symbol=symbol,
        start=start_dt,
        end=end_dt,
        timeframe_m=60 # Агрегируем всё в одну часовую свечу
    )
    
    print("="*60)
    print("ОТВЕТ АГЕНТА:")
    print("="*60)
    print(response)
    print("="*60)
    
    await repo.close()

if __name__ == "__main__":
    # Windows hack для asyncio
    import sys
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        
    asyncio.run(run_test())