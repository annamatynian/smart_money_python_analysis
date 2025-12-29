-- WHY: Semantic refactoring - разделение АГРЕССОРОВ (flow_) и СТЕН (wall_)
-- Дата: 2025-12-23
-- Цель: Устранение терминологической путаницы (Gemini validation)
--Ref: GEMINI_VALIDATION_TERMINOLOGY.md

-- ============================================================================
-- ПРОБЛЕМА:
-- ============================================================================
-- 1. whale_cvd, dolphin_cvd, minnow_cvd - это АГРЕССОРЫ (те кто БЬЁТ)
-- 2. is_shark в айсбергах - это СТЕНА (те кто ПРИНИМАЕТ удар)
-- 3. Диапазоны пересекаются: dolphin ($1k-$100k) vs shark ($10k-$100k)
-- 4. Когнитивный диссонанс: "Dolphin бьёт Shark" - мозг споткнётся!

-- РЕШЕНИЕ:
-- 1. Префикс flow_ для АГРЕССОРОВ (CVD)
-- 2. Префикс wall_ для СТЕН (Iceberg volumes)
-- 3. Единая размерная шкала: Whale > Dolphin > Minnow (для обеих ролей)

-- ============================================================================
-- RENAME EXISTING COLUMNS (Агрессоры)
-- ============================================================================
-- WHY: Идемпотентность - можно применять многократно

DO $
BEGIN
    -- whale_cvd_delta -> flow_whale_cvd_delta
    IF EXISTS (SELECT 1 FROM information_schema.columns 
               WHERE table_name='market_metrics_full' AND column_name='whale_cvd_delta') THEN
        ALTER TABLE market_metrics_full RENAME COLUMN whale_cvd_delta TO flow_whale_cvd_delta;
    END IF;
    
    -- dolphin_cvd_delta -> flow_dolphin_cvd_delta
    IF EXISTS (SELECT 1 FROM information_schema.columns 
               WHERE table_name='market_metrics_full' AND column_name='dolphin_cvd_delta') THEN
        ALTER TABLE market_metrics_full RENAME COLUMN dolphin_cvd_delta TO flow_dolphin_cvd_delta;
    END IF;
    
    -- minnow_cvd_delta -> flow_minnow_cvd_delta
    IF EXISTS (SELECT 1 FROM information_schema.columns 
               WHERE table_name='market_metrics_full' AND column_name='minnow_cvd_delta') THEN
        ALTER TABLE market_metrics_full RENAME COLUMN minnow_cvd_delta TO flow_minnow_cvd_delta;
    END IF;
    
    -- obi -> book_obi
    IF EXISTS (SELECT 1 FROM information_schema.columns 
               WHERE table_name='market_metrics_full' AND column_name='obi') THEN
        ALTER TABLE market_metrics_full RENAME COLUMN obi TO book_obi;
    END IF;
    
    -- ofi -> book_ofi
    IF EXISTS (SELECT 1 FROM information_schema.columns 
               WHERE table_name='market_metrics_full' AND column_name='ofi') THEN
        ALTER TABLE market_metrics_full RENAME COLUMN ofi TO book_ofi;
    END IF;
END $;

-- ============================================================================
-- ADD NEW COLUMNS (Стены - Iceberg volumes)
-- ============================================================================

-- WHY: Добавляем метрики для СТЕН (пассивных айсбергов)
-- Эти данные будут агрегироваться из iceberg_levels или iceberg_training_data

ALTER TABLE market_metrics_full 
ADD COLUMN IF NOT EXISTS wall_whale_vol NUMERIC,      -- Whale iceberg volume detected (>$100k)
ADD COLUMN IF NOT EXISTS wall_dolphin_vol NUMERIC;    -- Dolphin iceberg volume (ex-shark, $1k-$100k)

-- ============================================================================
-- UPDATE COMMENTS
-- ============================================================================

COMMENT ON COLUMN market_metrics_full.flow_whale_cvd_delta IS 
'CVD for whale aggressors (>$100k trades). Measures institutional BUYING/SELLING pressure.';

COMMENT ON COLUMN market_metrics_full.flow_dolphin_cvd_delta IS 
'CVD for dolphin aggressors ($1k-$100k trades). Bridge between retail and institutions.';

COMMENT ON COLUMN market_metrics_full.flow_minnow_cvd_delta IS 
'CVD for minnow aggressors (<$1k trades). Proxy for retail crowd sentiment.';

COMMENT ON COLUMN market_metrics_full.wall_whale_vol IS 
'Detected whale iceberg volume (>$100k hidden liquidity). PASSIVE limit orders absorbing flow.';

COMMENT ON COLUMN market_metrics_full.wall_dolphin_vol IS 
'Detected dolphin iceberg volume ($1k-$100k). Formerly "shark" - renamed for semantic consistency.';

COMMENT ON COLUMN market_metrics_full.book_obi IS 
'Order Book Imbalance (exponentially weighted). Measures visible liquidity skew.';

COMMENT ON COLUMN market_metrics_full.book_ofi IS 
'Order Flow Imbalance. Measures aggressive order flow direction.';

-- ============================================================================
-- ROLLBACK СКРИПТ
-- ============================================================================

/*
-- ОСТОРОЖНО: Откатит изменения имён колонок

ALTER TABLE market_metrics_full 
RENAME COLUMN flow_whale_cvd_delta TO whale_cvd_delta;

ALTER TABLE market_metrics_full 
RENAME COLUMN flow_dolphin_cvd_delta TO dolphin_cvd_delta;

ALTER TABLE market_metrics_full 
RENAME COLUMN flow_minnow_cvd_delta TO minnow_cvd_delta;

ALTER TABLE market_metrics_full 
RENAME COLUMN book_obi TO obi;

ALTER TABLE market_metrics_full 
RENAME COLUMN book_ofi TO ofi;

ALTER TABLE market_metrics_full 
DROP COLUMN IF EXISTS wall_whale_vol,
DROP COLUMN IF EXISTS wall_dolphin_vol;
*/
