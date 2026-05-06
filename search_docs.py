import requests

VPS_URL = "https://nottingham-protected-trivia-cassette.trycloudflare.com/embed"
SUPABASE_URL = "https://rgnncmgrumwjjgzyhmkt.supabase.co"
SB_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InJnbm5jbWdydW13ampnenlobWt0Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3NzczMTAwNSwiZXhwIjoyMDkzMzA3MDA1fQ.Rrq95ziEwcTmX1NNz62ZqGsN8GvyZcutqzAIwC7PkC8"
HEADERS = {"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}"}

# create RPC function first
rpc_sql = """
CREATE OR REPLACE FUNCTION match_documents(query_embedding vector(384), query text, match_threshold float, match_count int)
RETURNS TABLE(id int, company text, doc_type text, source text, content text, similarity float)
LANGUAGE plpgsql
AS $$
BEGIN
  RETURN QUERY
  SELECT d.id, d.company, d.doc_type, d.source, d.content,
    1 - (d.embedding <=> query_embedding) AS similarity
  FROM documents d
  WHERE 1 - (d.embedding <=> query_embedding) > match_threshold
  ORDER BY d.embedding <=> query_embedding
  LIMIT match_count;
END;
$$;
"""
print("Creating RPC function...")
r = requests.post(f"{SUPABASE_URL}/rest/v1/rpc/exec_sql", headers={**HEADERS, "Content-Type": "application/json"}, json={"query": rpc_sql})
print(f"RPC creation status: {r.status_code}")
if r.status_code not in (200, 201):
    print(r.text[:300])

# test query
query = "公司团队背景"
print(f"\nQuery: {query}")

emb_resp = requests.post(VPS_URL, json={"texts": [query]})
query_emb = emb_resp.json()["embeddings"][0]

payload = {
    "query_embedding": query_emb,
    "query": query,
    "match_threshold": 0.3,
    "match_count": 5
}
r = requests.post(f"{SUPABASE_URL}/rest/v1/rpc/match_documents", headers={**HEADERS, "Content-Type": "application/json"}, json=payload)
print(f"Search status: {r.status_code}")
if r.status_code == 200:
    results = r.json()
    print(f"\n找到 {len(results)} 条结果:\n")
    for i, m in enumerate(results, 1):
        print(f"--- 结果 {i} (相似度 {m.get('similarity', 0):.3f}) ---")
        print(f"公司: {m.get('company')} | 类型: {m.get('doc_type')}")
        print(f"内容: {m.get('content', '')[:200]}")
        print()
else:
    print(r.text[:300])
