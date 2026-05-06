#!/usr/bin/env python3
"""批量拉取生物医药公司数据，插入 documents 表（hash向量占位）"""
import sys, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, '/Users/els/scripts/invest_rag')

import akshare as ak
import httpx, hashlib, json
import numpy as np

SUPABASE_URL = "https://rgnncmgrumwjjgzyhmkt.supabase.co"
SB_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InJnbm5jbWdydW13ampnenlobWt0Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3NzczMTAwNSwiZXhwIjoyMDkzMzA3MDA1fQ.Rrq95ziEwcTmX1NNz62ZqGsN8GvyZcutqzAIwC7PkC8"
HEADERS = {"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}", "Content-Type": "application/json", "Prefer": "return=representation"}

def content_to_vec(text: str) -> str:
    """基于内容hash生成确定性384维归一化向量（占位用）"""
    vec = np.zeros(384, dtype=np.float32)
    for i in range(384):
        h = hashlib.md5(f'{text}:{i}'.encode()).digest()
        vec[i] = (int.from_bytes(h[:2], 'big') % 1000) / 1000.0 - 0.5
    norm = np.linalg.norm(vec)
    if norm > 0:
        vec = vec / norm
    return '[' + ','.join(f'{x:.6f}' for x in vec.tolist()) + ']'

def supabase_insert(table: str, data: dict):
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    resp = httpx.post(url, headers=HEADERS, json=data, timeout=30)
    if resp.status_code not in (200, 201):
        print(f"  INSERT ERROR {resp.status_code}: {resp.text[:300]}")
        return None
    return resp.json()

# ============ 公司数据 ============
companies = [
    {
        "name": "华东医药", "code": "000963",
        "industry": "化学制药",
        "business": "医药制造业，主营糖尿病药物（阿卡波糖）、器官移植免疫抑制剂、医美产品及医疗器械",
        "products": "阿卡波糖（糖尿病）、他克莫司（器官移植）、医美透明质酸",
    },
    {
        "name": "甘李药业", "code": "603087",
        "industry": "生物制品",
        "business": "专注于胰岛素类似物研发生产的生物制药企业，主营重组胰岛素类似物",
        "products": "甘精胰岛素（长秀霖）、门冬胰岛素（锐秀霖）、赖脯胰岛素",
    },
    {
        "name": "药明康德", "code": "603259",
        "industry": "医疗服务/CRO/CDMO",
        "business": "全球领先的CRO/CDMO医药外包服务企业，为制药企业提供药物发现、临床前研究、临床研究、商业化生产全流程服务",
        "products": "药物研发服务（CRO）、生产工艺开发及验证（CDMO）、药物生产服务",
    },
    {
        "name": "凯莱英", "code": "002821",
        "industry": "医疗服务/CDMO",
        "business": "医药定制研发生产（CDMO），主营小分子药物中间体、原料药的工艺开发及商业化生产",
        "products": "小分子药物CDMO、原料药及中间体、临床阶段药物生产",
    },
]

# 营收数据（从 akshare 拉取）
def get_revenue(code):
    try:
        rev = ak.stock_financial_benefit_ths(symbol=code, indicator="营业收入")
        if rev is None or rev.empty:
            return ""
        cols = [c for c in rev.columns if '报告日期' not in c and '股票代码' not in c]
        if not cols:
            return ""
        recent = rev[cols].iloc[:, :4]
        vals = []
        for _, row in recent.iterrows():
            for v in row:
                s = str(v)
                if s not in ('nan', 'None', '', '-'):
                    vals.append(s[:20])
        return " | ".join(vals[:6])
    except Exception as e:
        return f"拉取失败: {e}"

# 拉市值/行业
def get_info(code):
    try:
        info = ak.stock_individual_info_em(symbol=code)
        d = {}
        if len(info) > 0:
            for _, row in info.iterrows():
                k, v = str(row.iloc[0]), str(row.iloc[1])
                if v and v not in ('nan', 'None', ''):
                    d[k] = v
        return d
    except:
        return {}

results = []
for co in companies:
    name, code = co["name"], co["code"]
    print(f"\n{name} ({code})")

    info = get_info(code)
    revenue = get_revenue(code)
    market_cap = info.get('总市值', '?')
    industry = info.get('行业', co["industry"])

    content = f"""公司名称：{name}（股票代码：{code}）
所属行业：{industry}
总市值：{market_cap}
主营业务：{co['business']}
主要产品：{co['products']}
财务数据（营收）：{revenue if revenue else '暂无'}"""

    print(f"  行业: {industry}  市值: {market_cap}")
    print(f"  营收: {revenue[:80] if revenue else '暂无'}")

    emb_str = content_to_vec(content)

    doc = {
        "company": f"{name} {code}",
        "content": content,
        "doc_type": "公司概况/业务摘要",
        "source": "akshare",
        "embedding": emb_str,
    }
    result = supabase_insert("documents", doc)
    if result:
        doc_id = result[0].get("id")
        print(f"  ✅ doc_id={doc_id}")
        results.append({"company": f"{name} {code}", "doc_id": doc_id, "industry": industry})
    else:
        print(f"  ❌ 失败")

print(f"\n\n完成! 共插入 {len(results)} 条:")
for r in results:
    print(f"  doc_id={r['doc_id']} {r['company']} [{r['industry']}]")
