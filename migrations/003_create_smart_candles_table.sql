-- =========================================================================
-- MIGRATION 003: Smart Candles Materialized Table
-- =========================================================================
-- WHY: XGBoost и HMM требуют CONSISTENT, REPRODUCIBLE features.
--      SQL агрегация по запросу = feature drift + slow performance.
--
-- Решение: Материализованная таблица с FROZEN свечами.
--
-- Преимущества:
-- 1. Consistency: Раз сохранённые свечи не меняются (immutable)
-- 2. Speed: O(1) lookup вместо O(N) агрегации
-- 3. Versioning: Можно хранить разные версии формул
-- 4. Reproducibility: Backtesting на идентичных данных
-- =========================================================================

CREATE TABLE IF NOT EXISTS smart_candles (
    id SERIAL PRIMARY KEY,
    
    -- Идентификация свечи
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,  -- '1h', '4h', '1d', '1w', '1m'
    candle_time TIMESTAMPTZ NOT NULL,
    
    -- === БАЗОВЫЕ OHLCV ===
    open NUMERIC NOT NULL,
    high NUMERIC NOT NULL,
    low NUMERIC NOT NULL,
    close NUMERIC NOT NULL,
    volume NUMERIC NOT NULL,  -- Total volume (BTC/ETH/SOL)
    
    -- === АГРЕССОРЫ (FLOW): CVD метрики - кто БЬЁТ по рынку ===
    flow_whale_cvd DOUBLE PRECISION,      -- CVD для whale агрессоров (>$100k trades)
    flow_dolphin_cvd DOUBLE PRECISION,    -- CVD для dolphin агрессоров ($1k-$100k) - BRIDGE между retail и institutions
    flow_minnow_cvd DOUBLE PRECISION,     -- CVD для minnow агрессоров (<$1k trades) - retail толпа
    total_trades INTEGER,                 -- Количество сделок за свечу
    
    -- === КРИТИЧЕСКАЯ МЕТРИКА 1: FUTURES BASIS ===
    avg_basis_apr DOUBLE PRECISION,  -- Average Annualized Basis (%)
    min_basis_apr DOUBLE PRECISION,  -- Минимальный basis (для детекции backwardation)
    max_basis_apr DOUBLE PRECISION,  -- Максимальный basis (для детекции перегрева)
    
    -- === КРИТИЧЕСКАЯ МЕТРИКА 2: OPTIONS SKEW ===
    options_skew DOUBLE PRECISION,   -- 25-delta Put IV - 25-delta Call IV (%)
    
    -- === КРИТИЧЕСКАЯ МЕТРИКА 3: OPEN INTEREST DELTA ===
    oi_delta DOUBLE PRECISION,       -- Change in Open Interest за свечу
    
    -- === СТЕНЫ (WALL): Iceberg volumes - кто ПРИНИМАЕТ удар ===
    wall_whale_vol DOUBLE PRECISION,      -- Detected whale iceberg volume (>$100k hidden liquidity)
    wall_dolphin_vol DOUBLE PRECISION,    -- Detected dolphin iceberg volume ($1k-$100k) - ex-shark
    
    -- === ORDERBOOK МЕТРИКИ ===
    book_ofi DOUBLE PRECISION,       -- Order Flow Imbalance (среднее за свечу)
    book_obi DOUBLE PRECISION,       -- Order Book Imbalance exponentially weighted (среднее)
    avg_spread_bps DOUBLE PRECISION, -- Средний спред в базисных пунктах
    
    -- === WYCKOFF КОНТЕКСТ ===
    wyckoff_pattern TEXT,            -- 'SPRING', 'UPTHRUST', 'ACCUMULATION', 'DISTRIBUTION', NULL
    accumulation_confidence DOUBLE PRECISION, -- 0.0-1.0 из AccumulationDetector
    
    -- === GAMMA METRICS ===
    total_gex DOUBLE PRECISION,      -- Total Gamma Exposure
    
    -- === VPIN (Flow Toxicity) ===
    avg_vpin_score DOUBLE PRECISION, -- Среднее VPIN за свечу
    max_vpin_score DOUBLE PRECISION, -- Пиковое VPIN (для детекции токсичности)
    
    -- === METADATA ===
    created_at TIMESTAMPTZ DEFAULT NOW(),
    aggregation_version TEXT DEFAULT '1.0',  -- Версия формулы агрегации
    
    -- === CONSTRAINTS ===
    UNIQUE(symbol, timeframe, candle_time, aggregation_version)
);

-- === ИНДЕКСЫ ДЛЯ FAST LOOKUP ===

-- Основной индекс для ML training (загрузка последовательности свечей)
CREATE INDEX IF NOT EXISTS idx_smart_candles_ml_lookup 
ON smart_candles (symbol, timeframe, candle_time DESC);

-- Индекс для фильтрации по версии (важно при обновлении формул)
CREATE INDEX IF NOT EXISTS idx_smart_candles_version 
ON smart_candles (aggregation_version, symbol, timeframe);

-- Индекс для быстрого поиска Wyckoff паттернов
CREATE INDEX IF NOT EXISTS idx_smart_candles_wyckoff 
ON smart_candles (wyckoff_pattern) 
WHERE wyckoff_pattern IS NOT NULL;

-- === КОММЕНТАРИИ (для документации) ===

COMMENT ON TABLE smart_candles IS 
'Материализованные SmartCandles для ML training и backtesting.
IMMUTABLE: Раз сохранённые свечи не изменяются (версионирование через aggregation_version).
Используется для XGBoost, HMM, Reinforcement Learning.';

COMMENT ON COLUMN smart_candles.aggregation_version IS 
'Версия формулы агрегации. При изменении логики расчёта CVD/OFI создаётся новая версия.
Позволяет хранить несколько версий данных одновременно для A/B тестирования моделей.';

COMMENT ON COLUMN smart_candles.wyckoff_pattern IS 
'Wyckoff паттерн, детектированный в момент закрытия свечи.
SPRING: Цена пробила поддержку но откупилась (накопление).
UPTHRUST: Цена пробила сопротивление но вернулась (дистрибуция).';

COMMENT ON COLUMN smart_candles.avg_vpin_score IS 
'Среднее VPIN (Volume-Synchronized Probability of Informed Trading) за свечу.
> 0.7 = Высокая токсичность потока (риск пробоя айсбергов).
< 0.3 = Низкая токсичность (айсберги устоят).';
