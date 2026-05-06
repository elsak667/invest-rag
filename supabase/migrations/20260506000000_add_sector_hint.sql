-- 添加 sector_hint 字段
ALTER TABLE sector_match_history ADD COLUMN IF NOT EXISTS sector_hint TEXT;

-- 赛道名修正：去掉"飞博激光 2相关"
UPDATE sector_config
SET sector_name = '光纤激光器'
WHERE sector_name = '飞博激光 2相关 / 激光器';

-- 成员公司名修正：去掉"2"后缀
UPDATE sector_config
SET member_companies = ARRAY(SELECT REPLACE(unnest, ' 2', '') FROM unnest(member_companies))
WHERE '飞博激光 2' = ANY(member_companies);
