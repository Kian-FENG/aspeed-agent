"""同步记录与下游消费进度。"""

from datetime import datetime
from typing import Literal

from .common import Lease, Model


class Gap(Model):
    at: datetime
    error: str
    open: bool = True


class SyncRecord(Model):
    source: str
    scope: str
    mode: Literal["incremental", "full"] = "incremental"
    cursor: dict = {}
    overlap_seconds: int = 600
    last_attempt_at: datetime | None = None
    last_success_at: datetime | None = None
    last_reconcile_at: datetime | None = None
    reconcile_summary: dict | None = None
    lease: Lease | None = None
    gaps: list[Gap] = []
    stats: dict = {}


class ConsumerRecord(Model):
    """下游处理（视图、日报、任务联动）各自的水位线：已处理到的 Git commit。"""

    name: str
    paths: list[str]
    through_commit: str | None = None
    updated_at: datetime | None = None
