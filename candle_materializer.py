"""
WHY: Materialize SmartCandles for ML reproducibility.

Проблема: SQL агрегация по запросу → feature drift при изменении формул.
Решение: FROZEN snapshots с версионированием.

Workflow:
1. Каждый час запускается candle_materializer
2. Агрегирует market_metrics_full за последний час
3. Сохраняет в smart_candles с aggregation_version='1.0'
4. При изменении формул → создаём version='2.0'

Author: Basilisca
Created: 2025-12-23
"""

import asyncio
import asyncpg
from datetime import datetime, timedelta
from typing import List, Optional
from decimal import Decimal

from config import get_config
from domain_smartcandle import SmartCandle

# WHY: Database connection string (из main.py)
DB_DSN = "postgresql://postgres:Jayaasiri2185@localhost:5432/trading_db"

class CandleMaterializer:
    """
    WHY: Материализует SmartCandles из тиковых данных.
    
    Гарантирует:
    - IMMUTABILITY: Раз сохранённые свечи не меняются
    - VERSIONING: Разные версии формул хранятся отдельно
    - PERFORMANCE: Агрегация выполняется 1 раз (не каждый запрос)
    """
    
    def __init__(self, db_dsn: str, aggregation_version: str = '1.0'):
        self.db_dsn = db_dsn
        self.version = aggregation_version
        self.pool: Optional[asyncpg.Pool] = None
    
    async def connect(self):
        """Создаёт connection pool."""
        self.pool = await asyncpg.create_pool(self.db_dsn, min_size=2, max_size=10)
    
    async def close(self):
        """Закрывает connection pool."""
        if self.pool:
            await self.pool.close()
    
    async def materialize_candles(
        self,
        symbol: str,
        start_time: datetime,
        end_time: datetime,
        timeframe_minutes: int = 60,
        force_recompute: bool = False
    ) -> int:
        """
        WHY: Материализует свечи за указанный период.
        
        Args:
            symbol: BTCUSDT, ETHUSDT, etc.
            start_time: Начало периода
            end_time: Конец периода
            timeframe_minutes: 60 (1H), 240 (4H), 1440 (1D), etc.
            force_recompute: Если True - перезаписывает существующие свечи
        
        Returns:
            int: Количество материализованных свечей
        
        Example:
            >>> # Материализовать последние 7 дней (1H свечи)
            >>> materializer = CandleMaterializer(DB_DSN)
            >>> await materializer.connect()
            >>> count = await materializer.materialize_candles(
            ...     symbol='BTCUSDT',
            ...     start_time=datetime.now() - timedelta(days=7),
            ...     end_time=datetime.now(),
            ...     timeframe_minutes=60
            ... )
            >>> print(f"Materialized {count} candles")
        """
        if not self.pool:
            raise RuntimeError("Call connect() first")
        
        # 1. Определяем timeframe код
        # WHY: Добавлены 5m/15m для Sniper Entry ML (high-precision timing)
        timeframe_map = {
            5: '5m',      # Sniper Entry (sub-minute precision)
            15: '15m',    # Micro-swing context
            60: '1h',
            240: '4h',
            1440: '1d',
            10080: '1w',
            43200: '1m'
        }
        timeframe = timeframe_map.get(timeframe_minutes, f'{timeframe_minutes}m')
        
        # 2. Проверяем существующие свечи
        if not force_recompute:
            existing_count = await self._count_existing_candles(
                symbol, timeframe, start_time, end_time
            )
            if existing_count > 0:
                print(f"⚠️ {existing_count} candles already exist. Use force_recompute=True to overwrite.")
                return 0
        
        # 3. SQL агрегация (ТА ЖЕ ЛОГИКА что в repository.py)
        # WHY: PostgreSQL date_bin() требует timedelta, не строку
        interval = timedelta(minutes=timeframe_minutes)
        
        # WHY: Отдельный запрос для absorbed volumes из iceberg_lifecycle
        # Нельзя JOIN с market_metrics_full - разная гранулярность
        absorbed_query = """
            SELECT
                date_bin($1::interval, event_time, '2020-01-01') as candle_time,
                
                -- WHY: EXHAUSTED айсберги = полностью исполненные за свечу
                -- Используем total_volume_absorbed из lifecycle
                SUM(CASE 
                    WHEN total_volume_absorbed * price > 100000 THEN total_volume_absorbed 
                    ELSE 0 
                END) as absorbed_whale_vol,
                SUM(CASE 
                    WHEN total_volume_absorbed * price BETWEEN 1000 AND 100000 THEN total_volume_absorbed 
                    ELSE 0 
                END) as absorbed_dolphin_vol
                
            FROM iceberg_lifecycle
            WHERE symbol = $2 
              AND event_time >= $3 
              AND event_time < $4
              AND outcome = 'EXHAUSTION'  -- Только истощённые айсберги
            GROUP BY 1
            ORDER BY 1 ASC
        """
        
        query = """
            SELECT
                date_bin($1::interval, time, '2020-01-01') as candle_time,
                
                -- OHLCV
                (array_agg(price ORDER BY time ASC))[1] as open,
                MAX(price) as high,
                MIN(price) as low,
                (array_agg(price ORDER BY time DESC))[1] as close,
                SUM(volume) as volume,
                
                -- AGGRESSORS (FLOW)
                SUM(flow_whale_cvd_delta) as flow_whale_cvd,
                SUM(flow_dolphin_cvd_delta) as flow_dolphin_cvd,
                SUM(flow_minnow_cvd_delta) as flow_minnow_cvd,
                COUNT(*) as total_trades,
                
                -- Derivatives
                AVG(basis_apr) as avg_basis_apr,
                MIN(basis_apr) as min_basis_apr,
                MAX(basis_apr) as max_basis_apr,
                AVG(options_skew) as options_skew,
                SUM(oi_delta) as oi_delta,
                
                -- WALLS (Iceberg volumes)
                SUM(wall_whale_vol) as wall_whale_vol,
                SUM(wall_dolphin_vol) as wall_dolphin_vol,
                
                -- ORDERBOOK
                AVG(book_ofi) as book_ofi,
                AVG(book_obi) as book_obi,
                AVG(spread_bps) as avg_spread_bps,
                
                -- Gamma
                AVG(total_gex) as total_gex,
                
                -- VPIN
                AVG(vpin_score) as avg_vpin_score,
                MAX(vpin_score) as max_vpin_score
                
            FROM market_metrics_full
            WHERE symbol = $2 AND time >= $3 AND time < $4
            GROUP BY 1
            ORDER BY 1 ASC
        """
        
        async with self.pool.acquire() as conn:
            # 1. Загружаем основные метрики
            rows = await conn.fetch(query, interval, symbol, start_time, end_time)
            
            # 2. Загружаем absorbed volumes из iceberg_levels
            absorbed_rows = await conn.fetch(absorbed_query, interval, symbol, start_time, end_time)
            
            # 3. Создаём lookup для быстрого слияния
            absorbed_lookup = {
                row['candle_time']: {
                    'absorbed_whale_vol': float(row['absorbed_whale_vol'] or 0),
                    'absorbed_dolphin_vol': float(row['absorbed_dolphin_vol'] or 0)
                }
                for row in absorbed_rows
            }
        
        if not rows:
            print(f"⚠️ No data found for {symbol} {timeframe} in range {start_time} - {end_time}")
            return 0
        
        # 4. Конвертация в SmartCandle objects
        candles: List[SmartCandle] = []
        for row in rows:
            # WHY: Сливаем absorbed volumes из отдельного запроса
            candle_time = row['candle_time']
            absorbed_data = absorbed_lookup.get(candle_time, {
                'absorbed_whale_vol': 0.0,
                'absorbed_dolphin_vol': 0.0
            })
            
            absorbed_total = absorbed_data['absorbed_whale_vol'] + absorbed_data['absorbed_dolphin_vol']
            
            candle = SmartCandle(
                symbol=symbol,
                timeframe=timeframe,
                candle_time=candle_time,
                open=row['open'],
                high=row['high'],
                low=row['low'],
                close=row['close'],
                volume=row['volume'],
                # AGGRESSORS (FLOW)
                flow_whale_cvd=row['flow_whale_cvd'] or 0.0,
                flow_dolphin_cvd=row['flow_dolphin_cvd'] or 0.0,
                flow_minnow_cvd=row['flow_minnow_cvd'] or 0.0,
                total_trades=row['total_trades'] or 0,
                # DERIVATIVES
                avg_basis_apr=row['avg_basis_apr'],
                options_skew=row['options_skew'],
                oi_delta=row['oi_delta'],
                # WALLS
                wall_whale_vol=row['wall_whale_vol'],
                wall_dolphin_vol=row['wall_dolphin_vol'],
                # ABSORBED (Исполненные айсберги)
                absorbed_whale_vol=absorbed_data['absorbed_whale_vol'],
                absorbed_dolphin_vol=absorbed_data['absorbed_dolphin_vol'],
                absorbed_total_vol=absorbed_total,
                # ORDERBOOK
                book_ofi=row['book_ofi'],
                book_obi=row['book_obi'],
                # OTHER
                total_gex=row['total_gex']
            )
            candles.append(candle)
        
        # 5. BULK INSERT в smart_candles
        insert_query = """
            INSERT INTO smart_candles (
                symbol, timeframe, candle_time,
                open, high, low, close, volume,
                flow_whale_cvd, flow_dolphin_cvd, flow_minnow_cvd, total_trades,
                avg_basis_apr, options_skew, oi_delta,
                wall_whale_vol, wall_dolphin_vol,
                absorbed_whale_vol, absorbed_dolphin_vol, absorbed_total_vol,
                book_ofi, book_obi,
                total_gex,
                aggregation_version
            ) VALUES (
                $1, $2, $3,
                $4, $5, $6, $7, $8,
                $9, $10, $11, $12,
                $13, $14, $15,
                $16, $17,
                $18, $19, $20,
                $21, $22,
                $23,
                $24
            )
            ON CONFLICT (symbol, timeframe, candle_time, aggregation_version)
            DO UPDATE SET
                open = EXCLUDED.open,
                high = EXCLUDED.high,
                low = EXCLUDED.low,
                close = EXCLUDED.close,
                volume = EXCLUDED.volume,
                flow_whale_cvd = EXCLUDED.flow_whale_cvd,
                flow_dolphin_cvd = EXCLUDED.flow_dolphin_cvd,
                flow_minnow_cvd = EXCLUDED.flow_minnow_cvd,
                total_trades = EXCLUDED.total_trades,
                avg_basis_apr = EXCLUDED.avg_basis_apr,
                options_skew = EXCLUDED.options_skew,
                oi_delta = EXCLUDED.oi_delta,
                wall_whale_vol = EXCLUDED.wall_whale_vol,
                wall_dolphin_vol = EXCLUDED.wall_dolphin_vol,
                absorbed_whale_vol = EXCLUDED.absorbed_whale_vol,
                absorbed_dolphin_vol = EXCLUDED.absorbed_dolphin_vol,
                absorbed_total_vol = EXCLUDED.absorbed_total_vol,
                book_ofi = EXCLUDED.book_ofi,
                book_obi = EXCLUDED.book_obi,
                total_gex = EXCLUDED.total_gex
        """
        
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                for candle in candles:
                    await conn.execute(
                        insert_query,
                        candle.symbol, candle.timeframe, candle.candle_time,
                        candle.open, candle.high, candle.low, candle.close, candle.volume,
                        candle.flow_whale_cvd, candle.flow_dolphin_cvd, candle.flow_minnow_cvd, candle.total_trades,
                        candle.avg_basis_apr, candle.options_skew, candle.oi_delta,
                        candle.wall_whale_vol, candle.wall_dolphin_vol,
                        candle.absorbed_whale_vol, candle.absorbed_dolphin_vol, candle.absorbed_total_vol,
                        candle.book_ofi, candle.book_obi,
                        candle.total_gex,
                        self.version
                    )
        
        print(f"✅ Materialized {len(candles)} candles: {symbol} {timeframe} v{self.version}")
        return len(candles)
    
    async def _count_existing_candles(
        self,
        symbol: str,
        timeframe: str,
        start_time: datetime,
        end_time: datetime
    ) -> int:
        """Проверяет количество существующих свечей."""
        query = """
            SELECT COUNT(*) FROM smart_candles
            WHERE symbol = $1 
              AND timeframe = $2
              AND candle_time >= $3 
              AND candle_time < $4
              AND aggregation_version = $5
        """
        async with self.pool.acquire() as conn:
            count = await conn.fetchval(query, symbol, timeframe, start_time, end_time, self.version)
        return count


