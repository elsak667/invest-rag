import sys, os, json, httpx

ROLE_KEY = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InJnbm5jbWdydW13ampnenlobWt0Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3NzczMTAwNSwiZXhwIjoyMDkzMzA3MDA1fQ.Rrq95ziEwcTmX1NNz62ZqGsN8GvyZcutqzAIwC7PkC8'
HEADERS = {'apikey': ROLE_KEY, 'Authorization': f'Bearer {ROLE_KEY}', 'Content-Type': 'application/json'}

os.environ['SUPABASE_SERVICE_ROLE_KEY'] = ROLE_KEY

sys.path.insert(0, '/Users/els/scripts/invest_rag')

# Get 迈威生物 embedding
r = httpx.get('https://rgnncmgrumwjjgzyhmkt.supabase.co/rest/v1/documents?id=eq.151&select=embedding', headers=HEADERS, timeout=10)
emb = json.loads(r.json()[0]['embedding'])
print('迈威生物 embedding dim:', len(emb))

from sector_matcher import match_sector, get_all_sectors

sectors = get_all_sectors()
print('Sectors:', [(s['sector_name'], len(s.get('member_companies',[]))) for s in sectors])

result = match_sector(emb, sectors)
print('Match result:', result)
