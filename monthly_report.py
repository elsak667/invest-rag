#!/usr/bin/env python3
"""
monthly_report.py — 投资月报生成 v2
从 Supabase market_benchmark(行业均值) → 月频趋势 + 行业横向对比 + 赛道配置状态
"""
import re, json, httpx
from datetime import datetime, timezone
from pathlib import Path

SUPABASE_URL = "https://rgnncmgrumwjjgzyhmkt.supabase.co"
FEISHU_APP_ID     = "cli_a950307a10b8dcb1"
FEISHU_APP_SECRET = "TFlBj160Jm4p48uZ3t4RETpL3qz1oxaj"
FEISHU_OPEN_ID    = "ou_6a0c374101f34d947fba5948ed2ef1c6"
SNAPSHOT_FILE    = Path.home() / ".hermes" / "benchmark_snapshot.json"

def _get_key() -> str:
    with open("/Users/els/scripts/invest_rag/weekly_report.py") as f:
        txt = f.read()
    m = re.search(r'SUPABASE_SERVICE_ROLE_KEY = "([^"]+)"', txt)
    return m.group(1)

def _api(table: str, params: str = "") -> list:
    key = _get_key()
    q = "select=*" + (f"&{params}" if params else "")
    r = httpx.get(
        f"{SUPABASE_URL}/rest/v1/{table}?{q}",
        headers={"apikey": key, "Authorization": f"Bearer {key}"},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()

def fetch_industry_avgs() -> list:
    """取所有行业均值记录"""
    return _api("documents", "doc_type=eq.market_benchmark&order=created_at.desc")

def fetch_sector_config() -> list:
    return _api("sector_config", "order=sector_name.asc")

def feishu_token() -> str:
    r = httpx.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()["tenant_access_token"]

def feishu_send(uid: str, msg: str):
    token = feishu_token()
    r = httpx.post(
        "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=open_id",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"receive_id": uid, "msg_type": "text", "content": json.dumps({"text": msg})},
        timeout=10,
    )
    return r.status_code, r.json()

# ── 解析行业均值内容 ────────────────────────────────────────────────────────
def parse_industry_avg(content: str) -> dict | None:
    """从 markdown 内容提取行业均值指标（中位数/均值）"""
    data = {}
    for line in content.split("\n"):
        line = line.strip()
        if not line:
            continue
        # 优先按 markdown 表格 pipe 解析
        if "│" in line:
            parts = [p.strip().strip("|").strip() for p in line.split("│")]
        elif "：" in line:  # 全角冒号，纯文本格式
            parts = line.split("：")
            if len(parts) >= 2:
                key = parts[0].strip()
                raw = "：".join(parts[1:]).strip()
                m = re.search(r"-?[\d.]+", raw)
                val = float(m.group()) if m else None
                if "行业名称" in key:
                    data["industry"] = raw
                elif "成分股数量" in key:
                    data["count"] = int(raw) if raw.isdigit() else val
                elif "毛利率_中位数" in key:
                    data["gross_margin"] = val
                elif "毛利率_均值" in key and "gross_margin" not in data:
                    data["gross_margin"] = val
                elif "净利率_中位数" in key:
                    data["net_margin"] = val
                elif "净利率_均值" in key and "net_margin" not in data:
                    data["net_margin"] = val
                elif "ROE_中位数" in key or "净资产收益率_中位数" in key:
                    data["roe"] = val
                elif ("ROE_均值" in key or "净资产收益率_均值" in key) and "roe" not in data:
                    data["roe"] = val
                elif "营收增速_中位数" in key or "营业收入增长率_中位数" in key:
                    data["revenue_growth"] = val
                elif ("营收增速_均值" in key or "营业收入增长率_均值" in key) and "revenue_growth" not in data:
                    data["revenue_growth"] = val
            continue
        else:
            continue
        # markdown 表格格式处理（原始逻辑）
        if len(parts) < 2 or not parts[0]:
            continue
        key = parts[0]
        raw = parts[1]
        m = re.search(r"-?[\d.]+", raw)
        val = float(m.group()) if m else None
        if "行业名称" in key:
            data["industry"] = raw
        elif "成分股数量" in key:
            data["count"] = int(raw) if raw.isdigit() else val
        elif "毛利率_中位数" in key:
            data["gross_margin"] = val
        elif "净利率_中位数" in key:
            data["net_margin"] = val
        elif "ROE_中位数" in key:
            data["roe"] = val
        elif "营收增速_中位数" in key:
            data["revenue_growth"] = val
    return data if "industry" in data else None

