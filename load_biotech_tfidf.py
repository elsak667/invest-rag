#!/usr/bin/env python3
"""批量拉取生物医药公司数据，插入 documents 表（TF-IDF embedding 暂代）"""
import sys, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, '/Users/els/scripts/invest_rag')

import akshare as ak
import pandas as pd
import httpx, json, hashlib
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer

SUPABASE_URL = "https://rgnncmgrumwjjgzyhmkt.supabase.co"
SB_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InJnbm5jbWdydW13ampnenlobWt0Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3NzczMTAwNSwiZXhwIjoyMDkzMzA3MDA1fQ.Rrq95ziEwcTmX1NNz62ZqGsN8GvyZcutqzAIwC7PkC8"
HEADERS = {"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}", "Content-Type": "application/json", "Prefer": "return=representation"}

# TF-IDF 向量器（生成384维，兼容原系统）
def build_tfidf_vectorizer(corpus: list[str]):
    vec = TfidfVectorizer(max_features=384, ngram_range=(1, 2), min_df=1)
    vec.fit(corpus)
    return vec

def text_to_embedding(text: str, vectorizer) -> list:
    vec = vectorizer.transform([text]).toarray()[0]
    # L2归一化（模拟真实embedding）
    norm = np.linalg.norm(vec)
    if norm > 0:
        vec = vec / norm
    return vec.tolist()

def supabase_insert(table: str, data: dict):
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    resp = httpx.post(url, headers=HEADERS, json=data, timeout=30)
    if resp.status_code not in (200, 201):
        print(f"  INSERT ERROR {resp.status_code}: {resp.text[:300]}")
    resp.raise_for_status()
    return resp.json()

def build_summary(name, code, info_dict, business_desc, financial_notes) -> str:
    parts = [f"公司名称：{name}（股票代码：{code}）"]
    parts.append(f"所属行业：{info_dict.get('行业', '生物医药')}")
    parts.append(f"总市值：{info_dict.get('总市值', '未知')}")
    parts.append(f"主营业务：{business_desc if business_desc else info_dict.get('主营业务', '生物医药研发与生产')}")
    if financial_notes:
        parts.append(f"财务数据：{financial_notes}")
    return "\n".join(parts)

# ============ 公司数据 ============
companies = [
    {"name": "华东医药", "code": "000963"},
    {"name": "甘李药业", "code": "603087"},
    {"name": "药明康德", "code": "603259"},
    {"name": "凯莱英", "code": "002821"},
]

all_summaries = []

print("=== 拉取公司数据 ===")
for co in companies:
    name, code = co["name"], co["code"]
    print(f"\n{name} ({code})")

    # 基本信息
    try:
        info = ak.stock_individual_info_em(symbol=code)
        info_dict = {}
        if len(info) > 0:
            for _, row in info.iterrows():
                k, v = str(row.iloc[0]), str(row.iloc[1])
                if v and v not in ('nan', 'None', ''):
                    info_dict[k] = v
        print(f"  基本信息: 行业={info_dict.get('行业','?')} 市值={info_dict.get('总市值','?')}")
    except Exception as e:
        print(f"  基本信息失败: {e}")
        info_dict = {}

    # 营收
    financial_notes = ""
    try:
        rev = ak.stock_financial_benefit_ths(symbol=code, indicator="营业收入")
        if rev is not None and not rev.empty:
            cols = [c for c in rev.columns if '报告日期' not in c and '股票代码' not in c]
            if cols:
                recent = rev[cols].iloc[:, :4]
                vals = []
                for _, row in recent.iterrows():
                    for v in row:
                        s = str(v)
                        if s not in ('nan', 'None', '', '-'):
                            vals.append(s[:20])
                financial_notes = " | ".join(vals[:6])
                print(f"  营收: {financial_notes[:80]}")
    except Exception as e:
        print(f"  营收: {e}")

    # 业务描述
    business_desc = ""
    # 从 info_dict 构造业务描述
    industry = info_dict.get('行业', '生物医药')
    if name == "华东医药":
        business_desc = "医药制造业，主营糖尿病药物（阿卡波糖）、器官移植免疫抑制剂、医美产品及医疗器械"
    elif name == "甘李药业":
        business_desc = "生物制品行业，专注胰岛素类似物研发生产，主营甘精胰岛素、门冬胰岛素、赖脯胰岛素等"
    elif name == "药明康德":
        business_desc = "CRO/CDMO医药外包服务，为全球制药企业提供药物发现、临床前研究、临床研究、生产服务"
    elif name == "凯莱英":
        business_desc = "CDMO医药定制研发生产，主营小分子药物中间体、原料药的工艺开发及商业化生产"

    content = build_summary(name, code, info_dict, business_desc, financial_notes)
    print(f"  摘要长度: {len(content)} 字")
    all_summaries.append(content)

# ============ 构建 TF-IDF ============
print("\n=== 构建 TF-IDF 向量器 ===")
vectorizer = build_tfidf_vectorizer(all_summaries)
print(f"  词汇表大小: {len(vectorizer.vocabulary_)}")
print(f"  向量维度: 384")

# ============ 插入 Supabase ============
print("\n=== 插入 documents ===")
results = []
for co, content in zip(companies, all_summaries):
    name, code = co["name"], co["code"]
    emb = text_to_embedding(content, vectorizer)
    print(f"  {name}: embedding sum={sum(emb):.4f}, nonzero={sum(1 for x in emb if abs(x)>0.01)}")

    doc = {
        "company": f"{name} {code}",
        "content": content,
        "doc_type": "公司概况/业务摘要",
        "source": "akshare",
        "embedding": '[' + ','.join(str(x) for x in emb) + ']',
    }
    try:
        result = supabase_insert("documents", doc)
        doc_id = result[0].get("id") if isinstance(result, list) and result else "?"
        print(f"  ✅ doc_id={doc_id}")
        results.append({"company": f"{name} {code}", "doc_id": doc_id})
    except Exception as e:
        print(f"  ❌ 失败: {e}")

print(f"\n完成! 共插入 {len(results)} 条")
for r in results:
    print(f"  {r['company']}: doc_id={r['doc_id']}")
