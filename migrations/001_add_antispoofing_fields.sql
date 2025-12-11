-- WHY: Миграция для добавления антиспуфинг функциональности (Task 1.1)
-- Дата: 2025-01-08
-- Автор: Система антиспуфинга

-- ============================================================================
-- ИЗМЕНЕНИЯ В ТАБЛИЦЕ iceberg_levels
-- ============================================================================

-- 1. Добавляем новый статус CANCELLED
ALTER TYPE iceberg_status ADD VALUE IF NOT EXISTS 'CANCELLED';

-- 2. Добавляем поля для антиспуфинга
ALTER TABLE iceberg_levels 
ADD COLUMN IF NOT EXISTS spoofing_probability DOUBLE PRECISION DEFAULT 0.0,
ADD COLUMN IF NOT EXISTS refill_count INTEGER DEFAULT 0;

-- 3. Создаем таблицу для хранения контекста отмены
CREATE TABLE IF NOT EXISTS iceberg_cancellation_context (
    price NUMERIC PRIMARY KEY REFERENCES iceberg_levels(price) ON DELETE CASCADE,
    mid_price_at_cancel NUMERIC NOT NULL,
    distance_from_level_pct NUMERIC NOT NULL,
    price_velocity_5s NUMERIC NOT NULL,
    moving_towards_level BOOLEAN NOT NULL,
    volume_executed_pct NUMERIC NOT NULL,
    cancelled_at TIMESTAMPTZ DEFAULT NOW()
);

-- 4. Создаем индексы для оптимизации запросов
CREATE INDEX IF NOT EXISTS idx_spoofing_prob 
ON iceberg_levels(spoofing_probability DESC) 
WHERE spoofing_probability > 0.7;

CREATE INDEX IF NOT EXISTS idx_refill_count 
ON iceberg_levels(refill_count DESC);

CREATE INDEX IF NOT EXISTS idx_cancelled_levels 
ON iceberg_levels(status) 
WHERE status = 'CANCELLED';

-- ============================================================================
-- ОБНОВЛЕНИЕ СУЩЕСТВУЮЩИХ ЗАПИСЕЙ
-- ============================================================================

-- Устанавливаем значения по умолчанию для существующих айсбергов
UPDATE iceberg_levels 
SET spoofing_probability = 0.0, 
    refill_count = 0
WHERE spoofing_probability IS NULL;

-- ============================================================================
-- ROLLBACK СКРИПТ (на случай отката)
-- ============================================================================

-- ВНИМАНИЕ: Используйте этот скрипт ТОЛЬКО если нужно откатить миграцию!

/*
-- Удаляем индексы
DROP INDEX IF EXISTS idx_spoofing_prob;
DROP INDEX IF EXISTS idx_refill_count;
DROP INDEX IF EXISTS idx_cancelled_levels;

-- Удаляем таблицу контекста
DROP TABLE IF EXISTS iceberg_cancellation_context;

-- Удаляем колонки (ОСТОРОЖНО! Данные будут потеряны!)
ALTER TABLE iceberg_levels 
DROP COLUMN IF EXISTS spoofing_probability,
DROP COLUMN IF EXISTS refill_count;

-- Примечание: Удалить значение из ENUM невозможно в PostgreSQL
-- Статус CANCELLED останется, но не будет использоваться
*/
