"""发布前校验：按路径识别记录类型，做 Schema 校验和跨版本不变量检查。

同一套校验也用于 aspeed-context 的 CI（人工 MR），保证人和 Agent 写入同样规范。
"""

from __future__ import annotations

import fnmatch
import json

import yaml
from pydantic import ValidationError

from .. import frontmatter
from ..errors import ValidationFailed
from ..models import (
    Baseline,
    BaselineCandidate,
    ConsumerRecord,
    OpRecord,
    RawSnapshot,
    Roster,
    SyncRecord,
    TestPlan,
    TestRun,
    TRANSITIONS,
    compare_versions,
)
from ..models.policy import Policy
from ..timeutil import parse_dt
from .view import TreeView

IMMUTABLE_OP_FIELDS = ("op_id", "kind", "business_key", "requested_by", "created_at")
NO_DELETE = ("control/ops/*", "raw/*", "baselines/history/*", "reports/daily/*")


def _load(path: str, text: str):
    if path.endswith(".json"):
        return json.loads(text)
    return yaml.safe_load(text)


def _check_op(path: str, new: OpRecord, old_text: str | None, view: TreeView) -> None:
    expected = f"control/ops/{new.op_id[3:5]}/{new.op_id}.yaml"
    if path != expected:
        raise ValueError(f"操作记录路径应为 {expected}")
    if old_text is None:
        return
    old = OpRecord.model_validate(yaml.safe_load(old_text))
    for f in IMMUTABLE_OP_FIELDS:
        if getattr(old, f) != getattr(new, f):
            raise ValueError(f"操作记录字段 {f} 不可修改")
    if old.state not in ("proposed", "simulated") and old.params != new.params:
        raise ValueError("操作进入执行流程后参数不可修改")
    if len(new.history) < len(old.history) or new.history[: len(old.history)] != old.history:
        raise ValueError("操作历史只能追加")
    if len(new.attempts) < len(old.attempts):
        raise ValueError("尝试记录只能追加")
    if old.state != new.state and new.state not in TRANSITIONS[old.state]:
        raise ValueError(f"非法状态迁移 {old.state} → {new.state}")
    if old.state == new.state == "leased" and old.lease and new.lease and old.lease.holder != new.lease.holder:
        if parse_dt(old.lease.until) > view.now:
            raise ValueError(f"租约仍由 {old.lease.holder} 持有，不能接管")


def _check_raw(path: str, new: RawSnapshot, old_text: str | None) -> None:
    if old_text is None:
        return
    old = RawSnapshot.model_validate(json.loads(old_text))
    if compare_versions(new.source_version, old.source_version) == -1:
        raise ValueError(f"快照版本回退 {old.source_version} → {new.source_version}")


def _check_baseline(new: Baseline, view: TreeView) -> None:
    roster = Roster.model_validate(view.read_yaml("control/members.yaml") or {})
    member = roster.get(new.approved_by)
    if not member or "baseline_approver" not in member.roles:
        raise ValueError(f"{new.approved_by} 没有基线批准权限")
    if not new.evidence:
        raise ValueError("基线批准必须附人工确认证据")


def _check_roster(r: Roster) -> None:
    ids = [m.id for m in r.members]
    if len(ids) != len(set(ids)):
        raise ValueError("成员 ID 重复")


def _check_task(path: str, text: str) -> None:
    from ..taskdoc import parse

    doc = parse(text)
    if path != f"tasks/{doc.meta.id}.md":
        raise ValueError(f"任务文件名应与 ID 一致: tasks/{doc.meta.id}.md")


def _check_markdown(path: str, text: str) -> None:
    frontmatter.split(text)


RULES = [
    ("tasks/P-*.md", "task"),
    ("control/ops/*/op-*.yaml", "op"),
    ("control/sync/*/*.yaml", "sync"),
    ("control/consumers/*.yaml", "consumer"),
    ("control/members.yaml", "roster"),
    ("control/policy.yaml", "policy"),
    ("raw/*/*/*.json", "raw"),
    ("tests/plans/*.yaml", "plan"),
    ("tests/runs/*.yaml", "run"),
    ("baselines/current/*.yaml", "baseline"),
    ("baselines/history/*/*.yaml", "baseline_history"),
    ("baselines/candidates/*.yaml", "candidate"),
    ("*.md", "markdown"),
]


def kind_of(path: str) -> str | None:
    for pattern, kind in RULES:
        if fnmatch.fnmatch(path, pattern):
            return kind
    return None


def validate_file(view: TreeView, path: str, base: str | None) -> None:
    text = view.read_text(path)
    old_text = view.at(base, path) if base else None
    if text is None:
        if old_text is not None and any(fnmatch.fnmatch(path, p) for p in NO_DELETE):
            raise ValueError("该类记录不可删除（快照使用 deleted 墓碑标记）")
        return
    kind = kind_of(path)
    if kind is None:
        return
    if kind == "task":
        _check_task(path, text)
    elif kind == "markdown":
        _check_markdown(path, text)
    else:
        data = _load(path, text)
        if kind == "op":
            _check_op(path, OpRecord.model_validate(data), old_text, view)
        elif kind == "sync":
            SyncRecord.model_validate(data)
        elif kind == "consumer":
            ConsumerRecord.model_validate(data)
        elif kind == "roster":
            _check_roster(Roster.model_validate(data))
        elif kind == "policy":
            Policy.model_validate(data)
        elif kind == "raw":
            _check_raw(path, RawSnapshot.model_validate(data), old_text)
        elif kind == "plan":
            TestPlan.model_validate(data)
        elif kind == "run":
            TestRun.model_validate(data)
        elif kind == "baseline":
            _check_baseline(Baseline.model_validate(data), view)
        elif kind == "baseline_history":
            Baseline.model_validate(data)
        elif kind == "candidate":
            BaselineCandidate.model_validate(data)


def validate_paths(view: TreeView, paths, base: str | None) -> list[dict]:
    errors = []
    for path in sorted(paths):
        try:
            validate_file(view, path, base)
        except (ValidationError, ValueError, yaml.YAMLError, json.JSONDecodeError) as exc:
            errors.append({"path": path, "error": str(exc)})
    return errors


def publish_validator(ws, changed: set[str]) -> None:
    errors = validate_paths(ws, changed, ws.base)
    if errors:
        raise ValidationFailed("发布前校验失败", errors=errors)
