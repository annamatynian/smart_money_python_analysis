import asyncio
import asyncpg
import pandas as pd
from datetime import datetime, timezone
from tabulate import tabulate
from colorama import Fore, Style, init

# Инициализация цветов
init(autoreset=True)

# === КОНФИГУРАЦИЯ ===
DB_DSN = "postgresql://postgres:password@localhost:5432/trading_db" 

class DataQualityMonitor:
    def __init__(self, dsn):
        self.dsn = dsn
        self.conn = None

    async def connect(self):
        try:
            self.conn = await asyncpg.connect(self.dsn)
            print(f"{Fore.GREEN}✅ Connected to Database.{Style.RESET_ALL}")
        except Exception as e:
            print(f"{Fore.RED}❌ Database Connection Failed: {e}{Style.RESET_ALL}")
            exit(1)

    async def close(self):
        if self.conn:
            await self.conn.close()

    def print_header(self, title):
        print(f"\n{Fore.CYAN}{'='*60}")
        print(f" 🔍 {title}")
        print(f"{'='*60}{Style.RESET_ALL}")

    def evaluate_metric(self, name, value, condition_func, warn_msg):
        """Helper для красивого вывода статуса"""
        # Если value None, считаем ошибкой
        if value is None:
            is_ok = False
            val_str = "NULL"
        else:
            is_ok = condition_func(value)
            val_str = str(value)

        status = f"{Fore.GREEN}OK{Style.RESET_ALL}" if is_ok else f"{Fore.RED}FAIL{Style.RESET_ALL}"
        print(f" • {name:<40} : {val_str:<15} [{status}]")
        if not is_ok:
            print(f"   {Fore.YELLOW}⚠️  Warning: {warn_msg}{Style.RESET_ALL}")

    # =========================================================================
    # 1. DATA FRESHNESS & STREAM CONTINUITY
    # =========================================================================
    async def check_stream_freshness(self):
        self.print_header("1. DATA STREAM FRESHNESS")
        
        # 1.1 Last Update Time (Critical)
        row = await self.conn.fetchrow("""
            SELECT MAX(time) as last_time FROM market_metrics_full
        """)
        
        if not row or row['last_time'] is None:
            print(f"{Fore.RED}❌ market_metrics_full is EMPTY!{Style.RESET_ALL}")
            return
        
        last_time = row['last_time']
        now = datetime.now(timezone.utc)
        diff = (now - last_time).total_seconds()
        
        self.evaluate_metric("Time Since Last Metric", f"{diff:.1f} sec", lambda x: diff < 60, "Stream STOPPED! Check services.py")
        
        # 1.2 Basis Stream Continuity (для SmartCandle)
        row_basis = await self.conn.fetchrow("""
            SELECT COUNT(*) as total, COUNT(basis_apr) as cnt_basis
            FROM (SELECT * FROM market_metrics_full ORDER BY time DESC LIMIT 1000) sub
        """)
        
        if row_basis['total'] > 0:
            basis_cov = (row_basis['cnt_basis'] / row_basis['total']) * 100
            self.evaluate_metric("Basis Stream Coverage", f"{basis_cov:.1f}%", lambda x: basis_cov > 95, "Gaps in Basis! SmartCandles broken")

    # =========================================================================
    # 2. МИКРОСТРУКТУРА (Microstructure)
    # =========================================================================
    async def check_microstructure(self):
        self.print_header("2. MICROSTRUCTURE QUALITY")
        
        # 1.1 OBI Quality (Range & Variance)
        row_obi = await self.conn.fetchrow("""
            SELECT 
                MIN(obi) as min_obi,
                MAX(obi) as max_obi,
                COUNT(CASE WHEN ABS(obi) > 0.1 THEN 1 END) as cnt_significant,
                COUNT(*) as total
            FROM market_metrics_full
            WHERE time > NOW() - INTERVAL '1 hour'
        """)
        
        if not row_obi or row_obi['total'] == 0:
            print(f"{Fore.RED}❌ No data in market_metrics_full (1h){Style.RESET_ALL}")
            return

        total = row_obi['total']
        sig_ratio = (row_obi['cnt_significant'] / total) * 100
        
        self.evaluate_metric("OBI Min (>= -1.0)", row_obi['min_obi'], lambda x: x >= -1.0, "OBI < -1 (Parse Error?)")
        self.evaluate_metric("OBI Max (<= 1.0)", row_obi['max_obi'], lambda x: x <= 1.0, "OBI > 1 (Parse Error?)")
        self.evaluate_metric("OBI Activity (>0.1)", f"{sig_ratio:.1f}%", lambda x: sig_ratio > 5, "OBI is stuck near 0 (Sensor Error)")

        # 1.2 Whale CVD Delta Activity
        row_cvd = await self.conn.fetchrow("""
            SELECT COUNT(*) as cnt_nonzero, COUNT(*) as total
            FROM market_metrics_full
            WHERE time > NOW() - INTERVAL '1 hour'
              AND whale_cvd_delta != 0
        """)
        
        nonzero_ratio = (row_cvd['cnt_nonzero'] / row_obi['total']) * 100
        self.evaluate_metric("Whale CVD Activity", f"{nonzero_ratio:.1f}%", lambda x: nonzero_ratio > 10, "Whale CVD Delta is always 0!")

        # 1.3 Dolphin & Minnow Activity (NEW CHECK - after shark->dolphin refactor)
        row_segments = await self.conn.fetchrow("""
            SELECT 
                COUNT(CASE WHEN dolphin_cvd_delta != 0 THEN 1 END) as active_dolphins,
                COUNT(CASE WHEN minnow_cvd_delta != 0 THEN 1 END) as active_minnows,
                COUNT(*) as total
            FROM market_metrics_full
            WHERE time > NOW() - INTERVAL '1 hour'
        """)
        
        seg_total = row_segments['total']
        if seg_total > 0:
            dolphin_rate = (row_segments['active_dolphins'] / seg_total) * 100
            minnow_rate = (row_segments['active_minnows'] / seg_total) * 100
            self.evaluate_metric("Dolphin Activity", f"{dolphin_rate:.1f}%", lambda x: dolphin_rate > 5, "Dolphin CVD is dead (Zeros)!")
            self.evaluate_metric("Minnow Activity", f"{minnow_rate:.1f}%", lambda x: minnow_rate > 5, "Minnow CVD is dead (Zeros)!")
        
        # 1.4 Wall Volumes (Icebergs in Metrics)
        try:
            row_walls = await self.conn.fetchrow("""
                SELECT 
                    SUM(wall_whale_vol) as total_whale_wall,
                    SUM(wall_dolphin_vol) as total_dolphin_wall
                FROM market_metrics_full
                WHERE time > NOW() - INTERVAL '24 hours'
            """)
            
            if row_walls:
                w_vol = row_walls['total_whale_wall'] or 0
                d_vol = row_walls['total_dolphin_wall'] or 0
                print(f" • Walls Detected (24h)         : Whale={w_vol:.2f}, Dolphin={d_vol:.2f} [INFO]")
        except asyncpg.UndefinedColumnError:
             self.evaluate_metric("Wall Columns", "MISSING", lambda x: False, "Apply migration 005 (Wall columns)!")

    # =========================================================================
    # 3. ДЕРИВАТИВЫ (Derivatives Sanity)
    # =========================================================================
    async def check_derivatives_sanity(self):
        self.print_header("3. DERIVATIVES SANITY")

        # 2.1 Basis APR Range
        row_basis = await self.conn.fetchrow("""
            SELECT MIN(basis_apr) as min_basis, MAX(basis_apr) as max_basis
            FROM market_metrics_full
            WHERE time > NOW() - INTERVAL '24 hours' AND basis_apr IS NOT NULL
        """)
        
        if row_basis['min_basis'] is not None:
            self.evaluate_metric("Basis APR Min (> -100%)", row_basis['min_basis'], lambda x: x > -100, "Basis < -100% (Anomaly)")
            self.evaluate_metric("Basis APR Max (< 100%)", row_basis['max_basis'], lambda x: x < 100, "Basis > 100% (Anomaly)")
        else:
            print(f"{Fore.RED}❌ No Basis data in last 24h{Style.RESET_ALL}")

        # 2.2 Skew Range
        row_skew = await self.conn.fetchrow("""
            SELECT MIN(options_skew) as min_skew, MAX(options_skew) as max_skew
            FROM market_metrics_full
            WHERE time > NOW() - INTERVAL '24 hours' AND options_skew IS NOT NULL
        """)
        
        if row_skew['min_skew'] is not None:
            self.evaluate_metric("Skew Min (>= -10)", row_skew['min_skew'], lambda x: x >= -10, "Skew too low") # Skew в % обычно
            self.evaluate_metric("Skew Max (<= 10)", row_skew['max_skew'], lambda x: x <= 10, "Skew too high")

    # =========================================================================
    # 4. VPIN & TOXICITY
    # =========================================================================
    async def check_vpin_distribution(self):
        self.print_header("4. VPIN DISTRIBUTION")
        
        row = await self.conn.fetchrow("""
            SELECT 
                AVG(vpin_score) as avg_vpin,
                COUNT(CASE WHEN vpin_score > 0.7 THEN 1 END) as cnt_toxic,
                COUNT(*) as total
            FROM iceberg_feature_snapshot
            WHERE snapshot_time > NOW() - INTERVAL '24 hours'
        """)
        
        if not row or row['total'] == 0:
            print(f"{Fore.YELLOW}No snapshots in last 24h.{Style.RESET_ALL}")
            return

        toxic_rate = (row['cnt_toxic'] / row['total']) * 100
        
        self.evaluate_metric("Avg VPIN (0.3 - 0.6)", row['avg_vpin'], lambda x: 0.2 <= x <= 0.6, "VPIN average is skewed")
        self.evaluate_metric("Toxic Rate (< 30%)", f"{toxic_rate:.1f}%", lambda x: toxic_rate < 30, "Too many TOXIC events (Bug?)")

    # =========================================================================
    # 5. GAMMA & SPOOFING (Feature Completeness)
    # =========================================================================
    async def check_advanced_features(self):
        self.print_header("5. ADVANCED FEATURES (Gamma & Spoofing)")
        
        row = await self.conn.fetchrow("""
            SELECT 
                COUNT(*) as total,
                COUNT(total_gex) as cnt_gex,
                COUNT(spoofing_score) as cnt_spoof,
                AVG(spoofing_score) as avg_spoof
            FROM iceberg_feature_snapshot
            WHERE snapshot_time > NOW() - INTERVAL '24 hours'
        """)
        
        if not row or row['total'] == 0: return

        total = row['total']
        gex_cov = (row['cnt_gex'] / total) * 100
        spoof_cov = (row['cnt_spoof'] / total) * 100
        
        self.evaluate_metric("GEX Coverage (>90%)", f"{gex_cov:.1f}%", lambda x: gex_cov > 90, "GEX data missing")
        self.evaluate_metric("Spoofing Coverage (>90%)", f"{spoof_cov:.1f}%", lambda x: spoof_cov > 90, "Spoofing scores missing")
        
        if row['avg_spoof']:
            self.evaluate_metric("Avg Spoof Score (< 0.5)", row['avg_spoof'], lambda x: x < 0.5, "Everything looks like spoofing!")

    # =========================================================================
    # 6. EXECUTION & LATENCY (Delta-t)
    # =========================================================================
    async def check_execution_quality(self):
        self.print_header("6. EXECUTION & REFILLS")
        
        # Проверяем, есть ли колонка average_refill_delay_ms (мы могли ее не добавить в миграции)
        # Если упадет - значит колонки нет, это тоже результат.
        try:
            row = await self.conn.fetchrow("""
                SELECT 
                    AVG(average_refill_delay_ms) as avg_delay,
                    COUNT(CASE WHEN average_refill_delay_ms > 100 THEN 1 END) as cnt_slow,
                    COUNT(*) as total
                FROM iceberg_levels
                WHERE last_update_time > NOW() - INTERVAL '24 hours'
                  AND average_refill_delay_ms IS NOT NULL
            """)
            
            if row and row['total'] > 0:
                self.evaluate_metric("Avg Refill Delay (< 100ms)", row['avg_delay'], lambda x: x < 100, "Slow execution detected")
            else:
                print(f"{Fore.YELLOW}No refill data in last 24h.{Style.RESET_ALL}")
                
        except asyncpg.UndefinedColumnError:
             print(f"{Fore.RED}❌ Column 'average_refill_delay_ms' missing in iceberg_levels.{Style.RESET_ALL}")

    # =========================================================================
    # 7. SMART MONEY CONTEXT (Deep Memory)
    # =========================================================================
    async def check_smart_money_context(self):
        self.print_header("7. SMART MONEY CONTEXT")
        
        row = await self.conn.fetchrow("""
            SELECT 
                COUNT(*) as total,
                COUNT(whale_cvd_trend_6m) as cnt_6m,
                COUNT(whale_cvd_trend_3m) as cnt_3m
            FROM iceberg_feature_snapshot
            WHERE snapshot_time > NOW() - INTERVAL '24 hours'
        """)
        
        if not row or row['total'] == 0:
            print(f"{Fore.YELLOW}No snapshots in last 24h.{Style.RESET_ALL}")
            return
        
        total = row['total']
        null_rate_6m = 100 - (row['cnt_6m'] / total * 100)
        
        self.evaluate_metric("Whale CVD 6M Null Rate", f"{null_rate_6m:.1f}%", lambda x: null_rate_6m < 10, "HistoricalMemory not loaded! ML blind")

    # =========================================================================
    # 8. ACCUMULATION & DIVERGENCE
    # =========================================================================
    async def check_accumulation_logic(self):
        self.print_header("8. ACCUMULATION LOGIC")
        
        row = await self.conn.fetchrow("""
            SELECT 
                COUNT(*) as total,
                COUNT(CASE WHEN is_htf_divergence = 1 THEN 1 END) as cnt_bullish,
                COUNT(CASE WHEN is_htf_divergence = -1 THEN 1 END) as cnt_bearish
            FROM iceberg_feature_snapshot
            WHERE snapshot_time > NOW() - INTERVAL '7 days'
        """)
        
        if not row or row['total'] == 0: return
        
        total = row['total']
        div_rate = ((row['cnt_bullish'] + row['cnt_bearish']) / total) * 100
        
        self.evaluate_metric("Divergence Detected (>0%)", f"{div_rate:.1f}%", lambda x: div_rate > 0, "No Wyckoff divergences found in 7 days")
        self.evaluate_metric("Divergence Not Spam (<50%)", f"{div_rate:.1f}%", lambda x: div_rate < 50, "Logic identifies everything as divergence!")

    # =========================================================================
    # 9. INTENTION CLASSIFICATION
    # =========================================================================
    async def check_intention_classification(self):
        self.print_header("9. INTENTION CLASSIFICATION")
        
        rows = await self.conn.fetch("""
            SELECT intention_type, COUNT(*) as cnt 
            FROM iceberg_lifecycle 
            WHERE event_time > NOW() - INTERVAL '3 days'
            GROUP BY intention_type
        """)
        
        if not rows:
            print(f"{Fore.YELLOW}No events in last 3 days.{Style.RESET_ALL}")
            return
        
        df = pd.DataFrame(rows, columns=['Type', 'Count'])
        print(tabulate(df, headers='keys', tablefmt='psql'))
        
        has_positional = any(row['intention_type'] == 'POSITIONAL' for row in rows)
        if not has_positional:
            print(f"{Fore.YELLOW}⚠️  No POSITIONAL icebergs. Check ADV calculation.{Style.RESET_ALL}")

    # =========================================================================
    # 10. GRIM REAPER (Labels)
    # =========================================================================
    async def check_grim_reaper_status(self):
        self.print_header("10. GRIM REAPER (Labels)")
        
        # 10.1 Strategic Swing (7d horizon)
        pending_strategic = await self.conn.fetchval("SELECT COUNT(*) FROM iceberg_lifecycle WHERE event_time < NOW() - INTERVAL '7 days' AND y_strategic_result IS NULL")
        self.evaluate_metric("Unlabeled Strategic (>7d)", f"{pending_strategic}", lambda x: pending_strategic == 0, "Run repository.run_grim_reaper_labeling()")
        
        # 10.2 Intraday (24h horizon)
        pending_intraday = await self.conn.fetchval("SELECT COUNT(*) FROM iceberg_lifecycle WHERE event_time < NOW() - INTERVAL '24 hours' AND y_intraday_result IS NULL")
        self.evaluate_metric("Unlabeled Intraday (>24h)", f"{pending_intraday}", lambda x: pending_intraday == 0, "Grim Reaper skipping intraday?")
        
        # 10.3 Quality Metrics (должны считаться для closed events)
        missing_metrics = await self.conn.fetchval("SELECT COUNT(*) FROM iceberg_lifecycle WHERE outcome IS NOT NULL AND y_sharpe_ratio IS NULL")
        self.evaluate_metric("Missing Sharpe/MFE", f"{missing_metrics}", lambda x: missing_metrics == 0, "Quality metrics not calculated")

    # =========================================================================
    # 12. SMART CANDLES AGGREGATION (Multi-Timeframe)
    # =========================================================================
    async def check_smartcandle_quality(self):
        self.print_header("12. SMART CANDLES AGGREGATION")
        
        # 12.1 Проверка накопления данных за последние 7 дней
        timeframes = {
            '1h': '1 hour',
            '4h': '4 hours',
            '1d': '1 day',
            '1w': '7 days'
        }
        
        for tf, interval in timeframes.items():
            # Считаем сколько должно быть свечей
            if tf == '1h':
                expected_min = 168  # 7 дней * 24ч
                threshold = 160  # Допускаем 5% пропусков
            elif tf == '4h':
                expected_min = 42  # 7 дней * 6 свечей/день
                threshold = 40
            elif tf == '1d':
                expected_min = 7  # 7 дней
                threshold = 7
            elif tf == '1w':
                expected_min = 1  # 1 неделя (текущая незакрытая)
                threshold = 1
            else:
                continue
            
            # Проверяем фактическое количество свечей
            row = await self.conn.fetchrow(f"""
                SELECT COUNT(DISTINCT date_bin($1::interval, time, '2020-01-01'::timestamptz)) as candle_count
                FROM market_metrics_full
                WHERE time > NOW() - INTERVAL '7 days'
                  AND price IS NOT NULL
            """, interval)
            
            actual_count = row['candle_count'] if row else 0
            
            self.evaluate_metric(
                f"{tf.upper()} Candles (>= {threshold})",
                f"{actual_count}",
                lambda x: actual_count >= threshold,
                f"Missing {tf} candles! Expected ~{expected_min}, got {actual_count}"
            )
        
        # 12.2 Проверка полноты derivatives метрик (критично для SmartCandle)
        row_deriv = await self.conn.fetchrow("""
            WITH recent_candles AS (
                SELECT 
                    date_bin('1 hour'::interval, time, '2020-01-01'::timestamptz) as candle_time,
                    COUNT(*) as ticks,
                    COUNT(basis_apr) as cnt_basis,
                    COUNT(options_skew) as cnt_skew,
                    COUNT(oi_delta) as cnt_oi
                FROM market_metrics_full
                WHERE time > NOW() - INTERVAL '24 hours'
                GROUP BY candle_time
            )
            SELECT 
                COUNT(*) as total_candles,
                AVG(cnt_basis) as avg_basis_ticks,
                AVG(cnt_skew) as avg_skew_ticks
            FROM recent_candles
        """)
        
        if row_deriv and row_deriv['total_candles'] > 0:
            avg_basis = row_deriv['avg_basis_ticks'] or 0
            avg_skew = row_deriv['avg_skew_ticks'] or 0
            
            self.evaluate_metric(
                "Avg Basis Ticks/Candle",
                f"{avg_basis:.1f}",
                lambda x: avg_basis > 5,
                "Basis data sparse! SmartCandles will have NULL fields"
            )
            
            self.evaluate_metric(
                "Avg Skew Ticks/Candle",
                f"{avg_skew:.1f}",
                lambda x: avg_skew > 5,
                "Skew data sparse! Options context missing"
            )
        
        # 12.3 Проверка CVD continuity (для мульти-таймфреймового анализа)
        row_cvd = await self.conn.fetchrow("""
            SELECT 
                COUNT(*) as total,
                COUNT(whale_cvd_delta) as cnt_whale,
                COUNT(minnow_cvd_delta) as cnt_minnow
            FROM market_metrics_full
            WHERE time > NOW() - INTERVAL '24 hours'
        """)
        
        if row_cvd and row_cvd['total'] > 0:
            whale_cov = (row_cvd['cnt_whale'] / row_cvd['total']) * 100
            minnow_cov = (row_cvd['cnt_minnow'] / row_cvd['total']) * 100
            
            self.evaluate_metric(
                "Whale CVD Coverage",
                f"{whale_cov:.1f}%",
                lambda x: whale_cov > 90,
                "Whale CVD gaps! Wyckoff analysis will fail"
            )
            
            self.evaluate_metric(
                "Minnow CVD Coverage",
                f"{minnow_cov:.1f}%",
                lambda x: minnow_cov > 90,
                "Minnow CVD gaps! Sentiment tracking broken"
            )

    # =========================================================================
    # 13. MATERIALIZED CANDLES INTEGRITY (NEW!)
    # =========================================================================
    async def check_materialized_candles(self):
        self.print_header("13. MATERIALIZED CANDLES (SmartCandles Table)")
        
        # Проверяем наличие свечей версии 1.0
        row = await self.conn.fetchrow("""
            SELECT 
                COUNT(*) as total,
                MAX(candle_time) as last_candle,
                COUNT(DISTINCT timeframe) as tf_count
            FROM smart_candles
            WHERE aggregation_version = '1.0'
        """)
        
        if not row or row['total'] == 0:
            self.evaluate_metric("Materialized Rows", "0", lambda x: False, "CRITICAL: Table smart_candles is EMPTY! Run candle_materializer.py")
            return

        # 1. Проверяем свежесть
        last_candle = row['last_candle']
        if last_candle.tzinfo is None:
            last_candle = last_candle.replace(tzinfo=timezone.utc)
            
        diff_hours = (datetime.now(timezone.utc) - last_candle).total_seconds() / 3600
        self.evaluate_metric("Freshness (< 2h)", f"{diff_hours:.1f}h ago", lambda x: diff_hours < 2, "Materializer stopped working!")
        
        # 2. Проверяем наличие всех таймфреймов (1h, 4h, 1d, 1w, 1m)
        self.evaluate_metric("Timeframes (5)", row['tf_count'], lambda x: x >= 5, "Missing some timeframes (1h/4h/1d/1w/1m)")

    # =========================================================================
    # 14. DATA INTEGRITY
    # =========================================================================
    async def check_data_integrity(self):
        self.print_header("14. DATA INTEGRITY")
        
        # Orphaned events (без features)
        orphans = await self.conn.fetchval("""
            SELECT COUNT(*) FROM iceberg_lifecycle l
            LEFT JOIN iceberg_feature_snapshot f ON l.id = f.lifecycle_event_id
            WHERE f.id IS NULL
        """)
        
        self.evaluate_metric("Orphaned Events", f"{orphans}", lambda x: orphans == 0, "CRITICAL! Events saved without Features.")

