#!/usr/bin/env python3
"""批量拉取生物医药公司数据，插入 documents 表"""
import sys, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, '/Users/els/scripts/invest_rag')

import akshare as ak
import pandas as pd
import httpx, json

SUPABASE_URL = "https://rgnncmgrumwjjgzyhmkt.supabase.co"
SB_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InJnbm5jbWdydW13ampnenlobWt0Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3NzczMTAwNSwiZXhwIjoyMDkzMzA3MDA1fQ.Rrq95ziEwcTmX1NNz62ZqGsN8GvyZcutqzAIwC7PkC8"
HEADERS = {"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}", "Content-Type": "application/json", "Prefer": "return=representation"}
EMBED_URL = "https://nottingham-protected-trivia-cassette.trycloudflare.com/embed"

def get_embedding(text: str) -> list:
    resp = httpx.post(EMBED_URL, json={"texts": [text]}, timeout=30)
    resp.raise_for_status()
    return resp.json()["embeddings"][0]

def supabase_insert(table: str, data: dict):
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    resp = httpx.post(url, headers=HEADERS, json=data, timeout=30)
    if resp.status_code not in (200, 201):
        print(f"  INSERT ERROR {resp.status_code}: {resp.text[:300]}")
    resp.raise_for_status()
    return resp.json()

def parse_num(s):
    """解析中文数字"""
    if not isinstance(s, str) or s in ('', 'False', 'True', 'nan', '-'):
        return s
    s = s.strip().replace(',', '').replace(' ', '')
    if '亿' in s:
        try: return f"{float(s.replace('亿','')):.2f}亿"
        except: return s
    elif '万' in s:
        try: return f"{float(s.replace('万',''))/10000:.2f}亿"
        except: return s
    return s

def build_summary(name, code, info_dict, financials, business_desc, tech_platforms, pipeline, financial_notes) -> str:
    info_str = "\n".join([f"- {k}: {v}" for k, v in info_dict.items() if v and str(v) not in ('nan', '')])
    parts = [f"公司概况：{name}（{code}）"]
    if info_str:
        parts.append(info_str)
    if business_desc:
        parts.append(f"\n主营业务：{business_desc}")
    if tech_platforms:
        parts.append(f"\n核心技术平台：{tech_platforms}")
    if pipeline:
        parts.append(f"\n主要产品/在研管线：{pipeline}")
    if financial_notes:
        parts.append(f"\n财务数据：{financial_notes}")
    return "\n".join(parts)

# ============ 公司列表 ============
companies = [
    {"name": "华东医药", "code": "000963", "market": "A股"},
    {"name": "甘李药业", "code": "603087", "market": "A股"},
    {"name": "药明康德", "code": "603259", "market": "A股"},
    {"name": "凯莱英", "code": "002821", "market": "A股"},
]

results = []

for co in companies:
    name = co["name"]
    code = co["code"]
    symbol = f"{code}.SZ" if co["market"] == "A股" else code
    print(f"\n{'='*50}")
    print(f"处理: {name} ({code})")

    # 基本信息
    try:
        info = ak.stock_individual_info_em(symbol=code)
        info_dict = {}
        if len(info) > 0:
            for _, row in info.iterrows():
                k, v = str(row.iloc[0]), str(row.iloc[1])
                if v and v not in ('nan', 'None', ''):
                    info_dict[k] = v
        print(f"  基本信息: {list(info_dict.keys())}")
    except Exception as e:
        print(f"  基本信息失败: {e}")
        info_dict = {}

    # 业务描述（同花顺）
    business_desc = ""
    try:
        desc = ak.stock_business_analysis(symbol=code, indicator="主营业务")
        if desc is not None and not desc.empty:
            business_desc = desc.iloc[0, 1] if desc.shape[1] > 1 else str(desc.iloc[0, 0])
            business_desc = business_desc[:500] if business_desc else ""
            print(f"  业务描述: {business_desc[:100]}...")
    except Exception as e:
        print(f"  业务描述失败: {e}")

    # 营收趋势
    financial_notes = ""
    try:
        rev = ak.stock_financial_benefit_ths(symbol=code, indicator="营业收入")
        if rev is not None and not rev.empty:
            # 取最近4期
            cols = [c for c in rev.columns if '报告日期' not in c and '股票代码' not in c]
            if cols:
                recent = rev[cols].iloc[:, :4]
                vals = []
                for _, row in recent.iterrows():
                    for v in row:
                        if str(v) not in ('nan', 'None', '', '-'):
                            vals.append(str(v)[:20])
                financial_notes = " | ".join(vals[:8])
                print(f"  营收: {financial_notes[:100]}")
    except Exception as e:
        print(f"  营收数据失败: {e}")

    # 技术平台 / 产品（从 info_dict 和招股数据推断）
    tech_platforms = info_dict.get("主营业务", "")[:300]
    if not tech_platforms:
        if "医药" in name or "生物" in name:
            tech_platforms = "创新药研发/生产"
        elif "药明" in name:
            tech_platforms = "CRO/CDMO医药外包服务"

    pipeline = info_dict.get("产品类型", "")[:300]
    if not pipeline and "甘李" in name:
        pipeline = "胰岛素（甘精胰岛素/门冬胰岛素/赖脯胰岛素）、GLP-1受体激动剂"
    elif not pipeline and "华东" in name:
        pipeline = "阿卡波糖（糖尿病）、器官移植免疫抑制剂、医美产品"
    elif not pipeline and "药明" in name:
        pipeline = "药物研发服务（CRO）、生产服务（CDMO）"

    # 构造摘要
    content = build_summary(name, code, info_dict, None, business_desc, tech_platforms, pipeline, financial_notes)
    print(f"  摘要长度: {len(content)} 字")

    if len(content) < 100:
        print(f"  数据不足，跳过")
        continue

    # 计算 embedding
    try:
        print(f"  计算 embedding...")
        embedding = get_embedding(content)
        print(f"  embedding 维度: {len(embedding)}")
    except Exception as e:
        print(f"  embedding 失败: {e}")
        continue

    # 插入 Supabase
    try:
        doc = {
            "company": f"{name} {code}",
            "content": content,
            "doc_type": "公司概况/业务摘要",
            "embedding": embedding,
        }
        result = supabase_insert("documents", doc)
        doc_id = result[0].get("id") if isinstance(result, list) and result else "?"
        print(f"  ✅ 插入成功 doc_id={doc_id}")
        results.append({"company": f"{name} {code}", "doc_id": doc_id, "content_preview": content[:150]})
    except Exception as e:
        print(f"  ❌ 插入失败: {e}")

print(f"\n\n{'='*50}")
print(f"完成! 共插入 {len(results)} 条:")
for r in results:
    print(f"  doc_id={r['doc_id']} {r['company']}")
    print(f"    {r['content_preview']}...")
