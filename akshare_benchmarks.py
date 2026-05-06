#!/usr/bin/env python3
"""
akshare 市场基准数据采集脚本
功能：从 akshare 抓取行业财务均值、可比公司估值，入 Supabase

用法：
  python akshare_benchmarks.py              # 全量采集（首次）
  python akshare_benchmarks.py --dry-run   # 只打印，不写入
  python akshare_benchmarks.py --industries 半导体 激光设备   # 指定行业

数据来源：
  - akshare (东方财富、同花顺等实时/历史数据)
  - 权威性：A股上市公司公告 → 公开可查

Supabase 依赖：
  需要 SUPABASE_SERVICE_ROLE_KEY 环境变量（或本地 .env）
  写入 documents 表（doc_type='market_benchmark'）
"""

import os
import re
import json
import time
import subprocess
from datetime import datetime, date
from typing import Optional

import requests
import pandas as pd
import akshare as ak

# ── 配置 ────────────────────────────────────────────────────────────────────

SUPABASE_URL = "https://rgnncmgrumwjjgzyhmkt.supabase.co"
SB_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
SB_ANON_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InJnbm5jbWdydW13ampnenlobWt0Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3Nzc3MzEwMDUsImV4cCI6MjA5MzMwNzAwNX0.HvWIalLgc7qMNIR_RRGTvmg1nZTQenatyDvRKBz2Rwg"

# 如果没有 service_role key，尝试用 anon key（仅限读）
if not SB_KEY:
    SB_KEY = SB_ANON_KEY
    print("⚠️ 未设置 SERVICE_ROLE_KEY，使用 ANON_KEY（只读模式，不写入）")

# 目标行业列表（东方财富板块名称）
INDUSTRIES = [
    "半导体",
    "集成电路制造",
    "数字芯片设计",
    "模拟芯片设计",
    "半导体设备",
    "集成电路封测",
    "半导体材料",
    "激光设备",
    "光学光电子",
    "消费电子",
    "电子",
]

# 可比公司（直接抓个股估值）
COMPARABLE_STOCKS = [
    ("688256", "寒武纪",    "科创板/数字芯片设计/AI芯片"),
    ("603290", "斯达半导",  "主板/半导体/IGBT"),
    ("688220", "翱捷科技",  "科创板/蜂窝基带芯片"),
    ("688041", "海光信息",  "科创板/处理器芯片"),
    ("688036", "拓荆科技",  "科创板/半导体设备"),
    ("688521", "芯原股份",  "科创板/芯片设计服务"),
    ("688345", "博力微",    "科创板/芯片设计"),
    ("301087", "瑞可达",    "创业板/连接器"),
    ("002049", "紫光国微",  "主板/半导体/FPGA"),
    ("600745", "闻泰科技",  "主板/半导体/ODM"),
    ("688167", "炬光科技",  "科创板/激光雷达光学元件"),
    ("688322", "奥比中光",  "科创板/3D视觉传感器"),
]

# ── Supabase 操作 ─────────────────────────────────────────────────────────────

def sb_headers(is_write: bool = False) -> dict:
    h = {
        "apikey": SB_KEY,
        "Authorization": f"Bearer {SB_KEY}",
        "Content-Type": "application/json",
    }
    if is_write:
        h["Prefer"] = "return=representation"
    return h

def sb_get(query: str) -> list:
    """通过 REST API 查询（兼容 anon key）"""
    url = f"{SUPABASE_URL}/rest/v1/{query}"
    r = requests.get(url, headers=sb_headers(), timeout=20)
    if r.ok:
        return r.json()
    return []

def sb_post(payload: dict) -> bool:
    """写入单条记录（需要 service_role）"""
    url = f"{SUPABASE_URL}/rest/v1/documents"
    r = requests.post(url, headers=sb_headers(is_write=True), json=payload, timeout=20)
    return r.status_code in (200, 201)

def sb_delete(query: str) -> int:
    """删除满足条件的记录（需要 service_role）"""
    url = f"{SUPABASE_URL}/rest/v1/documents?{query}"
    r = requests.delete(url, headers=sb_headers(), timeout=20)
    return r.status_code

# ── 数据采集 ─────────────────────────────────────────────────────────────────

def get_industry_name() -> pd.DataFrame:
    """获取所有行业板块名称"""
    df = ak.stock_board_industry_name_em()
    return df[df["板块名称"].notna()]

def get_industry_members(industry: str) -> list[dict]:
    """获取行业成分股"""
    try:
        df = ak.stock_board_industry_cons_em(symbol=industry)
        return df[["代码", "名称"]].to_dict("records")
    except Exception as e:
        print(f"  ⚠️ 获取成分股失败 [{industry}]: {e}")
        return []

