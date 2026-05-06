#!/usr/bin/env python3
"""重建 biotech 赛道的真实 embedding 和 centroid"""
import sys, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, '/Users/els/scripts/invest_rag')

import httpx
import numpy as np

SUPABASE_URL = "https://rgnncmgrumwjjgzyhmkt.supabase.co"
SB_KEY = "eyJhbG...PkC8"  # 从 load_biotech.py 抄的，会在运行时从文件读取
EMBED_URL = "https://nottingham-protected-trivia-cassette.trycloudflare.com/embed"
BIOTECH_SECTOR_ID = "f57f795e-cf4d-4671-8d1a-3a77f12b742c"

# 从 load_biotech.py 读取完整 key
with open('/Users/els/scripts/invest_rag/load_biotech.py') as f:
    for line in f:
        if 'SB_KEY' in line and 'eyJ' in line:
            import re
            m = re.search(r'SB_KEY\s*=\s*["\']([^"\']+)["\']', line)
            if m:
                SB_KEY = m.group(1)
                break

HEADERS = {"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}", "Content-Type": "application/json", "Prefer": "return=representation"}
print(f"Using SB_KEY: {SB_KEY[:20]}...")

def get_embedding(text: str) -> list:
    resp = httpx.post(EMBED_URL, json={"texts": [text]}, timeout=30)
    resp.raise_for_status()
    return resp.json()["embeddings"][0]

# 华东医药、药明康德、甘李药业
companies = [
    ("华东医药 000963", "华东医药，总股本，总市值，流通市值，行业，上市时间，业务描述，营收数据"),
    ("甘李药业 603087", "甘李药业，总股本，流通股，总市值，流通市值，行业，上市时间，营收"),
    ("药明康德 603259", "药明康德，总股本，流通股，总市值，流通市值，行业，上市时间，营收"),
]

embeddings = []
for name, content in companies:
    print(f"Getting embedding for {name}...")
    emb = get_embedding(content)
    embeddings.append(emb)
    print(f"  dim={len(emb)}, sample={emb[:3]}")

# 计算 centroid
centroid = np.mean(embeddings, axis=0).tolist()
print(f"\nCentroid dim={len(centroid)}, sample={centroid[:3]}")

# 更新 sector_configs 的 centroid
print(f"\nUpdating sector_configs centroid for {BIOTECH_SECTOR_ID}...")
centroid_literal = "[" + ",".join(f"{x:.8f}" for x in centroid) + "]"
update_url = f"{SUPABASE_URL}/rest/v1/rpc/exec_sql"
sql = f"UPDATE sector_configs SET centroid = '{centroid_literal}'::vector WHERE id = '{BIOTECH_SECTOR_ID}'"
r = httpx.post(update_url, headers=HEADERS, json={"query": sql}, timeout=15)
print(f"  Update status: {r.status_code}")
if r.status_code not in (200, 204):
    print(f"  Error: {r.text[:300]}")
else:
    print("  ✅ Centroid updated!")

print(f"\nDone! Biotech sector centroid rebuilt with {len(embeddings)} companies.")
