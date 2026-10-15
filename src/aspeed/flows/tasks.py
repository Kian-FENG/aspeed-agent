"""任务上下文包的发布、认领、阶段流转、阻塞标记与验收。

未关联 Aone 的提案以 Git 为准，直接更新任务文件；
已关联 Aone 的任务，认领与状态变更转为 Aone 操作意图，经执行器写入、核验后刷新快照。
"""

from __future__ import annotations

from ..errors import Abort, NotFound, PermissionDenied, ValidationFailed
from ..gitstore.view import TreeView
from ..ids import proposal_id
from ..models import Stage, TaskPack
from ..models.member import Roster
from ..models.policy import Policy
from ..models.task import AcceptanceRecord, Blocked, Deliverable, Need, Participant
from ..ops.executor import OpService
from ..repo import load_baselines, load_policy, load_raw, load_roster, load_run, load_task, save_task, task_path
from ..rules import evaluate
from ..store import ContextStore
from ..taskdoc import TaskDoc, parse_meta_and_body

STAGE_FLOW: dict[Stage, set[Stage]] = {
    Stage.clarifying: {Stage.claimable, Stage.cancelled},
    Stage.claimable: {Stage.in_progress, Stage.clarifying, Stage.cancelled},
    Stage.in_progress: {Stage.in_review, Stage.claimable, Stage.cancelled},
    Stage.in_review: {Stage.done, Stage.in_progress, Stage.cancelled},
    Stage.done: {Stage.in_progress},       # 重新打开
    Stage.cancelled: {Stage.clarifying},   # 重新打开
}

AGENT_MANAGED_FIELDS = ("id", "aone", "created_at", "updated_at", "created_by", "acceptance_log")


# ---- 读取 ----
def stage_of_aone(policy: Policy, status: str | None) -> Stage | None:
    value = policy.stage_map.get("aone", {}).get(status or "")
    return Stage(value) if value else None


def aone_status_for(policy: Policy, stage: Stage) -> str | None:
    return next((s for s, st in policy.stage_map.get("aone", {}).items() if st == stage.value), None)


def effective(view: TreeView, doc: TaskDoc, roster: Roster | None = None, policy: Policy | None = None) -> dict:
    """任务的当前有效状态：已关联 Aone 时以 Aone 快照为准。"""
    roster = roster or load_roster(view)
    policy = policy or load_policy(view)
    m = doc.meta
    out = {
        "id": m.id, "title": m.title, "path": task_path(m.id), "blocked": m.blocked.dump() if m.blocked else None,
        "coordinator": m.coordinator, "acceptor": m.acceptor, "goal_refs": m.goal_refs,
        "skills_needed": m.skills_needed, "updated_at": str(m.updated_at),
    }
    if m.aone is None:
        lead = next((p.member for p in m.participants if p.role == "lead"), None)
        return {**out, "stage": m.stage.value, "authority": "git", "owner": lead, "due": None,
                "participants": [p.dump() for p in m.participants]}
    snap = load_raw(view, "aone", m.aone.scope, m.aone.id)
    if snap is None:
        return {**out, "stage": "unknown", "authority": f"aone:{m.aone.id}（尚未同步）", "owner": None, "due": None,
                "participants": [], "aone_id": m.aone.id}
    d = snap.data

    def member_of(account):
        mem = roster.by_aone(account)
        return mem.id if mem else (f"aone:{account}" if account else None)

    stage = stage_of_aone(policy, d.get("status"))
    return {
        **out,
        "stage": stage.value if stage else f"aone:{d.get('status')}",
        "authority": f"aone:{m.aone.id}@{snap.source_version}",
        "aone_id": m.aone.id, "aone_status": d.get("status"), "aone_version": snap.source_version,
        "owner": member_of(d.get("owner")), "due": d.get("due"),
        "participants": [{"member": member_of(p.get("account")), "role": p.get("role")} for p in d.get("participants", [])],
        "link": snap.link,
    }


def completeness(doc: TaskDoc) -> list[str]:
    """进入“可认领”的最低要求。返回缺失项。"""
    m, missing = doc.meta, []
    if not doc.section("为什么做"):
        missing.append("为什么做：关联目标、问题背景、预期价值")
    if not m.acceptance:
        missing.append("做到什么算完成：至少一条可检查的验收条件（acceptance）")
    if not doc.section("如何参与") and not m.skills_needed:
        missing.append("如何参与：所需能力或可独立认领的子任务")
    if not m.coordinator:
        missing.append("协调人（coordinator）")
    if not m.acceptor:
        missing.append("验收人（acceptor）")
    return missing


