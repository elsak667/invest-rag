#!/usr/bin/env python3
"""
赛道匹配服务 v3 — 向量相似度匹配
- 基于 embedding 向量余弦相似度匹配赛道
- 赛道 centroid = 成员文档 embedding 的平均向量
- 匹配阈值: sim >= 0.85 高置信直接归类, sim >= 0.70 中置信待确认, sim < 0.70 新赛道推荐
"""
import json
import re
import httpx
import numpy as np
from datetime import datetime, timezone

# ============ 配置（从 ~/.hermes/credentials.json 读取）============
import os, json

_CREDS_PATH = os.path.expanduser("~/.hermes/credentials.json")
try:
    with open(_CREDS_PATH) as f:
        _CREDS = json.load(f)
    SUPABASE_URL = _CREDS["supabase"]["url"]
    SUPABASE_SERVICE_ROLE_KEY = _CREDS["supabase"]["service_role_key"]
    FEISHU_APP_ID = _CREDS["feishu"]["app_id"]
    FEISHU_APP_SECRET = _CREDS["feishu"]["app_secret"]
    FEISHU_USER_OPEN_ID = _CREDS["feishu"].get("user_open_id", "ou_6a0c374101f34d947fba5948ed2ef1c6")
except Exception:
    raise RuntimeError(f"无法读取凭证文件 {_CREDS_PATH}，请确保文件存在且格式正确")

# 匹配阈值
SIM_HIGH = 0.85   # sim >= 0.85 → 直接归类
SIM_MEDIUM = 0.70  # sim >= 0.70 → 待确认

# ============ Supabase REST API ============
def supabase_select(table: str, params: dict) -> list:
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    headers = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
    }
    resp = httpx.get(url, headers=headers, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()

def supabase_insert(table: str, data: dict) -> list:
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    headers = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }
    resp = httpx.post(url, headers=headers, json=data, timeout=30)
    resp.raise_for_status()
    return resp.json()

def supabase_update(table: str, params: dict, data: dict) -> list:
    """按 params 更新 table 中的记录"""
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    headers = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }
    resp = httpx.patch(url, headers=headers, params=params, json=data, timeout=30)
    resp.raise_for_status()
    return resp.json()

def get_all_sectors():
    return supabase_select("sector_config", {})

# ============ 历史记录（判断落盘） ============
HISTORY_BONUS_PER_CONFIRMED = 0.5  # 每个已确认记录给 +0.5 分


def record_match_history(company: str, embedding: list, match_result: dict,
                          status: str, dims: dict = None, history_id: str = None) -> str:
    """
    将匹配结果写入 sector_match_history 表。
    - embedding: 文档的 embedding 向量（用于向量匹配）
    - dims: LLM 提取的三维度结果（来自 extract_three_dimensions）
    返回写入记录的 id。
    """
    best = match_result.get("result", {})
    sector = best.get("sector", {})
    sim_score = best.get("sim_score", 0.0)

    # 从 dims 中取 LLM 提取结果
    if dims:
        extracted_tech = dims.get("tech", [])
        extracted_apps = dims.get("apps", [])
        extracted_customers = dims.get("customers", [])
        sector_hint = dims.get("sector_hint")
    else:
        extracted_tech = []
        extracted_apps = []
        extracted_customers = []
        sector_hint = None

    record = {
        "company": company,
        "sector_config_id": sector.get("id"),
        "suggested_sector_name": sector.get("sector_name"),
        "confidence_score": sim_score,
        "tech_score": None,
        "apps_score": None,
        "pos_score": None,
        "cust_score": None,
        "tech_hits": [],
        "apps_hits": [],
        "extracted_tech": extracted_tech,
        "extracted_apps": extracted_apps,
        "extracted_customers": extracted_customers,
        "judgment": status,  # pending / confirmed / rejected / modified
    }
    result = supabase_insert("sector_match_history", record)
    return result[0]["id"] if result else None


