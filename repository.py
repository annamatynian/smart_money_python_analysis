import asyncpg
from decimal import Decimal
from typing import Dict, Optional
import os

# Дублируем Enum здесь, чтобы не было круговых импортов, 
# либо можно импортировать из domain если там нет circular dependency
# Проще передавать статус строкой.

class PostgresRepository:
    def __init__(self, dsn: str):
        self.dsn = dsn
        self.pool: Optional[asyncpg.Pool] = None

    async def connect(self):
        """Создает пул соединений и таблицу"""
        self.pool = await asyncpg.create_pool(self.dsn)
        
        async with self.pool.acquire() as conn:
            # Создаем таблицу, если её нет
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS iceberg_levels (
                    price NUMERIC PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    is_ask BOOLEAN NOT NULL,
                    total_hidden_volume NUMERIC NOT NULL,
                    creation_time TIMESTAMPTZ DEFAULT NOW(),
                    last_update_time TIMESTAMPTZ DEFAULT NOW(),
                    status TEXT NOT NULL,
                    is_gamma_wall BOOLEAN DEFAULT FALSE,
                    confidence_score DOUBLE PRECISION
                );
            """)
            # 2. НОВАЯ ТАБЛИЦА: История для ML (добавляем её)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS iceberg_training_data (
                    id SERIAL PRIMARY KEY,
                    event_time TIMESTAMPTZ,
                    symbol TEXT,
                    price NUMERIC,
                    is_ask BOOLEAN,
                    trade_quantity NUMERIC,       -- Насколько сильно ударили
                    visible_volume_before NUMERIC,-- Сколько стояло в стакане
                    added_volume NUMERIC,
                    total_accumulated NUMERIC,
                    spread NUMERIC,
                    obi_value NUMERIC,
                    dist_call NUMERIC,
                    dist_put NUMERIC,
                    total_gex NUMERIC,
                    confidence DOUBLE PRECISION,
                    is_breach BOOLEAN DEFAULT FALSE,
                    is_near_gamma_wall BOOLEAN DEFAULT FALSE,  -- НОВОЕ ПОЛЕ (GEX)
                    gamma_wall_type TEXT                       -- НОВОЕ ПОЛЕ (GEX)
                );
            """)
            
            # 3. НОВАЯ ТАБЛИЦА: Market Metrics (Task: Gemini Phase 3.2)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS market_metrics (
                    id SERIAL PRIMARY KEY,
                    symbol VARCHAR(20) NOT NULL,
                    timestamp TIMESTAMPTZ NOT NULL,
                    mid_price NUMERIC(18,8),
                    ofi NUMERIC(12,4),
                    obi NUMERIC(12,4),
                    spread_bps NUMERIC(8,2),
                    created_at TIMESTAMPTZ DEFAULT NOW()
                );
            """)
            
            # Индекс для быстрых временных запросов
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_market_metrics_symbol_timestamp
                ON market_metrics(symbol, timestamp DESC);
            """)
            
            print("🐘 PostgreSQL connected (Levels + History + Market Metrics tables ready).")
        

    async def close(self):
        if self.pool:
            await self.pool.close()

    async def save_level(self, level, symbol: str):
        """Сохраняет уровень (Insert или Update)"""
        if not self.pool: return

        # WHY: Обновленный запрос с новыми полями для антиспуфинга
        query = """
            INSERT INTO iceberg_levels (
                price, symbol, is_ask, total_hidden_volume, 
                creation_time, last_update_time, status, 
                is_gamma_wall, confidence_score,
                spoofing_probability, refill_count
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            ON CONFLICT (price) DO UPDATE SET
                total_hidden_volume = EXCLUDED.total_hidden_volume,
                last_update_time = EXCLUDED.last_update_time,
                status = EXCLUDED.status,
                is_gamma_wall = EXCLUDED.is_gamma_wall,
                confidence_score = EXCLUDED.confidence_score,
                spoofing_probability = EXCLUDED.spoofing_probability,
                refill_count = EXCLUDED.refill_count;
        """
        
        try:
            async with self.pool.acquire() as conn:
                await conn.execute(query, 
                    level.price, 
                    symbol,
                    level.is_ask, 
                    level.total_hidden_volume,
                    level.creation_time,
                    level.last_update_time,
                    level.status.value, 
                    level.is_gamma_wall,
                    level.confidence_score,
                    level.spoofing_probability,  # НОВОЕ
                    level.refill_count            # НОВОЕ
                )
                
                # WHY: Сохраняем контекст отмены отдельно (если есть)
                if level.cancellation_context is not None:
                    await self._save_cancellation_context(conn, level)
        except Exception as e:
            print(f"❌ DB Error: {e}")
    
    async def _save_cancellation_context(self, conn, level):
        """WHY: Сохраняет контекст отмены в отдельную таблицу"""
        ctx = level.cancellation_context
        query = """
            INSERT INTO iceberg_cancellation_context (
                price, mid_price_at_cancel, distance_from_level_pct,
                price_velocity_5s, moving_towards_level, volume_executed_pct
            ) VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (price) DO UPDATE SET
                mid_price_at_cancel = EXCLUDED.mid_price_at_cancel,
                distance_from_level_pct = EXCLUDED.distance_from_level_pct,
                price_velocity_5s = EXCLUDED.price_velocity_5s,
                moving_towards_level = EXCLUDED.moving_towards_level,
                volume_executed_pct = EXCLUDED.volume_executed_pct,
                cancelled_at = NOW();
        """
        try:
            await conn.execute(query,
                level.price,
                ctx.mid_price_at_cancel,
                ctx.distance_from_level_pct,
                ctx.price_velocity_5s,
                ctx.moving_towards_level,
                ctx.volume_executed_pct
            )
        except Exception as e:
            print(f"⚠️ Failed to save cancellation context: {e}")

    async def load_active_levels(self, symbol: str) -> Dict[Decimal, any]:
        """Загружает активные уровни"""
        if not self.pool: return {}

        # Чтобы избежать кругового импорта, мы вернем dict с данными,
        # а IcebergLevel создадим уже внутри domain.py
        loaded_data = {}
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT * FROM iceberg_levels 
                    WHERE symbol = $1 AND status = 'ACTIVE'
                """, symbol)
                
                for r in rows:
                    price = r['price']
                    # Собираем словарь данных
                    loaded_data[price] = {
                        'price': price,
                        'is_ask': r['is_ask'],
                        'total_hidden_volume': r['total_hidden_volume'],
                        'creation_time': r['creation_time'],
                        'last_update_time': r['last_update_time'],
                        'status': r['status'],
                        'is_gamma_wall': r['is_gamma_wall'],
                        'confidence_score': r['confidence_score']
                    }
            print(f"🐘 Loaded {len(loaded_data)} levels from DB.")
            return loaded_data
        except Exception as e:
            print(f"❌ DB Load Error: {e}")
            return {}

    async def log_training_event(self, data: dict):
        """Сохраняет историческое событие для ML (с GEX-контекстом)"""
        if not self.pool:
            return
        
        query = """
            INSERT INTO iceberg_training_data (
                event_time, symbol, price, is_ask,
                trade_quantity, visible_volume_before, 
                added_volume, total_accumulated, 
                spread, obi_value, 
                dist_call, dist_put, total_gex,
                confidence, is_breach,
                is_near_gamma_wall, gamma_wall_type
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17)
        """
        try:
            async with self.pool.acquire() as conn:
                await conn.execute(query, 
                    data['event_time'], data['symbol'], data['price'], data['is_ask'],
                    data['trade_quantity'], data['visible_volume_before'],
                    data['added_volume'], data['total_accumulated'], 
                    data['spread'], data['obi_value'],
                    data['dist_call'], data['dist_put'], data['total_gex'],
                    data['confidence'], data['is_breach'],
                    data.get('is_near_gamma_wall', False),  # НОВОЕ ПОЛЕ
                    data.get('gamma_wall_type', None)        # НОВОЕ ПОЛЕ
                )
        except Exception as e:
            print(f"❌ ML Logging Error: {e}")
            import traceback
            traceback.print_exc()
    
    # ===================================================================
    # НОВЫЙ МЕТОД: Market Metrics Logging (Task: Gemini Phase 3.2)
    # ===================================================================
    
    async def log_market_metrics(
        self,
        symbol: str,
        timestamp,
        mid_price: Optional[Decimal],
        ofi: Optional[float],
        obi: Optional[float],
        spread_bps: Optional[float]
    ):
        """
        WHY: Сохраняет рыночные метрики для ML обучения и бэктестинга.
        
        Таблица market_metrics хранит временной ряд:
        - mid_price: Средняя цена (Best Bid + Best Ask) / 2
        - ofi: Order Flow Imbalance (изменение ликвидности)
        - obi: Order Book Imbalance (дисбаланс bid/ask)
        - spread_bps: Спред в базисных пунктах
        
        Используется для:
        1. ML обучение моделей прогнозирования
        2. Бэктестинг стратегий
        3. Анализ корреляций OFI/OBI с движением цены
        
        Args:
            symbol: Торговая пара (BTCUSDT, ETHUSDT и т.д.)
            timestamp: Время события
            mid_price: Средняя цена (может быть None если стакан пуст)
            ofi: Order Flow Imbalance (None если нет previous_snapshot)
            obi: Order Book Imbalance
            spread_bps: Спред в базисных пунктах
        
        Примечание:
        - Создание таблицы происходит в connect() методе
        - Метод асинхронный для совместимости с asyncpg
        """
        if not self.pool:
            print("⚠️ DB pool not initialized, cannot log metrics")
            return
        
        query = """
            INSERT INTO market_metrics (
                symbol, timestamp, mid_price, ofi, obi, spread_bps
            ) VALUES ($1, $2, $3, $4, $5, $6)
        """
        
        try:
            async with self.pool.acquire() as conn:
                await conn.execute(
                    query,
                    symbol,
                    timestamp,
                    mid_price,
                    ofi,
                    obi,
                    spread_bps
                )
        except Exception as e:
            print(f"❌ Market Metrics Logging Error: {e}")
            import traceback
            traceback.print_exc()