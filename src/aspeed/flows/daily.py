"""每日同步：代码收集事实（带来源）→ LLM 撰写（每条引用 [F#]）→ 代码校验引用并附来源清单与截止时间。

不允许无来源的结论；来源同步失败时展示缺口，不把缺失解释为“没有进展”。
"""

from __future__ import annotations

import re
from datetime import timedelta

import yaml

from .. import frontmatter
from ..errors import ValidationFailed
from ..gitstore.view import TreeView, dump_yaml
from ..models import RawSnapshot, Stage, STAGE_LABELS
from ..models.report import CATEGORY_TITLES, Fact, FactSet, Ref
from ..repo import iter_ops, iter_tasks, load_consumer, load_policy, load_roster
from ..store import ContextStore
from ..sync import advance, source_status
from ..taskdoc import parse as parse_task
from ..timeutil import iso, local_date, parse_dt
from .tasks import effective

WATCHED = ["tasks/", "raw/", "tests/runs/", "baselines/", "decisions/", "research/", "goals/", "methods/", "pending/"]
_CITE = re.compile(r"\[(F\d+)\]")


def _label(stage) -> str:
    try:
        return STAGE_LABELS[Stage(stage)]
    except (ValueError, TypeError):
        return str(stage)


def report_path(date: str) -> str:
    return f"reports/daily/{date}.md"


