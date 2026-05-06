#!/usr/bin/env python3
"""
赛道匹配历史回填脚本
- 遍历 documents 表中所有真实公司文档
- 对 analysis_report / bp 类型文档跑 sector_matcher
- 回填 sector_match_history（confirmed 静默写入，pending 推送飞书）
- 对已确认的历史记录（无 pending 记录）直接写 confirmed
"""
import os
import sys
import re
import httpx

# 统一从 credentials.py 读取
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _SCRIPT_DIR)
from credentials import supabase_url, supabase_key

SUPABASE_URL = supabase_url()
KEY = supabase_key()
HEADERS = {"apikey": KEY, "Authorization": f"Bearer {KEY}"}

# 避免循环 import
sys.path.insert(0, _SCRIPT_DIR)

from sector_matcher import (
    extract_three_dimensions,
    match_sector,
    suggest_new_sector,
    get_all_sectors,
    get_pending_history,
    record_match_history,
    update_judgment,
    create_sector_config,
    get_feishu_token,
    send_feishu_message,
    FEISHU_USER_OPEN_ID,
    _add_member_to_sector,
)


def get_real_docs():
    """获取所有真实公司的 documents（排除测试公司，每公司取最新一条）"""
    resp = httpx.get(f'{SUPABASE_URL}/rest/v1/documents', headers=HEADERS, params={"order": "created_at.desc"}, timeout=15)
    resp.raise_for_status()
    all_docs = resp.json()

    # 过滤真实公司 + 有内容的文档
    skip_companies = {'测试公司', '', None}
    skip_prefixes = ('市场基准',)
    real = []
    for d in all_docs:
        company = d.get('company', '')
        if company in skip_companies or company.startswith(skip_prefixes):
            continue
        if not d.get('content', '').strip():
            continue
        real.append(d)

    # 每公司只保留最新一条（避免灵明光子 BP + analysis_report 重复）
    seen = {}
    for d in real:
        company = d['company']
        # 优先保留 analysis_report，其次 bp
        priority = {'analysis_report': 0, 'bp': 1, 'unknown': 2}
        p = priority.get(d.get('doc_type', 'unknown'), 2)
        if company not in seen or p < priority.get(seen[company].get('doc_type', 'unknown'), 2):
            seen[company] = d
    return list(seen.values())


