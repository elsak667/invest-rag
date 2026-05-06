-- 赛道名修正：去掉"飞博激光 2相关"
ALTER TABLE sector_match_history ADD COLUMN IF NOT EXISTS sector_hint TEXT;

UPDATE sector_config
SET sector_name = '光纤激光器'
WHERE sector_name = '飞博激光 2相关 / 激光器';
