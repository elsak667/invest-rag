# 未上市企业投资分析系统

基于 RAG 的非上市企业投资分析工具，支持 8 维评估、赛道匹配、生物医药行业批量分析。

## 凭证配置

所有凭据从 `~/.hermes/credentials.json` 读取（不提交到 Git），格式：

```json
{
  "supabase": {
    "url": "https://xxx.supabase.co",
    "service_role_key": "eyJ..."
  },
  "feishu": {
    "app_id": "...",
    "app_secret": "...",
    "user_open_id": "..."
  },
  "vps": {
    "embedding_url": "https://embed.xxx.com"
  }
}
```

## 核心脚本

| 文件 | 说明 |
|------|------|
| `analyze_company.py` | 8 维评估分析（团队/市场/产品/商业模式/竞争/股权/融资/业务数据） |
| `sector_matcher.py` | 赛道匹配：文档 → 赛道指纹 → 自动分类 |
| `load_biotech.py` | 生物医药赛道文档加载 |
| `embed_doc.py` | 文档向量化 |
| `scan_documents.py` | 文档扫描与解析 |
| `monthly_report.py` / `weekly_report.py` | 周期报告生成 |
| `akshare_benchmarks.py` | 基准数据（akshare） |
| `credentials.py` | 统一凭证读取 |

## 环境

```bash
cd invest_rag
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt   # 如果有
```
