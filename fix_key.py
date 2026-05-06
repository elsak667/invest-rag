#!/usr/bin/env python3
with open('/Users/els/scripts/invest_rag/sector_matcher.py', 'r') as f:
    content = f.read()

old = 'SUPABASE_SERVICE_ROLE_KEY = "eyJhbG...PkC8"'
new = 'SUPABASE_SERVICE_ROLE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InJnbm5jbWdydW13ampnenlobWt0Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3NzczMTAwNSwiZXhwIjoyMDkzMzA3MDA1fQ.Rrq95ziEwcTmX1NNz62ZqGsN8GvyZcutqzAIwC7PkC8"'

if old not in content:
    print('ERROR: old string not found')
    print('repr:', repr(old))
    import re
    m = re.search(r'SUPABASE_SERVICE_ROLE_KEY = \"[^\"]+\"', content)
    if m:
        print('Found instead:', repr(m.group(0)))
else:
    content = content.replace(old, new, 1)
    with open('/Users/els/scripts/invest_rag/sector_matcher.py', 'w') as f:
        f.write(content)
    print('SUCCESS')