def get_history_bonus(company: str, sector_id: str) -> float:
    """
    读取历史确认记录，对同一赛道已有确认公司给加成分。
    返回加成分数（0 ~ N * 0.5）。
    """
    history = supabase_select("sector_match_history", {
        "sector_config_id": f"eq.{sector_id}",
        "judgment": "eq.confirmed",
        "company": f"neq.{company}",
        "select": "id",
        "limit": 20,
    })

    bonus = len(history) * HISTORY_BONUS_PER_CONFIRMED
    return round(bonus, 2)


def get_pending_history() -> list:
    """取出所有待确认的匹配记录（judgment = 'pending'）"""
    return supabase_select("sector_match_history", {
        "judgment": "eq.pending",
        "order": "created_at.asc",
        "limit": 20,
    })


def update_judgment(history_id: str, judgment: str, note: str = None):
    """更新一条历史记录的 judgment"""
    data = {
        "judgment": judgment,
        "judged_at": "now()",
    }
    if note:
        data["judgment_note"] = note
    supabase_update("sector_match_history", {"id": f"eq.{history_id}"}, data)


# ============ LLM 三维度提取 ============
def _call_llm(prompt: str, system: str = "") -> str:
    """调用 LLM (nengpa / MiniMax-M2.7)"""
    import openai, os

    # 优先从环境变量读（Hermes 注入）
    nengpa_key = os.environ.get("MINIMAX_API_KEY", "")
    if not nengpa_key:
        # 尝试从 config.yaml 读（Hermes 运行时注入）
        try:
            import yaml
            cfg_path = os.path.expanduser("~/.hermes/config.yaml")
            with open(cfg_path) as f:
                cfg = yaml.safe_load(f)
            for p in cfg.get("custom_providers", []):
                if p.get("name") == "minimax":
                    nengpa_key = p.get("api_key", "")
                    base_url = p.get("base_url", "http://127.0.0.1:18800/v1")
                    break
        except Exception:
            pass

    # 回退：用本地 nengpa relay（Hermes 注入真实 key）
    if not nengpa_key:
        nengpa_key = os.environ.get("NVIDIA_API_KEY", "")
    base_url = os.environ.get("MINIMAX_BASE_URL", "http://127.0.0.1:18800/v1")

    client = openai.OpenAI(api_key=nengpa_key or "sk-cp-...f727", base_url=base_url)
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    try:
        resp = client.chat.completions.create(
            model="MiniMax-M2.7",
            messages=messages,
            temperature=0.1,
            max_tokens=1000,
        )
        return resp.choices[0].message.content
    except Exception as e:
        return f"LLM_ERROR: {e}"