def _require_member(view: TreeView, member: str):
    mem = load_roster(view).get(member)
    if mem is None:
        raise PermissionDenied(f"{member} 不是登记成员（control/members.yaml）")
    return mem


def _load(view: TreeView, task_id: str) -> TaskDoc:
    doc = load_task(view, task_id)
    if doc is None:
        raise NotFound(f"任务 {task_id} 不存在")
    return doc


def _update(store: ContextStore, task_id: str, fn, message: str, by: str) -> dict:
    def mutation(ws):
        doc = _load(ws, task_id)
        result = fn(doc, ws) or {}
        doc.meta.updated_at = ws.now
        save_task(ws, doc)
        return {"id": task_id, "stage": doc.meta.stage.value if doc.meta.stage else None, **result}

    res = store.publish(mutation, f"task: {message} {task_id}", on_behalf_of=by)
    if res.status == "aborted":
        return {"id": task_id, "status": "aborted", "reason": res.reason}
    return {**(res.value or {}), "status": res.status, "commit": res.commit}


# ---- 发布 ----
def create_task(store: ContextStore, draft: str, created_by: str) -> dict:
    """由 LLM 起草的 Markdown（frontmatter + 小节）创建提案。ID、时间、来源由代码分配。"""
    meta, preamble, secs = parse_meta_and_body(draft)
    for f in AGENT_MANAGED_FIELDS:
        meta.pop(f, None)
    requested = Stage(meta.pop("stage", None) or Stage.claimable)
    if requested not in (Stage.clarifying, Stage.claimable):
        raise ValidationFailed("新提案只能处于 clarifying 或 claimable")
    out: dict = {}

    def mutation(ws):
        task_id = proposal_id(load_policy(ws).timezone)
        while ws.exists(task_path(task_id)):
            task_id = proposal_id(load_policy(ws).timezone)
        try:
            pack = TaskPack.model_validate({**meta, "id": task_id, "stage": Stage.clarifying, "created_by": created_by,
                                            "created_at": ws.now, "updated_at": ws.now})
        except Exception as exc:
            raise ValidationFailed("任务草稿不符合 Schema", errors=str(exc)) from exc
        doc = TaskDoc(meta=pack, sections=secs, preamble=preamble)
        missing = completeness(doc)
        if requested == Stage.claimable and not missing:
            doc.meta.stage = Stage.claimable
        save_task(ws, doc)
        out.update(id=task_id, stage=doc.meta.stage.value, missing=missing, path=task_path(task_id))
        return out

    res = store.publish(mutation, "task: 发布提案", on_behalf_of=created_by)
    return {**res.value, "commit": res.commit}


# ---- 认领 ----
def join(store: ContextStore, ops: OpService, task_id: str, member: str, role: str = "contributor") -> dict:
    view = store.view()
    mem = _require_member(view, member)
    doc = _load(view, task_id)
    if doc.meta.aone:
        if not mem.aone:
            raise ValidationFailed(f"{member} 没有登记 Aone 账号")
        a = doc.meta.aone
        return {"id": task_id, "via": "aone", **ops.propose(
            "aone.add_participant", {"item": a.id, "account": mem.aone, "role": role},
            {"scope": a.scope, "item_id": a.id, "account": mem.aone, "role": role, "member": member},
            requested_by=member, on_behalf_of=member)}

    def fn(doc, ws):
        m = doc.meta
        if m.stage in (Stage.done, Stage.cancelled):
            raise Abort(f"任务处于 {m.stage.label}，不能加入")
        existing = next((p for p in m.participants if p.member == member), None)
        if existing:
            return {"joined": False, "note": f"已是参与者（{existing.role}）"}
        m.participants.append(Participant(member=member, role=role, since=ws.now))
        if role == "lead" and m.stage == Stage.claimable:
            m.stage = Stage.in_progress
        return {"joined": True}

    return _update(store, task_id, fn, f"{member} 加入（{role}）", member)


