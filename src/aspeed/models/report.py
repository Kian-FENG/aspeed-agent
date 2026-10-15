from datetime import datetime
from typing import Literal

from .common import Model

Category = Literal["progress", "blocked", "claimable", "decision", "testing"]

CATEGORY_TITLES = {
    "progress": "新增进展",
    "blocked": "阻塞与求助",
    "claimable": "可认领任务",
    "decision": "待决策事项",
    "testing": "测试与基线变化",
}


class Ref(Model):
    path: str | None = None
    commit: str | None = None
    link: str | None = None
    source: str | None = None


class Fact(Model):
    id: str
    category: Category
    text: str
    refs: list[Ref]


class SourceStatus(Model):
    source: str
    scope: str
    cursor: dict = {}
    last_success_at: datetime | None = None
    stale: bool = False
    open_gaps: list[str] = []


class FactSet(Model):
    date: str
    generated_at: datetime
    since_commit: str | None
    head_commit: str
    facts: list[Fact]
    sources: list[SourceStatus]
    gaps: list[str] = []