def extract_three_dimensions(content: str, company: str = "") -> dict:
    """
    用 LLM 从公司文档内容中提取三维度信息。
    返回: {
        "tech": [str],       # 核心技术词
        "apps": [str],       # 应用场景词
        "positioning": [str], # 市场定位词
        "customers": [str],   # 目标客户
        "sector_hint": str,  # 赛道提示
    }
    """
    # 截断过长的内容，避免 token 溢出
    max_chars = 6000
    truncated = content[:max_chars] if len(content) > max_chars else content
    if len(content) > max_chars:
        truncated += "\n[...]"

    system_prompt = (
        "你是一个专业的投资分析师助手。你的任务是从公司文档中提取结构化的赛道匹配信息。\n"
        "严格按 JSON 格式返回，不要有任何额外解释。"
    )

    user_prompt = (
        f"公司名称: {company or '未知'}\n\n"
        f"文档内容:\n{truncated}\n\n"
        "请从以上文档中提取以下信息，严格返回 JSON：\n"
        "{\n"
        '  "tech": ["核心技术词1", "核心技术词2", ...],   // 最多10个，如：抗体药物、ADC、基因编辑、SiC外延、IGBT等\n'
        '  "apps": ["应用场景1", "应用场景2", ...],       // 最多10个，如：肿瘤治疗、光伏逆变器、汽车半导体、数据中心等\n'
        '  "positioning": ["定位词1", "定位词2", ...],   // 最多8个，如：国产替代、高端制造、CXO、创新药等\n'
        '  "customers": ["目标客户1", "目标客户2", ...], // 最多8个，如：三甲医院、面板厂商、整车厂等\n'
        '  "sector_hint": "一句话赛道归属或领域定位"       // 可为空字符串\n'
        "}\n\n"
        "要求：\n"
        "- tech 和 apps 尽量具体（具体技术名称、应用领域名称）\n"
        "- positioning 用简短词组，不要长句\n"
        "- customers 可为空数组\n"
        "- sector_hint 最多一句话，概括公司所属领域\n"
        "- 只返回 JSON，不要有任何其他文字"
    )

    raw = _call_llm(user_prompt, system_prompt)

    if raw.startswith("LLM_ERROR:"):
        print(f"  LLM 调用失败: {raw}")
        return {"tech": [], "apps": [], "positioning": [], "customers": [], "sector_hint": None}

    # 尝试解析 JSON
    import json, re

    # 去掉 markdown 代码块标记
    cleaned = re.sub(r"```(?:json)?\s*", "", raw.strip()).strip()
    try:
        result = json.loads(cleaned)
        # 确保字段存在
        return {
            "tech": result.get("tech", []),
            "apps": result.get("apps", []),
            "positioning": result.get("positioning", []),
            "customers": result.get("customers", []),
            "sector_hint": result.get("sector_hint") or None,
        }
    except json.JSONDecodeError as e:
        print(f"  JSON 解析失败: {e}，原始返回: {raw[:200]}")
        return {"tech": [], "apps": [], "positioning": [], "customers": [], "sector_hint": None}

# ============ 向量相似度工具 ============
def cosine_similarity(a: list, b: list) -> float:
    """计算两个向量的余弦相似度"""
    import json as _json
    # Supabase REST API 对 vector 列返回 JSON 字符串而非 Python list
    if isinstance(a, str):
        a = _json.loads(a)
    if isinstance(b, str):
        b = _json.loads(b)
    a = np.array(a, dtype=np.float32)
    b = np.array(b, dtype=np.float32)
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


# ============ 赛道 centroid 构建 ============
def build_sector_fingerprint(sector: dict) -> dict:
    """
    根据 sector.member_companies 查找这些公司的最新文档 embedding，
    计算平均向量作为 centroid。
    返回: {"sector": sector, "centroid": np.array or None, "member_companies": [...], "doc_count": N}
    如果没有成员或找不到 embedding，centroid 为 None。
    """
    member_companies = sector.get("member_companies", [])
    if not member_companies:
        return {
            "sector": sector,
            "centroid": None,
            "member_companies": [],
            "doc_count": 0,
        }

    embeddings = []
    valid_members = []

    for company in member_companies:
        # 查该公司最新一条有 embedding 的文档
        docs = supabase_select("documents", {
            "company": f"eq.{company}",
            "embedding": "not.is.null",
            "order": "created_at.desc",
            "limit": 1,
            "select": "id,company,embedding",
        })
        if docs and docs[0].get("embedding"):
            emb = docs[0]["embedding"]
            # supabase REST API 返回 JSON string 或 list，需统一处理
            if isinstance(emb, list):
                emb_list = emb
            elif isinstance(emb, str) and emb.startswith("["):
                import json as _json
                emb_list = _json.loads(emb)
            else:
                emb_list = None
            if emb_list and len(emb_list) > 0:
                embeddings.append(emb_list)
                valid_members.append(company)

    if not embeddings:
        return {
            "sector": sector,
            "centroid": None,
            "member_companies": valid_members,
            "doc_count": 0,
        }

    # 计算平均向量
    emb_matrix = np.array(embeddings, dtype=np.float32)
    centroid = np.mean(emb_matrix, axis=0).tolist()

    return {
        "sector": sector,
        "centroid": centroid,
        "member_companies": valid_members,
        "doc_count": len(embeddings),
    }