# ── 月报生成 ────────────────────────────────────────────────────────────────
def median(lst):
    s = sorted([v for v in lst if v is not None])
    return s[len(s)//2] if s else None

def build_report(industry_rows: list, sectors: list) -> str:
    now = datetime.now(timezone.utc).strftime("%Y年%m月%d日")
    lines = [
        f"# 投资月报 · {now}\n",
        "## 一、行业基准异动\n",
    ]

    # 解析所有行业均值
    parsed = []
    for row in industry_rows:
        data = parse_industry_avg(row.get("content", ""))
        if data:
            data["source"] = row.get("source", "")
            parsed.append(data)

    if not parsed:
        lines.append("*暂无行业均值数据*\n")
        industries = []
    else:
        # 按行业分组（去重，取最新）
        seen, industries = {}, []
        for p in parsed:
            ind = p["industry"]
            if ind not in seen:
                seen[ind] = p
                industries.append(p)

        # 加载上月快照
        prev = {}
        if SNAPSHOT_FILE.exists():
            try:
                prev = json.loads(SNAPSHOT_FILE.read_text())
            except Exception:
                pass

        METRICS = [
            ("gross_margin",   "毛利率",    "%"),
            ("net_margin",     "净利率",    "%"),
            ("roe",            "ROE",        "%"),
            ("revenue_growth", "营收增速",   "%"),
        ]

        for ind in sorted(industries, key=lambda x: x["industry"]):
            name = ind["industry"]
            pdata = prev.get("industries", {}).get(name, {})
            lines.append(f"### {name}（{ind.get('count','?')}家）")
            has_data = False
            for mkey, mlabel, unit in METRICS:
                cv = ind.get(mkey)
                pv = pdata.get(mkey)
                if cv is not None:
                    has_data = True
                    if pv is not None:
                        d = round(cv - pv, 2)
                        arrow = "↑" if d > 0 else "↓" if d < 0 else "→"
                        lines.append(f"- {mlabel} {cv:.1f}{unit}（{arrow}{abs(d):.1f}pp vs上月）")
                    else:
                        lines.append(f"- {mlabel} {cv:.1f}{unit}（首次收录）")
            if not has_data:
                lines.append(f"- 暂无财务指标数据")
            lines.append("")

    # 保存快照
    snap = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "industries": {p["industry"]: {k: v for k, v in p.items()
                                       if k in ("gross_margin","net_margin","roe","revenue_growth","count")}
                       for p in industries}
    }
    SNAPSHOT_FILE.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_FILE.write_text(json.dumps(snap, ensure_ascii=False, indent=2))

    # 行业横向对比表
    lines += ["## 二、行业横向对比\n", "| 行业 | 家数 | 毛利率 | 净利率 | ROE | 营收增速 |",
              "|------|------|--------|-------|-----|---------|"]
    for ind in sorted(industries, key=lambda x: x["industry"]):
        def f(v): return f"{v:.1f}%" if v is not None else "—"
        lines.append(f"| {ind['industry']} | {ind.get('count','—')} | "
                     f"{f(ind.get('gross_margin'))} | {f(ind.get('net_margin'))} | "
                     f"{f(ind.get('roe'))} | {f(ind.get('revenue_growth'))} |")
    lines.append("")

    # 赛道配置状态
    lines.append("## 三、赛道配置状态\n")
    if not sectors:
        lines.append("*暂无赛道配置*\n")
    else:
        for s in sectors:
            members = s.get("member_companies") or []
            comparables = s.get("comparable_companies") or []
            status = s.get("status", "?")
            lines.append(f"- **{s['sector_name']}** | {status} | "
                         f"成员:{len(members)}家 | 可比:{len(comparables)}家")
            if members:
                lines.append(f"  - 成员公司: {', '.join(members)}")
            if comparables:
                lines.append(f"  - 可比公司: {', '.join(comparables)}")
    lines += ["\n---", f"*月报生成于 {now}*"]
    return "\n".join(lines)

def main():
    print("[月报] 开始生成...")
    industry_rows = fetch_industry_avgs()
    sectors       = fetch_sector_config()
    print(f"  行业均值记录: {len(industry_rows)} 条")
    print(f"  赛道配置: {len(sectors)} 条")
    for row in industry_rows[:3]:
        print(f"  行业: {row.get('company','')[:40]}")
        print(f"  内容: {row.get('content','')[:60]}")

    report_md = build_report(industry_rows, sectors)
    print("\n" + report_md)

    code, body = feishu_send(FEISHU_OPEN_ID, report_md)
    print(f"\n[月报] 飞书推送: code={code}", "成功" if code == 0 else body)

if __name__ == "__main__":
    main()
