#!/usr/bin/env python3
"""
批量获取所有A股财务数据 → Supabase stock_profiles
全量抓取 → 一次性批量写入
"""
import asyncio
import base64
import subprocess
import time
from datetime import datetime

import akshare as ak

MAX_CONCURRENCY = 30

def parse_num(s):
    """Parse number from string/value. Returns SQL literal string or 'NULL'."""
    if s is None:
        return 'NULL'
    if isinstance(s, (int, float)):
        return str(float(s))
    s = str(s).strip().replace(',', '')
    if s in ('-', '', 'None', 'nan'):
        return 'NULL'
    try:
        return str(float(s))
    except:
        pass
    for unit, mul in [('万亿', 1e12), ('亿', 1e8), ('万', 1e4), ('千', 1e3)]:
        if unit in s:
            try:
                return str(float(s.replace(unit, '')) * mul)
            except:
                pass
    try:
        return str(float(s))
    except:
        return 'NULL'


def fetch_one(code: str) -> dict | None:
    try:
        info = ak.stock_individual_info_em(symbol=code)
        info_dict = dict(zip(info.iloc[:, 0], info.iloc[:, 1]))
        name = info_dict.get('股票简称', info_dict.get('名称', code))
        industry_raw = info_dict.get('行业', None)

        benefit = ak.stock_financial_benefit_ths(symbol=code)
        debt = ak.stock_financial_debt_ths(symbol=code)
        if benefit.empty or debt.empty:
            return None

        latest = benefit.iloc[0]
        report_date = str(latest.iloc[0])[:10]

        col_map = {c: i for i, c in enumerate(benefit.columns)}
        dcol_map = {c: i for i, c in enumerate(debt.columns)}

        return {
            'stock_code': code,
            'stock_name': name,
            'industry': industry_raw if industry_raw else None,
            'market_cap': parse_num(info_dict.get('总市值', None)),
            'pe_ratio': parse_num(info_dict.get('市盈率(动态)', None)),
            'pb_ratio': parse_num(info_dict.get('市净率', None)),
            'revenue': parse_num(latest.iloc[col_map['一、营业总收入']]),
            'net_profit': parse_num(latest.iloc[col_map['五、净利润']]),
            'total_assets': parse_num(debt.iloc[0].iloc[dcol_map['*资产合计']]),
            'total_liabilities': parse_num(debt.iloc[0].iloc[dcol_map['*负债合计']]),
            'report_date': report_date,
        }
    except Exception as e:
        return None


async def fetch_all_concurrent(stocks: list[str]) -> list[dict]:
    sem = asyncio.Semaphore(MAX_CONCURRENCY)

    async def safe_fetch(code):
        async with sem:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, fetch_one, code)

    results = []
    t0 = time.time()
    for i in range(0, len(stocks), 200):
        batch = stocks[i:i+200]
        tasks = [safe_fetch(c) for c in batch]
        batch_results = await asyncio.gather(*tasks)
        valid = [r for r in batch_results if r is not None]
        results.extend(valid)
        elapsed = time.time() - t0
        print(f"  抓取 {min(i+200, len(stocks))}/{len(stocks)} "
              f"({len(valid)}有效, {elapsed:.0f}s)")
    return results


def batch_upsert(data_list: list[dict]) -> bool:
    """通过 supabase db query --linked 批量 upsert，文字用 base64 编码避免转义问题."""
    if not data_list:
        return True

    values_parts = []
    for d in data_list:
        # base64 编码中文字段，彻底避免 SQL 注入和引号问题
        name_b64 = base64.b64encode(d['stock_name'].encode('utf-8')).decode('ascii')
        ind_b64 = base64.b64encode(d['industry'].encode('utf-8')).decode('ascii') if d['industry'] else ''
        name_sql = f"CONVERT_FROM(decode('{name_b64}', 'base64'), 'UTF8')"
        ind_sql = f"CONVERT_FROM(decode('{ind_b64}', 'base64'), 'UTF8')" if ind_b64 else 'NULL'
        values_parts.append(
            f"('{d['stock_code']}', "
            f"{name_sql}, "
            f"{ind_sql}, "
            f"{d['market_cap']}, {d['pe_ratio']}, {d['pb_ratio']}, "
            f"{d['revenue']}, {d['net_profit']}, "
            f"{d['total_assets']}, {d['total_liabilities']}, "
            f"'{d['report_date']}', NOW())"
        )

    sql = (
        "INSERT INTO stock_profiles "
        "(stock_code, stock_name, industry, market_cap, pe_ratio, pb_ratio, "
        "revenue, net_profit, total_assets, total_liabilities, latest_report_date, updated_at) "
        "VALUES\n" + ",\n".join(values_parts) + "\n"
        "ON CONFLICT (stock_code) DO UPDATE SET\n"
        "  stock_name = EXCLUDED.stock_name,\n"
        "  industry = EXCLUDED.industry,\n"
        "  market_cap = EXCLUDED.market_cap,\n"
        "  pe_ratio = EXCLUDED.pe_ratio,\n"
        "  pb_ratio = EXCLUDED.pb_ratio,\n"
        "  revenue = EXCLUDED.revenue,\n"
        "  net_profit = EXCLUDED.net_profit,\n"
        "  total_assets = EXCLUDED.total_assets,\n"
        "  total_liabilities = EXCLUDED.total_liabilities,\n"
        "  latest_report_date = EXCLUDED.latest_report_date,\n"
        "  updated_at = EXCLUDED.updated_at;"
    )

    import tempfile, os
    with tempfile.NamedTemporaryFile(mode='w', suffix='.sql', delete=False, encoding='utf-8') as f:
        f.write(sql)
        sql_file = f.name

    try:
        r = subprocess.run(
            ['supabase', 'db', 'query', '--linked', '-f', sql_file],
            capture_output=True, text=True, timeout=120,
            cwd='/Users/els/scripts/invest_rag'
        )
        if r.returncode == 0:
            print(f"  写入成功 {len(data_list)} 条")
            return True
        else:
            # 失败时打印真实错误
            err = (r.stderr + r.stdout)[:500]
            print(f"  SQL失败: {err}")
            return False
    except Exception as e:
        print(f"  执行异常: {e}")
        return False
    finally:
        os.unlink(sql_file)


async def main():
    print("获取A股列表...")
    df = ak.stock_info_a_code_name()
    stocks = df['code'].tolist()
    print(f"共 {len(stocks)} 支股票\n")

    print("阶段一：并发抓取财务数据...")
    t0 = time.time()
    results = await fetch_all_concurrent(stocks)
    fetch_time = time.time() - t0
    print(f"\n抓取完成: {len(results)} 条有效数据, 耗时 {fetch_time:.0f}s\n")

    print("阶段二：批量写入 Supabase (每批200条)...")
    write_start = time.time()
    for i in range(0, len(results), 200):
        batch = results[i:i+200]
        ok = batch_upsert(batch)
        if not ok:
            print(f"  第 {i//200 + 1} 批写入失败")
        time.sleep(0.5)

    write_time = time.time() - write_start
    total_time = time.time() - t0
    print(f"\n写入完成: 耗时 {write_time:.0f}s")
    print(f"总计: {len(results)} 条, 总耗时 {total_time:.0f}s")


if __name__ == '__main__':
    asyncio.run(main())