# ============ 赛道匹配（余弦相似度） ============
def score_match(embedding: list, fp: dict) -> dict:
    """
    计算新文档 embedding 与赛道 centroid 的余弦相似度。
    输入: embedding (list), fp["centroid"] (list)
    返回: {"total": sim_score, "sector_name": ...}
    """
    centroid = fp.get("centroid")
    if centroid is None:
        return {"total": 0.0, "sector_name": fp["sector"].get("sector_name", "")}

    sim = cosine_similarity(embedding, centroid)
    return {
        "total": round(sim, 4),
        "sector_name": fp["sector"].get("sector_name", ""),
    }


def match_sector(embedding: list, sectors: list) -> dict:
    """
    在已有赛道中找最佳匹配（带历史加成）。
    输入: embedding (list), sectors 列表
    对每个有 centroid 的赛道计算 cosine_sim，排序取最高。
    - sim >= 0.85 → high，直接归入（confirmed）
    - sim >= 0.70 → medium，推荐确认（pending）
    - sim < 0.70 → none，推荐新赛道
    返回: {"status": "high"|"medium"|"none", "result": best, "all": [...]} 
    """
    results = []
    for sector in sectors:
        fp = build_sector_fingerprint(sector)

        # 无 centroid 的赛道跳过
        if fp["centroid"] is None:
            continue

        scores = score_match(embedding, fp)

        # 历史加成：同一赛道已有确认记录
        sector_id = sector.get("id")
        bonus = 0.0
        if sector_id:
            bonus = get_history_bonus(embedding, sector_id)

        # 已有成员直接命中 → 直接归类（sector.member_companies 包含该公司）
        company = ""
        member_hit = company in fp["member_companies"] if company else False

        results.append({
            "sector": sector,
            "fp": fp,
            "scores": scores,
            "member_hit": member_hit,
            "history_bonus": bonus,
            "total_with_bonus": round(scores["total"] + bonus * 0.01, 4),  # bonus 极小，仅打破平局
            "sim_score": scores["total"],
            "doc_count": fp["doc_count"],
        })

    # 按带加成的总分排序
    results.sort(key=lambda x: x["total_with_bonus"], reverse=True)

    if not results:
        return {"status": "none", "result": None, "all": []}

    best = results[0]
    sim_score = best["sim_score"]

    print(f"  最佳赛道: {best['sector']['sector_name']}")
    print(f"  相似度: {sim_score:.4f} | 历史加成: +{best['history_bonus']*0.01:.4f} | 最终: {best['total_with_bonus']:.4f}")
    print(f"  成员文档数: {best['doc_count']}")

    # 决策阈值
    if sim_score >= SIM_HIGH:
        return {"status": "high", "result": best, "all": results[:3]}
    elif sim_score >= SIM_MEDIUM:
        return {"status": "medium", "result": best, "all": results[:3]}
    else:
        return {"status": "none", "result": best, "all": results[:3]}


# ============ 新赛道推荐 ============
def suggest_new_sector(company: str) -> dict:
    """
    无法匹配时，从 documents 表取 pending=true 的文档，提取公司名，
    直接推荐 "{公司名}相关" 作为新赛道名。关键词改为空列表。
    """
    # 查 pending 文档找公司名（如果传入的是 embedding 对应的公司名则直接用）
    if company:
        sector_name = f"{company}相关"
    else:
        sector_name = "新赛道"

    return {
        "sector_name": sector_name,
        "keywords": [],
        "reason": "无法归入现有赛道，建议新建",
        "customers": [],
    }


# ============ 飞书推送 ============
def get_feishu_token() -> str:
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    resp = httpx.post(url, json={
        "app_id": FEISHU_APP_ID,
        "app_secret": FEISHU_APP_SECRET
    }, timeout=10)
    resp.raise_for_status()
    return resp.json()["tenant_access_token"]

