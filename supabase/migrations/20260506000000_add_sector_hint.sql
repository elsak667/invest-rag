-- 添加 sector_hint 字段
ALTER TABLE sector_match_history ADD COLUMN IF NOT EXISTS sector_hint TEXT;
