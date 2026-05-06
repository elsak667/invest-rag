#!/usr/bin/env python3
"""
投资建议分析系统
输入：股票代码
输出：基于财务数据的投资建议报告
"""
import subprocess
from datetime import datetime

WORKDIR = "/Users/els/scripts/invest_rag"

def db_query(sql: str) -> list[dict]:
    """通过 supabase CLI 执行 SQL 查询"""
    import tempfile
    with tempfile.NamedTemporaryFile(mode='w', suffix='.sql', delete=False, encoding='utf-8') as f:
        f.write(sql)
        sql_file = f.name
    r = subprocess.run(
        ['supabase', 'db', 'query', '--linked', '-f', sql_file],
        capture_output=True, text=True, timeout=30, cwd=WORKDIR
    )
    import os
    os.unlink(sql_file)
    if r.returncode != 0:
        return []
    # 解析 Unicode 框字符输出
    lines = [l for l in r.stdout.split('\n') if 'Initialising' not in l and l.strip() and not l.startswith('+')]
    if len(lines) < 3:
        return []
    headers = [h.strip() for h in lines[1].split('│') if h.strip()]
    results = []
    for line in lines[2:]:
        cells = [c.strip() for c in line.split('│')]
        cells = [c for c in cells if c]
        if len(cells) == len(headers):
            results.append(dict(zip(headers, cells)))
    return results

def to_float(v):
    if v is None or v == '' or v == 'NULL':
        return 0.0
    try:
        return float(v)
    except:
        return 0.0

EVAL_DIMENSIONS = [
    "行业前景", "竞争壁垒", "团队背景", "产品技术",
    "股权结构", "估值参考", "退出可能性", "潜在风险"
]

def analyze_stock(stock_code: str) -> dict:
    """分析单只股票"""
    sql = f"SELECT * FROM stock_profiles WHERE stock_code = '{stock_code}' LIMIT 1;"
    rows = db_query(sql)
    if not rows:
        return None
    p = rows[0]

    revenue = to_float(p.get('revenue'))
    net_profit = to_float(p.get('net_profit'))
    total_assets = to_float(p.get('total_assets'))
    total_liabilities = to_float(p.get('total_liabilities'))
    market_cap = to_float(p.get('market_cap'))

    # 财务指标
    net_margin = (net_profit / revenue * 100) if revenue > 0 else 0
    debt_ratio = (total_liabilities / total_assets * 100) if total_assets > 0 else 0
    pe = (market_cap / net_profit) if net_profit > 0 else None

    # 估值评分 (简单启发式)
    score = 50
    if pe:
        if pe < 15:
            score += 20
        elif pe < 30:
            score += 10
        elif pe > 60:
            score -= 20
    if net_margin > 30:
        score += 15
    elif net_margin < 10:
        score -= 10
    if debt_ratio < 50:
        score += 10
    elif debt_ratio > 80:
        score -= 15
    score = max(0, min(100, score))

    # 估值标签
    if score >= 80:
        label = "强烈推荐"
    elif score >= 65:
        label = "推荐"
    elif score >= 50:
        label = "中性"
    elif score >= 35:
        label = "谨慎"
    else:
        label = "不推荐"

    return {
        'stock_code': p.get('stock_code'),
        'stock_name': p.get('stock_name'),
        'industry': p.get('industry'),
        'market_cap': market_cap,
        'pe': p.get('pe_ratio'),
        'pb': p.get('pb_ratio'),
        'revenue': revenue,
        'net_profit': net_profit,
        'net_margin': net_margin,
        'debt_ratio': debt_ratio,
        'score': score,
        'label': label,
        'report_date': p.get('latest_report_date'),
    }

