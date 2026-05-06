import sys, os, json, httpx

ROLE_KEY = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InJnbm5jbWdydW13ampnenlobWt0Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3NzczMTAwNSwiZXhwIjoyMDkzMzA3MDA1fQ.Rrq95ziEwcTmX1NNz62ZqGsN8GvyZcutqzAIwC7PkC8'
os.environ['SUPABASE_SERVICE_ROLE_KEY'] = ROLE_KEY
sys.path.insert(0, '/Users/els/scripts/invest_rag')

from sector_matcher import match_sector, get_all_sectors, build_sector_fingerprint
import numpy as np

# Get 迈威生物 embedding
HEADERS = {'apikey': ROLE_KEY, 'Authorization': f'Bearer {ROLE_KEY}'}
r = httpx.get('https://rgnncmgrumwjjgzyhmkt.supabase.co/rest/v1/documents?id=eq.151&select=embedding', headers=HEADERS, timeout=10)
emb = json.loads(r.json()[0]['embedding'])
print('迈威生物 embedding dim:', len(emb))

sectors = get_all_sectors()
print('Sectors:', [(s['sector_name'], len(s.get('member_companies',[]))) for s in sectors])

# Test build_sector_fingerprint for biotech
biotech = [s for s in sectors if '医药' in s['sector_name']][0]
fp = build_sector_fingerprint(biotech)
print('Biotech fingerprint centroid:', fp.get('centroid') is not None, 'doc_count:', fp.get('doc_count'))
print('Biotech members with docs:', fp.get('member_companies', []))

# Manual cosine similarity test
if fp.get('centroid'):
    from sector_matcher import cosine_similarity
    sim = cosine_similarity(emb, fp['centroid'])
    print(f'Cosine sim with biotech centroid: {sim:.4f}')