def send_feishu_message(token: str, open_id: str, msg_type: str, content: dict):
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

def build_card_high_conf(company: str, match: dict) -> dict:
    """高置信：直接归类，用户只看不点"""
    sector = match["sector"]
    sim_score = match.get("sim_score", 0.0)
    return {
        "zh_cn": {
            "title": f"✅ 赛道已归类 — {company}",
            "content": [
                [{"tag": "text", "text": f"**归入赛道**: {sector['sector_name']}"}],
                [{"tag": "text", "text": f"**置信度**: 高（相似度 {sim_score:.2f}）"}],
                [{"tag": "text", "text": f"**匹配方式**: 向量相似度"}],
                [{"tag": "text", "text": f"已有成员文档数: {match.get('doc_count', 0)}"}],
                [{"tag": "text", "text": f"已有成员: {', '.join(sector.get('member_companies', [])[:5]) or '无'}"}],
                [{"tag": "text", "text": "\n直接写入赛道配置，无需操作。"}],
            ]
        }
    }

def build_card_medium_conf(company: str, match: dict) -> dict:
    """中置信：推送匹配建议，等用户确认"""
    sector = match["sector"]
    sim_score = match.get("sim_score", 0.0)
    return {
        "zh_cn": {
            "title": f"🔗 赛道匹配建议 — {company}",
            "content": [
                [{"tag": "text", "text": f"**建议归入**: {sector['sector_name']}"}],
                [{"tag": "text", "text": f"**置信度**: 中（相似度 {sim_score:.2f}）"}],
                [{"tag": "text", "text": f"**匹配方式**: 向量相似度"}],
                [{"tag": "text", "text": f"已有成员文档数: {match.get('doc_count', 0)}"}],
                [{"tag": "text", "text": f"已有成员: {', '.join(sector.get('member_companies', [])[:5]) or '无'}"}],
                [{"tag": "text", "text": "\n回复「确认」归入赛道，「拒绝」忽略，「修改」重命名"}],
            ]
        }
    }

def build_card_new_sector(company: str, suggestion: dict) -> dict:
    """无匹配：推荐新赛道"""
    return {
        "zh_cn": {
            "title": f"🆕 新赛道发现 — {company}",
            "content": [
                [{"tag": "text", "text": f"**推荐新赛道**: {suggestion['sector_name']}"}],
                [{"tag": "text", "text": f"**推荐理由**: {suggestion['reason']}"}],
                [{"tag": "text", "text": f"**建议关键词**: {', '.join(suggestion['keywords']) or '暂无'}"}],
                [{"tag": "text", "text": "\n回复「确认」创建赛道，「修改 赛道名」重命名，「拒绝」忽略"}],
            ]
        }
    }

