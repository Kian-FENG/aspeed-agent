"""稳定 ID 与规范化哈希。"""

from __future__ import annotations

import hashlib
import json
import secrets

from .timeutil import local_date, now

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def canonical_json(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def digest(value, length: int = 16) -> str:
    if not isinstance(value, (str, bytes)):
        value = canonical_json(value)
    if isinstance(value, str):
        value = value.encode()
    return hashlib.sha256(value).hexdigest()[:length]


def random_code(n: int = 4) -> str:
    return "".join(secrets.choice(_CROCKFORD) for _ in range(n))


def proposal_id(tz: str = "Asia/Shanghai") -> str:
    """P-YYYYMMDD-XXXX：日期便于阅读，随机后缀保证多沙箱并发创建不冲突（发布时仍会查重）。"""
    return f"P-{local_date(now(), tz).strftime('%Y%m%d')}-{random_code()}"


def run_id() -> str:
    return f"run-{now().strftime('%Y%m%dT%H%M%S')}-{random_code(6).lower()}"


def safe_name(value: str) -> str:
    """把外部对象 ID 转成可用作文件名的形式。"""
    out = []
    for ch in str(value):
        out.append(ch if ch.isalnum() or ch in "-_.@" else "_")
    return "".join(out) or "_"
