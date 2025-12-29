"""
WHY: Применяет SQL миграции через asyncpg (правильно обрабатывает UTF-8).

Использует встроенный механизм repository.run_migrations().
Безопаснее чем прямой psql на Windows (проблемы кодировки).

Author: Basilisca
Created: 2025-12-23
"""

import asyncio
from repository import PostgresRepository

# WHY: Database connection string (из main.py)
DB_DSN = "postgresql://postgres:Jayaasiri2185@localhost:5432/trading_db"

async def main():
    """Применяет все непримененные миграции из migrations/"""
    print("🚀 Starting migration process...")
    
    repo = PostgresRepository(DB_DSN)
    
    try:
        # 1. Подключаемся к БД
        await repo.connect()
        print("✅ Connected to PostgreSQL")
        
        # 2. Применяем миграции
        # WHY: Метод автоматически:
        # - Создаёт таблицу _migrations для отслеживания
        # - Читает файлы из migrations/
        # - Применяет только НОВЫЕ миграции
        # - Обрабатывает UTF-8 корректно (через Python)
        await repo.run_migrations()
        
        print("✅ All migrations applied successfully!")
        
    except Exception as e:
        print(f"❌ Migration failed: {e}")
        import traceback
        traceback.print_exc()
        
    finally:
        await repo.close()

if __name__ == '__main__':
    asyncio.run(main())
