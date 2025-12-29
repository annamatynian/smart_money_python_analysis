-- WHY: Lifecycle management + Feature snapshots для ML (Task: Feature Engineering)
-- Дата: 2025-01-08
-- Цель: Собирать ПОЛНЫЙ контекст метрик для Feature Importance анализа

-- ============================================================================
-- ТАБЛИЦА 1: LIFECYCLE СОБЫТИЙ (Grim Reaper)
-- ============================================================================

CREATE TABLE IF NOT EXISTS iceberg_lifecycle (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    
    -- Идентификация айсберга
    symbol TEXT NOT NULL,
    price NUMERIC NOT NULL,
    is_ask BOOLEAN NOT NULL,
    
    -- Событие
    event_type TEXT NOT NULL,  -- 'DETECTED' | 'REFILLED' | 'BREACHED' | 'EXHAUSTED' | 'CANCELLED'
    event_time TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    -- Контекст смерти (NULL если айсберг жив)
    survival_seconds INTEGER,           -- Как долго прожил
    total_volume_absorbed NUMERIC,      -- Сколько съел
    refill_count INTEGER,               -- Сколько раз пополнялся
    
    -- Исход для ML
    outcome TEXT,                       -- 'BREACH' | 'EXHAUSTION' | 'CANCEL' | NULL
    price_at_death NUMERIC,             -- Цена в момент смерти
    price_move_1h_after NUMERIC         -- % изменения через 1ч (заполняется позже)
);

-- Индексы для быстрого поиска
CREATE INDEX IF NOT EXISTS idx_lifecycle_symbol_price 
ON iceberg_lifecycle(symbol, price);

CREATE INDEX IF NOT EXISTS idx_lifecycle_outcome 
ON iceberg_lifecycle(outcome) 
WHERE outcome IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_lifecycle_time 
ON iceberg_lifecycle(event_time DESC);

-- Комментарии
COMMENT ON TABLE iceberg_lifecycle IS 
'Полная история жизни айсберга: от обнаружения до смерти. Для ML labeling.';

COMMENT ON COLUMN iceberg_lifecycle.outcome IS 
'Финальный исход айсберга. NULL = еще жив. BREACH = пробит. EXHAUSTION = съеден полностью. CANCEL = отменен трейдером.';

COMMENT ON COLUMN iceberg_lifecycle.price_move_1h_after IS 
'Процентное изменение цены через 1 час после смерти айсберга. Заполняется background task. Используется как регрессионный таргет для ML.';


-- ============================================================================
-- ТАБЛИЦА 2: FEATURE SNAPSHOT (Фичи для ML)
-- ============================================================================

CREATE TABLE IF NOT EXISTS iceberg_feature_snapshot (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    lifecycle_event_id UUID REFERENCES iceberg_lifecycle(id) ON DELETE CASCADE,
    
    -- Timestamp
    snapshot_time TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    -- === ORDERBOOK METRICS ===
    obi_value NUMERIC,              -- Order Book Imbalance
    ofi_value NUMERIC,              -- Order Flow Imbalance
    spread_bps NUMERIC,             -- Спред в basis points
    depth_ratio NUMERIC,            -- Bid depth / Ask depth (top 10 levels)
    
    -- === FLOW METRICS (CVD) ===
    whale_cvd NUMERIC,              -- CVD китов (>$100k trades)
    fish_cvd NUMERIC,               -- CVD рыб (<$1k trades)
    dolphin_cvd NUMERIC,            -- CVD дельфинов ($1k-$100k)
    
    whale_cvd_delta_5m NUMERIC,     -- Изменение whale CVD за 5 минут
    total_cvd NUMERIC,              -- Общий CVD (все сегменты)
    
    -- === DERIVATIVES METRICS ===
    futures_basis_apr NUMERIC,      -- Аннуализированный базис фьючерсов
    basis_state TEXT,               -- 'NORMAL' | 'OPTIMISTIC' | 'OVERHEATED' | 'EXTREME' | 'BACKWARDATION'
    
    options_skew NUMERIC,           -- Put IV - Call IV (25-delta)
    skew_state TEXT,                -- 'FEAR' | 'NEUTRAL' | 'GREED' | 'DIVERGENCE'
    
    total_gex NUMERIC,              -- Суммарная гамма-экспозиция
    dist_to_gamma_wall NUMERIC,     -- Расстояние до ближайшей GEX wall (%)
    gamma_wall_type TEXT,           -- 'CALL' | 'PUT' | NULL
    
    -- === PRICE METRICS ===
    current_price NUMERIC,          -- Текущая mid price
    twap_5m NUMERIC,                -- 5-минутная TWAP
    price_vs_twap_pct NUMERIC,      -- (Price - TWAP) / TWAP * 100
    
    volatility_1h NUMERIC,          -- Реализованная волатильность (1h)
    
    -- === SPOOFING DETECTION ===
    spoofing_score NUMERIC,         -- 0-100, вероятность манипуляции
    cancel_ratio_5m NUMERIC,        -- Доля отмененных ордеров за 5 мин
    
    -- === MARKET REGIME (добавим позже) ===
    trend_regime TEXT,              -- 'UPTREND' | 'DOWNTREND' | 'RANGING'
    volatility_regime TEXT          -- 'LOW' | 'MEDIUM' | 'HIGH'
);

