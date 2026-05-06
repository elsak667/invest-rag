#!/usr/bin/env python3
"""
基准异动检测脚本
逻辑：
  1. 读取上次的基准快照（~/.hermes/benchmark_snapshot.json）
  2. 从 Supabase 查询当前最新基准数据
  3. 对比关键指标（净利率/ROE/营收增速中位数），变化超阈值则推飞书
  4. 更新本地快照文件

阈值逻辑：
  - 净利率/ROE 中位数：变化 > ±3pp（绝对值）触发预警
  - 营收增速：变化 > ±15pp 触发预警
  - 新增公司：变化 > ±10% 样本数触发预警
  - 新上市可比公司：列出（不预警）

依赖：SUPABASE_SERVICE_ROLE_KEY 环境变量（或 ~/.hermes/.env）
"""

import os
import json
import requests
SNAPSHOT_FILE = os.path.expanduser("~/.hermes/benchmark_snapshot.json")
SUPABASE_URL = "https://rgnncmgrumwjjgzyhmkt.supabase.co"

# ── 飞书 Webhook 推送 ──────────────────────────────────────────────

FEISHU_APP_ID = "cli_a950307a10b8dcb1"
FEISHU_APP_SECRET = "TFlBj160Jm4p48uZ3t4RETpL3qz1oxaj"
FEISHU_USER_OPEN_ID = "ou_6a0c374101f34d947fba5948ed2ef1c6"

_token_cache = {"token": None, "expire_at": 0}

def get_feishu_token() -> str:
    """获取 tenant_access_token，带缓存"""
    import time
    now = time.time()
    if _token_cache["token"] and now < _token_cache["expire_at"] - 60:
        return _token_cache["token"]
    r = requests.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET},
        timeout=10
    )
    data = r.json()
    if data.get("code") == 0:
        _token_cache["token"] = data["tenant_access_token"]
        _token_cache["expire_at"] = now + data.get("expire", 3600)
        return _token_cache["token"]
    raise RuntimeError(f"获取飞书 token 失败: {data}")

def parse_benchmark_content(content: str) -> dict:
    """把基准 content 文本解析成结构化 dict（中文字段冒号分隔）"""
    result = {}
    for line in content.split("\n"):
        line = line.strip()
        if "：" in line:
            key, _, val = line.partition("：")
            key = key.strip()
            val = val.strip()
            if key and val:
                result[key] = val
    return result

def send_feishu_alert(changes: dict, fetch_date: str) -> bool:
    """推送飞书消息（卡片 → 个人私信）"""
    token = get_feishu_token()

    # 构造卡片元素
    elements = []
    if changes.get("industry"):
        elements.append({"tag": "markdown", "content": "**【行业基准异动】**"})
        for ind, metrics in changes["industry"].items():
            row_parts = []
            for metric, (old, new, delta) in metrics.items():
                sign = "+" if delta > 0 else ""
                row_parts.append(f"{metric}: {old} → {new} ({sign}{delta:.1f}pp)")
            elements.append({"tag": "markdown", "content": f"**{ind}**：{' | '.join(row_parts)}"})

    if changes.get("new_comparable"):
        if elements:
            elements.append({"tag": "hr"})
        elements.append({"tag": "markdown", "content": "**【新增可比公司】**"})
        for name, pe in changes["new_comparable"]:
            elements.append({"tag": "markdown", "content": f"- {name}：PE {pe}x"})

    if not elements:
        return False

    elements.append({"tag": "hr"})
    elements.append({"tag": "markdown", "content": f"*数据抓取：{fetch_date} | akshare_benchmarks.py 自动推送*"})

    payload = {
        "receive_id": FEISHU_USER_OPEN_ID,
        "msg_type": "interactive",
        "content": json.dumps({
            "type": "template",
            "data": {
                "template_id": "",
                "variables": {}
            }
        })
    }

    # 直接用卡片消息
    card_payload = {
        "receive_id": FEISHU_USER_OPEN_ID,
        "msg_type": "interactive",
        "content": json.dumps({
            "type": "template",
            "data": {"template_id": "", "variables": {}}
        }, ensure_ascii=False)
    }

    # 实际发文本（卡片格式各版本兼容性问题，用文本消息更稳）
    text_lines = [f"⚠️ 市场基准异动预警（{fetch_date})"]
    if changes.get("industry"):
        text_lines.append("")
        text_lines.append("【行业均值变化】")
        for ind, metrics in changes["industry"].items():
            for metric, (old, new, delta) in metrics.items():
                sign = "+" if delta > 0 else ""
                text_lines.append(f"  {ind} {metric}: {old} → {new} ({sign}{delta:.1f}pp)")
    if changes.get("new_comparable"):
        text_lines.append("")
        text_lines.append("【新增可比公司】")
        for name, pe in changes["new_comparable"]:
            text_lines.append(f"  {name} PE {pe}x")
    text_lines.append("")
    text_lines.append("_由 akshare_benchmarks.py 自动推送_")

    text_payload = {
        "receive_id": FEISHU_USER_OPEN_ID,
        "msg_type": "text",
        "content": json.dumps({"text": "\n".join(text_lines)}, ensure_ascii=False)
    }

    try:
        r = requests.post(
            "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=open_id",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=text_payload, timeout=10
        )
        return r.json().get("code") == 0
    except Exception as e:
        print(f"飞书推送失败: {e}")
        return False

# ── 数据读写 ─────────────────────────────────────────────────────────

def get_sb_key() -> str:
    """获取 Supabase service_role key"""
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if key:
        return key
    try:
        with open(os.path.expanduser("~/.hermes/.env")) as f:
            for line in f:
                if "SUPABASE_SERVICE_ROLE_KEY" in line and "=" in line:
                    k = line.split("=", 1)[1].strip()
                    if k not in ("***", ""):
                        return k
    except:
        pass
    return ""

