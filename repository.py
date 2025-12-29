import asyncpg
from decimal import Decimal
from typing import Dict, Optional, List
from datetime import datetime, timedelta
import os
import pandas as pd

# Импортируем SmartCandle для типизации возвращаемых данных
from domain_smartcandle import SmartCandle

# ML Data Quality Guards
from utils_ml import DataLeakageGuard, safe_merge_candles_features

class PostgresRepository:
    def __init__(self, dsn: str):
        self.dsn = dsn
        self.pool: Optional[asyncpg.Pool] = None

    async def connect(self):
        """Создает пул соединений и ВСЕ необходимые таблицы"""
        self.pool = await asyncpg.create_pool(self.dsn)
        
        async with self.pool.acquire() as conn:
            # 1. ТАБЛИЦА АЙСБЕРГОВ (Iceberg Registry)
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
                    confidence_score DOUBLE PRECISION,
                    spoofing_probability DOUBLE PRECISION,
                    refill_count INTEGER
                );
            """)

            # 2. ТАБЛИЦА КОНТЕКСТА ОТМЕНЫ (FIX: Этого не было в твоем коде создания!)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS iceberg_cancellation_context (
                    price NUMERIC PRIMARY KEY,
                    mid_price_at_cancel NUMERIC,
                    distance_from_level_pct NUMERIC,
                    price_velocity_5s NUMERIC,
                    moving_towards_level BOOLEAN,
                    volume_executed_pct NUMERIC,
                    cancelled_at TIMESTAMPTZ DEFAULT NOW()
                );
            """)

            # 3. ТАБЛИЦА ДЛЯ ML ОБУЧЕНИЯ (Raw Events)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS iceberg_training_data (
                    id SERIAL PRIMARY KEY,
                    event_time TIMESTAMPTZ,
                    symbol TEXT,
                    price NUMERIC,
                    is_ask BOOLEAN,
                    trade_quantity NUMERIC,
                    visible_volume_before NUMERIC,
                    added_volume NUMERIC,
                    total_accumulated NUMERIC,
                    spread NUMERIC,
                    obi_value NUMERIC,
                    dist_call NUMERIC,
                    dist_put NUMERIC,
                    total_gex NUMERIC,
                    confidence DOUBLE PRECISION,
                    is_breach BOOLEAN DEFAULT FALSE,
                    is_near_gamma_wall BOOLEAN DEFAULT FALSE,
                    gamma_wall_type TEXT
                );
            """)
            
            # 4. СУПЕР-ТАБЛИЦА МЕТРИК (Unified Market Metrics)
            # Объединяет старую market_metrics и новые требования для SmartCandle
            # WHY: Синхронизировано с миграцией 005 (flow_/wall_/book_ префиксы)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS market_metrics_full (
                    time TIMESTAMPTZ NOT NULL,
                    symbol TEXT NOT NULL,
                    price NUMERIC,            -- mid_price
                    spread_bps NUMERIC,
                    
                    -- Микроструктура (book_ префикс = видимая книга ордеров)
                    book_ofi NUMERIC,                   -- Order Flow Imbalance
                    book_obi NUMERIC,                   -- Weighted Order Book Imbalance
                    
                    -- Агрессоры (flow_ префикс = те кто БЬЁТ)
                    flow_whale_cvd_delta NUMERIC,       -- Киты (>$100k trades)
                    flow_dolphin_cvd_delta NUMERIC,     -- Дельфины ($1k-$100k trades)
                    flow_minnow_cvd_delta NUMERIC,      -- Рыбы (<$1k trades)
                    
                    -- Стены (wall_ префикс = пассивные айсберги)
                    wall_whale_vol NUMERIC,             -- Whale iceberg volume detected
                    wall_dolphin_vol NUMERIC,           -- Dolphin iceberg volume
                    
                    -- Деривативы (для SmartCandle)
                    basis_apr NUMERIC,        -- Фьючерсный базис
                    options_skew NUMERIC,     -- Опционный страх
                    oi_delta NUMERIC,         -- Изменение OI
                    
                    -- Технические поля
                    is_aggressor_buy BOOLEAN
                );
                
                -- Индекс для быстрого RAG-поиска по времени
                CREATE INDEX IF NOT EXISTS idx_metrics_time_symbol 
                ON market_metrics_full (time DESC, symbol);
            """)
            
            # 5. ТАБЛИЦА ЖИЗНЕННОГО ЦИКЛА АЙСБЕРГОВ (для ML)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS iceberg_lifecycle (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    symbol TEXT NOT NULL,
                    price NUMERIC NOT NULL,
                    is_ask BOOLEAN NOT NULL,
                    event_type TEXT NOT NULL,  -- 'DETECTED' | 'REFILLED' | 'BREACHED' | 'EXHAUSTED' | 'CANCELLED'
                    event_time TIMESTAMPTZ DEFAULT NOW(),
                    survival_seconds INTEGER,   -- Сколько прожил
                    total_volume_absorbed NUMERIC,  -- Сколько объема съел
                    refill_count INTEGER,       -- Количество пополнений
                    outcome TEXT,               -- 'BREACH' | 'EXHAUSTION' | 'CANCEL'
                    price_at_death NUMERIC,     -- Цена в момент смерти
                    price_move_1h_after NUMERIC -- % изменения цены через 1ч
                );
                
                CREATE INDEX IF NOT EXISTS idx_lifecycle_symbol_time
                ON iceberg_lifecycle (symbol, event_time DESC);
            """)
            
            # 6. ТАБЛИЦА СНИМКОВ МЕТРИК (Feature Snapshots)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS iceberg_feature_snapshot (
                    id SERIAL PRIMARY KEY,
                    lifecycle_event_id UUID NOT NULL,
                    snapshot_time TIMESTAMPTZ NOT NULL,
                    
                    -- Orderbook metrics
                    obi_value DOUBLE PRECISION,
                    ofi_value DOUBLE PRECISION,
                    spread_bps DOUBLE PRECISION,
                    depth_ratio DOUBLE PRECISION,
                    
                    -- Flow metrics (CVD)
                    whale_cvd DOUBLE PRECISION,
                    fish_cvd DOUBLE PRECISION,
                    dolphin_cvd DOUBLE PRECISION,
                    whale_cvd_delta_5m DOUBLE PRECISION,
                    total_cvd DOUBLE PRECISION,
                    
                    -- Derivatives metrics
                    futures_basis_apr DOUBLE PRECISION,
                    basis_state TEXT,
                    options_skew DOUBLE PRECISION,
                    skew_state TEXT,
                    total_gex DOUBLE PRECISION,
                    dist_to_gamma_wall DOUBLE PRECISION,
                    gamma_wall_type TEXT,
                    
                    -- Price metrics
                    current_price DOUBLE PRECISION,
                    twap_5m DOUBLE PRECISION,
                    price_vs_twap_pct DOUBLE PRECISION,
                    volatility_1h DOUBLE PRECISION,
                    
                    -- Spoofing metrics
                    spoofing_score DOUBLE PRECISION,
                    cancel_ratio_5m DOUBLE PRECISION,
                    
                    -- Market regime
                    trend_regime TEXT,
                    volatility_regime TEXT,
                    
                    FOREIGN KEY (lifecycle_event_id) REFERENCES iceberg_lifecycle(id)
                );
                
                CREATE INDEX IF NOT EXISTS idx_feature_lifecycle
                ON iceberg_feature_snapshot (lifecycle_event_id);
            """)
            
            # === SAFE CHANGE: ADD SWING COLUMNS ===
            # WHY: Добавляем колонки для Grim Reaper и Smart Money (если их нет)
            await conn.execute("""
                ALTER TABLE iceberg_lifecycle 
                ADD COLUMN IF NOT EXISTS intention_type TEXT,       -- 'SCALPER' | 'INTRADAY' | 'POSITIONAL'
                ADD COLUMN IF NOT EXISTS iir_value NUMERIC,         -- Iceberg Impact Ratio
                
                ADD COLUMN IF NOT EXISTS volatility_at_entry NUMERIC, -- ATR в момент входа
                ADD COLUMN IF NOT EXISTS vpin_at_entry NUMERIC,       -- VPIN в момент входа
                ADD COLUMN IF NOT EXISTS t_settled TIMESTAMPTZ,       -- Время старта после остывания
                
                ADD COLUMN IF NOT EXISTS y_intraday_result INTEGER,   -- 4H-24H Target
                ADD COLUMN IF NOT EXISTS y_swing_result INTEGER,      -- 1D-3D Target
                ADD COLUMN IF NOT EXISTS y_strategic_result INTEGER,  -- 3D-7D Target (MAIN)
                
                ADD COLUMN IF NOT EXISTS y_mfe_mae_ratio NUMERIC,     -- Quality metric
                ADD COLUMN IF NOT EXISTS y_sharpe_ratio NUMERIC;      -- Sharpe metric
            """)
            
            # === SAFE CHANGE: ADD FEATURE COLUMNS (SMART MONEY CONTEXT) ===
            # WHY: Добавляем колонки контекста в таблицу признаков
            await conn.execute("""
                ALTER TABLE iceberg_feature_snapshot 
                ADD COLUMN IF NOT EXISTS whale_cvd_trend_1w DOUBLE PRECISION,
                ADD COLUMN IF NOT EXISTS whale_cvd_trend_1m DOUBLE PRECISION,
                ADD COLUMN IF NOT EXISTS whale_cvd_trend_3m DOUBLE PRECISION,  -- КВАРТАЛ
                ADD COLUMN IF NOT EXISTS whale_cvd_trend_6m DOUBLE PRECISION,
                
                ADD COLUMN IF NOT EXISTS vpin_score DOUBLE PRECISION,
                ADD COLUMN IF NOT EXISTS vpin_level TEXT,
                
                ADD COLUMN IF NOT EXISTS is_htf_divergence INTEGER,
                ADD COLUMN IF NOT EXISTS basis_regime_weekly TEXT;
            """)
            
            print("🐘 PostgreSQL connected. All tables & Swing columns ready.")

    async def run_migrations(self):
        """
        WHY: Применяет SQL миграции из папки migrations/
        
        Логика:
        1. Создает таблицу _migrations для отслеживания примененных миграций
        2. Сканирует папку migrations/
        3. Применяет только новые миграции (по имени файла)
        
        ВАЖНО: Вызывать ПОСЛЕ connect(), но ДО начала работы с БД
        """
        if not self.pool:
            raise RuntimeError("Pool not connected. Call connect() first.")
        
        migrations_dir = os.path.join(os.path.dirname(__file__), 'migrations')
        
        if not os.path.exists(migrations_dir):
            print("⚠️ No migrations/ directory found. Skipping.")
            return
        
        async with self.pool.acquire() as conn:
            # 1. Создаем таблицу для отслеживания миграций
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS _migrations (
                    filename TEXT PRIMARY KEY,
                    applied_at TIMESTAMPTZ DEFAULT NOW()
                );
            """)
            
            # 2. Получаем список уже примененных миграций
            applied = await conn.fetch("SELECT filename FROM _migrations")
            applied_set = {row['filename'] for row in applied}
            
            # 3. Получаем список файлов миграций
            migration_files = sorted([
                f for f in os.listdir(migrations_dir) 
                if f.endswith('.sql')
            ])
            
            # 4. Применяем только новые миграции
            for filename in migration_files:
                if filename in applied_set:
                    print(f"✅ Migration {filename} already applied. Skipping.")
                    continue
                
                filepath = os.path.join(migrations_dir, filename)
                
                try:
                    # Читаем SQL файл
                    with open(filepath, 'r', encoding='utf-8') as f:
                        sql = f.read()
                    
                    # Выполняем миграцию (в транзакции)
                    async with conn.transaction():
                        await conn.execute(sql)
                        
                        # Записываем в _migrations
                        await conn.execute(
                            "INSERT INTO _migrations (filename) VALUES ($1)",
                            filename
                        )
                    
                    print(f"🚀 Migration {filename} applied successfully.")
                    
                except Exception as e:
                    print(f"❌ Migration {filename} FAILED: {e}")
                    print(f"   Rolling back and stopping migration process.")
                    raise  # Останавливаем если миграция сломалась
            
            print("✨ All migrations completed.")

    async def close(self):
        if self.pool:
            await self.pool.close()

    # --- МЕТОДЫ СОХРАНЕНИЯ (WRITERS) ---

    async def save_level(self, level, symbol: str):
        """Сохраняет уровень (Iceberg Registry)"""
        if not self.pool: return

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
                    level.price, symbol, level.is_ask, 
                    level.total_hidden_volume, level.creation_time,
                    level.last_update_time, level.status.value, 
                    level.is_gamma_wall, level.confidence_score,
                    level.spoofing_probability, level.refill_count
                )
                
                # Сохраняем контекст отмены, если есть
                if level.cancellation_context is not None:
                    await self._save_cancellation_context(conn, level)
        except Exception as e:
            print(f"❌ DB Error (save_level): {e}")

    async def _save_cancellation_context(self, conn, level):
        """Сохраняет данные для анти-спуфинга"""
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
                level.price, ctx.mid_price_at_cancel,
                ctx.distance_from_level_pct, ctx.price_velocity_5s,
                ctx.moving_towards_level, ctx.volume_executed_pct
            )
        except Exception as e:
            print(f"⚠️ Failed to save cancellation context: {e}")

    async def log_training_event(self, data: dict):
        """Сохраняет сырые события для ML"""
        if not self.pool: return
        
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
                    data.get('is_near_gamma_wall', False),
                    data.get('gamma_wall_type', None)
                )
        except Exception as e:
            print(f"❌ ML Logging Error: {e}")

    async def log_full_metric(self, data: dict):
        """
        === GEMINI FIX: Обновлено под Migration 005 (Flow/Wall Semantics) ===
        
        WHY: Колонки переименованы:
        - ofi/obi → book_ofi/book_obi (ордербук метрики)
        - whale_cvd_delta → flow_whale_cvd_delta (агрессоры)
        - minnow_cvd_delta → flow_minnow_cvd_delta
        - Добавлены: flow_dolphin_cvd_delta, wall_whale_vol, wall_dolphin_vol
        """
        if not self.pool: return
        
        query = """
            INSERT INTO market_metrics_full (
                time, symbol, price, spread_bps, 
                book_ofi, book_obi,
                flow_whale_cvd_delta,
                flow_dolphin_cvd_delta,
                flow_minnow_cvd_delta,
                wall_whale_vol,
                wall_dolphin_vol,
                basis_apr, options_skew, oi_delta
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
        """
        try:
            async with self.pool.acquire() as conn:
                await conn.execute(query, 
                    data['timestamp'], 
                    data['symbol'], 
                    data['price'],
                    data.get('spread_bps', 0),
                    data.get('book_ofi', 0),  # ✅ NEW NAME
                    data.get('book_obi', 0),  # ✅ NEW NAME
                    data.get('flow_whale_cvd_delta', 0),  # ✅ NEW NAME
                    data.get('flow_dolphin_cvd_delta', 0),  # ✅ NEW COLUMN
                    data.get('flow_minnow_cvd_delta', 0),  # ✅ NEW NAME
                    data.get('wall_whale_vol', 0),  # ✅ NEW COLUMN
                    data.get('wall_dolphin_vol', 0),  # ✅ NEW COLUMN
                    data.get('basis'), 
                    data.get('skew'), 
                    data.get('oi_delta')
                )
        except Exception as e:
            print(f"❌ Full Metric Logging Error: {e}")

    # --- МЕТОДЫ ЧТЕНИЯ (READERS) ---

    async def load_active_levels(self, symbol: str) -> Dict[Decimal, any]:
        """Загружает активные уровни при старте"""
        if not self.pool: return {}
        loaded_data = {}
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT * FROM iceberg_levels 
                    WHERE symbol = $1 AND status = 'ACTIVE'
                """, symbol)
                for r in rows:
                    price = r['price']
                    loaded_data[price] = dict(r)
            print(f"🐘 Loaded {len(loaded_data)} levels.")
            return loaded_data
        except Exception as e:
            print(f"❌ DB Load Error: {e}")
            return {}

    async def get_aggregated_smart_candles(
        self, 
        symbol: str, 
        start_time: datetime, 
        end_time: datetime, 
        timeframe_minutes: int = 60
    ) -> List[SmartCandle]:
        """
        ДЛЯ АГЕНТА: Агрегирует историю в SmartCandles.
        Используется для RAG (Retrieval Augmented Generation).
        """
        if not self.pool: return []

        query = f"""
            SELECT
                to_timestamp(floor((extract('epoch' from time) / {timeframe_minutes * 60})) * {timeframe_minutes * 60}) AT TIME ZONE 'UTC' as candle_time,
                (array_agg(price ORDER BY time ASC))[1] as open,
                MAX(price) as high,
                MIN(price) as low,
                (array_agg(price ORDER BY time DESC))[1] as close,
                COUNT(*) as volume_proxy,
                
                SUM(flow_whale_cvd_delta) as whale_cvd,
                SUM(flow_minnow_cvd_delta) as minnow_cvd,
                SUM(book_ofi) as ofi,
                AVG(book_obi) as weighted_obi,
                AVG(basis_apr) as avg_basis_apr,
                AVG(options_skew) as options_skew,
                SUM(oi_delta) as oi_delta

            FROM market_metrics_full
            WHERE symbol = $1 
              AND time >= $2 
              AND time <= $3
            GROUP BY 1
            ORDER BY 1 ASC;
        """

        smart_candles = []
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(query, symbol, start_time, end_time)
                for r in rows:
                    candle = SmartCandle(
                        symbol=symbol,
                        timeframe=f"{timeframe_minutes}m",
                        timestamp=r['candle_time'],
                        open=r['open'] or 0, 
                        high=r['high'] or 0, 
                        low=r['low'] or 0, 
                        close=r['close'] or 0,
                        volume=r['volume_proxy'], 
                        
                        whale_cvd=float(r['whale_cvd'] or 0),
                        minnow_cvd=float(r['minnow_cvd'] or 0),
                        total_trades=int(r['volume_proxy']),
                        
                        avg_basis_apr=float(r['avg_basis_apr']) if r['avg_basis_apr'] else None,
                        options_skew=float(r['options_skew']) if r['options_skew'] else None,
                        oi_delta=float(r['oi_delta']) if r['oi_delta'] else None,
                        
                        ofi=float(r['ofi'] or 0),
                        weighted_obi=float(r['weighted_obi'] or 0)
                    )
                    smart_candles.append(candle)
        except Exception as e:
            print(f"❌ Aggregation Error: {e}")
            
        return smart_candles
    
    # ========================================================================
    # LIFECYCLE & FEATURE SNAPSHOT METHODS (для ML)
    # ========================================================================
    
    async def save_lifecycle_event(
        self,
        symbol: str,
        price: Decimal,
        is_ask: bool,
        event_type: str,
        survival_seconds: Optional[int] = None,
        total_volume_absorbed: Optional[Decimal] = None,
        refill_count: Optional[int] = None,
        outcome: Optional[str] = None,
        price_at_death: Optional[Decimal] = None,
        intention_type: Optional[str] = None,  # NEW: 'SCALPER' | 'INTRADAY' | 'POSITIONAL'
        iir_value: Optional[float] = None       # NEW: Iceberg Impact Ratio
    ) -> Optional[str]:
        """
        WHY: Сохраняет событие жизненного цикла айсберга.
        
        Args:
            symbol: Символ (BTCUSDT)
            price: Цена айсберга
            is_ask: True если на стороне продажи
            event_type: 'DETECTED' | 'REFILLED' | 'BREACHED' | 'EXHAUSTED' | 'CANCELLED'
            survival_seconds: Сколько прожил (заполняется при смерти)
            total_volume_absorbed: Сколько объема съел
            refill_count: Количество пополнений
            outcome: 'BREACH' | 'EXHAUSTION' | 'CANCEL' | None
            price_at_death: Цена в момент смерти
            intention_type: 'SCALPER' | 'INTRADAY' | 'POSITIONAL' (Smart Money classification)
            iir_value: Iceberg Impact Ratio (hidden_volume / book_depth)
        
        Returns:
            UUID созданного события или None при ошибке
        """
        if not self.pool:
            return None
        
        query = """
            INSERT INTO iceberg_lifecycle (
                symbol, price, is_ask, event_type, event_time,
                survival_seconds, total_volume_absorbed, refill_count,
                outcome, price_at_death,
                intention_type, iir_value
            ) VALUES ($1, $2, $3, $4, NOW(), $5, $6, $7, $8, $9, $10, $11)
            RETURNING id;
        """
        
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    query,
                    symbol, price, is_ask, event_type,
                    survival_seconds, total_volume_absorbed, refill_count,
                    outcome, price_at_death,
                    intention_type, iir_value  # NEW: Smart Money classification
                )
                return str(row['id']) if row else None
        except Exception as e:
            print(f"❌ Save lifecycle event error: {e}")
            return None
    
    async def save_feature_snapshot(
        self,
        lifecycle_event_id: str,
        snapshot  # FeatureSnapshot object
    ) -> bool:
        """
        WHY: Сохраняет полный снимок метрик для ML.
        
        Args:
            lifecycle_event_id: UUID события из iceberg_lifecycle
            snapshot: FeatureSnapshot object с метриками
        
        Returns:
            True если успешно сохранено
        """
        if not self.pool:
            return False
        
        query = """
            INSERT INTO iceberg_feature_snapshot (
                lifecycle_event_id, snapshot_time,
                -- Orderbook
                obi_value, ofi_value, spread_bps, depth_ratio,
                -- Flow
                whale_cvd, fish_cvd, dolphin_cvd, whale_cvd_delta_5m, total_cvd,
                -- Derivatives
                futures_basis_apr, basis_state, options_skew, skew_state,
                total_gex, dist_to_gamma_wall, gamma_wall_type,
                -- Price
                current_price, twap_5m, price_vs_twap_pct, volatility_1h,
                -- Spoofing
                spoofing_score, cancel_ratio_5m,
                -- Regime
                trend_regime, volatility_regime,
                -- Smart Money Context (Step 2)
                whale_cvd_trend_1w, whale_cvd_trend_1m, whale_cvd_trend_3m, whale_cvd_trend_6m,
                vpin_score, vpin_level,
                is_htf_divergence, basis_regime_weekly
            ) VALUES (
                $1, $2,
                $3, $4, $5, $6,
                $7, $8, $9, $10, $11,
                $12, $13, $14, $15,
                $16, $17, $18,
                $19, $20, $21, $22,
                $23, $24,
                $25, $26,
                $27, $28, $29, $30,
                $31, $32,
                $33, $34
            );
        """
        
        try:
            async with self.pool.acquire() as conn:
                await conn.execute(
                    query,
                    lifecycle_event_id, snapshot.snapshot_time,
                    # Orderbook
                    snapshot.obi_value, snapshot.ofi_value, snapshot.spread_bps, snapshot.depth_ratio,
                    # Flow
                    snapshot.whale_cvd, snapshot.fish_cvd, snapshot.dolphin_cvd,
                    snapshot.whale_cvd_delta_5m, snapshot.total_cvd,
                    # Derivatives
                    snapshot.futures_basis_apr, snapshot.basis_state,
                    snapshot.options_skew, snapshot.skew_state,
                    snapshot.total_gex, snapshot.dist_to_gamma_wall, snapshot.gamma_wall_type,
                    # Price
                    snapshot.current_price, snapshot.twap_5m,
                    snapshot.price_vs_twap_pct, snapshot.volatility_1h,
                    # Spoofing
                    snapshot.spoofing_score, snapshot.cancel_ratio_5m,
                    # Regime
                    snapshot.trend_regime, snapshot.volatility_regime,
                    # Smart Money Context (Step 2)
                    snapshot.whale_cvd_trend_1w, snapshot.whale_cvd_trend_1m,
                    snapshot.whale_cvd_trend_3m, snapshot.whale_cvd_trend_6m,  # КВАРТАЛ
                    snapshot.vpin_score, snapshot.vpin_level,
                    snapshot.is_htf_divergence, snapshot.basis_regime_weekly
                )
                return True
        except Exception as e:
            print(f"❌ Save feature snapshot error: {e}")
            return False
    
    async def update_lifecycle_outcome(
        self,
        lifecycle_id: str,
        outcome: str,
        price_move_1h_after: Optional[float] = None
    ) -> bool:
        """
        WHY: Обновляет исход айсберга (вызывается через 1 час после смерти).
        
        Args:
            lifecycle_id: UUID события
            outcome: 'BREACH' | 'EXHAUSTION' | 'CANCEL'
            price_move_1h_after: Процентное изменение цены через 1ч
        
        Returns:
            True если успешно обновлено
        """
        if not self.pool:
            return False
        
        query = """
            UPDATE iceberg_lifecycle
            SET outcome = $1,
                price_move_1h_after = $2
            WHERE id = $3;
        """
        
        try:
            async with self.pool.acquire() as conn:
                await conn.execute(query, outcome, price_move_1h_after, lifecycle_id)
                return True
        except Exception as e:
            print(f"❌ Update lifecycle outcome error: {e}")
            return False
    
    async def get_aggregated_smart_candles(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 100
    ) -> List[SmartCandle]:
        """
        WHY: Загружает SmartCandles для cold start (ЗАДАЧА 3).
        
        SQL-агрегация market_metrics_full в SmartCandles.
        Используется HistoricalMemory для заполнения истории.
        
        Таблица: market_metrics_full (пишется каждые 5-10 сек)
        Агрегация: date_bin(timeframe) + средние метрики
        
        Args:
            symbol: BTCUSDT, ETHUSDT
            timeframe: '1h', '4h', '1d', '1w'
            limit: Количество свечей
        
        Returns:
            List[SmartCandle] отсортированные по времени
        """
        if not self.pool:
            return []
        # WHY: Преобразование timeframe в interval
        interval_map = {
            '1h': '1 hour',
            '4h': '4 hours',
            '1d': '1 day',
            '1w': '7 days'
        }
        
        interval = interval_map.get(timeframe)
        if not interval:
            print(f"⚠️  Invalid timeframe: {timeframe}")
            return []
        
        # WHY: SQL-запрос с агрегацией (ОБНОВЛЕНО под Migration 005)
        query = f"""
            SELECT 
                date_bin($1::interval, time, '2020-01-01'::timestamptz) AS candle_time,
                $2 AS symbol,
                AVG(price) AS close,  -- WHY: Используем close для цены
                AVG(book_ofi) AS avg_ofi,
                AVG(book_obi) AS avg_obi,
                AVG(spread_bps) AS avg_spread_bps,
                -- WHY: CVD должен быть кумулятивным (пока просто берём last)
                LAST(flow_whale_cvd_delta, time) AS whale_cvd,
                LAST(flow_minnow_cvd_delta, time) AS minnow_cvd
            FROM market_metrics_full
            WHERE symbol = $2
            GROUP BY candle_time
            ORDER BY candle_time DESC
            LIMIT $3;
        """
        
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(query, interval, symbol, limit)
                
                # WHY: Преобразуем в SmartCandle
                candles = []
                for row in reversed(rows):  # Возвращаем в хронологическом порядке
                    candle = SmartCandle(
                        timestamp=row['candle_time'],
                        symbol=row['symbol'],
                        close=Decimal(str(row['close'])),
                        whale_cvd=float(row['whale_cvd']) if row['whale_cvd'] else 0.0,
                        minnow_cvd=float(row['minnow_cvd']) if row['minnow_cvd'] else 0.0,
                        ofi=float(row['avg_ofi']) if row['avg_ofi'] else 0.0,
                        obi=float(row['avg_obi']) if row['avg_obi'] else 0.0
                    )
                    candles.append(candle)
                
                return candles
                
        except Exception as e:
            print(f"❌ get_aggregated_smart_candles error: {e}")
            import traceback
            traceback.print_exc()
            return []
    
    async def get_materialized_candles(
        self,
        symbol: str,
        start_time: datetime,
        end_time: datetime,
        timeframe: str = '1h',
        version: str = '1.0'
    ) -> List[SmartCandle]:
        """
        WHY: O(1) SELECT вместо O(N) агрегации.
        PERFORMANCE: 15 сек → 0.3 сек.
        REPRODUCIBILITY: Frozen features для ML.
        
        В отличие от get_aggregated_smart_candles(), этот метод читает
        МАТЕРИАЛИЗОВАННЫЕ свечи из таблицы smart_candles.
        
        Преимущества:
        1. CONSISTENCY: Раз сохранённые свечи не меняются
        2. SPEED: Прямой SELECT вместо агрегации market_metrics_full
        3. VERSIONING: Можно хранить разные версии формул (v1.0, v2.0)
        4. REPRODUCIBILITY: Backtesting на идентичных данных
        
        Использование:
        - ML training (XGBoost, HMM) - требуют FROZEN features
        - Backtesting - требуют reproducible results
        - HistoricalMemory - для cold start агента
        
        Args:
            symbol: BTCUSDT, ETHUSDT, SOLUSDT
            start_time: Начало диапазона
            end_time: Конец диапазона
            timeframe: '1h', '4h', '1d', '1w'
            version: '1.0' (aggregation formula version)
        
        Returns:
            List[SmartCandle] отсортированные по candle_time
        
        Example:
            >>> repo = PostgresRepository(DB_DSN)
            >>> await repo.connect()
            >>> candles = await repo.get_materialized_candles(
            ...     symbol='BTCUSDT',
            ...     start_time=datetime(2025, 1, 1),
            ...     end_time=datetime(2025, 1, 7),
            ...     timeframe='1h'
            ... )
            >>> len(candles)  # 168 hourly candles (7 days * 24 hours)
        """
        if not self.pool:
            return []
        
        query = """
            SELECT
                symbol, timeframe, candle_time,
                open, high, low, close, volume,
                whale_cvd, minnow_cvd, dolphin_cvd, total_trades,
                avg_basis_apr, min_basis_apr, max_basis_apr,
                options_skew, oi_delta,
                avg_ofi, avg_obi, avg_spread_bps,
                total_gex,
                avg_vpin_score, max_vpin_score,
                wyckoff_pattern, accumulation_confidence
            FROM smart_candles
            WHERE symbol = $1
              AND timeframe = $2
              AND candle_time >= $3
              AND candle_time < $4
              AND aggregation_version = $5
            ORDER BY candle_time ASC;
        """
        
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(
                    query, symbol, timeframe, start_time, end_time, version
                )
                
                # WHY: Конвертируем в SmartCandle objects
                candles = []
                for row in rows:
                    candle = SmartCandle(
                        symbol=row['symbol'],
                        timeframe=row['timeframe'],
                        candle_time=row['candle_time'],
                        
                        # OHLCV
                        open=Decimal(str(row['open'])),
                        high=Decimal(str(row['high'])),
                        low=Decimal(str(row['low'])),
                        close=Decimal(str(row['close'])),
                        volume=Decimal(str(row['volume'])),
                        
                        # CVD
                        whale_cvd=float(row['whale_cvd'] or 0),
                        minnow_cvd=float(row['minnow_cvd'] or 0),
                        total_trades=int(row['total_trades'] or 0),
                        
                        # Derivatives
                        avg_basis_apr=float(row['avg_basis_apr']) if row['avg_basis_apr'] else None,
                        options_skew=float(row['options_skew']) if row['options_skew'] else None,
                        oi_delta=float(row['oi_delta']) if row['oi_delta'] else None,
                        
                        # Microstructure
                        ofi=float(row['avg_ofi'] or 0),
                        weighted_obi=float(row['avg_obi'] or 0),
                        
                        # Gamma
                        total_gex=float(row['total_gex']) if row['total_gex'] else None,
                        
                        # Wyckoff
                        wyckoff_pattern=row['wyckoff_pattern'],
                        accumulation_confidence=float(row['accumulation_confidence']) if row['accumulation_confidence'] else None
                    )
                    candles.append(candle)
                
                return candles
                
        except Exception as e:
            print(f"❌ get_materialized_candles error: {e}")
            import traceback
            traceback.print_exc()
            return []
    
    # ========================================================================
    # GRIM REAPER: Retrospective Labeling (Step 3)
    # ========================================================================
    
    async def run_grim_reaper_labeling(self, batch_size: int = 100):
        """
        WHY: Ретроспективная разметка данных (Dynamic Labeling).
        Заполняет y_strategic_result, глядя в будущее.
        
        Процесс:
        1. Находит неразмеченные айсберги старше 7 дней
        2. Для каждого вычисляет исход (Win/Loss/Neutral)
        3. Обновляет y_strategic_result в БД
        
        Args:
            batch_size: Количество айсбергов для обработки за раз
        
        Returns:
            Количество размеченных записей
        """
        if not self.pool:
            print("⚠️ Grim Reaper: Pool not connected")
            return 0
        
        print("💀 Grim Reaper: Starting labeling process...")
        
        # 1. Находим неразмеченные айсберги старше 7 дней (для Strategic Swing)
        # WHY: ОБЯЗАТЕЛЬНО подтягиваем CVD данные для Smart Settling!
        fetch_query = """
            SELECT 
                l.id, l.symbol, l.price, l.is_ask, l.event_time, 
                l.volatility_at_entry, l.vpin_at_entry, l.t_settled,
                
                -- WHY: Подтягиваем CVD дельты из feature_snapshot
                -- Используем COALESCE если snapshot отсутствует
                COALESCE(f.whale_cvd_delta_5m, 0) as whale_cvd_delta,
                COALESCE((
                    SELECT SUM(flow_minnow_cvd_delta) 
                    FROM market_metrics_full 
                    WHERE symbol = l.symbol 
                      AND time BETWEEN l.event_time - INTERVAL '5 minutes' AND l.event_time
                ), 0) as minnow_cvd_delta,
                
                -- === GEMINI FIX #1: Algo Detection (Stealth Whale Protection) ===
                -- WHY: Подтягиваем spoofing_score как proxy для algo detection
                -- Высокий spoofing_score = алгоритмические продажи (TWAP/VWAP/Iceberg)
                COALESCE(f.spoofing_score, 0) as algo_score
                
            FROM iceberg_lifecycle l
            LEFT JOIN iceberg_feature_snapshot f 
              ON f.lifecycle_event_id = l.id
            WHERE l.y_strategic_result IS NULL
              AND l.event_time < NOW() - INTERVAL '7 days'
            LIMIT $1;
        """
        
        labeled_count = 0
        
        try:
            async with self.pool.acquire() as conn:
                candidates = await conn.fetch(fetch_query, batch_size)
                print(f"💀 Grim Reaper: Found {len(candidates)} candidates for labeling.")
                
                for row in candidates:
                    # Рассчитываем исход
                    outcome = await self._calculate_outcome(conn, row)
                    
                    # Записываем результат
                    await conn.execute("""
                        UPDATE iceberg_lifecycle
                        SET y_strategic_result = $1
                        WHERE id = $2
                    """, outcome, row['id'])
                    
                    labeled_count += 1
                
                print(f"💀 Grim Reaper: Labeled {labeled_count} icebergs.")
                return labeled_count
                
        except Exception as e:
            print(f"❌ Grim Reaper error: {e}")
            import traceback
            traceback.print_exc()
            return labeled_count
    
    # =========================================================================
    # ML DATASET PREPARATION (with Data Leakage Protection)
    # =========================================================================
    async def prepare_ml_dataset_safe(
        self,
        start_date: datetime,
        end_date: datetime,
        timeframe: str = '1h',
        target_col: str = 'next_hour_close',
        symbol: str = 'BTCUSDT'
    ) -> pd.DataFrame:
        """
        🛡️ БЕЗОПАСНАЯ ПОДГОТОВКА ДАТАСЕТА ДЛЯ ML
        
        WHY: Объединяет SmartCandles + IcebergFeatures с автоматической валидацией.
        Гарантирует, что модель НЕ ВИДИТ данных из будущего (Data Leakage Protection).
        
        Использует:
        1. pd.merge_asof(..., direction='backward') - берет только прошлое
        2. DataLeakageGuard - 5 проверок на утечки
        3. Валидация timeframe и aggregation_version
        
        Args:
            start_date: Начало периода обучения
            end_date: Конец периода обучения
            timeframe: Таймфрейм свечей ('1h', '4h', '1d', '1w', '1m')
            target_col: Целевая переменная для предсказания
            symbol: Торговая пара (по умолчанию BTCUSDT)
        
        Returns:
            pd.DataFrame: Проверенный датасет, готовый для model.fit()
            
        Raises:
            ValueError: Если найдена утечка данных (timestamp/correlation/shift)
        
        Example:
            repo = PostgresRepository(dsn="postgresql://...")
            await repo.connect()
            
            # Загружаем данные с защитой
            df = await repo.prepare_ml_dataset_safe(
                start_date=datetime(2024, 1, 1),
                end_date=datetime(2024, 12, 1),
                timeframe='1h',
                target_col='next_hour_close'
            )
            
            # Если код дошел сюда - данные чисты!
            from xgboost import XGBRegressor
            X = df.drop(columns=['candle_time', 'next_hour_close', 'snapshot_time'])
            y = df['next_hour_close']
            model = XGBRegressor()
            model.fit(X, y)  # ✅ Никаких утечек!
        """
        async with self.pool.acquire() as conn:
            # 1. ЗАГРУЗКА SMARTCANDLES (таргет)
            print(f"🔍 Loading SmartCandles ({timeframe}) from {start_date} to {end_date}...")
            candles_raw = await conn.fetch("""
                SELECT 
                    candle_time,
                    symbol,
                    timeframe,
                    aggregation_version,
                    open,
                    high,
                    low,
                    close,
                    volume,
                    
                    -- Микроструктура
                    avg_ofi,
                    avg_obi,
                    
                    -- CVD сегменты
                    whale_cvd_change,
                    dolphin_cvd_change,
                    minnow_cvd_change,
                    
                    -- Деривативы
                    avg_basis_apr,
                    avg_options_skew,
                    
                    -- Целевая переменная (следующая свеча)
                    LEAD(close) OVER (ORDER BY candle_time) as next_hour_close
                    
                FROM smart_candles
                WHERE candle_time >= $1
                  AND candle_time <= $2
                  AND timeframe = $3
                  AND symbol = $4
                  AND aggregation_version = '1.0'
                ORDER BY candle_time
            """, start_date, end_date, timeframe, symbol)
            
            if not candles_raw:
                raise ValueError(f"No SmartCandles found for {symbol} {timeframe} in date range")
            
            candles = pd.DataFrame(candles_raw)
            print(f"   ✅ Loaded {len(candles)} candles")
            
            # 2. ЗАГРУЗКА ICEBERG FEATURES (предикторы)
            print(f"🔍 Loading IcebergFeatures from {start_date} to {end_date}...")
            features_raw = await conn.fetch("""
                SELECT 
                    snapshot_time,
                    lifecycle_event_id,
                    
                    -- Orderbook
                    obi_value,
                    ofi_value,
                    spread_bps,
                    depth_ratio,
                    
                    -- CVD Flow
                    whale_cvd,
                    dolphin_cvd,
                    whale_cvd_delta_5m,
                    total_cvd,
                    
                    -- Derivatives
                    futures_basis_apr,
                    basis_state,
                    options_skew,
                    skew_state,
                    total_gex,
                    dist_to_gamma_wall,
                    gamma_wall_type,
                    
                    -- Price
                    current_price,
                    twap_5m,
                    price_vs_twap_pct,
                    volatility_1h,
                    
                    -- Anti-Spoofing
                    spoofing_score,
                    cancel_ratio_5m,
                    
                    -- Regime
                    trend_regime,
                    volatility_regime,
                    
                    -- Smart Money Context (Deep Memory)
                    whale_cvd_trend_1w,
                    whale_cvd_trend_1m,
                    whale_cvd_trend_3m,
                    whale_cvd_trend_6m
                    
                FROM iceberg_feature_snapshot
                WHERE snapshot_time >= $1
                  AND snapshot_time <= $2
                ORDER BY snapshot_time
            """, start_date, end_date)
            
            if not features_raw:
                print("   ⚠️  No IcebergFeatures found (may train on SmartCandles only)")
                features = pd.DataFrame()
            else:
                features = pd.DataFrame(features_raw)
                print(f"   ✅ Loaded {len(features)} feature snapshots")
            
            # 3. БЕЗОПАСНЫЙ MERGE (backward only)
            print(f"🔗 Merging candles + features (safe merge_asof)...")
            
            if features.empty:
                # Если нет фичей - используем только свечи
                df = candles.copy()
                print("   ⚠️  Training on SmartCandles only (no iceberg features)")
            else:
                # Используем safe_merge_candles_features (backward merge)
                df = safe_merge_candles_features(
                    candles,
                    features,
                    candle_time_col='candle_time',
                    feature_time_col='snapshot_time'
                )
            
            # 4. 🛡️ ВАЛИДАЦИЯ ДАННЫХ (Data Leakage Guard)
            print(f"🛡️ Running Data Leakage Guard...")
            
            # Удаляем последнюю строку (у нее нет next_hour_close из-за LEAD)
            df = df[df[target_col].notna()].reset_index(drop=True)
            
            if df.empty:
                raise ValueError("Dataset is empty after removing NaN targets")
            
            # Запускаем полную проверку
            guard = DataLeakageGuard(df, time_col='candle_time', target_col=target_col)
            
            if not features.empty:
                # Полная проверка (если есть фичи)
                guard.check_all(
                    feature_time_col='snapshot_time',
                    timeframe_col='timeframe',
                    version_col='aggregation_version'
                )
            else:
                # Упрощенная проверка (только свечи)
                guard.check_timeframe_consistency('timeframe')
                guard.check_aggregation_version('aggregation_version')
            
            print(f"✅ Dataset validated: {len(df)} rows, {len(df.columns)} columns")
            print(f"✅ Safe for ML training (no data leakage detected)")
            
            return df
    
    async def _calculate_outcome(self, conn, iceberg) -> int:
        """
        WHY: Считает исход сделки на горизонте 7 дней.
        
        === UPDATE: Settling Time Support (Gemini Recommendation) ===
        Теперь учитывает VPIN и откладывает начало анализа если рынок "горячий".
        
        Логика барьеров:
        - BUY Iceberg (is_ask=False): Win если цена растёт выше take_profit
        - SELL Iceberg (is_ask=True): Win если цена падает ниже take_profit
        
        Барьеры:
        - stop_loss: entry ± 3 * ATR (широкие стопы для свинга)
        - take_profit: entry ± 6 * ATR (R:R = 1:2)
        
        Settling Time Logic:
        - Если VPIN > 0.7 (высокая волатильность) → пропускаем первые 15-30 мин
        - Это позволяет "остыть" рынку прежде чем оценивать результат
        - Исключает краткосрочный шум сразу после детекции
        
        Args:
            conn: Database connection
            iceberg: Row с данными айсберга
        
        Returns:
            1 = Win, 0 = Neutral, -1 = Loss
        """
        # Извлекаем ATR (volatility_at_entry)
        atr = float(iceberg['volatility_at_entry'] or 0)
        
        if atr == 0:
            return 0  # WHY: Не можем посчитать без ATR
        
        price = float(iceberg['price'])
        is_ask = iceberg['is_ask']
        
        # WHY: Wide Stops для Swing Trading (3x ATR)
        stop_dist = 3.0 * atr
        take_dist = 6.0 * atr  # Risk/Reward 1:2
        
        # WHY: Барьеры зависят от направления
        if is_ask:  # SELL Iceberg (сопротивление)
            upper_barrier = price + stop_dist   # Stop Loss выше
            lower_barrier = price - take_dist   # Take Profit ниже
        else:  # BUY Iceberg (поддержка)
            upper_barrier = price + take_dist   # Take Profit выше
            lower_barrier = price - stop_dist   # Stop Loss ниже
        
        # === GEMINI FIX #2: Falling Knife Veto (Cascade Protection) ===
        # WHY: Проверяем экстремальную волатильность (каскад ликвидаций)
        # Если ATR > 2% от цены -> VETO на вход независимо от паники
        # Это признак "falling knife" - цена пролетит через айсберг
        atr_pct = (atr / price) * 100  # ATR в % от цены
        event_time = iceberg['event_time']
        
        # HARD VETO: Если волатильность >2% -> запрет входа
        if atr_pct > 2.0:
            # WHY: Экстремальная волатильность = каскад ликвидаций
            # Цена может пролететь на 5-10% за минуту
            # Айсберг будет снесен принудительными market orders
            # ЖДЕМ затухания импульса (минимум 30 мин)
            start_time = event_time + timedelta(minutes=30)
            # Optional: логирование
            # print(f"🔥 FALLING KNIFE! ATR={atr_pct:.2f}%. Forced 30min settling.")
            
        # Если волатильность нормальная -> продолжаем Smart Settling
        elif iceberg.get('t_settled'):
            # WHY: Если t_settled уже рассчитан при сохранении - используем его
            start_time = iceberg['t_settled']
        
        # === GEMINI FIX: Smart Settling Time (CRYPTO-AWARE) ===
        # WHY: Различаем Panic Absorption (вход сразу) vs Whale Attack (ждем)
        else:
            vpin = float(iceberg.get('vpin_at_entry') or 0)
            
            if vpin > 0.7:
                # WHY: High VPIN → нужно проверить ИСТОЧНИК волатильности
                
                # 1. Проверяем кто создал VPIN (киты или рыбы)
                minnow_cvd_delta = float(iceberg.get('minnow_cvd_delta') or 0)
                whale_cvd_delta = float(iceberg.get('whale_cvd_delta') or 0)
                
                # 2. Panic Absorption: Minnows паникуют (CVD падает), Whales покупают
                is_panic_dump = (minnow_cvd_delta < whale_cvd_delta)
                
                # === GEMINI FIX #1: Algo Detection (Stealth Whale Protection) ===
                # WHY: Проверяем алгоритмические продажи
                # Высокий algo_score означает TWAP/VWAP/Iceberg алгоритм
                # Это НЕ паника - это стелс-кит, маскирующийся под рыбу!
                algo_score = float(iceberg.get('algo_score', 0))
                is_algo_selling = (algo_score > 0.7)
                
                # CRITICAL: ТОЛЬКО хаотичная паника = Panic Absorption
                # Если продажи алгоритмические -> ЭТО КИТ, ждем!
                if is_panic_dump and not is_algo_selling:
                    # СЦЕНАРИЙ А: TRUE Panic Absorption (V-shape recovery)
                    # WHY: НЕ ЖДЕМ! Входим сразу чтобы поймать отскок
                    # Это самые прибыльные сделки в крипте
                    start_time = event_time
                    # Optional: можно логировать для анализа
                    # print(f"⚡ TRUE Panic Absorption (VPIN={vpin:.2f}, algo={algo_score:.2f}). No delay.")
                else:
                    # СЦЕНАРИЙ Б: Whale Attack / Algo Masking / Неопределенность
                    # WHY: ЖДЕМ остывания рынка (15-30 мин)
                    settling_minutes = 15 + int((vpin - 0.7) * 50)
                    start_time = event_time + timedelta(minutes=settling_minutes)
                    # Optional: логирование
                    # if is_algo_selling:
                    #     print(f"🤖 Algo Detected ({algo_score:.2f}). Settling {settling_minutes}m.")
                    # else:
                    #     print(f"⚠️ High VPIN risk ({vpin:.2f}). Settling {settling_minutes}m.")
            else:
                # WHY: Low VPIN → спокойный рынок → начинаем сразу
                start_time = event_time
        
        # Загружаем свечи ПОСЛЕ settling period (следующие 7 дней)
        try:
            candles = await conn.fetch("""
                SELECT price as close, time 
                FROM market_metrics_full
                WHERE symbol = $1 
                  AND time > $2 
                  AND time < $3
                ORDER BY time ASC
            """, iceberg['symbol'], start_time, start_time + timedelta(days=7))
            
            # Проверяем каждую свечу на пробитие барьеров
            # WHY: Используем только свечи ПОСЛЕ start_time (уже отфильтрованы SQL)
            for c in candles:
                p = float(c['close'])
                
                if is_ask:  # SELL Iceberg
                    if p >= upper_barrier:
                        return -1  # Loss (Stop Hit)
                    if p <= lower_barrier:
                        return 1   # Win (Take Hit)
                else:  # BUY Iceberg
                    if p <= lower_barrier:
                        return -1  # Loss (Stop Hit)
                    if p >= upper_barrier:
                        return 1   # Win (Take Hit)
            
            # WHY: Time Expiration - ни один барьер не пробит за 7 дней
            return 0
            
        except Exception as e:
            print(f"⚠️ _calculate_outcome error for iceberg {iceberg['id']}: {e}")
            return 0  # WHY: При ошибке считаем Neutral