-- Индексы
CREATE INDEX IF NOT EXISTS idx_snapshot_lifecycle 
ON iceberg_feature_snapshot(lifecycle_event_id);

CREATE INDEX IF NOT EXISTS idx_snapshot_time 
ON iceberg_feature_snapshot(snapshot_time DESC);

-- Foreign key для каскадного удаления
-- Если удаляется lifecycle event, удаляются и все его snapshots

-- Комментарии
COMMENT ON TABLE iceberg_feature_snapshot IS 
'Полный снимок ВСЕХ метрик в момент айсберг-события. Для Feature Importance анализа в XGBoost/CatBoost.';

COMMENT ON COLUMN iceberg_feature_snapshot.lifecycle_event_id IS 
'Связь с событием в iceberg_lifecycle. Один lifecycle event может иметь 0-N снапшотов (throttling).';

COMMENT ON COLUMN iceberg_feature_snapshot.whale_cvd_delta_5m IS 
'Критическая метрика: изменение позиций китов за 5 минут. Положительная дельта при падении цены = накопление.';

COMMENT ON COLUMN iceberg_feature_snapshot.dist_to_gamma_wall IS 
'Расстояние от текущей цены до ближайшей Gamma Wall. <0.5% = сильное влияние дилеров опционов на цену.';


-- ============================================================================
-- THROTTLING VIEW (для оптимизации записи)
-- ============================================================================

-- Представление для проверки "нужен ли новый снапшот?"
CREATE OR REPLACE VIEW should_create_snapshot AS
SELECT 
    l.id as lifecycle_id,
    l.event_type,
    l.event_time,
    COALESCE(
        EXTRACT(EPOCH FROM (NOW() - MAX(fs.snapshot_time))), 
        999999
    ) as seconds_since_last_snapshot
FROM iceberg_lifecycle l
LEFT JOIN iceberg_feature_snapshot fs ON fs.lifecycle_event_id = l.id
WHERE l.outcome IS NULL  -- Только живые айсберги
GROUP BY l.id, l.event_type, l.event_time;

COMMENT ON VIEW should_create_snapshot IS 
'Helper view: проверяет сколько прошло времени с последнего снапшота. Используется для throttling (не писать на каждый refill).';


-- ============================================================================
-- АНАЛИТИЧЕСКИЕ ЗАПРОСЫ (для проверки после запуска)
-- ============================================================================

-- Проверка 1: Статистика lifecycle событий
/*
SELECT 
    event_type,
    COUNT(*) as count,
    AVG(survival_seconds) FILTER (WHERE survival_seconds IS NOT NULL) as avg_survival_sec
FROM iceberg_lifecycle
GROUP BY event_type
ORDER BY count DESC;
*/

-- Проверка 2: Feature coverage (сколько % снапшотов имеют каждую метрику)
/*
SELECT 
    COUNT(*) as total_snapshots,
    COUNT(whale_cvd) * 100.0 / COUNT(*) as whale_cvd_coverage_pct,
    COUNT(futures_basis_apr) * 100.0 / COUNT(*) as basis_coverage_pct,
    COUNT(options_skew) * 100.0 / COUNT(*) as skew_coverage_pct,
    COUNT(spoofing_score) * 100.0 / COUNT(*) as spoofing_coverage_pct
FROM iceberg_feature_snapshot;
*/

-- Проверка 3: Outcome distribution
/*
SELECT 
    outcome,
    COUNT(*) as count,
    AVG(price_move_1h_after) FILTER (WHERE price_move_1h_after IS NOT NULL) as avg_price_move_pct
FROM iceberg_lifecycle
WHERE outcome IS NOT NULL
GROUP BY outcome;
*/


-- ============================================================================
-- ROLLBACK СКРИПТ (ОСТОРОЖНО!)
-- ============================================================================

/*
-- Удаляем view
DROP VIEW IF EXISTS should_create_snapshot;

-- Удаляем таблицы (CASCADE удалит и связанные snapshots)
DROP TABLE IF EXISTS iceberg_feature_snapshot CASCADE;
DROP TABLE IF EXISTS iceberg_lifecycle CASCADE;
*/
