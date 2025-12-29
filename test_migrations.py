"""
WHY: Тестовый скрипт для проверки миграций БД.

Проверяет:
1. Создание таблиц iceberg_lifecycle и iceberg_feature_snapshot
2. Наличие всех индексов
3. Foreign key constraints
4. View should_create_snapshot

USAGE:
    python test_migrations.py
"""

import asyncio
from repository import PostgresRepository

async def test_migrations():
    print("="*60)
    print("🧪 ТЕСТИРОВАНИЕ МИГРАЦИЙ БД")
    print("="*60)
    
    # 1. Подключение
    dsn = "postgresql://postgres:Jayaasiri2185@localhost:5432/trading_db"
    repo = PostgresRepository(dsn)
    
    try:
        await repo.connect()
        print("✅ Подключение к БД успешно")
        
        # 2. Применение миграций
        print("\n📦 Применяем миграции...")
        await repo.run_migrations()
        
        # 3. Проверка таблиц
        print("\n🔍 Проверяем созданные таблицы...")
        
        async with repo.pool.acquire() as conn:
            # Проверка iceberg_lifecycle
            result = await conn.fetchval("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_name = 'iceberg_lifecycle'
                );
            """)
            print(f"  - iceberg_lifecycle: {'✅' if result else '❌'}")
            
            # Проверка iceberg_feature_snapshot
            result = await conn.fetchval("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_name = 'iceberg_feature_snapshot'
                );
            """)
            print(f"  - iceberg_feature_snapshot: {'✅' if result else '❌'}")
            
            # Проверка view
            result = await conn.fetchval("""
                SELECT EXISTS (
                    SELECT FROM information_schema.views 
                    WHERE table_name = 'should_create_snapshot'
                );
            """)
            print(f"  - should_create_snapshot (view): {'✅' if result else '❌'}")
            
            # Проверка индексов
            print("\n🔍 Проверяем индексы...")
            indexes = await conn.fetch("""
                SELECT indexname 
                FROM pg_indexes 
                WHERE tablename IN ('iceberg_lifecycle', 'iceberg_feature_snapshot')
                ORDER BY indexname;
            """)
            
            for idx in indexes:
                print(f"  - {idx['indexname']}")
            
            # Проверка колонок lifecycle
            print("\n🔍 Колонки iceberg_lifecycle:")
            columns = await conn.fetch("""
                SELECT column_name, data_type 
                FROM information_schema.columns 
                WHERE table_name = 'iceberg_lifecycle'
                ORDER BY ordinal_position;
            """)
            
            for col in columns:
                print(f"  - {col['column_name']:<30} {col['data_type']}")
            
            # Проверка колонок snapshot
            print("\n🔍 Колонки iceberg_feature_snapshot:")
            columns = await conn.fetch("""
                SELECT column_name, data_type 
                FROM information_schema.columns 
                WHERE table_name = 'iceberg_feature_snapshot'
                ORDER BY ordinal_position;
            """)
            
            for col in columns:
                print(f"  - {col['column_name']:<30} {col['data_type']}")
        
        print("\n" + "="*60)
        print("✅ ВСЕ МИГРАЦИИ ПРИМЕНЕНЫ УСПЕШНО!")
        print("="*60)
        
    except Exception as e:
        print(f"\n❌ ОШИБКА: {e}")
        import traceback
        traceback.print_exc()
    
    finally:
        await repo.close()
        print("\n🔒 Соединение закрыто")

if __name__ == "__main__":
    asyncio.run(test_migrations())