def leave(store: ContextStore, task_id: str, member: str) -> dict:
    def fn(doc, ws):
        m = doc.meta
        if m.aone:
            raise Abort("已关联 Aone：请在 Aone 中退出，ASpeed 会在同步后更新")
        before = len(m.participants)
        m.participants = [p for p in m.participants if p.member != member]
        if len(m.participants) == before:
            raise Abort(f"{member} 不是参与者")
        if m.stage == Stage.in_progress and not any(p.role == "lead" for p in m.participants):
            m.stage = Stage.claimable
        return {"left": True}

    return _update(store, task_id, fn, f"{member} 退出", member)


# ---- 阶段 ----
def set_stage(store: ContextStore, ops: OpService, task_id: str, to: str, by: str, reason: str | None = None) -> dict:
    target = Stage(to)
    view = store.view()
    _require_member(view, by)
    doc = _load(view, task_id)
    if doc.meta.aone:
        policy = load_policy(view)
        status = aone_status_for(policy, target)
        if not status:
            raise ValidationFailed(f"stage_map 中没有映射到 {target.value} 的 Aone 状态")
        a = doc.meta.aone
        snap = load_raw(view, "aone", a.scope, a.id)
        version = snap.source_version if snap else None
        return {"id": task_id, "via": "aone", **ops.propose(
            "aone.transition", {"item": a.id, "to": status, "from_version": version},
            {"scope": a.scope, "item_id": a.id, "to_status": status, "task_id": task_id, "reason": reason},
            requested_by=by, on_behalf_of=by, preconditions={"expected_version": version})}
    if target == Stage.done:
        raise ValidationFailed("完成需经验收：请使用 aspeed task submit / accept")

    def fn(doc, ws):
        m = doc.meta
        if target not in STAGE_FLOW[m.stage]:
            raise Abort(f"不能从 {m.stage.label} 变为 {target.label}")
        if target == Stage.claimable:
            missing = completeness(doc)
            if missing:
                raise Abort("信息不完整，不能进入可认领：" + "；".join(missing))
        m.stage = target
        return {"from_reason": reason}

    return _update(store, task_id, fn, f"阶段 → {target.value}", by)


def block(store: ContextStore, task_id: str, by: str, reason: str, needs: list[str] | None = None,
          depends_on: list[str] | None = None, unblock_when: str | None = None) -> dict:
    # needs 形如 "m:alice=提供 A100 节点"
    parsed = []
    for n in needs or []:
        who, _, what = n.partition("=")
        parsed.append(Need(who=who.strip(), what=what.strip() or "协助"))

    def fn(doc, ws):
        doc.meta.blocked = Blocked(reason=reason, since=ws.now, by=by, needs=parsed, depends_on=depends_on or [],
                                   unblock_when=unblock_when)
        return {"blocked": True}

    return _update(store, task_id, fn, "标记阻塞", by)


def unblock(store: ContextStore, task_id: str, by: str, note: str | None = None) -> dict:
    def fn(doc, ws):
        if not doc.meta.blocked:
            raise Abort("任务未处于阻塞状态")
        doc.meta.blocked = None
        return {"blocked": False, "note": note}

    return _update(store, task_id, fn, "解除阻塞", by)


# ---- 交付与验收 ----
def _auto_checks(view: TreeView, doc: TaskDoc, evidence: list[str]) -> list[dict]:
    runs = []
    for ref in evidence:
        plan_id = ref.removeprefix("run:").removeprefix("tests/runs/").removesuffix(".yaml")
        run = load_run(view, plan_id)
        if run:
            runs.append(run)
    metrics: dict = {}
    for r in runs:
        metrics.update(r.metrics)
    verdict = None
    if runs:
        verdict = "pass" if all(r.verdict == "pass" for r in runs) else "fail"
    baselines = {name: b.metrics for name, b in load_baselines(view).items()}
    results = []
    for item in doc.meta.acceptance:
        if item.mode != "auto":
            results.append({"id": item.id, "mode": "human", "passed": None, "note": item.check})
            continue
        results.append({"id": item.id, "mode": "auto", **evaluate(item.rule, metrics, verdict, baselines)})
    return results


