"""任务全景、阻塞清单、可认领任务。

视图不随每次请求提交：IM 请求现场渲染（views show），定时任务在事实变化时重建并提交，
避免 views/ 成为写入热点。
"""

from __future__ import annotations

from ..flows.tasks import effective
from ..gitstore.view import TreeView
from ..models import STAGE_LABELS, Stage
from ..repo import iter_ops, iter_tasks, load_policy, load_roster
from ..store import ContextStore
from ..sync import advance, pending, source_status
from ..timeutil import iso

WATCHED = ["tasks/", "raw/", "tests/", "baselines/", "control/ops/", "control/sync/"]
ORDER = ["in_progress", "in_review", "claimable", "clarifying", "done", "cancelled"]


def task_views(view: TreeView) -> list[dict]:
    roster, policy = load_roster(view), load_policy(view)
    return [effective(view, doc, roster, policy) for doc in iter_tasks(view)]


def _label(stage: str) -> str:
    try:
        return STAGE_LABELS[Stage(stage)]
    except ValueError:
        return stage


def _header(view: TreeView, title: str) -> str:
    lines = [f"# {title}", "", f"> 基于 aspeed-context@{(view.head or '')[:10]}。外部来源同步状态："]
    statuses = source_status(view)
    if not statuses:
        lines.append(">  - （尚无外部来源同步）")
    for s in statuses:
        flag = "⚠ 数据可能过期" if s.stale else ""
        gap = f"；最近失败：{s.open_gaps[-1]}" if s.open_gaps else ""
        lines.append(f">  - {s.source}/{s.scope}：截至 {iso(s.last_success_at) or '从未成功'} {flag}{gap}")
    return "\n".join(lines) + "\n"


def _people(t: dict) -> str:
    people = [p["member"] for p in t.get("participants", []) if p.get("member")]
    owner = t.get("owner")
    if owner and owner not in people:
        people.insert(0, owner)
    return "、".join(people) or "—"


def render_board(view: TreeView) -> str:
    tasks = task_views(view)
    out = [_header(view, "任务全景")]
    for stage in ORDER + sorted({t["stage"] for t in tasks} - set(ORDER)):
        group = [t for t in tasks if t["stage"] == stage]
        if not group:
            continue
        out.append(f"## {_label(stage)}（{len(group)}）\n")
        out.append("| 任务 | 参与者 | 期限 | 阻塞 | 权威来源 |")
        out.append("| --- | --- | --- | --- | --- |")
        for t in sorted(group, key=lambda x: x["id"]):
            blocked = t["blocked"]["reason"] if t.get("blocked") else ""
            out.append(f"| [{t['id']}]({t['path']}) {t['title']} | {_people(t)} | {t.get('due') or ''} | {blocked} | {t['authority']} |")
        out.append("")
    return "\n".join(out)


def render_blockers(view: TreeView) -> str:
    out = [_header(view, "阻塞清单")]
    blocked = [t for t in task_views(view) if t.get("blocked")]
    if not blocked:
        out.append("当前没有标记阻塞的任务。")
    for t in blocked:
        b = t["blocked"]
        out.append(f"## [{t['id']}]({t['path']}) {t['title']}（{_label(t['stage'])}）\n")
        out.append(f"- 原因：{b['reason']}（自 {b['since']}）")
        for n in b.get("needs", []):
            out.append(f"- 需要 {n['who']}：{n['what']}")
        if b.get("depends_on"):
            out.append(f"- 依赖：{'、'.join(b['depends_on'])}")
        if b.get("unblock_when"):
            out.append(f"- 解除条件：{b['unblock_when']}")
        out.append("")
    pending_ops = [op for op in iter_ops(view) if op.state in ("proposed", "needs_human")]
    if pending_ops:
        out.append("## 等待人工处理的操作\n")
        for op in pending_ops:
            out.append(f"- `{op.op_id}` {op.kind}（{op.state}）{op.note or ''}")
    return "\n".join(out)


def render_claimable(view: TreeView) -> str:
    from ..repo import load_task

    out = [_header(view, "可认领任务")]
    items = [t for t in task_views(view) if t["stage"] == "claimable"]
    if not items:
        out.append("当前没有可认领任务。")
    for t in items:
        doc = load_task(view, t["id"])
        why = (doc.section("为什么做").splitlines() or [""])[0]
        how = (doc.section("如何参与").splitlines() or [""])[0]
        out.append(f"## [{t['id']}]({t['path']}) {t['title']}\n")
        out.append(f"- 为什么做：{why}")
        if t["skills_needed"]:
            out.append(f"- 所需能力：{'、'.join(t['skills_needed'])}")
        if how:
            out.append(f"- 如何参与：{how}")
        out.append(f"- 协调人：{t.get('coordinator') or '—'}；当前参与者：{_people(t)}")
        out.append(f"- 加入：`aspeed task join {t['id']} --member <你的成员ID>`，或在群里告诉 ASpeed")
        out.append("")
    return "\n".join(out)


RENDERERS = {"board": render_board, "blockers": render_blockers, "claimable": render_claimable}


def rebuild(store: ContextStore, force: bool = False) -> dict:
    view = store.view()
    since, changed = pending(view, "views", WATCHED)
    if not changed and since and not force:
        return {"status": "noop", "reason": "自上次重建以来事实没有变化"}

    def mutation(ws):
        for name, fn in RENDERERS.items():
            ws.write_text(f"views/{name}.md", fn(ws))
        ws.write_json("views/index.json", {"head": ws.base, "tasks": task_views(ws)})
        advance(ws, "views", WATCHED)

    res = store.publish(mutation, "views: 重建任务视图")
    return {"status": res.status, "commit": res.commit, "changed_inputs": len(changed)}