def collect(view: TreeView, date: str | None = None) -> FactSet:
    policy = load_policy(view)
    roster = load_roster(view)
    date = date or local_date(view.now, policy.timezone).isoformat()
    existing = view.read_text(report_path(date))
    if existing:
        since = frontmatter.split(existing)[0].get("since_commit")
    else:
        rec = load_consumer(view, "daily")
        since = rec.through_commit if rec else None
    head = view.head
    first = since is None
    if first:
        # 第一份日报：以 24 小时前（或仓库初始化）的版本为基准
        cutoff = iso(view.now - timedelta(hours=24))
        since = (view.git.run("rev-list", "-1", f"--before={cutoff}", head).stdout.strip()
                 or view.git.run("rev-list", "--max-parents=0", head).stdout.split()[0])
    changed = view.git.diff_names(since, head, WATCHED)
    facts: list[Fact] = []

    def add(category, text, path=None, link=None, source=None):
        facts.append(Fact(id=f"F{len(facts) + 1}", category=category, text=text,
                          refs=[Ref(path=path, commit=head[:12] if path else None, link=link, source=source)]))

    # 1. 新增进展（相对上次日报的变化）
    for path in changed:
        text = view.read_text(path)
        old = view.at(since, path)
        if path.startswith("tasks/P-") and text:
            new_doc, old_doc = parse_task(text), parse_task(old) if old else None
            m = new_doc.meta
            if old_doc is None:
                add("progress", f"新提案「{m.title}」（{_label(m.stage) if m.stage else 'Aone 任务'}）", path)
                continue
            om = old_doc.meta
            if om.stage != m.stage and m.stage and om.stage:
                add("progress", f"「{m.title}」{_label(om.stage)} → {_label(m.stage)}", path)
            if om.aone is None and m.aone is not None:
                add("progress", f"「{m.title}」已在 Aone 建单 {m.aone.id}", path)
            for p in m.participants:
                if p.member not in {x.member for x in om.participants}:
                    add("progress", f"{p.member} 加入「{m.title}」（{p.role}）", path)
            for d in m.deliverables:
                if d not in om.deliverables:
                    add("progress", f"「{m.title}」新增交付物（{d.type}）：{d.link}", path, link=d.link)
            if om.blocked and not m.blocked:
                add("progress", f"「{m.title}」解除阻塞", path)
        elif path.startswith("raw/aone/") and text:
            snap = RawSnapshot.model_validate_json(text)
            prev = RawSnapshot.model_validate_json(old) if old else None
            d, title = snap.data, snap.data.get("title", "")
            src = f"aone:{snap.object_id}@{snap.source_version}"
            if snap.deleted and not (prev and prev.deleted):
                add("progress", f"Aone {snap.object_id}「{title}」已在来源中删除", path, snap.link, src)
            elif prev is None:
                add("progress", f"Aone 新增{d.get('type', '')} {snap.object_id}「{title}」（{d.get('status')}）", path, snap.link, src)
            elif prev.data.get("status") != d.get("status"):
                add("progress", f"Aone {snap.object_id}「{title}」{prev.data.get('status')} → {d.get('status')}", path, snap.link, src)
        elif path.split("/")[0] in ("decisions", "research", "goals", "methods") and text and not path.endswith("README.md"):
            meta, _ = frontmatter.split(text)
            kind = {"decisions": "决策", "research": "研究结论", "goals": "目标", "methods": "方法"}[path.split("/")[0]]
            add("progress", f"{'新增' if old is None else '更新'}{kind}：{meta.get('title') or meta.get('id') or path}", path)
        elif path.startswith("tests/runs/") and text:
            run = yaml.safe_load(text)
            if run.get("state") in ("completed", "failed", "cancelled") and (not old or yaml.safe_load(old).get("state") != run.get("state")):
                add("testing", f"测试 {run['plan_id']}（{run.get('build')}）结论 {run.get('verdict')}：{run.get('verdict_reason')}", path, run.get("link"))
        elif path.startswith("baselines/current/") and text:
            b = yaml.safe_load(text)
            add("testing", f"基线 {b['name']} 更新为 {b['version_id']}（批准人 {b['approved_by']}）", path)
        elif path.startswith("baselines/candidates/") and text and not old:
            c = yaml.safe_load(text)
            add("decision", f"候选基线 {c['id']} 待批准（来自运行 {c['from_run']}）", path)
        elif path.startswith("pending/") and text and not path.endswith("README.md"):
            meta, _ = frontmatter.split(text)
            add("decision", f"待审修改：{meta.get('title') or path}", path)

    # 2-4. 当前状态：阻塞、可认领、待决策
    for doc in iter_tasks(view):
        t = effective(view, doc, roster, policy)
        path = t["path"]
        if t.get("blocked"):
            b = t["blocked"]
            needs = "；".join(f"需要 {n['who']} {n['what']}" for n in b.get("needs", []))
            add("blocked", f"「{t['title']}」阻塞：{b['reason']}{('；' + needs) if needs else ''}", path)
        if t["stage"] == "claimable":
            skills = "、".join(t["skills_needed"]) or "见任务说明"
            add("claimable", f"「{t['title']}」可认领，所需能力：{skills}；协调人 {t.get('coordinator') or '—'}", path)
        if t["stage"] == "in_review" and t["authority"] == "git":
            add("decision", f"「{t['title']}」待验收，验收人 {t.get('acceptor') or '未指定'}", path)
        if t["stage"] == "clarifying":
            add("decision", f"「{t['title']}」待澄清，需要补齐信息后才能认领", path)
    since_time = None
    if since:
        ts = view.git.run("show", "-s", "--format=%cI", since).stdout.strip()
        since_time = parse_dt(ts) if ts else None
    for op in iter_ops(view):
        path = f"control/ops/{op.op_id[3:5]}/{op.op_id}.yaml"
        if op.state in ("proposed", "needs_human"):
            what = "等待确认" if op.state == "proposed" else "需要人工处理"
            add("decision", f"操作 {op.kind}（{op.note or op.business_key}）{what}", path)
        elif op.state == "simulated" and (since_time is None or op.created_at > since_time):
            # 影子模式：列出“本应发送”的动作，供协调人评估规则是否合理
            target = op.params.get("to") or op.params.get("item_id") or ""
            add("decision", f"影子模式（未执行）：{op.kind} → {target}：{op.params.get('text') or op.note or ''}", path)

    sources = source_status(view)
    gaps = []
    for s in sources:
        if s.stale:
            gaps.append(f"{s.source}/{s.scope} 数据可能过期：最近成功同步 {iso(s.last_success_at) or '从未'}")
        for g in s.open_gaps:
            gaps.append(f"{s.source}/{s.scope} 同步失败：{g}")
    if first:
        gaps.append("这是第一份日报：“新增进展”只覆盖最近 24 小时（或仓库初始化以来）的变化")
    return FactSet(date=date, generated_at=view.now, since_commit=since, head_commit=head, facts=facts,
                   sources=sources, gaps=gaps)