def submit(store: ContextStore, ops: OpService, task_id: str, by: str, evidence: list[str],
           deliverables: list[dict] | None = None) -> dict:
    """成员反馈完成：记录交付物与证据，能按规则判定的自动验收，其余提交验收人。"""
    view = store.view()
    _require_member(view, by)
    doc = _load(view, task_id)
    if doc.meta.aone:
        res = set_stage(store, ops, task_id, Stage.in_review.value, by, reason="成员反馈完成")
        return {**res, "note": "已关联 Aone：已登记流转到待验收的操作意图"}

    def fn(doc, ws):
        m = doc.meta
        if m.stage != Stage.in_progress:
            raise Abort(f"任务处于 {m.stage.label}，只有进行中的任务可以提交验收")
        m.evidence = sorted(set(m.evidence) | set(evidence))
        for d in deliverables or []:
            dl = Deliverable.model_validate(d)
            if dl not in m.deliverables:
                m.deliverables.append(dl)
        checks = _auto_checks(ws, doc, m.evidence)
        auto = [c for c in checks if c["mode"] == "auto"]
        human = [c for c in checks if c["mode"] == "human"]
        failed = [c for c in auto if c["passed"] is False]
        unknown = [c for c in auto if c["passed"] is None]
        if failed:
            m.acceptance_log.append(AcceptanceRecord(at=ws.now, by="aspeed:auto", mode="auto", decision="rejected",
                                                     evidence=m.evidence, note="; ".join(f"{c['id']}: {c['note']}" for c in failed)))
            return {"result": "auto_rejected", "checks": checks}
        if auto and not human and not unknown:
            m.stage = Stage.done
            m.acceptance_log.append(AcceptanceRecord(at=ws.now, by="aspeed:auto", mode="auto", decision="accepted",
                                                     evidence=m.evidence, note="全部验收项由规则判定通过"))
            return {"result": "auto_accepted", "checks": checks}
        m.stage = Stage.in_review
        m.acceptance_log.append(AcceptanceRecord(at=ws.now, by=by, mode="human", decision="pending", evidence=m.evidence,
                                                 note=f"等待验收人 {m.acceptor or '（未指定）'}"))
        return {"result": "awaiting_acceptor", "acceptor": m.acceptor, "checks": checks}

    return _update(store, task_id, fn, "提交验收", by)


def accept(store: ContextStore, task_id: str, by: str, accepted: bool, note: str | None = None) -> dict:
    view = store.view()
    mem = _require_member(view, by)
    doc = _load(view, task_id)
    if doc.meta.acceptor != by and "coordinator" not in mem.roles:
        raise PermissionDenied(f"{by} 不是该任务的验收人")

    def fn(doc, ws):
        m = doc.meta
        if m.stage != Stage.in_review:
            raise Abort(f"任务处于 {m.stage.label}，不在待验收")
        m.stage = Stage.done if accepted else Stage.in_progress
        m.acceptance_log.append(AcceptanceRecord(at=ws.now, by=by, mode="human",
                                                 decision="accepted" if accepted else "rejected", evidence=m.evidence, note=note))
        return {"accepted": accepted}

    return _update(store, task_id, fn, "验收" + ("通过" if accepted else "退回"), by)


def promote(store: ContextStore, ops: OpService, task_id: str, by: str, scope: str) -> dict:
    """信息完整的提案登记为 Aone 建单意图；建单成功并核验后，任务文件写入 Aone 映射。"""
    view = store.view()
    _require_member(view, by)
    doc = _load(view, task_id)
    m = doc.meta
    if m.aone:
        return {"id": task_id, "status": "already_linked", "aone": m.aone.dump()}
    missing = completeness(doc)
    if missing or m.stage not in (Stage.claimable, Stage.in_progress):
        raise ValidationFailed("只有信息完整的可认领/进行中提案才能建单", missing=missing)
    roster = load_roster(view)
    participants = []
    for p in m.participants:
        mem = roster.get(p.member)
        if mem and mem.aone:
            participants.append({"account": mem.aone, "role": p.role})
    lead = next((x["account"] for x in participants if x["role"] == "lead"), None)
    fields = {
        "type": "task", "title": m.title,
        "description": f"{doc.section('为什么做')}\n\n上下文：aspeed-context/{task_path(task_id)}",
        "participants": participants, "owner": lead,
        "status": aone_status_for(load_policy(view), m.stage) or "open",
    }
    return {"id": task_id, **ops.propose("aone.create", {"task": task_id}, {"scope": scope, "task_id": task_id, "fields": fields},
                                         requested_by=by, on_behalf_of=by)}
