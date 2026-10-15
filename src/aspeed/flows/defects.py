"""缺陷跟进：按规则扫描 Aone 缺陷快照，生成去重的提醒与验证闭环操作。

提醒的幂等键 = (缺陷, 检查项, 接收人, 时间窗)，多个实例重复扫描不会重复提醒。
所有生成的操作在一次提交中登记，执行模式由 policy 决定（默认 dry_run）。
"""

from __future__ import annotations

from datetime import date

from ..gitstore.view import Workspace
from ..models import Stage
from ..ops.executor import make_op_id, propose_in
from ..repo import iter_ops, iter_raw, iter_runs, load_op, load_policy, load_roster
from ..store import ContextStore
from ..timeutil import epoch_day, local_date
from .tasks import stage_of_aone

TEMPLATES = {
    "missing_owner": "缺陷 {id}「{title}」还没有负责人，请协调认领。{link}",
    "missing_due": "缺陷 {id}「{title}」还没有期限，请补充预计修复时间。{link}",
    "due_soon": "缺陷 {id}「{title}」将于 {due} 到期，还剩 {days} 天。如有阻塞，请在任务中标注需要谁提供什么。{link}",
    "overdue": "缺陷 {id}「{title}」已逾期 {days} 天（期限 {due}）。请更新进展或调整期限。{link}",
    "overdue_escalate": "缺陷 {id}「{title}」已逾期 {days} 天，负责人 {owner}。请协调人判断是否需要调配人手或调整优先级。{link}",
    "missing_verification": "缺陷 {id}「{title}」已修复待验证，但还没有关联的回归结果。请安排验证。{link}",
}


def _remind_count(ws: Workspace, member: str, today: date, tz: str) -> int:
    return sum(
        1 for op in iter_ops(ws)
        if op.kind == "im.remind" and op.params.get("to") == member and op.state != "superseded"
        and local_date(op.created_at, tz) == today
    )


def evaluate(ws: Workspace) -> list[dict]:
    """纯规则判断：返回需要的提醒与流转建议（不写入）。"""
    policy = load_policy(ws)
    roster = load_roster(ws)
    rules = policy.defects
    today = local_date(ws.now, policy.timezone)
    coordinators = policy.coordinators or [m.id for m in roster.with_role("coordinator")]
    runs_by_defect: dict[str, list] = {}
    for run in iter_runs(ws):
        for d in run.defects:
            runs_by_defect.setdefault(d, []).append(run)
    findings = []
    for scope in rules.scopes:
        for snap in iter_raw(ws, "aone", scope):
            d = snap.data
            if snap.deleted or d.get("type") != "defect":
                continue
            stage = stage_of_aone(policy, d.get("status"))
            if stage in (Stage.done, Stage.cancelled):
                continue
            owner = roster.by_aone(d.get("owner"))
            info = {"id": snap.object_id, "title": d.get("title", ""), "link": snap.link or "", "due": d.get("due"),
                    "owner": owner.id if owner else d.get("owner"), "scope": scope, "version": snap.source_version}

            def add(check, audience, days=None):
                findings.append({**info, "check": check, "audience": audience, "days": days})

            if not d.get("owner"):
                for c in coordinators:
                    add("missing_owner", c)
                continue
            if owner is None:
                continue
            if stage == Stage.in_review:
                linked = runs_by_defect.get(snap.object_id, [])
                verdicts = sorted((r for r in linked if r.verdict in ("pass", "fail")),
                                  key=lambda r: str(r.completed_at or ""))
                if not verdicts:
                    for c in coordinators:
                        add("missing_verification", c)
                else:
                    latest = verdicts[-1]
                    to = rules.close_status if latest.verdict == "pass" else rules.reopen_status
                    findings.append({**info, "check": "close_loop", "to_status": to, "run": latest.plan_id,
                                     "verdict": latest.verdict})
                continue
            if not d.get("due"):
                add("missing_due", owner.id)
                continue
            days_left = (date.fromisoformat(d["due"]) - today).days
            if 0 <= days_left <= rules.due_soon_days:
                add("due_soon", owner.id, days_left)
            elif days_left < 0:
                add("overdue", owner.id, -days_left)
                if -days_left >= rules.escalate_after_days:
                    for c in coordinators:
                        add("overdue_escalate", c, -days_left)
    return findings


def scan(store: ContextStore, requested_by: str = "schedule:defect-scan") -> dict:
    summary: dict = {}

    def mutation(ws):
        summary.clear()
        policy = load_policy(ws)
        roster = load_roster(ws)
        tz = policy.timezone
        today = local_date(ws.now, tz)
        cap = policy.budget.reminders_per_person_per_day
        created, existing, deferred, transitions = [], 0, [], []
        counts: dict[str, int] = {}
        for f in evaluate(ws):
            if f["check"] == "close_loop":
                op, new = propose_in(
                    ws, "aone.transition", {"item": f["id"], "to": f["to_status"], "run": f["run"]},
                    {"scope": f["scope"], "item_id": f["id"], "to_status": f["to_status"],
                     "reason": f"回归 {f['run']} 结论 {f['verdict']}"},
                    requested_by, store.ctx.run_id, preconditions={"expected_version": f["version"]},
                    note=f"验证闭环：回归 {f['verdict']}")
                if new:
                    transitions.append(op.op_id)
                continue
            repeat = policy.defects.repeat_days.get(f["check"], 1) or 1
            window = f"{f['check']}:{epoch_day(today) // repeat}"
            key = {"defect": f["id"], "check": f["check"], "audience": f["audience"], "window": window}
            member = roster.get(f["audience"])
            if load_op(ws, make_op_id("im.remind", key)):
                existing += 1
                continue
            if counts.setdefault(f["audience"], _remind_count(ws, f["audience"], today, tz)) >= cap:
                deferred.append({"defect": f["id"], "check": f["check"], "audience": f["audience"], "reason": "超过每人每日提醒上限"})
                continue
            text = TEMPLATES[f["check"]].format(**{**f, "days": f.get("days") or 0})
            op, new = propose_in(ws, "im.remind", key,
                                 {"to": f["audience"], "im": member.im if member else None, "text": text,
                                  "ref": f"aone:{f['id']}@{f['version']}"},
                                 requested_by, store.ctx.run_id, note=f["check"])
            if new:
                created.append({"op_id": op.op_id, "defect": f["id"], "check": f["check"], "to": f["audience"], "state": op.state})
                counts[f["audience"]] += 1
            else:
                existing += 1
        summary.update(created=created, already_reminded=existing, deferred=deferred, transitions=transitions)

    res = store.publish(mutation, "defects: 规则扫描")
    return {**summary, "commit": res.commit if res.status == "committed" else None}
