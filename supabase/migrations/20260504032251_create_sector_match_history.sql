-- 赛道匹配历史记录表
CREATE TABLE IF NOT EXISTS sector_match_history (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    company VARCHAR(200) NOT NULL,
    sector_config_id UUID REFERENCES sector_config(id) ON DELETE SET NULL,
    suggested_sector_name VARCHAR(200),
    confidence_score FLOAT,
    tech_score FLOAT,
    apps_score FLOAT,
    pos_score FLOAT,
    cust_score FLOAT,
    tech_hits TEXT[],
    apps_hits TEXT[],
    extracted_tech TEXT[],
    extracted_apps TEXT[],
    extracted_customers TEXT[],
    judgment VARCHAR(20) DEFAULT 'pending',
    judgment_note TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    judged_at TIMESTAMPTZ
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_history_company ON sector_match_history(company);
CREATE INDEX IF NOT EXISTS idx_history_judgment ON sector_match_history(judgment);
CREATE INDEX IF NOT EXISTS idx_history_sector ON sector_match_history(sector_config_id);

-- RLS
ALTER TABLE sector_match_history ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Allow all" ON sector_match_history FOR ALL USING (true) WITH CHECK (true);