# === BACKGROUND JOB (Scheduled Hourly) ===

async def materialize_last_hour(settling_delay_minutes: int = 5):
    """
    WHY: Background job для материализации последнего часа.
    
    CRITICAL: Settling Delay Strategy
    - Запускается каждый час (cron/scheduler)
    - Материализует час С OFFSET для безопасности
    
    Example (settling_delay_minutes=5):
        Current time: 15:00:00
        Materialize window: 13:55:00 → 14:55:00  (НЕ 14:00 → 15:00!)
        
        WHY: Последние тики за 14:59:59 могут ещё лететь по сети
             или лежать в буфере TradingEngine.producer_queue.
             5 минут offset гарантирует "settling" всех данных.
    
    Idempotency:
        force_recompute=True делает операцию безопасной для повторных запусков.
        Если запустить в 15:00 и ещё раз в 15:05 - данные перезапишутся.
    
    Args:
        settling_delay_minutes: Отступ назад от текущего времени (дефолт 5 мин)
    
    Cron Setup:
        # Запускать на 5-й минуте каждого часа
        # 5 * * * * cd /path && python -c "..."
        # Или: 0 * * * * sleep 300 && python -c "..."
    """
    materializer = CandleMaterializer(DB_DSN, aggregation_version='1.0')
    await materializer.connect()
    
    try:
        # WHY: Материализуем час С OFFSET
        now = datetime.now()
        settled_time = now - timedelta(minutes=settling_delay_minutes)
        
        # Час назад от settled_time
        start_time = settled_time - timedelta(hours=1)
        end_time = settled_time
        
        print(f"⏰ Current time: {now.strftime('%H:%M:%S')}")
        print(f"📊 Materializing window: {start_time.strftime('%H:%M')} → {end_time.strftime('%H:%M')}")
        print(f"   (Settling delay: {settling_delay_minutes} min)\n")
        
        # Материализуем для всех активных символов
        symbols = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT']
        
        for symbol in symbols:
            count = await materializer.materialize_candles(
                symbol=symbol,
                start_time=start_time,
                end_time=end_time,
                timeframe_minutes=60,
                force_recompute=True  # WHY: Idempotent - можно запускать много раз
            )
            if count > 0:
                print(f"✅ Materialized {count} candles for {symbol}")
    
    finally:
        await materializer.close()


