"""操作记录：每个外部写操作的意图、执行权、尝试与回执。"""

from datetime import datetime
from typing import Literal

from .common import Lease, Model

OpState = Literal[
    "proposed",      # 已记录意图，等待人工确认
    "simulated",     # dry-run：记录将要执行的动作，不执行
    "approved",      # 可执行
    "leased",        # 某个运行实例持有执行权
    "executed",      # 外部调用返回成功，待核验
    "verified",      # 独立查询确认生效
    "recorded",      # Git 快照已更新，闭环
    "failed",
    "needs_human",
    "superseded",    # 业务条件变化，不再需要
]
OpMode = Literal["dry_run", "confirm", "auto"]

TERMINAL_STATES = {"recorded", "superseded"}

# 合法状态迁移；validate 在发布时按 base 与新版本比对
TRANSITIONS: dict[str, set[str]] = {
    "proposed": {"approved", "simulated", "superseded", "failed"},
    "simulated": {"approved", "superseded"},
    "approved": {"leased", "needs_human", "superseded"},
    "leased": {"leased", "approved", "executed", "failed", "needs_human", "superseded"},
    "executed": {"verified", "needs_human", "failed"},
    "verified": {"recorded"},
    "recorded": set(),
    "failed": {"approved", "superseded"},
    "needs_human": {"approved", "verified", "failed", "superseded"},
    "superseded": set(),
}


class Attempt(Model):
    run_id: str
    at: datetime
    outcome: Literal[
        "started", "executed", "found_existing", "failed", "unknown", "precondition_failed", "deferred"
    ]
    detail: str | None = None


class Approval(Model):
    by: str
    at: datetime
    evidence: str | None = None


class Transition(Model):
    at: datetime
    to: str
    by: str
    note: str | None = None


class OpRecord(Model):
    op_id: str
    kind: str
    business_key: dict
    params: dict
    requested_by: str
    on_behalf_of: str | None = None
    preconditions: dict = {}
    mode: OpMode
    state: OpState
    created_at: datetime
    updated_at: datetime
    lease: Lease | None = None
    attempts: list[Attempt] = []
    approval: Approval | None = None
    receipt: dict | None = None
    observation: dict | None = None
    note: str | None = None
    history: list[Transition] = []
