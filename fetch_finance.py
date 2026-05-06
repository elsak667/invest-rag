import requests

_session = requests.Session()
_session.trust_env = False  # 禁用代理，避免 SSL 问题

def parse_num(s):
    """Parse Chinese number strings like '853.10亿', '6.03万', '6.03万亿' into float"""
    if not isinstance(s, str) or s in ('', 'False', 'True', 'nan'):
        return 0.0
    s = s.strip().replace(',', '').replace(' ', '')
    if not s:
        return 0.0
    if '万亿' in s:
        try:
            return float(s.replace('万亿', '')) * 1e12
        except:
            return 0.0
    elif '亿' in s:
        try:
            return float(s.replace('亿', '')) * 1e8
        except:
            return 0.0
    elif '万' in s:
        try:
            return float(s.replace('万', '')) * 1e4
        except:
            return 0.0
    else:
        try:
            return float(s)
        except:
            return 0.0

def upsert_stock_profile(data):
    check = _session.get(
        f"{SUPABASE_URL}/rest/v1/stock_profiles?stock_code=eq.{data['stock_code']}&select=id",
        headers=HEADERS
    )
    existing = check.json() if check.status_code == 200 else []
    if existing:
        r = _session.patch(
            f"{SUPABASE_URL}/rest/v1/stock_profiles?id=eq.{existing[0]['id']}",
            headers={**HEADERS}, json=data
        )
        print(f"  Updated id={existing[0]['id']}")
    else:
        r = _session.post(f"{SUPABASE_URL}/rest/v1/stock_profiles", headers=HEADERS, json=data)
        print(f"  Inserted new record")
    if r.status_code not in (200, 201, 204):
        print(f"  ERROR {r.status_code}: {r.text[:200]}")

def fetch_stock(symbol):
    print(f"\n=== Fetching {symbol} ===\n")
    
    # 1. 基本信息：市值、行业、股票名称
    print("Fetching basic info...")
    try:
        df_info = ak.stock_individual_info_em(symbol=symbol)
        info = {}
        # 修复列名读取问题
        for idx, row in df_info.iterrows():
            item = str(row.iloc[0]).strip()
            val = str(row.iloc[1]).strip()
            info[item] = val
        print(f"  Raw info keys: {list(info.keys())}")
    except Exception as e:
        print(f"  info error: {e}")
        info = {}
    
    stock_name = info.get('股票简称', symbol)
    industry = info.get('行业', '')
    
    # 解析市值（已经是数字）
    market_cap_raw = info.get('总市值', '0')
    try:
        market_cap = float(str(market_cap_raw).replace(',', ''))
    except:
        market_cap = 0.0
    
    # 如果市值是0，用流通市值试试
    if market_cap == 0:
        try:
            market_cap = float(str(info.get('流通市值', '0')).replace(',', ''))
        except:
            market_cap = 0.0
    
    print(f"  Name: {stock_name}, Industry: {industry}, Market Cap: {market_cap:,.0f}")
    
    # 2. 财务数据：利润表
    revenue = net_profit = 0.0
    report_date = ''
    try:
        df_benefit = ak.stock_financial_benefit_ths(symbol=symbol)
        if df_benefit is not None and not df_benefit.empty:
            latest = df_benefit.iloc[0]
            revenue = parse_num(str(latest.get('*营业总收入', 0)))
            net_profit = parse_num(str(latest.get('*净利润', 0)))
            report_date = str(latest.get('报告期', ''))[:10]
            print(f"  Revenue: {revenue:,.0f}, Net Profit: {net_profit:,.0f} (as of {report_date})")
    except Exception as e:
        print(f"  benefit error: {e}")
    
    # 3. 资产负债表
    total_assets = total_liabilities = 0.0
    try:
        df_debt = ak.stock_financial_debt_ths(symbol=symbol)
        if df_debt is not None and not df_debt.empty:
            latest = df_debt.iloc[0]
            total_assets = parse_num(str(latest.get('*资产合计', 0)))
            total_liabilities = parse_num(str(latest.get('*负债合计', 0)))
            print(f"  Total Assets: {total_assets:,.0f}, Liabilities: {total_liabilities:,.0f}")
    except Exception as e:
        print(f"  debt error: {e}")
    
    # 4. 计算 PE/PB（如果有市值和净利润）
    pe_ratio = pb_ratio = 0.0
    if market_cap > 0 and net_profit > 0:
        pe_ratio = market_cap / net_profit
    if market_cap > 0 and total_assets > 0:
        # PB = market_cap / book_value
        book_value = total_assets - total_liabilities
        if book_value > 0:
            pb_ratio = market_cap / book_value
    
    data = {
        "stock_code": symbol,
        "stock_name": stock_name,
        "industry": industry,
        "market_cap": market_cap,
        "pe_ratio": round(pe_ratio, 2) if pe_ratio > 0 else 0,
        "pb_ratio": round(pb_ratio, 2) if pb_ratio > 0 else 0,
        "revenue": revenue,
        "net_profit": net_profit,
        "total_assets": total_assets,
        "total_liabilities": total_liabilities,
        "latest_report_date": report_date,
    }
    
    print(f"\n=== Summary: {stock_name} ({symbol}) ===")
    print(f"  Industry:    {data['industry']}")
    print(f"  Market Cap:  {data['market_cap']:>18,.0f} 元 ({data['market_cap']/1e8:.2f}亿)")
    print(f"  PE(TTM):     {data['pe_ratio']}")
    print(f"  PB(MRQ):     {data['pb_ratio']}")
    print(f"  Revenue:     {data['revenue']:>18,.0f} 元 ({data['revenue']/1e8:.2f}亿)")
    print(f"  Net Profit: {data['net_profit']:>18,.0f} 元 ({data['net_profit']/1e8:.2f}亿)")
    print(f"  Total Assets:{data['total_assets']:>18,.0f}")
    print(f"  Liabilities: {data['total_liabilities']:>18,.0f}")
    
    print(f"\nSaving to Supabase...")
    upsert_stock_profile(data)
    print("Done.")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 fetch_finance.py <stock_code>")
        print("  e.g. python3 fetch_finance.py 600519")
        sys.exit(1)
    symbol = sys.argv[1].strip()
    if not (symbol.isdigit() and len(symbol) == 6):
        print("Stock code must be 6 digits (e.g. 600519, 000001)")
        sys.exit(1)
    fetch_stock(symbol)
