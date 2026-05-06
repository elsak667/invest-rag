#!/usr/bin/env python3
"""
投资周报生成脚本
- 拉取过去一周的分析报告
- 拉取最新基准数据
- 拼成结构化摘要推送到飞书
"""
import json
import re
import httpx
from datetime import datetime, timedelta, timezone

# ============ 配置 ============
SUPABASE_URL = "https://rgnncmgrumwjjgzyhmkt.supabase.co"
SUPABASE_SERVICE_ROLE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InJnbm5jbWdydW13ampnenlobWt0Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3NzczMTAwNSwiZXhwIjoyMDkzMzA3MDA1fQ.Rrq95ziEwcTmX1NNz62ZqGsN8GvyZcutqzAIwC7PkC8"

FEISHU_APP_ID = "cli_a950307a10b8dcb1"
FEISHU_APP_SECRET = "TFlBj160Jm4p48uZ3t4RETpL3qz1oxaj"
FEISHU_USER_OPEN_ID = "ou_6a0c374101f34d947fba5948ed2ef1c6"

# ============ Supabase REST API ============
def supabase_select(table: str, params: dict) -> list:
    """执行 Supabase select 查询"""
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    headers = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
    }
    resp = httpx.get(url, headers=headers, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()

def get_recent_reports(days: int = 7):
    """获取过去N天的分析报告"""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
    return supabase_select("documents", {
        "doc_type": "eq.analysis_report",
        "created_at": f"gte.{since}",
        "order": "created_at.desc",
        "limit": 20,
    })

def get_benchmarks():
    """获取最新基准数据"""
    return supabase_select("documents", {
        "doc_type": "eq.market_benchmark",
        "limit": 50,
    })

# ============ 解析工具 ============
def parse_benchmark_kv(content: str) -> dict:
    """从纯文本 key-value 格式解析基准数据"""
    result = {}
    for line in content.split("\n"):
        line = line.strip()
        if "：" in line or ":" in line:
            sep = "：" if "：" in line else ":"
            key, _, val = line.partition(sep)
            key = key.strip()
            val = val.strip().rstrip("％%")
            if val:
                try:
                    result[key] = float(val)
                except ValueError:
                    result[key] = val
    return result

def parse_report_from_markdown(content: str) -> dict:
    """从 markdown 分析报告提取关键信息"""
    result = {}

    # 公司名：从 # 标题行提取
    m = re.search(r"^#\s+(.+?)\s*投资分析报告", content, re.MULTILINE)
    if m:
        result["公司"] = m.group(1).strip()

    # 综合评分：`**加权总分** | 100% | **60**` 类似格式
    m = re.search(r"\*\*加权总分\*\*.*?\|\s*\d+%.*?\|\s*\*\*(\d+)\*\*", content, re.DOTALL)
    if m:
        result["综合评分"] = int(m.group(1))

    # 评级：投资亮点/风险等级部分
    rating_patterns = [
        r"结论[:：]?\s*>?\s*\n?\s*(.+?)(?:\n\n|\Z)",
        r"推荐理由[:：]?\s*(.+?)(?:\n|$)",
        r"评级[:：]?\s*(.+?)(?:\n|$)",
        r"结论[:：]?\s*\n?\s*(.+?)(?:\n\n|\Z)",
    ]
    for pattern in rating_patterns:
        m = re.search(pattern, content, re.DOTALL)
        if m:
            raw = m.group(1).strip()
            # 去掉 markdown 标记
            cleaned = re.sub(r"\*+", "", raw).strip()
            if cleaned and len(cleaned) < 100:
                result["结论"] = cleaned
                break

    # 风险：提取 ★ 数量
    risks = re.findall(r"(★+)", content)
    if risks:
        max_stars = max(len(r) for r in risks)
        result["最高风险"] = max_stars

    # 行业：提取「所属行业」或「行业」行
    m = re.search(r"所属行业[:：]\s*(.+?)(?:\n|$)", content)
    if not m:
        m = re.search(r"\*\*行业\*\*.*?\|\s*(.+?)\|", content, re.DOTALL)
    if m:
        result["行业"] = m.group(1).strip()

    return result

# ============ 飞书推送 ============
def get_feishu_token() -> str:
    """获取飞书 tenant_access_token"""
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    resp = httpx.post(url, json={
        "app_id": FEISHU_APP_ID,
        "app_secret": FEISHU_APP_SECRET
    }, timeout=10)
    resp.raise_for_status()
    return resp.json()["tenant_access_token"]

def send_feishu_message(token: str, open_id: str, msg_type: str, content: dict):
    """发送飞书消息给指定用户"""
    url = "https://open.feishu.cn/open-apis/im/v1/messages"
    params = {"receive_id_type": "open_id"}
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    payload = {
        "receive_id": open_id,
        "msg_type": msg_type,
        "content": json.dumps(content, ensure_ascii=False)
    }
    resp = httpx.post(url, params=params, headers=headers, json=payload, timeout=15)
    resp.raise_for_status()
    return resp.json()

def format_benchmark_summary(benchmarks: list) -> str:
    """格式化基准速览（行业均值部分）"""
    lines = []
    for b in benchmarks:
        company = b.get("company", "")
        # 只取行业均值
        if "行业均值" not in company:
            continue
        content_str = b.get("content", "")
        kv = parse_benchmark_kv(content_str)
        industry = kv.get("行业名称", company.split("|")[-1])
        pe = kv.get("市盈率_中位数") or kv.get("市盈率中位数") or kv.get("市盈率")
        nl = kv.get("净利率_中位数") or kv.get("净利率中位数")
        roe = kv.get("ROE_中位数") or kv.get("ROE中位数")
        pe_str = f"{pe:.0f}x" if isinstance(pe, (int, float)) else "N/A"
        nl_str = f"{nl:.1f}%" if isinstance(nl, (int, float)) else "N/A"
        roe_str = f"{roe:.1f}%" if isinstance(roe, (int, float)) else "N/A"
        lines.append(f"  {industry}  PE {pe_str} | 净利率 {nl_str} | ROE {roe_str}")

    if not lines:
        return "  暂无基准数据"
    return "\n".join(lines)

# ============ 主流程 ============
def main():
    print("=" * 40)
    print("投资周报生成")
    print("=" * 40)

    # 1. 获取过去一周的报告
    reports = get_recent_reports(days=7)
    print(f"\n过去7天分析报告: {len(reports)} 篇")

    # 2. 获取基准数据
    benchmarks = get_benchmarks()
    print(f"基准数据: {len(benchmarks)} 条")

    # 3. 生成报告日期范围
    end_date = datetime.now().strftime("%m/%d")
    start_date = (datetime.now() - timedelta(days=7)).strftime("%m/%d")
    date_range = f"{start_date} - {end_date}"

    # 4. 构建飞书消息
    token = get_feishu_token()

    if not reports:
        content = {
            "zh_cn": {
                "title": f"📊 投资周报 {date_range}",
                "content": [
                    [{"tag": "text", "text": "■ 本周分析\n  无新增分析报告\n"}],
                    [{"tag": "text", "text": "■ 基准速览\n" + format_benchmark_summary(benchmarks)}],
                    [{"tag": "text", "text": "_由 invest_rag 自动生成_"}],
                ]
            }
        }
    else:
        report_lines = []
        for i, r in enumerate(reports, 1):
            parsed = parse_report_from_markdown(r.get("content", ""))
            company = parsed.get("公司", "未知公司")
            score = parsed.get("综合评分", "—")
            conclusion = parsed.get("结论", "—")
            industry = parsed.get("行业", "")
            rating = parsed.get("评级", "—")

            # 截断结论
            if conclusion and len(conclusion) > 40:
                conclusion = conclusion[:40] + "..."

            score_str = f"综合评分 {score}/100" if isinstance(score, int) else ""
            industry_str = f" | {industry}" if industry else ""

            report_lines.append(
                f"  {i}. {company}{industry_str}\n"
                f"     {score_str}\n"
                f"     {conclusion}"
            )

        content = {
            "zh_cn": {
                "title": f"📊 投资周报 {date_range}",
                "content": [
                    [{"tag": "text", "text": f"■ 本周分析（共 {len(reports)} 篇）\n" + "\n".join(report_lines)}],
                    [{"tag": "text", "text": "■ 基准速览\n" + format_benchmark_summary(benchmarks)}],
                    [{"tag": "text", "text": "_由 invest_rag 自动生成_"}],
                ]
            }
        }

    # 5. 发送
    result = send_feishu_message(token, FEISHU_USER_OPEN_ID, "post", content)
    print(f"\n飞书推送结果: {result.get('code', result.get('status'))}")
    print("完成")

if __name__ == "__main__":
    main()