def backfill_company(doc: dict, silent: bool = False):
    """
    对单条文档跑赛道匹配，回填 history。
    - silent=True: confirmed 结果不推送飞书（用于已知结果的存量数据回填）
    - silent=False: pending 结果推送飞书等确认
    返回 (status, message)
    """
    company = doc['company']
    content = doc['content']
    doc_type = doc.get('doc_type', 'unknown')
    doc_id = doc.get('id')

    print(f"\n{'─'*50}")
    print(f"处理: {company} ({doc_type}) id={doc_id}")
    print(f"内容字数: {len(content)}")

    # 1. LLM 三维度提取（用于历史记录）
    dims = extract_three_dimensions(content, company=company)
    print(f"  技术: {dims['tech'][:5]}")
    print(f"  应用: {dims['apps'][:5]}")
    print(f"  定位: {dims['positioning'][:5]}")

    # 2. 向量匹配（用文档的 embedding）
    embedding = doc.get('embedding')
    if embedding is None:
        print("  ⚠️ 无 embedding，跳过向量匹配")
        return 'skipped', 'no_embedding'

    # embedding 可能是 JSON 字符串
    import json as _json
    if isinstance(embedding, str) and embedding.startswith('['):
        embedding = _json.loads(embedding)

    sectors = get_all_sectors()
    print(f"  已有赛道: {[s['sector_name'] for s in sectors]}")

    result = match_sector(embedding, sectors)
    status = result['status']
    best = result.get('result')

    if best:
        print(f"  匹配状态: {status}, 得分: {best.get('sim_score', 0):.4f}")
        print(f"  赛道: {best['sector']['sector_name']}")
    else:
        print(f"  匹配状态: {status}（无匹配赛道）")

    # 3. 写入 history（embedding 传入向量，dims 含 LLM 提取结果）
    history_id = record_match_history(company, embedding, result, status, dims)
    print(f"  history 写入: {history_id[:8] if history_id else 'FAIL'} ({status})")

    if status == 'high':
        # 高置信 → 直接归类
        sector = best['sector']
        _add_member_to_sector(sector['id'], company)
        update_judgment(history_id, 'confirmed')
        msg = f"✓ 已归入赛道: {company} → {sector['sector_name']}（历史回填）"
        print(f"  {msg}")
        if not silent:
            try:
                token = get_feishu_token()
                send_feishu_message(token, FEISHU_USER_OPEN_ID, 'text', {'text': msg})
            except Exception:
                pass
        return 'confirmed', sector['sector_name']

    elif status == 'medium':
        # 中置信 → 已有赛道建议追加
        sector = best['sector']
        if not silent:
            # 推送飞书等确认
            card = {
                'zh_cn': {
                    'title': f'🔗 赛道匹配建议（历史回填）— {company}',
                    'content': [
                        [{'tag': 'text', 'text': f'建议归入: {sector["sector_name"]}'}],
                        [{'tag': 'text', 'text': f'置信度: 中（{best["scores"]["total"]}分）'}],
                        [{'tag': 'text', 'text': f'技术匹配: {best["scores"]["tech_hits"]}'}],
                        [{'tag': 'text', 'text': f'应用匹配: {best["scores"]["apps_hits"]}'}],
                        [{'tag': 'text', 'text': '\n回复「确认」归入赛道，「拒绝」忽略'}],
                    ]
                }
            }
            try:
                token = get_feishu_token()
                send_feishu_message(token, FEISHU_USER_OPEN_ID, 'post', card)
            except Exception as e:
                print(f"  飞书推送失败: {e}")
        else:
            # 回填模式 → 直接确认
            _add_member_to_sector(sector['id'], company)
            update_judgment(history_id, 'confirmed')
            print(f"  回填静默确认: {company} → {sector['sector_name']}")
            return 'confirmed', sector['sector_name']

        return 'pending', sector['sector_name']

    else:
        # 无匹配 → 推荐新赛道
        suggestion = suggest_new_sector(dims)
        sector_name = suggestion['sector_name']
        print(f"  推荐新赛道: {sector_name}")

        if not silent:
            card = {
                'zh_cn': {
                    'title': f'🆕 新赛道发现（历史回填）— {company}',
                    'content': [
                        [{'tag': 'text', 'text': f'推荐新赛道: {sector_name}'}],
                        [{'tag': 'text', 'text': f'推荐理由: {suggestion["reason"]}'}],
                        [{'tag': 'text', 'text': f'建议关键词: {", ".join(suggestion["keywords"][:8])}'}],
                        [{'tag': 'text', 'text': '\n回复「确认」创建赛道，「修改 赛道名」重命名，「拒绝」忽略'}],
                    ]
                }
            }
            try:
                token = get_feishu_token()
                send_feishu_message(token, FEISHU_USER_OPEN_ID, 'post', card)
            except Exception as e:
                print(f"  飞书推送失败: {e}")
        else:
            # 回填模式 → 静默创建赛道并确认
            from sector_matcher import supabase_update
            new_id = create_sector_config(sector_name, dims, company)
            # 同步更新 history 的 sector_config_id（创建前 record 时是旧赛道）
            supabase_update('sector_match_history', {'id': f'eq.{history_id}'}, {
                'sector_config_id': new_id,
                'suggested_sector_name': sector_name,
                'judgment': 'confirmed',
                'judged_at': 'now()',
            })
            print(f"  回填静默创建: {company} → 「{sector_name}」（id: {new_id}）")
            return 'confirmed', sector_name

        return 'pending', sector_name


def main():
    import argparse
    parser = argparse.ArgumentParser(description='赛道匹配历史回填')
    parser.add_argument('--company', help='只处理指定公司')
    parser.add_argument('--silent', action='store_true', help='confirmed 结果不推送飞书（用于回填）')
    parser.add_argument('--dry-run', action='store_true', help='只显示不写入')
    args = parser.parse_args()

    docs = get_real_docs()
    print(f"找到 {len(docs)} 条真实公司文档")

    if args.company:
        docs = [d for d in docs if args.company in d['company']]
        print(f"过滤后: {len(docs)} 条")

    results = []
    for doc in docs:
        try:
            status, sector = backfill_company(doc, silent=args.silent)
            results.append((doc['company'], status, sector))
        except Exception as e:
            print(f"  ✗ 错误: {e}")
            results.append((doc['company'], 'error', str(e)))

    print(f"\n{'='*50}")
    print("回填结果:")
    for company, status, sector in results:
        icon = '✓' if status == 'confirmed' else '?' if status == 'pending' else '✗'
        print(f"  {icon} {company}: {status} → {sector}")


if __name__ == '__main__':
    main()