async def backfill_historical_candles():
    """
    WHY: One-time job для заполнения исторических свечей.
    
    === RAM-AWARE STRATEGY (6GB limit) ===
    CRITICAL: Минутные таймфреймы требуют ОГРОМНОГО объёма данных.
    
    Безопасная стратегия для 6GB RAM:
    - 5m/15m: ТОЛЬКО последний месяц (для Sniper Entry ML)
    - 1h/4h/1d/1w/1m: Полные 6 месяцев (для Swing Trading)
    
    Почему так:
    - 5m × 6 месяцев × 3 символа = ~156,000 свечей (RAM explosion!)
    - 5m × 1 месяц × 3 символа = ~26,000 свечей (безопасно)
    - Sniper Entry работает на RECENT data (давняя микроструктура не нужна)
    - Swing context требует DEEP history (квартальные тренды китов)
    
    Батчинг:
    - По неделям (7 дней) для минутных таймфреймов
    - По месяцам (30 дней) для часовых+ таймфреймов
    
    === ⚠️ GEMINI WARNING: ML OVERFITTING RISK ===
    ПРОБЛЕМА:
        Обучая модель только на данных за последний месяц (5m/15m),
        она выучит "характер" только этого конкретного месяца.
        
    ПРИМЕР РИСКА:
        Если последний месяц был бычий (Bull Run), модель на 5-минутках
        может РАЗУЧИТЬСЯ шортить. Она будет оптимизирована только под
        восходящий тренд и провалится при развороте рынка.
        
    РЕШЕНИЕ ДЛЯ ПРОДАКШН:
        1. ДЛЯ СТАРТА: Текущая стратегия (1 месяц) достаточна для MVP
        2. ПОСЛЕ СТАБИЛИЗАЦИИ: Запускать ПОСТЕПЕННЫЙ backfill старых месяцев:
           - По 1 неделе в день (чтобы не убить сервер)
           - Приоритет: месяцы с РАЗНЫМИ market regimes (Bull/Bear/Sideways)
           - Цель: Накопить минимум 3-6 месяцев минутных данных с разнообразием
        3. CONTINUOUS TRAINING: Переобучать модель каждую неделю на ПОЛНОМ датасете
        
    КАК ЗАПУСТИТЬ ПОСТЕПЕННЫЙ BACKFILL (FUTURE):
        >>> # В cron (запускать 1 раз в день в 03:00 ночи)
        >>> materializer = CandleMaterializer(DB_DSN)
        >>> await materializer.materialize_candles(
        ...     symbol='BTCUSDT',
        ...     start_time=datetime(2024, 6, 1),  # Старый месяц
        ...     end_time=datetime(2024, 6, 8),    # +1 неделя
        ...     timeframe_minutes=5,
        ...     force_recompute=False  # Не перезаписывать существующие
        ... )
        >>> # Следующий день: datetime(2024, 6, 8) → datetime(2024, 6, 15)
        >>> # И так далее, пока не заполним весь 2024 год
    
    МОНИТОРИНГ КАЧЕСТВА ML:
        - Track Win Rate по месяцам (должна быть стабильной >50%)
        - Если модель проваливается в новом месяце → нужно больше истории
        - Используй Sharpe Ratio как метрику качества обобщения
    """
    materializer = CandleMaterializer(DB_DSN, aggregation_version='1.0')
    await materializer.connect()
    
    try:
        now = datetime.now()
        
        symbols = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT']
        
        # === СТРАТЕГИЯ 1: МИНУТНЫЕ ТАЙМФРЕЙМЫ (RECENT ONLY) ===
        # WHY: 5m/15m нужны только для Sniper Entry (недавний микроконтекст)
        minute_timeframes = [
            5,       # 5M  (Sniper Entry ML - high precision)
            15,      # 15M (Micro-swing context)
        ]
        minute_history_weeks = 4  # ТОЛЬКО 1 месяц истории (4 недели)
        
        for symbol in symbols:
            for tf in minute_timeframes:
                print(f"\n=== Backfilling {symbol} {tf}m (LAST {minute_history_weeks} WEEKS ONLY - RAM safety) ===")
                
                # WHY: Батчинг по НЕДЕЛЯМ для минутных данных
                for week_offset in range(minute_history_weeks):
                    end_time = now - timedelta(weeks=week_offset)
                    start_time = end_time - timedelta(weeks=1)
                    
                    print(f"  Week {week_offset + 1}/{minute_history_weeks}: {start_time.date()} → {end_time.date()}")
                    
                    count = await materializer.materialize_candles(
                        symbol=symbol,
                        start_time=start_time,
                        end_time=end_time,
                        timeframe_minutes=tf,
                        force_recompute=True
                    )
                    
                    if count > 0:
                        print(f"    ✅ {count} candles materialized")
                    await asyncio.sleep(0.2)  # WHY: RAM breathing room
        
        # === СТРАТЕГИЯ 2: ЧАСОВЫЕ+ ТАЙМФРЕЙМЫ (DEEP HISTORY) ===
        # WHY: 1h+ нужны для Swing Trading (квартальные тренды, Smart Money)
        hourly_timeframes = [
            60,      # 1H
            240,     # 4H  
            1440,    # 1D
            10080,   # 1W
            43200    # 1M
        ]
        hourly_history_months = 6  # Полная глубина для swing context
        
        for symbol in symbols:
            for tf in hourly_timeframes:
                print(f"\n=== Backfilling {symbol} {tf}m (FULL 6 MONTHS - Deep History) ===")
                
                # WHY: Батчинг по месяцам - безопасно для RAM
                for month_offset in range(hourly_history_months):
                    # Расчёт границ месяца
                    end_time = now - timedelta(days=30 * month_offset)
                    start_time = end_time - timedelta(days=30)
                    
                    print(f"  Month {month_offset + 1}/6: {start_time.date()} → {end_time.date()}")
                    
                    count = await materializer.materialize_candles(
                        symbol=symbol,
                        start_time=start_time,
                        end_time=end_time,
                        timeframe_minutes=tf,
                        force_recompute=True
                    )
                    
                    if count > 0:
                        print(f"    ✅ {count} candles materialized")
                    await asyncio.sleep(0.1)
    
    finally:
        await materializer.close()


if __name__ == '__main__':
    # Для первого запуска - backfill исторических данных
    asyncio.run(backfill_historical_candles())
    
    # Для регулярного запуска (cron) - только последний час
    # asyncio.run(materialize_last_hour())