def get_stock_financial_latest(stock_code: str) -> Optional[dict]:
    """抓取个股最新一期年报财务指标（优先12-31日期的年报，其次最新季度）"""
    try:
        df = ak.stock_financial_analysis_indicator(symbol=stock_code, start_year="2022")
        if df is None or len(df) == 0:
            return None

        # 优先取年报（12-31）
        df["日期_str"] = df["日期"].astype(str)
        annual = df[df["日期_str"].str.match(r".*-12-31")]
        target = annual.head(1)
        if len(target) == 0:
            # 没有年报，取最新一期
            target = df.head(1)

        row = target.iloc[0]
        gm = row.get("销售毛利率(%)")
        nm = row.get("销售净利率(%)")
        roe = row.get("净资产收益率(%)")
        roe_w = row.get("加权净资产收益率(%)")  # 加权ROE更准确
        rev_growth = row.get("主营业务收入增长率(%)")
        profit_growth = row.get("净利润增长率(%)")
        asset_turnover = row.get("总资产周转率(次)")  # 资产周转率
        current_ratio = row.get("流动比率")           # 流动比率
        debt_ratio = row.get("资产负债率(%)")         # 资产负债率

        return {
            "报告日期": row["日期_str"],
            "报告类型": "年报" if "-12-31" in row["日期_str"] else "季报",
            "销售毛利率": round(float(gm), 2) if pd.notna(gm) else None,
            "销售净利率": round(float(nm), 2) if pd.notna(nm) else None,
            "净资产收益率": round(float(roe), 2) if pd.notna(roe) else None,
            "加权净资产收益率": round(float(roe_w), 2) if pd.notna(roe_w) else None,
            "营收增长率": round(float(rev_growth), 2) if pd.notna(rev_growth) else None,
            "净利润增长率": round(float(profit_growth), 2) if pd.notna(profit_growth) else None,
            "资产周转率": round(float(asset_turnover), 3) if pd.notna(asset_turnover) else None,
            "流动比率": round(float(current_ratio), 2) if pd.notna(current_ratio) else None,
            "资产负债率": round(float(debt_ratio), 2) if pd.notna(debt_ratio) else None,
        }
    except Exception as e:
        return None

def get_stock_valuation(stock_code: str) -> Optional[dict]:
    """抓取个股实时估值（东方财富实时行情）"""
    try:
        # 实时行情
        df = ak.stock_board_industry_cons_em(symbol="半导体")
        # stock_bid_ask_em 拿实时报价
        df2 = ak.stock_bid_ask_em(symbol=stock_code, indicator="实时")
        if df2 is None:
            # 尝试 stock_spot_em
            try:
                df3 = ak.stock_spot_em()
                row = df3[df3["代码"] == stock_code]
                if len(row) > 0:
                    r = row.iloc[0]
                    return {
                        "最新价": float(r.get("最新价", 0)) if pd.notna(r.get("最新价")) else None,
                        "市盈率": float(r.get("市盈率-动态", 0)) if pd.notna(r.get("市盈率-动态")) else None,
                        "市净率": float(r.get("市净率", 0)) if pd.notna(r.get("市净率")) else None,
                        "总市值": float(r.get("总市值", 0)) if pd.notna(r.get("总市值")) else None,
                    }
            except:
                pass
        return None
    except Exception:
        return None

def get_stock_price_and_val(stock_code: str) -> Optional[dict]:
    """用新浪行情接口抓个股实时估值"""
    try:
        # 东方财富实时行情（单个股票）
        url = f"https://push2.eastmoney.com/api/qt/stock/get?secid={'1.' + stock_code if stock_code.startswith('6') else '0.' + stock_code}&fields=f43,f57,f58,f107,f116,f117,f162,f163,f167,f168&ut=fa5fd1943c7b386f172d6893dbfba10b"
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=10)
        data = r.json().get("data", {})
        if not data:
            return None
        f43 = data.get("f43")  # 最新价（分）
        f57 = data.get("f57")  # 代码
        f58 = data.get("f58")  # 名称
        f167 = data.get("f167") # 市盈率
        f168 = data.get("f168") # 市净率
        return {
            "最新价": round(float(f43) / 100, 2) if f43 else None,
            "市盈率": float(f167) if f167 else None,
            "市净率": float(f168) if f168 else None,
        }
    except Exception as e:
        return None

def calc_industry_financials(industry: str, max_stocks: int = 20) -> Optional[dict]:
    """计算行业财务均值（最多取前 max_stocks 只成分股，年报优先）"""
    members = get_industry_members(industry)
    if not members:
        return None
    members = members[:max_stocks]

    gms, nms, roes, rev_gs, prof_gs = [], [], [], [], []
    report_types = []
    sample_dates = []

    for m in members:
        time.sleep(0.15)  # 避免请求过快
        fin = get_stock_financial_latest(m["代码"])
        if fin:
            report_types.append(fin.get("报告类型", ""))
            sample_dates.append(fin.get("报告日期", ""))
            if fin.get("销售毛利率") is not None:
                gms.append(fin["销售毛利率"])
            if fin.get("销售净利率") is not None:
                nms.append(fin["销售净利率"])
            if fin.get("加权净资产收益率") is not None:
                roes.append(fin["加权净资产收益率"])
            elif fin.get("净资产收益率") is not None:
                roes.append(fin["净资产收益率"])
            if fin.get("营收增长率") is not None:
                rev_gs.append(fin["营收增长率"])
            if fin.get("净利润增长率") is not None:
                prof_gs.append(fin["净利润增长率"])

    if not gms and not nms and not roes:
        return None

    def med(lst):
        return round(float(pd.Series(lst).median()), 2) if lst else None

    def mean(lst):
        return round(float(pd.Series(lst).mean()), 2) if lst else None

    # 报告类型多数派
    report_type = max(set(report_types), key=report_types.count) if report_types else ""
    latest_date = max((d for d in sample_dates if d), default="")

    return {
        "行业名称": industry,
        "成分股数量": len(members),
        "有效样本_毛利率": len(gms),
        "有效样本_净利率": len(nms),
        "有效样本_ROE": len(roes),
        "毛利率_中位数": med(gms),
        "毛利率_均值": mean(gms),
        "净利率_中位数": med(nms),
        "净利率_均值": mean(nms),
        "ROE_中位数": med(roes),
        "ROE_均值": mean(roes),
        "营收增速_中位数": med(rev_gs),
        "营收增速_均值": mean(rev_gs),
        "净利润增速_中位数": med(prof_gs),
        "净利润增速_均值": mean(prof_gs),
        "数据报告期": latest_date,
        "报告类型": report_type,
    }