async def main():
    monitor = DataQualityMonitor(DB_DSN)
    await monitor.connect()
    
    print(f"\n{Fore.MAGENTA}🚀 STARTING ULTIMATE DATA AUDIT...{Style.RESET_ALL}")
    
    await monitor.check_stream_freshness()        # 1. Freshness & Continuity
    await monitor.check_microstructure()           # 2. OBI/CVD Quality
    await monitor.check_derivatives_sanity()       # 3. Basis/Skew Ranges
    await monitor.check_vpin_distribution()        # 4. VPIN Stats
    await monitor.check_advanced_features()        # 5. GEX/Spoofing
    await monitor.check_execution_quality()        # 6. Refill Timing
    await monitor.check_smart_money_context()      # 7. Deep Memory
    await monitor.check_accumulation_logic()       # 8. Divergences
    await monitor.check_intention_classification() # 9. Intention Types
    await monitor.check_grim_reaper_status()       # 10. Labels
    await monitor.check_smartcandle_quality()      # 12. SmartCandles Aggregation
    await monitor.check_materialized_candles()     # 13. Materialized Table
    await monitor.check_data_integrity()           # 14. Integrity
    
    await monitor.close()
    print(f"\n{Fore.MAGENTA}🏁 AUDIT COMPLETE.{Style.RESET_ALL}")



if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass