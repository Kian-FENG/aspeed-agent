"""任务上下文包：Markdown 正文 + YAML frontmatter。"""

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from .common import Lease, MemberId, Model, SourceRef, Stage  # noqa: F401
from .rules import Rule

TASK_ID_PATTERN = r"^P-\d{8}-[0-9A-Z]{4}$"

# 正文固定小节，顺序即渲染顺序
SECTIONS = [
    "为什么做",
    "已知什么",
    "还不知道什么",
    "做到什么算完成",
    "如何参与",
    "谁在协作",
    "当前卡在哪里",
    "结果",
]


class Need(Model):
    who: str
    what: str


class Blocked(Model):
    """附加标记：不改变所在阶段。"""

    reason: str
    since: datetime
    by: str | None = None
    depends_on: list[str] = []
    needs: list[Need] = []
    unblock_when: str | None = None


class Participant(Model):
    member: MemberId
    role: Literal["lead", "contributor", "reviewer"] = "contributor"
    since: datetime


class AcceptanceItem(Model):
    id: str
    check: str
    mode: Literal["auto", "human"] = "human"
    rule: Rule | None = None

    @model_validator(mode="after")
    def _auto_needs_rule(self):
        if self.mode == "auto" and self.rule is None:
            raise ValueError(f"验收项 {self.id} 为 auto，必须提供 rule")
        return self


class Deliverable(Model):
    type: Literal["code", "test", "decision", "doc", "data", "other"]
    link: str
    note: str | None = None


class AoneLink(Model):
    """建立映射后，认领、期限、状态以 Aone 为准；Git 只保留上下文。"""

    id: str
    scope: str
    linked_at: datetime
    op_id: str | None = None


class AcceptanceRecord(Model):
    at: datetime
    by: str
    mode: Literal["auto", "human"]
    decision: Literal["accepted", "rejected", "pending"]
    evidence: list[str] = []
    note: str | None = None


class TaskPack(Model):
    id: str = Field(pattern=TASK_ID_PATTERN)
    title: str = Field(min_length=1)
    stage: Stage | None = None
    blocked: Blocked | None = None
    goal_refs: list[str] = []
    aone: AoneLink | None = None
    coordinator: MemberId | None = None
    acceptor: MemberId | None = None
    participants: list[Participant] = []
    skills_needed: list[str] = []
    acceptance: list[AcceptanceItem] = []
    deliverables: list[Deliverable] = []
    evidence: list[str] = []
    acceptance_log: list[AcceptanceRecord] = []
    sources: list[SourceRef] = []
    tags: list[str] = []
    created_by: str
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def _stage_authority(self):
        if self.aone is None and self.stage is None:
            raise ValueError("未关联 Aone 的提案必须有 stage")
        if self.aone is not None and self.stage is not None:
            raise ValueError("已关联 Aone 的任务以 Aone 状态为准，不应再保存 stage")
        return self
