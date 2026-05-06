#!/usr/bin/env python3
"""
未上市企业投资分析系统
8 维评估: 团队 / 市场 / 产品壁垒 / 商业模式 / 竞争护城河 / 股权结构 / 融资背调 / 业务数据
"""
import subprocess
import requests
import os
import tempfile
from datetime import datetime
import sys as _sys
_sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import credentials

# 配置
VPS_URL = credentials.get_vps_embedding_url() + "/embed"
SUPABASE_URL = credentials.supabase_url()
SB_KEY = credentials.supabase_key()

# 8 维评估维度
EVAL_DIMENSIONS = [
    ("团队", "创始人背景、经历、过往成就；团队完整性；股权激励"),
    ("市场", "市场规模、增速、政策环境"),
    ("产品与壁垒", "解决的真问题、差异化、护城河来源"),
    ("商业模式", "盈利逻辑、单位经济、规模化潜力"),
    ("竞争与护城河", "主要竞品、竞争优势、护城河持续性"),
    ("股权结构", "股权分散度、历史融资轮次、稀释预期"),
    ("融资与背调", "过往投资方、战略价值、背调资源"),
    ("业务数据", "收入增长、客户留存、关键里程碑"),
]

def db_query(sql: str) -> list[dict]:
    """通过 Supabase REST API 执行查询（不使用 exec_sql）"""
    import re, json

    headers = {
        "apikey": SB_KEY,
        "Authorization": f"Bearer {SB_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }
    base_url = f"{SUPABASE_URL}/rest/v1"

    sql_upper = sql.upper().strip()

    # ---- COUNT(*) ----
    if 'COUNT(*)' in sql_upper and 'FROM' in sql_upper:
        table_match = re.search(r'FROM\s+(\w+)', sql, re.IGNORECASE)
        if table_match:
            table = table_match.group(1)
            # 用 SELECT * + count-header 方式获取总数
            url = f"{base_url}/{table}?select=id"
            r = requests.get(url, headers=headers, timeout=15, verify=False)
            if r.status_code == 200:
                return [{"cnt": len(r.json())}]
            else:
                print(f"  REST API error {r.status_code}: {r.text[:200]}")
        return []

    # ---- SELECT ... FROM ... WHERE id = X LIMIT 1 ----
    id_match = re.search(r'FROM\s+\w+\s+WHERE\s+id\s*=\s*(\d+)', sql, re.IGNORECASE)
    if id_match and 'LIMIT' in sql_upper:
        table_match = re.search(r'FROM\s+(\w+)', sql, re.IGNORECASE)
        if table_match:
            table = table_match.group(1)
            row_id = id_match.group(1)
            # 取所有列
            url = f"{base_url}/{table}?id=eq.{row_id}&limit=1"
            r = requests.get(url, headers=headers, timeout=15, verify=False)
            if r.status_code == 200:
                rows = r.json()
                if rows:
                    return [rows[0]]
        return []

    # ---- 通用：FROM table (simple where parsing) ----
    table_match = re.search(r'FROM\s+(\w+)', sql, re.IGNORECASE)
    if not table_match:
        return []
    table = table_match.group(1)

    # 解析 WHERE company ilike '...' 等
    params = []
    where_match = re.search(r'WHERE\s+(.+?)\s*(?:ORDER|LIMIT|$)', sql, re.IGNORECASE | re.DOTALL)
    if where_match:
        where_clause = where_match.group(1).strip()
        # 解析 key ilike 'value' 或 key = 'value'
        ilike_match = re.search(r"(\w+)\s+ilike\s+'%?([^'%]+)%?'", where_clause, re.IGNORECASE)
        if ilike_match:
            col, val = ilike_match.group(1), ilike_match.group(2)
            params.append(f"{col}=ilike.*{val}*")
        eq_match = re.search(r"(\w+)\s*=\s*'([^']+)'", where_clause)
        if eq_match:
            col, val = eq_match.group(1), eq_match.group(2)
            params.append(f"{col}=eq.{val}")

    # ORDER BY embedding <=> ...
    order_match = re.search(r'ORDER BY\s+\w+\s*<=>\s*', sql, re.IGNORECASE)
    if order_match:
        # 向量排序不支持，跳过，用默认顺序
        pass

    limit_match = re.search(r'LIMIT\s+(\d+)', sql, re.IGNORECASE)
    limit = int(limit_match.group(1)) if limit_match else 5

    url = f"{base_url}/{table}?select=*"
    for p in params:
        url += f"&{p}"
    if limit:
        url += f"&limit={limit}"

    r = requests.get(url, headers=headers, timeout=15, verify=False)
    if r.status_code == 200:
        return r.json()
    else:
        print(f"  REST API error {r.status_code}: {r.text[:200]}")
        return []

def search_documents(query: str, top_k: int = 5) -> list[dict]:
    """基于公司名搜索（无 exec_sql，跳过向量排序）"""
    try:
        # 从 query 提公司名（第一个词或引号内内容）
        import re
        m = re.search(r'["\u201c\u201d](.+?)["\u201c\u201d]', query)
        if m:
            company_name = m.group(1)
        else:
            # 取第一个词
            words = query.strip().split()
            company_name = words[0] if words else ''

        headers = {
            "apikey": SB_KEY,
            "Authorization": f"Bearer {SB_KEY}",
            "Content-Type": "application/json"
        }
        base_url = f"{SUPABASE_URL}/rest/v1"
        url = f"{base_url}/documents?company=ilike.*{company_name}*&select=*&limit={top_k}"
        r = requests.get(url, headers=headers, timeout=15, verify=False)
        if r.status_code == 200:
            rows = r.json()
            # 补充 similarity 字段（无向量排序，统一给 1.0）
            for row in rows:
                row['similarity'] = 1.0
            return rows
        else:
            print(f"  搜索异常: {r.status_code} {r.text[:200]}")
            return []
    except Exception as e:
        print(f"  搜索异常: {e}")
        return []

def get_market_benchmarks(industry_keywords: list[str] = None) -> list[dict]:
    """从基准库查询相关行业的市场基准数据（支持行业均值和可比公司）

    注意：PostgREST OR 查询对 ilike 特殊字符处理有限制，故用全量查+客户端过滤。
    """
    import urllib.parse

    headers = {
        "apikey": SB_KEY,
        "Authorization": f"Bearer {SB_KEY}",
        "Content-Type": "application/json"
    }
    base_url = f"{SUPABASE_URL}/rest/v1"

    # 先查全量 market_benchmark（limit 放宽）
    url = f"{base_url}/documents?doc_type=eq.market_benchmark&select=*&limit=100"
    r = requests.get(url, headers=headers, timeout=15, verify=False)
    if r.status_code != 200:
        print(f"  基准查询异常: {r.status_code} {r.text[:200]}")
        return []

    all_benchmarks = r.json()
    if not industry_keywords:
        return all_benchmarks

    # 客户端过滤：company 或 content 含关键词
    def score(b):
        text = ((b.get('company') or '') + ' ' + (b.get('content') or '')).lower()
        s = 0
        for kw in industry_keywords:
            if kw.lower() in text:
                s += 1
        return s

    filtered = [b for b in all_benchmarks if score(b) > 0]
    # 按匹配度降序
    filtered.sort(key=score, reverse=True)
    return filtered[:30]

def parse_benchmark_content(content: str) -> dict:
    """把基准 content 文本解析成结构化 dict（中文字段冒号分隔）"""
    result = {}
    for line in content.split('\n'):
        line = line.strip()
        if '：' in line:  # 中文冒号
            key, _, val = line.partition('：')
            key = key.strip()
            val = val.strip()
            if key and val:
                result[key] = val
    return result

def build_llm_context(company: str, docs: list[dict], benchmarks: list[dict] = None) -> str:
    """构建 LLM 分析上下文"""
    parts = [f"## 待分析公司: {company}\n"]

    if docs:
        parts.append(f"\n## 相关文档 ({len(docs)} 份)\n")
        for i, doc in enumerate(docs, 1):
            content = doc.get('content', '')[:800]
            sim = float(doc.get('similarity', 0))
            parts.append(
                f"### 文档 {i} [{doc.get('doc_type', 'unknown')} | 相似度 {sim:.2f}]\n"
                f"- 公司: {doc.get('company', '未知')}\n"
                f"- 来源: {doc.get('source', '未知')}\n"
                f"- 内容:\n{content}\n"
            )
    else:
        parts.append("\n## 相关文档: 未在数据库中找到匹配文档\n")

    # 市场基准数据（结构化展示）
    if benchmarks:
        parts.append(f"\n## 市场基准数据（真实数据来源，可直接引用）\n")
        parts.append("以下数据来自 akshare/东方财富 实时抓取的 A 股行业均值和可比公司估值，"
                     "**可直接在分析中引用，格式为：来源 + 数值 + 日期**。\n")
        for i, b in enumerate(benchmarks, 1):
            parsed = parse_benchmark_content(b.get('content', ''))
            btype = parsed.get('数据类型', b.get('company', ''))
            source = parsed.get('数据来源', '未知')
            fetch_date = parsed.get('抓取日期', '')

            parts.append(f"### 基准 {i} [{btype}]\n")
            parts.append(f"**来源**: {source} | **抓取日期**: {fetch_date}\n")

            if btype == '行业财务均值':
                # 行业均值格式化输出
                industry = parsed.get('行业名称', '')
                n = parsed.get('成分股数量', '')
                samples_gm = parsed.get('有效样本_毛利率', '')
                samples_nm = parsed.get('有效样本_净利率', '')
                samples_roe = parsed.get('有效样本_ROE', '')
                report_type = parsed.get('报告类型', '')
                report_date = parsed.get('数据报告期', '')

                parts.append(f"**行业**: {industry} | **成分股数**: {n} | **报告期**: {report_date}（{report_type}）\n")
                parts.append("| 指标 | 中位数 | 均值 |\n")
                parts.append("|------|--------|------|\n")

                rows = [
                    ("毛利率", parsed.get('毛利率_中位数'), parsed.get('毛利率_均值'), samples_gm),
                    ("净利率", parsed.get('净利率_中位数'), parsed.get('净利率_均值'), samples_nm),
                    ("ROE", parsed.get('ROE_中位数'), parsed.get('ROE_均值'), samples_roe),
                    ("营收增速(%)", parsed.get('营收增速_中位数'), parsed.get('营收增速_均值'), ''),
                    ("净利润增速(%)", parsed.get('净利润增速_中位数'), parsed.get('净利润增速_均值'), ''),
                ]
                for metric, med, mean, sample in rows:
                    if med or mean:
                        sample_str = f" (n={sample})" if sample else ""
                        parts.append(f"| {metric} | {med or '-'} | {mean or '-'}{sample_str} |\n")

            elif btype == '可比公司估值':
                # 可比公司格式化输出
                name = parsed.get('股票名称', '')
                code = parsed.get('股票代码', '')
                tag = parsed.get('公司标签', '')
                price = parsed.get('最新价', '-')
                pe = parsed.get('市盈率', '-')
                pb = parsed.get('市净率', '-')

                parts.append(f"**公司**: {name}({code}) | **标签**: {tag}  \n")
                parts.append(f"最新价: **{price}** | 市盈率(PE): **{pe}** | 市净率(PB): **{pb}**\n")

            parts.append("\n")
    else:
        parts.append("\n## 市场基准数据: 库中无相关行业基准\n")

    return "\n".join(parts)

def call_hermes(prompt: str, system: str = "") -> str:
    """调用 LLM (nengpa / MiniMax-M2.7)"""
    import openai

    # 读取 MINIMAX_API_KEY (nengpa)
    nengpa_key = ''
    for var in ['MINIMAX_API_KEY', 'NVIDIA_API_KEY']:
        nengpa_key = os.environ.get(var, '')
        if nengpa_key:
            break
    if not nengpa_key:
        try:
            with open(os.path.expanduser('~/.hermes/.env')) as f:
                for line in f:
                    if line.startswith('MINIMAX_API_KEY=') or line.startswith('NVIDIA_API_KEY='):
                        k = line.split('=', 1)[1].strip()
                        if k not in ('***', ''):
                            nengpa_key = k
                            break
        except:
            pass

    # nengpa endpoint (MiniMax)
    client = openai.OpenAI(
        api_key="sk-cp-43f555710f86c754db61d37fb4039514f25b05d95097a62cb971d10575f7f727",
        base_url="https://api.nengpa.com/v1"
    )

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    try:
        response = client.chat.completions.create(
            model="MiniMax-M2.7",
            messages=messages,
            temperature=0.3,
            max_tokens=8000
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"LLM 调用失败: {e}"

def save_analysis_report(company: str, analysis: str, benchmarks_used: list[dict] = None,
                          model_version: str = "v1") -> dict:
    """分析完成后自动写入 Supabase，版本自动递增"""
    from datetime import datetime

    SB_SVC_KEY = credentials.supabase_key()
    if not SB_SVC_KEY:
        return {"status": "skip", "reason": "无法从 credentials.json 获取 service_role key"}

    headers = {
        "apikey": SB_SVC_KEY,
        "Authorization": f"Bearer {SB_SVC_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }

    base_url = f"{SUPABASE_URL}/rest/v1"

    # 查当前最大版本号
    try:
        r = requests.get(
            f"{base_url}/documents?company=eq.{company}&doc_type=eq.analysis_report&select=id,source&order=created_at.desc&limit=1",
            headers=headers, timeout=10, verify=False
        )
        existing = r.json() if r.status_code == 200 else []
    except:
        existing = []

    # 版本号：company_v1, company_v2, ...
    version_num = len(existing) + 1
    version_tag = f"{model_version}_{version_num}"

    # 构造摘要（前3行投资亮点+结论）
    summary_lines = []
    in_highlights = False
    in_conclusion = False
    for line in analysis.split('\n'):
        if '## 投资亮点' in line or '## 关注点' in line:
            in_highlights = '亮点' in line
            in_conclusion = '结论' in line or '推荐' in line
            continue
        if in_highlights and line.startswith('###'):
            in_highlights = False
        if line.startswith('## ') and ('关注点' in line or '一、公司' in line):
            in_conclusion = False
        if (in_highlights or in_conclusion) and line.strip() and not line.startswith('#'):
            summary_lines.append(line.strip())
        if len(summary_lines) >= 6:
            break

    summary = ' '.join(summary_lines[:6])
    if len(summary) > 500:
        summary = summary[:500] + '...'

    # 基准摘要
    benchmark_refs = []
    if benchmarks_used:
        for b in benchmarks_used[:5]:
            parsed = parse_benchmark_content(b.get('content', ''))
            btype = parsed.get('数据类型', '')
            if btype == '行业财务均值':
                benchmark_refs.append(f"行业均值|{parsed.get('行业名称','')}")
            elif btype == '可比公司估值':
                benchmark_refs.append(f"可比|{parsed.get('股票名称','')}")

    # 查该公司的 embedding（用于后续赛道匹配）
    # 注意：Supabase REST API 对 vector 列的 not.is.null 过滤有问题，改用 ilike 模糊匹配公司名，然后逐个检查 embedding
    company_emb = None
    try:
        import json as _json
        client = credentials.supabase_client()
        re = client.get(
            f"{base_url}/documents",
            headers=headers,
            params={"company": f"ilike.*{company}*", "select": "embedding,doc_type,company", "order": "created_at.desc", "limit": 10},
            timeout=15
        )
        emb_data = re.json() if re.status_code == 200 else []
        for d in emb_data:
            raw = d.get('embedding')
            if raw is None:
                continue
            if isinstance(raw, str):
                company_emb = _json.loads(raw)
            elif isinstance(raw, list):
                company_emb = raw
            if company_emb and len(company_emb) == 384:
                break
            company_emb = None
    except Exception as _e:
        pass

    payload = {
        "company": company,
        "doc_type": "analysis_report",
        "source": f"analyze_company.py/{version_tag}",
        "content": analysis[:10000],  # 截断防超限
    }
    if company_emb:
        payload["embedding"] = company_emb

    try:
        r = requests.post(f"{base_url}/documents",
                          headers=headers, json=payload, timeout=20, verify=False)
        if r.status_code in (200, 201):
            result = r.json()
            record_id = result[0].get('id') if isinstance(result, list) else None
            return {"status": "saved", "version": version_tag, "id": record_id}
        else:
            return {"status": "error", "detail": r.text[:200]}
    except Exception as e:
        return {"status": "error", "detail": str(e)}

def get_previous_reports(company: str, limit: int = 3) -> list[dict]:
    """获取同一公司历史分析报告，按时间倒序"""
    SB_SVC_KEY = credentials.supabase_key()
    if not SB_SVC_KEY:
        return []

    headers = {
        "apikey": SB_SVC_KEY,
        "Authorization": f"Bearer {SB_SVC_KEY}",
        "Content-Type": "application/json"
    }
    base_url = f"{SUPABASE_URL}/rest/v1"

    # 查询该公司历史报告（按时间倒序）
    url = (f"{base_url}/documents"
           f"?doc_type=eq.analysis_report"
           f"&company=eq.{company}"
           f"&select=id,source,content,created_at"
           f"&order=created_at.desc"
           f"&limit={limit}")
    r = requests.get(url, headers=headers, timeout=15, verify=False)
    if r.status_code != 200:
        return []
    return r.json()

def extract_report_summary(content: str) -> dict:
    """从报告内容中提取关键字段，便于对比"""
    import re
    lines = content.split('\n')
    result = {
        "highlights": [],
        "concerns": [],
        "score": None,
        "conclusion": None,
    }
    section = None
    for line in lines:
        stripped = line.strip()
        if stripped == '## 投资亮点':
            section = 'highlights'
            continue
        elif stripped == '## 关注点':
            section = 'concerns'
            continue
        elif '## 三、综合评分' in stripped or '## 综合评分' in stripped:
            section = 'score'
            continue
        elif '## 五、投资建议' in stripped or '## 结论' in stripped:
            section = 'conclusion'
            continue
        elif stripped.startswith('## ') and section:
            section = None

        if section == 'highlights' and stripped and not stripped.startswith('#'):
            result["highlights"].append(stripped[:100])
        elif section == 'concerns' and stripped and not stripped.startswith('#'):
            result["concerns"].append(stripped[:100])
        elif section == 'score' and ('总分' in stripped or re.search(r'\b\d{2,3}\b', stripped)):
            if not result["score"]:
                result["score"] = stripped[:80]
        elif section == 'conclusion' and stripped and not stripped.startswith('#'):
            result["conclusion"] = stripped[:200]
            section = None  # 只取第一条结论

    return result

def generate_analysis_prompt(company: str, docs: list[dict], benchmarks: list[dict] = None,
                              previous_reports: list[dict] = None) -> str:
    """生成完整分析 prompt"""
    context = build_llm_context(company, docs, benchmarks)
    dims = "\n".join([f"{i+1}. **{n}** — {d}" for i, (n, d) in enumerate(EVAL_DIMENSIONS)])

    # 历史报告对比区块
    prev_section = ""
    if previous_reports:
        prev_section += "\n## 历史报告对比（本轮 vs 历史）\n"
        prev_section += "该公司已有历史分析报告，**本轮分析需重点标注与历史的差异**。\n"
        for i, prev in enumerate(previous_reports, 1):
            summary = extract_report_summary(prev.get('content', ''))
            date = prev.get('created_at', '')[:10]
            version = prev.get('source', '')
            prev_section += f"\n### 历史版本 {i} [{date}] [{version}]\n"
            if summary.get('conclusion'):
                prev_section += f"- 结论：{summary['conclusion']}\n"
            if summary.get('score'):
                prev_section += f"- 评分：{summary['score']}\n"
            if summary.get('highlights'):
                prev_section += f"- 亮点：{'；'.join(summary['highlights'][:3])}\n"
            if summary.get('concerns'):
                prev_section += f"- 关注点：{'；'.join(summary['concerns'][:3])}\n"
        prev_section += "\n**本轮分析要求**：在输出报告时，每个维度的结论必须明确说明\"与上次相比有何变化\"，变化处用 **▲ 变好 / ▼ 变差 / → 持平** 标注。\n"

    return f"""你是资深 VC/PE 投资分析师。请基于提供的文档和行业基准数据，对目标公司进行严格的投资分析。

【核心原则】区分"公司声称"与"可验证事实"。不轻信 BP 原文，不人云亦云，有对比才有判断。

{prev_section}
{context}

---

## 8 维评估框架

{dims}

---

## 输出要求

请严格按照以下格式输出（Markdown）：

### 投资亮点
从文档中提炼 3-5 个最有价值的投资看点，每点一行，简练有力。

### 关注点
列出 2-4 个需要进一步验证的核心问题，不回避短板。

### 一、公司基本信息
从文档中提取公司名称、行业、业务描述、成立时间、团队背景。

### 二、8 维评估
对每个维度进行独立分析，说明依据、优劣势、风险点，每个维度 100-300 字。
如果文档中无相关信息，明确说明"文档未提供"。

### 三、综合评分
总分 0-100，给出评分理由。

### 四、风险评级
从以下 5 个维度各给出 1-5 星风险评级（★ 越多风险越高）：
- 经营风险（团队执行力、市场波动）
- 技术风险（替代技术、研发失败）
- 竞争风险（龙头挤压、价格战）
- 财务风险（现金流、负债、收入结构）
- 退出风险（IPO/并购路径、时间不确定）

并给出总体风险判断。

### 五、投资建议
- 推荐理由（最多 3 点）
- 主要风险（最多 3 点）
- 结论（强烈推荐 / 推荐 / 中性 / 谨慎 / 不推荐）

---

保持客观专业，不回避风险，不夸大机会。"""

def main():
    import sys

    if len(sys.argv) < 2:
        print("用法:")
        print("  python analyze_company.py <公司名称>")
        print("  python scan_documents.py <文件夹路径>  # 先入库文档")
        sys.exit(1)

    company = sys.argv[1]

    print(f"\n{'='*60}")
    print(f"未上市企业投资分析")
    print(f"{'='*60}")
    print(f"公司: {company}\n")

    # Step 1: 检查文档库
    print("1. 检查文档库...")
    count_rows = db_query("SELECT COUNT(*) as cnt FROM documents;")
    total = 0
    if count_rows:
        val = count_rows[0].get('cnt', '0')
        total = int(val)
    print(f"   文档库共 {total} 份")

    if total == 0:
        print("   ⚠️  文档库为空，请先运行 scan_documents.py 入库")
        print("   示例: python scan_documents.py /path/to/docs")
        sys.exit(1)

    # Step 2: RAG 检索
    print(f"\n2. RAG 检索...")
    query = f"{company} 团队 产品 商业 模式 融资 股权 行业"
    results = search_documents(query, top_k=5)
    print(f"   找到 {len(results)} 份相关文档")

    for d in results:
        sim = float(d.get('similarity', 0))
        print(f"   - [{d.get('doc_type')}] {d.get('company')} ({sim:.2f})")

    # Step 3: 补充文档内容
    print(f"\n3. 加载文档内容...")
    docs_with_content = []
    for d in results:
        rows = db_query(f"SELECT content FROM documents WHERE id = {d.get('id')} LIMIT 1;")
        if rows:
            d['content'] = rows[0].get('content', '')
            docs_with_content.append(d)
    results = docs_with_content

    # Step 3b: 查询市场基准数据
    print(f"\n3b. 查询市场基准数据...")
    industry_keywords = ['半导体', '激光雷达', '光通信', '消费电子', '无人机', '芯片', '激光设备']
    benchmarks = get_market_benchmarks(industry_keywords)
    print(f"   找到 {len(benchmarks)} 条相关基准")
    for b in benchmarks:
        parsed = parse_benchmark_content(b.get('content', ''))
        btype = parsed.get('数据类型', '')
        company_name = b.get('company', '')
        if btype == '行业财务均值':
            ind = parsed.get('行业名称', '')
            nm_med = parsed.get('净利率_中位数', '-')
            roe_med = parsed.get('ROE_中位数', '-')
            rev_med = parsed.get('营收增速_中位数', '-')
            date = parsed.get('数据报告期', '')
            print(f"   ✓ 行业均值 [{ind}] 净利率{ nm_med}% ROE{roe_med}% 营收增速{rev_med}% ({date})")
        elif btype == '可比公司估值':
            name = parsed.get('股票名称', '')
            price = parsed.get('最新价', '-')
            pe = parsed.get('市盈率', '-')
            print(f"   ✓ 可比公司 {name} 最新价{price} PE{pe}x")

    # Step 4: LLM 分析
    print(f"\n4. AI 8 维分析（含基准对照）...")
    print("   (稍等...)\n")

    system_prompt = """你是资深 VC/PE 投资分析师，专注未上市企业股权投资。
擅长从 BP、财务数据、尽调报告中提取关键信息。
风格：客观、专业、有深度。
核心要求：区分"公司声称"与"可验证事实"，每个判断必须有市场基准对照。"""

    # 查历史版本
    previous_reports = get_previous_reports(company, limit=3)
    if previous_reports:
        print(f"   发现 {len(previous_reports)} 个历史版本，将进行对比分析")
        for i, prev in enumerate(previous_reports, 1):
            date = prev.get('created_at', '')[:10]
            version = prev.get('source', '')
            print(f"     v{i}: {date} [{version}]")
        print()

    analysis = call_hermes(
        generate_analysis_prompt(company, results, benchmarks, previous_reports),
        system=system_prompt
    )

    # Step 4b: 输出
    print(f"{'='*60}")
    print(f"分析报告: {company}")
    print(f"{'='*60}\n")
    print(analysis)

    # 保存
    safe_name = company.replace(' ', '_').replace('/', '_')
    report_file = f"/tmp/investment_report_{safe_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    with open(report_file, 'w', encoding='utf-8') as f:
        f.write(f"# {company} 投资分析报告\n")
        f.write(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        f.write(analysis)

    # Step 5: 写回 Supabase（版本自动递增）
    print(f"\n5. 写回 Supabase...")
    save_result = save_analysis_report(company, analysis, benchmarks)
    if save_result.get("status") == "saved":
        print(f"   ✓ 已写入 Supabase，版本: {save_result.get('version')}，ID: {save_result.get('id')}")
    elif save_result.get("status") == "skip":
        print(f"   ⚠️ 跳过写入: {save_result.get('reason')}（可手动设置 SUPABASE_SERVICE_ROLE_KEY 环境变量启用）")
    else:
        print(f"   ✗ 写入失败: {save_result.get('detail', '未知错误')}")

    # Step 6: 赛道匹配（分析报告入库后自动触发）
    print(f"\n6. 赛道匹配...")
    try:
        import os as _os
        venv_python = _os.path.join(
            _os.path.dirname(_os.path.abspath(__file__)),
            "venv", "bin", "python3"
        )
        result = subprocess.run(
            [venv_python, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "sector_matcher.py"), company],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode == 0:
            for line in result.stdout.strip().split("\n"):
                if line.strip():
                    print(f"   {line}")
        else:
            print(f"   ⚠️ 赛道匹配异常: {result.stderr.strip()[:100]}")
    except Exception as e:
        print(f"   ⚠️ 赛道匹配跳过: {e}")

    print(f"\n{'='*60}")
    print(f"报告已保存: {report_file}")
    print(f"{'='*60}")

if __name__ == '__main__':
    main()
