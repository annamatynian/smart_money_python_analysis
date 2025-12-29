-- =========================================================================
-- MIGRATION 006: Add Absorbed Iceberg Volumes
-- =========================================================================
-- WHY: ML модель должна видеть НЕ ТОЛЬКО пассивную ликвидность (wall_*_vol),
--      но и ИСПОЛНЕННУЮ ликвидность (absorbed_*_vol) для предсказания пробоя.
--
-- Проблема (из Gemini Review):
-- - wall_whale_vol показывает "сколько айсбергов СТОИТ в стакане" (snapshot)
-- - absorbed_whale_vol покажет "сколько айсбергов было СЪЕДЕНО за свечу" (flow)
--
-- Для ML предсказания пробоя критичны обе метрики:
-- - Высокий wall_vol + низкий absorbed_vol = Сильный уровень (отскок)
-- - Высокий wall_vol + высокий absorbed_vol = Слабеющий уровень (пробой близко)
-- =========================================================================

-- === ADD NEW COLUMNS ===

ALTER TABLE smart_candles
ADD COLUMN IF NOT EXISTS absorbed_whale_vol DOUBLE PRECISION DEFAULT 0,
ADD COLUMN IF NOT EXISTS absorbed_dolphin_vol DOUBLE PRECISION DEFAULT 0,
ADD COLUMN IF NOT EXISTS absorbed_total_vol DOUBLE PRECISION DEFAULT 0;

-- === КОММЕНТАРИИ ===

COMMENT ON COLUMN smart_candles.absorbed_whale_vol IS 
'Суммарный объем ИСПОЛНЕННЫХ whale айсбергов за свечу (>$100k trades).
WHY: Показывает агрессивность атаки на уровни.
High absorbed + high wall = Истощение защиты (скоро пробой).';

COMMENT ON COLUMN smart_candles.absorbed_dolphin_vol IS 
'Суммарный объем ИСПОЛНЕННЫХ dolphin айсбергов за свечу ($1k-$100k).
WHY: Средний класс - промежуточные уровни защиты.';

COMMENT ON COLUMN smart_candles.absorbed_total_vol IS 
'Общий объем всех исполненных айсбергов за свечу (whale + dolphin).
WHY: Агрегированная метрика для быстрой оценки.';

-- === ИНДЕКС ДЛЯ АНАЛИТИКИ ===

-- WHY: Быстрый поиск свечей с крупным поглощением (для исследования паттернов)
CREATE INDEX IF NOT EXISTS idx_smart_candles_absorbed 
ON smart_candles (absorbed_total_vol DESC) 
WHERE absorbed_total_vol > 0;

-- === DATA MIGRATION (ОПЦИОНАЛЬНО) ===
-- WHY: Если нужно пересчитать absorbed volumes для исторических свечей,
--      это делается через Python скрипт (не здесь).
--      SQL миграция обновляет только схему.
