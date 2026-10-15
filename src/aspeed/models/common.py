"""模型公共部分。控制记录禁止未知字段，避免 LLM 拼错字段名后被静默忽略。"""

from datetime import datetime
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

MemberId = Annotated[str, StringConstraints(pattern=r"^m:[A-Za-z0-9_.\-]+$")]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    def dump(self) -> dict:
        return self.model_dump(mode="json", exclude_none=True)


class Stage(str, Enum):
    clarifying = "clarifying"
    claimable = "claimable"
    in_progress = "in_progress"
    in_review = "in_review"
    done = "done"
    cancelled = "cancelled"

    @property
    def label(self) -> str:
        return STAGE_LABELS[self]


STAGE_LABELS = {
    Stage.clarifying: "待澄清",
    Stage.claimable: "可认领",
    Stage.in_progress: "进行中",
    Stage.in_review: "待验收",
    Stage.done: "已完成",
    Stage.cancelled: "已取消",
}


class SourceRef(Model):
    """可追溯来源：ref 形如 im:msg/123@v2、aone:X-1@7、path@commit。"""

    ref: str
    title: str | None = None
    link: str | None = None


class Lease(Model):
    holder: str
    acquired_at: datetime
    until: datetime