# ============ 主流程 ============
def process_company(company: str, content: str, embedding: list):
    """
    主流程：基于向量相似度匹配赛道。
    - company: 公司名
    - content: 文档内容（已废弃用于匹配，仅用于记录）
    - embedding: 该文档的 embedding 向量（调用方传入，scan_documents.py 已计算）
    """
    print(f"\n{'='*50}")
    print(f"赛道匹配（向量模式）: {company}")
    print(f"{'='*50}")

    # Step 1: 获取已有赛道
    sectors = get_all_sectors()
    print(f"\n已有赛道: {[s['sector_name'] for s in sectors]}")

    # Step 2: 构建赛道 centroid（如尚未计算，可缓存）
    print("\n[赛道 centroid 构建]")
    fps = {}
    for s in sectors:
        fp = build_sector_fingerprint(s)
        fps[s["id"]] = fp
        if fp["centroid"] is not None:
            print(f"  {s['sector_name']}: centroid OK ({fp['doc_count']} docs)")
        else:
            print(f"  {s['sector_name']}: 无 centroid（跳过匹配）")

    # Step 3: 匹配
    match_result = match_sector(embedding, sectors)
    status = match_result["status"]

    print(f"\n[匹配结果] 状态: {status}")
    best = match_result.get("result")
    if best:
        print(f"  赛道: {best['sector']['sector_name']}")
        print(f"  相似度: {best.get('sim_score', 0.0):.4f}")

    # Step 4: 写入历史记录 + 飞书推送
    history_id = record_match_history(company, embedding, match_result, status)
    print(f"  历史记录: {history_id} ({status})")

    try:
        token = get_feishu_token()
    except Exception as e:
        print(f"  ⚠️ 飞书 token 获取失败: {e}")
        token = None

    if token:
        if status == "high":
            print(f"\n→ 高置信，直接归入赛道")
            card = build_card_high_conf(company, best)
            # 直接写入 sector_config（追加成员公司）
            _add_member_to_sector(best["sector"]["id"], company)
            # 更新历史记录为 confirmed
            update_judgment(history_id, "confirmed")
            print(f"  ✓ 已写入: {best['sector']['sector_name']} + {company}")

        elif status == "medium":
            print(f"\n→ 中置信，推送确认（待回复）")
            card = build_card_medium_conf(company, best)

        else:
            print(f"\n→ 无匹配，推荐新赛道（待回复）")
            suggestion = suggest_new_sector(company)
            print(f"  推荐: {suggestion['sector_name']}")
            card = build_card_new_sector(company, suggestion)

        result = send_feishu_message(token, FEISHU_USER_OPEN_ID, "post", card)
        print(f"  飞书推送: {result.get('code')}")


def _add_member_to_sector(sector_id: str, company: str):
    """将公司追加到赛道的成员列表"""
    url = f"{SUPABASE_URL}/rest/v1/sector_config"
    headers = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }
    # 先查现有成员
    resp = httpx.get(url, headers=headers, params={"id": f"eq.{sector_id}"}, timeout=10)
    existing = resp.json()
    if not existing:
        return
    current_members = existing[0].get("member_companies", [])
    if company not in current_members:
        current_members.append(company)
        httpx.patch(url, headers=headers, params={"id": f"eq.{sector_id}"},
                    json={"member_companies": current_members, "updated_at": "now()"}, timeout=10)


# ============ 创建赛道配置 ============
def create_sector_config(sector_name: str, company: str, embedding: list = None) -> str:
    """
    在 sector_config 表中创建新赛道，返回新赛道 id。
    - sector_name: 赛道名
    - company: 创始成员公司
    - embedding: 该公司文档的 embedding（可用于后续计算 centroid）
    """
    record = {
        "sector_name": sector_name,
        "keywords": [],
        "member_companies": [company],
        "comparable_companies": [],
        "status": "confirmed",
    }
    result = supabase_insert("sector_config", record)
    new_id = result[0]["id"] if result else None
    return new_id