def fetch_current_benchmarks() -> dict:
    """从 Supabase 查询当前所有 market_benchmark 数据"""
    key = get_sb_key()
    if not key:
        print("⚠️ 未配置 SUPABASE_SERVICE_ROLE_KEY，无法查询当前基准")
        return {}

    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json"
    }
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/documents?doc_type=eq.market_benchmark&select=*&limit=100",
        headers=headers, timeout=15
    )
    if r.status_code != 200:
        print(f"⚠️ 查询失败: {r.status_code}")
        return {}
    return r.json()

def load_snapshot() -> dict:
    """读取本地基准快照"""
    try:
        with open(SNAPSHOT_FILE) as f:
            return json.load(f)
    except:
        return {}

def save_snapshot(data: dict):
    """保存基准快照"""
    os.makedirs(os.path.dirname(SNAPSHOT_FILE), exist_ok=True)
    with open(SNAPSHOT_FILE, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def build_current_state(benchmarks: list) -> dict:
    """把 Supabase 数据转成快照格式"""
    state = {"industry": {}, "comparable": {}}
    for b in benchmarks:
        parsed = parse_benchmark_content(b.get("content", ""))
        btype = parsed.get("数据类型", "")
        if btype == "行业财务均值":
            ind = parsed.get("行业名称", "")
            state["industry"][ind] = {
                "净利率_中位数": _float(parsed.get("净利率_中位数")),
                "ROE_中位数": _float(parsed.get("ROE_中位数")),
                "营收增速_中位数": _float(parsed.get("营收增速_中位数")),
                "净利润增速_中位数": _float(parsed.get("净利润增速_中位数")),
                "样本量_净利率": _int(parsed.get("有效样本_净利率")),
                "fetch_date": parsed.get("抓取日期", ""),
            }
        elif btype == "可比公司估值":
            name = parsed.get("股票名称", "")
            if name:
                state["comparable"][name] = {
                    "最新价": _float(parsed.get("最新价")),
                    "市盈率": _float(parsed.get("市盈率")),
                    "市净率": _float(parsed.get("市净率")),
                    "fetch_date": parsed.get("抓取日期", ""),
                }
    return state

def _float(v):
    try:
        return float(v)
    except:
        return None

def _int(v):
    try:
        return int(v)
    except:
        return None

# ── 变化检测 ─────────────────────────────────────────────────────────

THRESHOLDS = {
    "净利率_中位数": 3.0,      # pp，绝对值
    "ROE_中位数": 3.0,         # pp，绝对值
    "营收增速_中位数": 15.0,    # pp，绝对值
    "净利润增速_中位数": 15.0,  # pp，绝对值
    "样本量_净利率": 3,         # 绝对变化数量
}

def detect_changes(prev: dict, current: dict) -> dict:
    """对比新旧快照，返回异动内容"""
    result = {"industry": {}, "new_comparable": []}

    for ind, cmetrics in current.get("industry", {}).items():
        pm = prev.get("industry", {}).get(ind, {})
        if not pm:
            continue  # 新行业，不报警（首次抓取）

        industry_changes = {}
        for metric, threshold in THRESHOLDS.items():
            old_val = pm.get(metric)
            new_val = cmetrics.get(metric)
            if old_val is not None and new_val is not None and abs(old_val) > 0.01:
                delta = new_val - old_val
                if abs(delta) > threshold:
                    industry_changes[metric] = (old_val, new_val, delta)

        if industry_changes:
            result["industry"][ind] = industry_changes

    # 新增可比公司检测
    prev_comparable = set(prev.get("comparable", {}).keys())
    curr_comparable = set(current.get("comparable", {}).keys())
    new_companies = curr_comparable - prev_comparable
    for name in new_companies:
        c = current["comparable"][name]
        if c.get("市盈率"):
            result["new_comparable"].append((name, c.get("市盈率")))

    return result

# ── 主流程 ───────────────────────────────────────────────────────────

def main():
    from datetime import date
    fetch_date = date.today().isoformat()

    print(f"[{fetch_date}] 基准异动检测开始")

    # 1. 读旧快照
    prev_state = load_snapshot()
    print(f"  上次快照: {'有' if prev_state else '无（首次运行）'}")

    # 2. 查当前基准
    benchmarks = fetch_current_benchmarks()
    print(f"  当前基准: {len(benchmarks)} 条")

    if not benchmarks:
        print("⚠️ 无法获取当前基准数据，跳过检测")
        return

    # 3. 构建当前状态
    current_state = build_current_state(benchmarks)

    # 4. 对比
    changes = detect_changes(prev_state, current_state)

    # 5. 打印结果
    has_alert = False
    if changes.get("industry"):
        print(f"\n  ⚠️ 检测到 {len(changes['industry'])} 个行业异动:")
        for ind, metrics in changes["industry"].items():
            print(f"    [{ind}]")
            for metric, (old, new, delta) in metrics.items():
                sign = "+" if delta > 0 else ""
                print(f"      {metric}: {old} → {new} ({sign}{delta:.1f}pp)")
        has_alert = True

    if changes.get("new_comparable"):
        print(f"\n  📋 新增 {len(changes['new_comparable'])} 家可比公司:")
        for name, pe in changes["new_comparable"]:
            print(f"    {name} PE {pe}x")

    # 6. 推送飞书
    if has_alert or changes.get("new_comparable"):
        ok = send_feishu_alert(changes, fetch_date)
        print(f"\n  飞书推送: {'✓ 成功' if ok else '✗ 失败（未配置或推送失败）'}")

    # 7. 更新快照
    save_snapshot(current_state)
    print(f"\n  快照已更新: {SNAPSHOT_FILE}")

if __name__ == "__main__":
    main()
