"""RAW 快照：外部对象的最新一次观察，历史版本由 Git 历史保存。"""

from datetime import datetime

from ..timeutil import parse_dt
from .common import Model


class RawSnapshot(Model):
    source: str
    scope: str
    object_id: str
    object_type: str | None = None
    source_version: int | str | None = None
    source_updated_at: datetime | None = None
    observed_at: datetime
    visibility: str = "team"
    deleted: bool = False
    content_hash: str
    link: str | None = None
    data: dict


def compare_versions(a, b) -> int | None:
    """比较来源版本：返回 -1/0/1；无法判断返回 None（调用方应重新读取）。"""
    if a is None or b is None:
        return None
    if isinstance(a, int) and isinstance(b, int):
        return (a > b) - (a < b)
    if isinstance(a, str) and isinstance(b, str):
        try:
            da, db = parse_dt(a), parse_dt(b)
        except ValueError:
            return 0 if a == b else None
        return (da > db) - (da < db)
    return None