# ── 基准记录构造 ─────────────────────────────────────────────────────────────

def fin_to_text(data: dict, source: str, fetch_date: str) -> str:
    """把财务字典转成文本行（写入 content 字段）"""
    lines = [
        f"数据来源：{source}",
        f"抓取日期：{fetch_date}",
        f"数据类型：行业财务均值",
    ]
    for k, v in data.items():
        if v is not None:
            lines.append(f"{k}：{v}")
    return "\n".join(lines)

def val_to_text(data: dict, source: str, fetch_date: str) -> str:
    """把估值字典转成文本行"""
    lines = [
        f"数据来源：{source}",
        f"抓取日期：{fetch_date}",
        f"数据类型：可比公司估值",
    ]
    for k, v in data.items():
        if v is not None:
            lines.append(f"{k}：{v}")
    return "\n".join(lines)

# ── 主流程 ───────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="akshare 市场基准采集")
    parser.add_argument("--industries", nargs="+", default=None,
                        help="指定行业名称（默认全部）")
    parser.add_argument("--dry-run", action="store_true",
                        help="只打印，不写入 Supabase")
    parser.add_argument("--clear", action="store_true",
                        help="先删除旧的市场基准记录再写入")
    args = parser.parse_args()

    industries = args.industries or INDUSTRIES
    fetch_date = date.today().isoformat()
    is_write = not args.dry_run and SB_KEY == os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

    print(f"\n{'='*60}")
    print(f"市场基准数据采集")
    print(f"目标行业: {industries}")
    print(f"写入模式: {'Supabase' if is_write else 'DRY RUN'}")
    print(f"{'='*60}\n")

    # ── Step 1: 删除旧记录 ──────────────────────────────────────────────────
    if args.clear and is_write:
        print("清除旧的市场基准记录...")
        deleted = sb_delete("doc_type=eq.market_benchmark")
        print(f"  删除状态: {deleted}")

    # ── Step 2: 行业财务均值 ─────────────────────────────────────────────────
    print("\n【行业财务均值】")
    for ind in industries:
        print(f"\n  采集: {ind} ...", end=" ", flush=True)
        try:
            data = calc_industry_financials(ind, max_stocks=20)
            if data:
                content = fin_to_text(
                    data,
                    source="akshare/东方财富-行业成分股财务数据",
                    fetch_date=fetch_date
                )
                payload = {
                    "company": f"市场基准|行业均值|{ind}",
                    "doc_type": "market_benchmark",
                    "source": "akshare_benchmarks.py",
                    "content": content,
                }
                if is_write:
                    ok = sb_post(payload)
                    print(f"✓ 写入" if ok else f"✗ 失败")
                else:
                    print(f"\n  [DRY RUN] {content[:200]}")
            else:
                print("⚠️ 无数据")
        except Exception as e:
            print(f"✗ 异常: {e}")
        time.sleep(0.5)

    # ── Step 3: 可比公司估值 ─────────────────────────────────────────────────
    print("\n\n【可比公司估值】")
    for code, name, tag in COMPARABLE_STOCKS:
        print(f"  采集: {name}({code}) ...", end=" ", flush=True)
        try:
            val = get_stock_price_and_val(code)
            if val:
                val["股票代码"] = code
                val["股票名称"] = name
                val["公司标签"] = tag
                content = val_to_text(
                    val,
                    source="akshare/东方财富-实时行情数据",
                    fetch_date=fetch_date
                )
                payload = {
                    "company": f"市场基准|可比公司|{name}",
                    "doc_type": "market_benchmark",
                    "source": "akshare_benchmarks.py",
                    "content": content,
                }
                if is_write:
                    ok = sb_post(payload)
                    print(f"✓ 写入" if ok else f"✗ 失败")
                else:
                    print(f"\n  [DRY RUN] {content[:200]}")
            else:
                print("⚠️ 无数据")
        except Exception as e:
            print(f"✗ 异常: {e}")
        time.sleep(0.3)

    print("\n\n采集完成！")

if __name__ == "__main__":
    main()
