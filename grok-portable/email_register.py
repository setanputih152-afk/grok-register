from __future__ import annotations

import json
import logging
import random
import re
import string
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from curl_cffi import requests as curl_requests
except ImportError:
    curl_requests = None

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ============================================================
# 临时邮箱配置（无 bearer token，注册即用，多提供商轮换）
# ============================================================

_config_path = Path(__file__).parent / "config.json"
_conf: Dict[str, Any] = {}
if _config_path.exists():
    with _config_path.open("r", encoding="utf-8") as _f:
        _conf = json.load(_f)

# duckmail.sbs / mail.gw / mail.tm 共用 mail.tm 协议（/domains → /accounts → /token）。
MAIL_PROVIDERS = [
    str(_conf.get("duckmail_api_base", "https://api.duckmail.sbs")).rstrip("/"),
    "https://api.mail.gw",
    "https://api.mail.tm",
]
# mail_token -> 创建该邮箱的 api base（读取邮件必须回同一个 base）
_TOKEN_BASE: Dict[str, str] = {}
PROXY = str(_conf.get("proxy", ""))


class MailError(Exception):
    pass


# ============================================================
# 适配层：为自动化脚本提供简单接口
# ============================================================

_temp_email_cache: Dict[str, str] = {}


def get_email_and_token() -> Tuple[Optional[str], Optional[str]]:
    """
    创建临时邮箱并返回 (email, mail_token)。
    """
    email, _password, mail_token = create_temp_email()
    if email and mail_token:
        _temp_email_cache[email] = mail_token
        return email, mail_token
    return None, None


def get_oai_code(dev_token: str, email: str, timeout: int = 30) -> Optional[str]:
    """
    轮询临时邮箱获取 OTP 验证码（去除连字符，如 "MM0SF3"）。
    """
    code = wait_for_verification_code(mail_token=dev_token, timeout=timeout)
    if code:
        code = code.replace("-", "")
    return code


# ============================================================
# HTTP 会话
# ============================================================

def _create_session():
    """创建请求会话（优先 curl_cffi 绕 TLS 指纹）"""
    if curl_requests:
        session = curl_requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
            "Content-Type": "application/json",
        })
        if PROXY:
            session.proxies = {"http": PROXY, "https": PROXY}
        return session, True

    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    s = requests.Session()
    retry = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
        "Content-Type": "application/json",
    })
    if PROXY:
        s.proxies = {"http": PROXY, "https": PROXY}
    return s, False


def _do_request(session, use_cffi, method, url, **kwargs):
    if use_cffi:
        kwargs.setdefault("impersonate", "chrome131")
    return getattr(session, method)(url, **kwargs)


def _generate_password(length=14):
    lower = string.ascii_lowercase
    upper = string.ascii_uppercase
    digits = string.digits
    special = "!@#$%"
    pwd = [random.choice(lower), random.choice(upper),
           random.choice(digits), random.choice(special)]
    all_chars = lower + upper + digits + special
    pwd += [random.choice(all_chars) for _ in range(length - 4)]
    random.shuffle(pwd)
    return "".join(pwd)


# ============================================================
# 临时邮箱核心（mail.tm 协议）
# ============================================================

def _get_domains(session, use_cffi, api_base: str) -> List[str]:
    """获取可用邮箱域名（无需鉴权）。兼容 dict/list 两种响应形态。"""
    res = _do_request(session, use_cffi, "get", f"{api_base}/domains", timeout=15)
    if res.status_code != 200:
        raise MailError(f"获取域名失败: {res.status_code} - {res.text[:200]}")
    data = res.json()
    items = data
    if isinstance(data, dict):
        items = data.get("hydra:member") or data.get("member") or data.get("data") or []
    domains = []
    for item in items:
        if isinstance(item, str):
            domains.append(item)
        elif isinstance(item, dict):
            d = item.get("domain")
            if d and item.get("isActive", item.get("is_active", True)):
                domains.append(d)
    return domains


def _create_account_on_base(session, use_cffi, api_base: str) -> Tuple[str, str, str]:
    """在单个 API base 上自助注册邮箱，返回 (email, password, mail_token)"""
    domains = _get_domains(session, use_cffi, api_base)
    if not domains:
        raise MailError("无可用域名")

    chars = string.ascii_lowercase + string.digits
    length = random.randint(8, 13)
    email_local = "".join(random.choice(chars) for _ in range(length))
    email = f"{email_local}@{random.choice(domains)}"
    password = _generate_password()

    res = _do_request(session, use_cffi, "post",
                      f"{api_base}/accounts",
                      json={"address": email, "password": password},
                      timeout=15)
    if res.status_code not in (200, 201):
        raise MailError(f"创建邮箱失败: {res.status_code} - {res.text[:200]}")

    time.sleep(0.5)
    token_res = _do_request(session, use_cffi, "post",
                            f"{api_base}/token",
                            json={"address": email, "password": password},
                            timeout=15)
    if token_res.status_code == 200:
        mail_token = token_res.json().get("token")
        if mail_token:
            print(f"[*] 临时邮箱创建成功: {email}")
            return email, password, mail_token
    raise MailError(f"获取邮件 Token 失败: {token_res.status_code}")


