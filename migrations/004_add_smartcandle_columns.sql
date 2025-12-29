-- WHY: Добавляет недостающие колонки в market_metrics_full для SmartCandles
-- Дата: 2025-12-23
-- Цель: Поддержка материализации SmartCandles (volume, dolphin_cvd, gex, vpin)

-- ============================================================================
-- РАСШИРЕНИЕ ТАБЛИЦЫ market_metrics_full
-- ============================================================================

-- WHY: Эти колонки критичны для расчёта SmartCandles но отсутствуют в базовой схеме
ALTER TABLE market_metrics_full 
ADD COLUMN IF NOT EXISTS volume NUMERIC,                -- Объем сделки (критично для OHLCV)
ADD COLUMN IF NOT EXISTS dolphin_cvd_delta NUMERIC,     -- CVD средних игроков ($1k-$100k)
ADD COLUMN IF NOT EXISTS total_gex NUMERIC,             -- Суммарная Gamma Exposure
ADD COLUMN IF NOT EXISTS vpin_score NUMERIC,            -- VPIN flow toxicity score
ADD COLUMN IF NOT EXISTS weighted_obi NUMERIC;          -- Exponential-weighted OBI (alias для obi)

-- Комментарии
COMMENT ON COLUMN market_metrics_full.volume IS 
'Trade volume (in base currency, e.g. BTC). Critical for OHLCV SmartCandle aggregation.';

COMMENT ON COLUMN market_metrics_full.dolphin_cvd_delta IS 
'Cumulative Volume Delta for mid-size traders ($1k-$100k). Bridges whale and minnow segments.';

COMMENT ON COLUMN market_metrics_full.total_gex IS 
'Total Gamma Exposure from options market (integrated from Deribit). Affects price stickiness near strikes.';

COMMENT ON COLUMN market_metrics_full.vpin_score IS 
'Volume-synchronized Probability of Informed Trading. >0.7 = toxic flow (panic/accumulation).';

COMMENT ON COLUMN market_metrics_full.weighted_obi IS 
'Exponentially-weighted Order Book Imbalance. Uses adaptive lambda decay. Alias: same data as obi but computed with weighting.';

-- ============================================================================
-- ОБНОВЛЕНИЕ ИНДЕКСОВ (опционально, если нужна фильтрация по vpin/gex)
-- ============================================================================

-- Индекс для быстрого поиска toxic flow событий
CREATE INDEX IF NOT EXISTS idx_metrics_vpin_toxic 
ON market_metrics_full (time DESC, symbol) 
WHERE vpin_score > 0.7;

COMMENT ON INDEX idx_metrics_vpin_toxic IS 
'Accelerates queries for toxic flow events (VPIN > 0.7). Used by ML feature engineering.';

-- ============================================================================
-- ROLLBACK СКРИПТ
-- ============================================================================

/*
-- ОСТОРОЖНО: Удаление колонок приведет к потере данных!

ALTER TABLE market_metrics_full 
DROP COLUMN IF EXISTS volume,
DROP COLUMN IF EXISTS dolphin_cvd_delta,
DROP COLUMN IF EXISTS total_gex,
DROP COLUMN IF EXISTS vpin_score,
DROP COLUMN IF EXISTS weighted_obi;

DROP INDEX IF EXISTS idx_metrics_vpin_toxic;
*/
