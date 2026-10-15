"""时间工具。所有时间以带时区的 UTC 存储；ASPEED_NOW 可固定当前时间（测试、回放）。"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo


def now() -> datetime:
    fixed = os.environ.get("ASPEED_NOW")
    if fixed:
        return parse_dt(fixed)
    return datetime.now(timezone.utc).replace(microsecond=0)


def parse_dt(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return parse_dt(dt).isoformat().replace("+00:00", "Z")


def local_date(dt: datetime, tz: str) -> date:
    return parse_dt(dt).astimezone(ZoneInfo(tz)).date()


def local_time(dt: datetime, tz: str):
    return parse_dt(dt).astimezone(ZoneInfo(tz)).time()


def epoch_day(d: date) -> int:
    return (d - date(1970, 1, 1)).days


def plus(dt: datetime, seconds: float) -> datetime:
    return dt + timedelta(seconds=seconds)