# ============ 反馈处理（处理 pending 记录） ============
def process_feedback(company: str, judgment: str, modified_name: str = None,
                     suggested_sector_name: str = None):
    """
    处理用户对赛道匹配的确认/拒绝/修改。
    - judgment: 'confirmed' | 'rejected' | 'modified'
    - modified_name: 当 judgment='modified' 时传入新赛道名
    - suggested_sector_name: 当 judgment='confirmed' 且无 sector_id 时，用这个作为新赛道名
    """
    pending = get_pending_history()
    # 找对应公司的最新 pending 记录
    record = None
    for p in pending:
        if p["company"] == company:
            record = p
            break

    if not record:
        print(f"未找到 {company} 的待确认记录")
        return

    history_id = record["id"]
    sector_id = record.get("sector_config_id")

    if judgment == "confirmed":
        if sector_id:
            # 已有赛道 → 追加成员
            update_judgment(history_id, "confirmed")
            _add_member_to_sector(sector_id, company)
            sectors = get_all_sectors()
            s_name = next((s["sector_name"] for s in sectors if s["id"] == sector_id), sector_id)
            print(f"✓ 已确认: {company} → {s_name}")
        else:
            # 无已有赛道 → 创建新赛道
            new_name = suggested_sector_name or record.get("suggested_sector_name") or f"{company}相关赛道"
            try:
                new_id = create_sector_config(new_name, company)
                update_judgment(history_id, "confirmed")
                print(f"✓ 已确认+新建赛道: {company} → 「{new_name}」（id: {new_id}）")
            except Exception as e:
                print(f"✗ 创建赛道失败: {e}")

    elif judgment == "rejected":
        update_judgment(history_id, "rejected")
        print(f"✗ 已拒绝: {company}")

    elif judgment == "modified":
        if not modified_name:
            print("错误：修改赛道需要提供新名称")
            return
        try:
            new_id = create_sector_config(modified_name, company)
            update_judgment(history_id, "modified", modified_name)
            print(f"✓ 已修改+创建新赛道: {company} → 「{modified_name}」（id: {new_id}）")
        except Exception as e:
            print(f"✗ 创建赛道失败: {e}")

    # 飞书通知结果
    try:
        token = get_feishu_token()
        if judgment == "confirmed" and not sector_id:
            msg = {"text": f"赛道匹配确认：{company} → 新建赛道「{suggested_sector_name or modified_name}」✅"}
        elif judgment == "modified":
            msg = {"text": f"赛道匹配修改：{company} → 新赛道「{modified_name}」✅"}
        else:
            msg = {"text": f"赛道匹配反馈已处理：{company} → {judgment}"}
        send_feishu_message(token, FEISHU_USER_OPEN_ID, "text", msg)
    except Exception as e:
        print(f"飞书通知失败（不影响记录）: {e}")


# ============ 手动触发 ============
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("用法:")
        print("  python3 sector_matcher.py <公司名>              # 运行匹配（需embedding）")
        print("  python3 sector_matcher.py feedback <公司名> <confirmed|rejected|modified> [新赛道名]")
        print("  python3 sector_matcher.py pending               # 查看待确认记录")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "feedback":
        if len(sys.argv) < 4:
            print("用法: python3 sector_matcher.py feedback <公司> <confirmed|rejected|modified> [新赛道名]")
            sys.exit(1)
        company = sys.argv[2]
        judgment = sys.argv[3]
        modified_name = sys.argv[4] if len(sys.argv) > 4 else None
        process_feedback(company, judgment, modified_name,
                         suggested_sector_name=modified_name if judgment == "confirmed" else None)

    elif cmd == "pending":
        pending = get_pending_history()
        print(f"\n待确认记录 ({len(pending)} 条):\n")
        for p in pending:
            print(f"  [{p['id'][:8]}] {p['company']}")
            print(f"    建议赛道: {p.get('suggested_sector_name', '新建')}")
            print(f"    置信度: {p.get('confidence_score', '?')}")
            print(f"    创建: {p.get('created_at', '')}")
            print()

    else:
        # 运行匹配（默认行为）
        company = sys.argv[1]

        # 从 Supabase 找最新报告及其 embedding
        KEY = SUPABASE_SERVICE_ROLE_KEY
        url = f"{SUPABASE_URL}/rest/v1/documents"
        headers = {"apikey": KEY, "Authorization": f"Bearer {KEY}"}
        resp = httpx.get(url, headers=headers, params={
            "doc_type": "eq.analysis_report",
            "order": "created_at.desc",
            "limit": 1,
        }, timeout=15)
        data = resp.json()
        if data:
            doc = data[0]
            process_company(
                doc.get("company", company),
                doc.get("content", ""),
                doc.get("embedding", [])
            )
        else:
            print(f"未找到 {company} 的分析报告，或报告无 embedding，请先运行 scan_documents.py")