def render_template(fs: FactSet) -> str:
    """无 LLM 时的确定性渲染；也是 LLM 撰写的结构参考。"""
    out = []
    for cat, title in CATEGORY_TITLES.items():
        out.append(f"## {title}\n")
        items = [f for f in fs.facts if f.category == cat]
        out += [f"- {f.text} [{f.id}]" for f in items] or ["- 无"]
        out.append("")
    return "\n".join(out)


def check_draft(draft: str, fs: FactSet) -> dict:
    """引用校验：每条要点必须引用事实清单中存在的 [F#]。"""
    ids = {f.id for f in fs.facts}
    _, secs = frontmatter.sections(draft)
    errors = []
    for title in CATEGORY_TITLES.values():
        if title not in secs:
            errors.append(f"缺少小节「{title}」")
    cited: set[str] = set()
    for title, body in secs.items():
        if title not in CATEGORY_TITLES.values():
            errors.append(f"不允许的小节「{title}」（来源清单由程序附加）")
            continue
        for line in body.splitlines():
            line = line.strip()
            if not line.startswith(("-", "*")) or line.lstrip("-* ").strip() in ("无", ""):
                continue
            refs = _CITE.findall(line)
            if not refs:
                errors.append(f"要点没有引用来源：{line[:60]}")
            for r in refs:
                if r not in ids:
                    errors.append(f"引用了不存在的事实 {r}")
                cited.add(r)
    return {"errors": errors, "uncited_facts": sorted(ids - cited, key=lambda x: int(x[1:]))}


def _manifest(fs: FactSet) -> str:
    out = ["## 数据来源与截止时间\n", f"- 生成时间：{iso(fs.generated_at)}",
           f"- 上下文版本：aspeed-context@{fs.head_commit[:12]}（对比基准 {fs.since_commit[:12] if fs.since_commit else '无'}）", ""]
    if fs.sources:
        out += ["| 来源 | 范围 | 最近成功同步 | 游标 | 状态 |", "| --- | --- | --- | --- | --- |"]
        for s in fs.sources:
            state = "⚠ 可能过期" if s.stale else "正常"
            if s.open_gaps:
                state += "；有未恢复的失败"
            out.append(f"| {s.source} | {s.scope} | {iso(s.last_success_at) or '从未'} | {s.cursor.get('updated_since', '')} | {state} |")
        out.append("")
    if fs.gaps:
        out.append("**数据缺口**（以下缺失不代表没有进展）：\n")
        out += [f"- {g}" for g in fs.gaps]
        out.append("")
    out.append("**事实清单**\n")
    for f in fs.facts:
        ref = f.refs[0]
        loc = f"{ref.path}@{ref.commit}" if ref.path else ""
        link = f" {ref.link}" if ref.link else ""
        out.append(f"- [{f.id}] {loc}{link}")
    return "\n".join(out) + "\n"


def publish(store: ContextStore, fs: FactSet, draft: str | None = None) -> dict:
    body = draft or render_template(fs)
    check = check_draft(body, fs)
    if check["errors"]:
        raise ValidationFailed("日报引用校验失败", errors=check["errors"])
    if not store.git.is_ancestor(fs.head_commit, store.view().head):
        raise ValidationFailed("事实清单所基于的 commit 不在当前历史中，请重新 collect")
    meta = {"date": fs.date, "generated_at": iso(fs.generated_at), "since_commit": fs.since_commit,
            "head_commit": fs.head_commit, "sources": [s.dump() for s in fs.sources], "gaps": fs.gaps}
    text = f"---\n{dump_yaml(meta)}---\n\n# 团队日报 {fs.date}\n\n{body.strip()}\n\n{_manifest(fs)}"

    def mutation(ws):
        ws.write_text(report_path(fs.date), text)
        advance(ws, "daily", WATCHED, through=fs.head_commit)

    res = store.publish(mutation, f"daily: {fs.date} 日报")
    return {"path": report_path(fs.date), "commit": res.commit, "status": res.status,
            "uncited_facts": check["uncited_facts"]}
