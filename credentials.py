"""
统一凭证读取 — 所有 invest_rag 脚本统一从这里获取凭证
数据源: ~/.hermes/credentials.json
"""
import json, os

_PATH = os.path.expanduser("~/.hermes/credentials.json")

def load() -> dict:
    with open(_PATH) as f:
        return json.load(f)

def get_supabase() -> tuple[str, str]:
    """返回 (url, service_role_key)"""
    c = load()
    return c["supabase"]["url"], c["supabase"]["service_role_key"]

def supabase_key() -> str:
    """只返回 service_role_key"""
    return get_supabase()[1]

def supabase_url() -> str:
    """只返回 url"""
    return get_supabase()[0]

def get_feishu() -> tuple[str, str, str]:
    """返回 (app_id, app_secret, user_open_id)"""
    c = load()
    fs = c["feishu"]
    return fs["app_id"], fs["app_secret"], fs.get("user_open_id", "")

def get_vps_embedding_url() -> str:
    """返回 VPS embedding 服务的 URL"""
    c = load()
    return c["vps"]["embedding_url"]

# Supabase REST API 专用 httpx client（禁用 SSL 验证，兼容 nengpa-relay）
def supabase_client():
    import httpx
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    return httpx.Client(verify=False, timeout=30)