def generate_report(stock_code: str) -> str:
    """生成投资建议报告"""
    print(f"\n{'='*60}")
    print(f"投资建议分析报告")
    print(f"{'='*60}\n")

    data = analyze_stock(stock_code)
    if not data:
        return f"❌ 未找到股票 {stock_code} 的数据"

    print(f"股票: {data['stock_name']} ({data['stock_code']})")
    print(f"行业: {data['industry']}")
    print(f"评分: {data['score']}/100 [{data['label']}]\n")

    lines = [
        f"# {data['stock_name']} ({data['stock_code']}) 投资建议报告",
        f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## 一、基本信息",
        f"| 指标 | 值 |",
        f"| --- | --- |",
        f"| 股票代码 | {data['stock_code']} |",
        f"| 股票名称 | {data['stock_name']} |",
        f"| 所属行业 | {data['industry']} |",
        f"| 总市值 | {data['market_cap']:.2e} 元 |",
        f"| 市盈率(PE) | {data['pe']} |",
        f"| 市净率(PB) | {data['pb']} |",
        f"| 最新报告期 | {data['report_date']} |",
        "",
        "## 二、财务指标",
        f"| 指标 | 值 | 评级 |",
        f"| --- | --- | --- |",
        f"| 营业收入 | {data['revenue']:.2e} 元 | - |",
        f"| 净利润 | {data['net_profit']:.2e} 元 | - |",
        f"| 净利率 | {data['net_margin']:.2f}% | {'✅ 优秀' if data['net_margin'] > 20 else '⚠️ 偏低' if data['net_margin'] < 10 else '✅ 良好'} |",
        f"| 资产负债率 | {data['debt_ratio']:.2f}% | {'✅ 健康' if data['debt_ratio'] < 60 else '⚠️ 偏高'} |",
        "",
        "## 三、综合评分",
        f"**评分: {data['score']}/100 [{data['label']}]**",
        "",
        "## 四、八维评估",
    ]

    # 根据数据生成八维评估
    assessments = {}
    # 行业前景
    if '白酒' in str(data['industry']):
        assessments['行业前景'] = "✅ 白酒行业龙头，品牌壁垒高，盈利能力稳定"
    elif '银行' in str(data['industry']):
        assessments['行业前景'] = "⚠️ 银行业受宏观经济影响大，估值偏低"
    elif '通信' in str(data['industry']):
        assessments['行业前景'] = "✅ 通信服务需求稳定，5G 带来增长空间"
    else:
        assessments['行业前景'] = f"行业: {data['industry']}"

    assessments['竞争壁垒'] = "✅ 净利率高表明具有较强的竞争优势"
    assessments['团队背景'] = "⚠️ 公开数据有限，建议进一步调研"
    assessments['产品技术'] = "⚠️ 需结合具体公司业务分析"
    assessments['股权结构'] = "⚠️ 建议查看股东结构"
    assessments['估值参考'] = f"PE: {data['pe']}, PB: {data['pb']} — {'估值合理' if data['score'] >= 50 else '估值偏高/偏低'}"
    assessments['退出可能性'] = "✅ A股上市，流动性好"
    assessments['潜在风险'] = f"资产负债率 {data['debt_ratio']:.1f}% — {'风险可控' if data['debt_ratio'] < 70 else '⚠️ 负债较高'}"

    for dim, assessment in assessments.items():
        lines.append(f"- **{dim}**: {assessment}")

    lines.extend([
        "",
        "## 五、投资建议",
        f"基于财务指标分析，{data['stock_name']}综合评分 **{data['score']}/100**，给予 **{data['label']}** 评级。",
        "",
        "### 优势",
        f"- 净利率 {data['net_margin']:.1f}%，盈利能力{'强' if data['net_margin'] > 20 else '一般'}",
        f"- 资产负债率 {data['debt_ratio']:.1f}%，财务结构{'健康' if data['debt_ratio'] < 60 else '需关注'}",
        "",
        "### 风险",
        f"- 市盈率 {data['pe']}，{'相对合理' if data['pe'] and data['pe'] != 'NULL' and 10 < to_float(data['pe']) < 50 else '需结合行业判断'}",
        "",
        "---",
        "*本报告基于公开财务数据生成，仅供参考，不构成投资建议。*",
    ])

    return "\n".join(lines)

if __name__ == '__main__':
    import sys
    if len(sys.argv) < 2:
        print("用法: python investment_advisor.py <股票代码>")
        print("示例: python investment_advisor.py 600519")
        sys.exit(1)

    stock_code = sys.argv[1]
    report = generate_report(stock_code)

    # 保存报告
    report_file = f"/tmp/investment_report_{stock_code}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    with open(report_file, 'w', encoding='utf-8') as f:
        f.write(report)

    print(f"\n{'='*60}")
    print(f"报告已保存: {report_file}")
    print(f"{'='*60}")
