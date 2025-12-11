-- WHY: Добавляем GEX-контекст в ML данные (Task: GEX Integration)
-- Дата: 2025-01-08
-- Автор: Система GEX-интеграции

-- ============================================================================
-- ИЗМЕНЕНИЯ В ТАБЛИЦЕ iceberg_training_data
-- ============================================================================

-- 1. Добавляем поля для GEX-контекста
ALTER TABLE iceberg_training_data 
ADD COLUMN IF NOT EXISTS is_near_gamma_wall BOOLEAN DEFAULT FALSE,
ADD COLUMN IF NOT EXISTS gamma_wall_type TEXT;

-- 2. Создаем индекс для быстрого поиска gamma events
CREATE INDEX IF NOT EXISTS idx_gamma_events 
ON iceberg_training_data(is_near_gamma_wall) 
WHERE is_near_gamma_wall = TRUE;

-- 3. Комментарии для документации
COMMENT ON COLUMN iceberg_training_data.is_near_gamma_wall IS 
'True если айсберг был близко (±0.5%) к Gamma Wall от Deribit';

COMMENT ON COLUMN iceberg_training_data.gamma_wall_type IS 
'Тип стены: CALL (сопротивление) или PUT (поддержка). NULL если далеко от стен';

-- 4. Обновление существующих записей (если есть)
UPDATE iceberg_training_data 
SET is_near_gamma_wall = FALSE 
WHERE is_near_gamma_wall IS NULL;

-- ============================================================================
-- АНАЛИТИЧЕСКИЕ ЗАПРОСЫ (для проверки)
-- ============================================================================

-- Проверка: Сколько айсбергов были на Gamma Walls?
-- SELECT 
--     gamma_wall_type, 
--     COUNT(*) as count,
--     AVG(confidence) as avg_confidence
-- FROM iceberg_training_data
-- WHERE is_near_gamma_wall = TRUE
-- GROUP BY gamma_wall_type;

-- Проверка: Сравнение уверенности на/вне Gamma Walls
-- SELECT 
--     is_near_gamma_wall,
--     AVG(confidence) as avg_conf,
--     COUNT(*) as count
-- FROM iceberg_training_data
-- GROUP BY is_near_gamma_wall;

-- ============================================================================
-- ROLLBACK СКРИПТ (на случай отката)
-- ============================================================================

-- ВНИМАНИЕ: Используйте этот скрипт ТОЛЬКО если нужно откатить миграцию!

/*
-- Удаляем индекс
DROP INDEX IF EXISTS idx_gamma_events;

-- Удаляем колонки (ОСТОРОЖНО! Данные будут потеряны!)
ALTER TABLE iceberg_training_data 
DROP COLUMN IF EXISTS is_near_gamma_wall,
DROP COLUMN IF EXISTS gamma_wall_type;
*/
