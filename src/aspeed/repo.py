"""aspeed-context 的目录约定与读写函数。函数接收 TreeView/Workspace，既可只读，也可在 mutation 中使用。"""

from __future__ import annotations

from typing import Iterator

from .gitstore.view import TreeView, Workspace
from .ids import safe_name
from .models import (
    ConsumerRecord,
    OpRecord,
    RawSnapshot,
    Roster,
    SyncRecord,
)
from .models.policy import Policy
from .taskdoc import TaskDoc, parse as parse_task

POLICY = "control/policy.yaml"
MEMBERS = "control/members.yaml"


def task_path(task_id: str) -> str:
    return f"tasks/{task_id}.md"


def op_path(op_id: str) -> str:
    # 路径只由 op_id 决定（按哈希前缀分片），保证不同沙箱对同一意图落到同一文件
    return f"control/ops/{op_id[3:5]}/{op_id}.yaml"


def sync_path(source: str, scope: str) -> str:
    return f"control/sync/{safe_name(source)}/{safe_name(scope)}.yaml"


def consumer_path(name: str) -> str:
    return f"control/consumers/{safe_name(name)}.yaml"


def raw_path(source: str, scope: str, object_id: str) -> str:
    return f"raw/{safe_name(source)}/{safe_name(scope)}/{safe_name(object_id)}.json"


# ---- 读取 ----
def load_policy(view: TreeView) -> Policy:
    data = view.read_yaml(POLICY)
    return Policy.model_validate(data) if data else Policy()


def load_roster(view: TreeView) -> Roster:
    data = view.read_yaml(MEMBERS)
    return Roster.model_validate(data) if data else Roster()


def load_task(view: TreeView, task_id: str) -> TaskDoc | None:
    text = view.read_text(task_path(task_id))
    return parse_task(text) if text is not None else None


def iter_tasks(view: TreeView) -> Iterator[TaskDoc]:
    for rel in view.glob("tasks/P-*.md"):
        yield parse_task(view.read_text(rel))


def load_op(view: TreeView, op_id: str) -> OpRecord | None:
    data = view.read_yaml(op_path(op_id))
    return OpRecord.model_validate(data) if data else None


def iter_ops(view: TreeView) -> Iterator[OpRecord]:
    for rel in view.glob("control/ops/*/op-*.yaml"):
        yield OpRecord.model_validate(view.read_yaml(rel))


def load_sync(view: TreeView, source: str, scope: str) -> SyncRecord | None:
    data = view.read_yaml(sync_path(source, scope))
    return SyncRecord.model_validate(data) if data else None


def iter_sync(view: TreeView) -> Iterator[SyncRecord]:
    for rel in view.glob("control/sync/*/*.yaml"):
        yield SyncRecord.model_validate(view.read_yaml(rel))


def load_consumer(view: TreeView, name: str) -> ConsumerRecord | None:
    data = view.read_yaml(consumer_path(name))
    return ConsumerRecord.model_validate(data) if data else None


def load_raw(view: TreeView, source: str, scope: str, object_id: str) -> RawSnapshot | None:
    data = view.read_json(raw_path(source, scope, object_id))
    return RawSnapshot.model_validate(data) if data else None


def iter_raw(view: TreeView, source: str, scope: str | None = None) -> Iterator[RawSnapshot]:
    pattern = f"raw/{safe_name(source)}/{safe_name(scope) if scope else '*'}/*.json"
    for rel in view.glob(pattern):
        yield RawSnapshot.model_validate(view.read_json(rel))


# ---- 写入（仅在 mutation 中调用）----
def save_task(ws: Workspace, doc: TaskDoc) -> bool:
    return ws.write_text(task_path(doc.meta.id), doc.render())


def save_op(ws: Workspace, op: OpRecord) -> bool:
    return ws.write_yaml(op_path(op.op_id), op.dump())


def save_sync(ws: Workspace, rec: SyncRecord) -> bool:
    return ws.write_yaml(sync_path(rec.source, rec.scope), rec.dump())


def save_consumer(ws: Workspace, rec: ConsumerRecord) -> bool:
    return ws.write_yaml(consumer_path(rec.name), rec.dump())


# ---- 测试与基线 ----
def plan_path(plan_id: str) -> str:
    return f"tests/plans/{plan_id}.yaml"


def run_path(plan_id: str) -> str:
    return f"tests/runs/{plan_id}.yaml"


def load_run(view: TreeView, plan_id: str):
    from .models import TestRun

    data = view.read_yaml(run_path(plan_id))
    return TestRun.model_validate(data) if data else None


def iter_runs(view: TreeView):
    from .models import TestRun

    for rel in view.glob("tests/runs/*.yaml"):
        yield TestRun.model_validate(view.read_yaml(rel))


def load_plan(view: TreeView, plan_id: str):
    from .models import TestPlan

    data = view.read_yaml(plan_path(plan_id))
    return TestPlan.model_validate(data) if data else None


def baseline_path(name: str) -> str:
    return f"baselines/current/{safe_name(name)}.yaml"


def load_baselines(view: TreeView) -> dict:
    from .models import Baseline

    out = {}
    for rel in view.glob("baselines/current/*.yaml"):
        b = Baseline.model_validate(view.read_yaml(rel))
        out[b.name] = b
    return out