def create_temp_email() -> Tuple[str, str, str]:
    """创建临时邮箱，返回 (email, password, mail_token)。

    每轮从随机提供商开始尝试，全部失败则按顺序重试一整圈。
    """
    session, use_cffi = _create_session()
    order = MAIL_PROVIDERS[:]
    random.shuffle(order)
    errors = []
    for base in order:
        try:
            email, password, token = _create_account_on_base(session, use_cffi, base)
            _TOKEN_BASE[token] = base
            return email, password, token
        except MailError as e:
            errors.append(f"{base}: {e}")
            time.sleep(0.5)
    raise Exception("临时邮箱创建失败: " + " | ".join(errors))


def _base_for_token(mail_token: str) -> str:
    return _TOKEN_BASE.get(mail_token, MAIL_PROVIDERS[0])


def fetch_emails(mail_token: str) -> List[Dict[str, Any]]:
    """获取邮件列表"""
    api_base = _base_for_token(mail_token)
    try:
        session, use_cffi = _create_session()
        headers = {"Authorization": f"Bearer {mail_token}"}
        res = _do_request(session, use_cffi, "get",
                          f"{api_base}/messages",
                          headers=headers, timeout=15)
        if res.status_code == 200:
            data = res.json()
            items = data
            if isinstance(data, dict):
                items = data.get("hydra:member") or data.get("member") or data.get("data") or []
            return [m for m in items if isinstance(m, dict)]
    except Exception:
        pass
    return []


def fetch_email_detail(mail_token: str, msg_id: str) -> Optional[Dict]:
    """获取单封邮件详情"""
    api_base = _base_for_token(mail_token)
    try:
        session, use_cffi = _create_session()
        headers = {"Authorization": f"Bearer {mail_token}"}

        if isinstance(msg_id, str) and msg_id.startswith("/messages/"):
            msg_id = msg_id.split("/")[-1]

        res = _do_request(session, use_cffi, "get",
                          f"{api_base}/messages/{msg_id}",
                          headers=headers, timeout=15)
        if res.status_code == 200:
            return res.json()
    except Exception:
        pass
    return None


def wait_for_verification_code(mail_token: str, timeout: int = 120) -> Optional[str]:
    """轮询等待验证码邮件"""
    start = time.time()
    seen_ids = set()

    while time.time() - start < timeout:
        messages = fetch_emails(mail_token)
        for msg in messages:
            msg_id = msg.get("id") or msg.get("@id")
            if not msg_id or msg_id in seen_ids:
                continue
            seen_ids.add(msg_id)

            detail = fetch_email_detail(mail_token, str(msg_id))
            if detail:
                content = detail.get("text") or detail.get("html") or ""
                code = extract_verification_code(content)
                if code:
                    print(f"[*] 提取到验证码: {code}")
                    return code
        time.sleep(3)
    return None


def extract_verification_code(content: str) -> Optional[str]:
    """
    从邮件内容提取验证码。
    Grok/x.ai 格式：MM0-SF3（3位-3位字母数字混合）或 6 位纯数字。
    """
    if not content:
        return None

    m = re.search(r"(?<![A-Z0-9-])([A-Z0-9]{3}-[A-Z0-9]{3})(?![A-Z0-9-])", content)
    if m:
        return m.group(1)

    m = re.search(r"(?:verification code|验证码|your code)[:\s]*[<>\s]*([A-Z0-9]{3}-[A-Z0-9]{3})\b", content, re.IGNORECASE)
    if m:
        return m.group(1)

    m = re.search(r"background-color:\s*#F3F3F3[^>]*>[\s\S]*?([A-Z0-9]{3}-[A-Z0-9]{3})[\s\S]*?</p>", content)
    if m:
        return m.group(1)

    m = re.search(r"Subject:.*?(\d{6})", content)
    if m and m.group(1) != "177010":
        return m.group(1)

    for code in re.findall(r">\s*(\d{6})\s*<", content):
        if code != "177010":
            return code

    for code in re.findall(r"(?<![&#\d])(\d{6})(?![&#\d])", content):
        if code != "177010":
            return code

    return None


if __name__ == "__main__":
    email, token = get_email_and_token()
    print("email:", email)
    print("token:", bool(token))
    print("provider:", _base_for_token(token or ""))
