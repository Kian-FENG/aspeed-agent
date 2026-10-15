"""aspeed 命令行：Agent 与定时任务唯一的写入入口。

默认输出 JSON（供 Agent 读取）；views show / task show --markdown 输出 Markdown。
错误输出 {"error": ..., "message": ...}，退出码 2（业务错误）或 1（其他错误）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import yaml

from .errors import AspeedError


def _store(args):
    from .store import ContextStore

    return ContextStore(args.context)


def _adapters(args):
    from .adapters import load_adapters

    return load_adapters(args.adapters)


def _ops(args, store=None):
    from .ops import OpService

    return OpService(store or _store(args), _adapters(args))


def _read(path: str) -> str:
    return sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")


def _data(path: str):
    text = _read(path)
    return json.loads(text) if path.endswith(".json") else yaml.safe_load(text)


# ---------------- 命令实现 ----------------
def cmd_init_context(a):
    from .context_init import init_context

    return init_context(a.path, a.remote)


def cmd_clone_context(a):
    from .context_init import clone_context

    return clone_context(a.remote, a.path, partial=not a.no_partial)


def cmd_validate(a):
    from .gitstore.validate import validate_paths
    from .gitstore.view import TreeView

    store = _store(a)
    view = TreeView(store.git)
    if a.paths:
        paths = a.paths
    elif a.base:
        paths = store.git.diff_names(a.base, "HEAD")
    else:
        paths = [p for p in store.git.run("ls-files").stdout.splitlines()]
    errors = validate_paths(view, paths, a.base)
    return {"checked": len(paths), "errors": errors, "ok": not errors}


def cmd_schema_export(a):
    from . import models
    from .models.policy import Policy

    out = Path(a.dir)
    out.mkdir(parents=True, exist_ok=True)
    names = {"task-pack": models.TaskPack, "op-record": models.OpRecord, "sync-record": models.SyncRecord,
             "consumer": models.ConsumerRecord, "members": models.Roster, "policy": Policy, "raw-snapshot": models.RawSnapshot,
             "test-plan": models.TestPlan, "test-run": models.TestRun, "baseline": models.Baseline,
             "baseline-candidate": models.BaselineCandidate, "daily-facts": models.FactSet}
    for name, model in names.items():
        (out / f"{name}.schema.json").write_text(json.dumps(model.model_json_schema(), ensure_ascii=False, indent=2) + "\n")
    return {"exported": sorted(names), "dir": str(out)}


def cmd_task(a):
    from .flows import search, tasks
    from .repo import load_task

    store = _store(a)
    if a.task_cmd == "create":
        return tasks.create_task(store, _read(a.file), a.as_)
    if a.task_cmd == "list":
        from .views.render import task_views

        items = task_views(store.view())
        return [t for t in items if not a.stage or t["stage"] == a.stage]
    if a.task_cmd == "related":
        query = a.query or _read(a.file)
        return search.search(store.view(), query, k=a.k)
    view = store.view()
    if a.task_cmd in ("show", "check"):
        doc = load_task(view, a.id)
        if doc is None:
            raise AspeedError(f"任务 {a.id} 不存在")
        if a.task_cmd == "check":
            return {"id": a.id, "missing": tasks.completeness(doc)}
        if a.markdown:
            return doc.render()
        return {**tasks.effective(view, doc), "sections": doc.sections, "missing": tasks.completeness(doc)}
    ops = _ops(a, store)
    if a.task_cmd == "join":
        return tasks.join(store, ops, a.id, a.member, a.role)
    if a.task_cmd == "leave":
        return tasks.leave(store, a.id, a.member)
    if a.task_cmd == "stage":
        return tasks.set_stage(store, ops, a.id, a.to, a.by, a.reason)
    if a.task_cmd == "block":
        return tasks.block(store, a.id, a.by, a.reason, a.needs, a.depends_on, a.unblock_when)
    if a.task_cmd == "unblock":
        return tasks.unblock(store, a.id, a.by, a.note)
    if a.task_cmd == "submit":
        deliverables = []
        for d in a.deliverable or []:
            kind, _, link = d.partition("=")
            deliverables.append({"type": kind, "link": link})
        return tasks.submit(store, ops, a.id, a.by, a.evidence or [], deliverables)
    if a.task_cmd == "accept":
        return tasks.accept(store, a.id, a.by, not a.reject, a.note)
    if a.task_cmd == "promote":
        return tasks.promote(store, ops, a.id, a.by, a.scope)


def cmd_search(a):
    from .flows.search import search

    return search(_store(a).view(), a.query, k=a.k, visibility=tuple(a.visibility))


def cmd_op(a):
    from .repo import iter_ops, load_op

    store = _store(a)
    if a.op_cmd == "list":
        return [
            {"op_id": o.op_id, "kind": o.kind, "state": o.state, "mode": o.mode, "note": o.note, "business_key": o.business_key,
             "updated_at": str(o.updated_at)}
            for o in iter_ops(store.view())
            if (not a.state or o.state == a.state) and (not a.kind or o.kind == a.kind)
        ]
    if a.op_cmd == "show":
        op = load_op(store.view(), a.id)
        if op is None:
            raise AspeedError(f"操作 {a.id} 不存在")
        return op.dump()
    ops = _ops(a, store)
    if a.op_cmd == "approve":
        return ops.approve(a.id, a.by, a.evidence)
    if a.op_cmd == "supersede":
        return ops.supersede(a.id, a.by, a.reason)
    if a.op_cmd == "run":
        return ops.run(a.id)
    if a.op_cmd == "recover":
        return ops.recover(include_approved=a.include_approved)
    if a.op_cmd == "ticket":
        return ops.ticket(a.id)
    if a.op_cmd == "record-result":
        return ops.record_result(a.id, _data(a.result), a.outcome)
    if a.op_cmd == "record-observation":
        return ops.record_observation(a.id, _data(a.observed))


def cmd_sync(a):
    from .sync import SyncEngine, pull_object, source_status

    store = _store(a)
    if a.sync_cmd == "status":
        return [s.dump() for s in source_status(store.view())]
    source = _adapters(a).source(a.source)
    if a.sync_cmd == "run":
        return SyncEngine(store, source, a.scope, a.source).run()
    if a.sync_cmd == "reconcile":
        return SyncEngine(store, source, a.scope, a.source).reconcile()
    if a.sync_cmd == "pull":
        return pull_object(store, source, a.scope, a.id)


def cmd_defects(a):
    from .flows import defects

    store = _store(a)
    if a.defects_cmd == "check":
        return defects.evaluate(store.view())
    return defects.scan(store)


def cmd_test(a):
    from .flows import testing
    from .repo import load_run

    store = _store(a)
    if a.test_cmd == "submit":
        return testing.submit(store, _data(a.plan), a.by)
    if a.test_cmd == "collect":
        return testing.collect(store, _adapters(a), a.plan_id)
    if a.test_cmd == "status":
        run = load_run(store.view(), a.plan_id)
        return run.dump() if run else {"plan_id": a.plan_id, "state": "not_submitted"}


def cmd_baseline(a):
    from .flows import testing

    store = _store(a)
    if a.baseline_cmd == "propose":
        return testing.propose_baseline(store, a.run, a.name, a.by)
    return testing.approve_baseline(store, a.candidate, a.by, a.evidence, a.expect_current)


def cmd_daily(a):
    from .flows import daily
    from .models import FactSet

    store = _store(a)
    if a.daily_cmd == "collect":
        fs = daily.collect(store.view(), a.date)
        data = fs.dump()
        if a.out:
            Path(a.out).write_text(json.dumps(data, ensure_ascii=False, indent=2))
            return {"facts": len(fs.facts), "out": a.out, "gaps": fs.gaps, "template": daily.render_template(fs)}
        return data
    fs = FactSet.model_validate_json(_read(a.facts))
    if a.daily_cmd == "check":
        return daily.check_draft(_read(a.draft), fs)
    return daily.publish(store, fs, _read(a.draft) if a.draft else None)


def cmd_views(a):
    from .views import render

    store = _store(a)
    if a.views_cmd == "rebuild":
        return render.rebuild(store, force=a.force)
    return render.RENDERERS[a.name](store.view())


def cmd_schedule(a):
    from .schedule import tick

    return tick(_store(a), _adapters(a))


def cmd_contract(a):
    from .adapters import contracts

    if a.target == "git":
        return contracts.check_git_remote(a.remote)
    adapters = _adapters(a)
    if a.target == "aone":
        return contracts.check_aone(adapters.get("aone"), a.scope)
    if a.target == "nodemanager":
        return contracts.check_nodemanager(adapters.get("nodemanager"), _data(a.plan) if a.plan else None)
    if a.target == "im":
        return contracts.check_im(adapters.get("im"), a.im_target, a.chat, a.sample_user)
    if a.target == "kb":
        return contracts.check_kb(adapters.get("kb"), a.scope)


# ---------------- 参数定义 ----------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="aspeed", description="ASpeed：团队共享上下文与任务驱动 Agent 的控制层")
    p.add_argument("--context", default=os.environ.get("ASPEED_CONTEXT", "."), help="aspeed-context 专用 clone 路径")
    p.add_argument("--adapters", default=os.environ.get("ASPEED_ADAPTERS"), help="接入配置 YAML（默认全部 Fake）")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init-context", help="初始化 aspeed-context 仓库")
    s.add_argument("path")
    s.add_argument("--remote")
    s.set_defaults(fn=cmd_init_context)

    s = sub.add_parser("clone-context", help="为运行实例准备专用 clone（partial clone）")
    s.add_argument("remote")
    s.add_argument("path")
    s.add_argument("--no-partial", action="store_true")
    s.set_defaults(fn=cmd_clone_context)

    s = sub.add_parser("validate", help="校验上下文仓库（CI 用：--base origin/main）")
    s.add_argument("paths", nargs="*")
    s.add_argument("--base")
    s.set_defaults(fn=cmd_validate)

    s = sub.add_parser("schema", help="导出 JSON Schema")
    s.add_argument("action", choices=["export"])
    s.add_argument("dir")
    s.set_defaults(fn=cmd_schema_export)

    t = sub.add_parser("task", help="任务上下文包").add_subparsers(dest="task_cmd", required=True)
    s = t.add_parser("create", help="由草稿创建提案（--file - 读 stdin）")
    s.add_argument("--file", required=True)
    s.add_argument("--as", dest="as_", required=True, help="发布者成员 ID 或 agent:xxx")
    s = t.add_parser("list")
    s.add_argument("--stage")
    s = t.add_parser("related", help="发布前检索相关/重复任务")
    s.add_argument("--file")
    s.add_argument("--query")
    s.add_argument("-k", type=int, default=5)
    for name in ("show", "check"):
        s = t.add_parser(name)
        s.add_argument("id")
        if name == "show":
            s.add_argument("--markdown", action="store_true")
    s = t.add_parser("join")
    s.add_argument("id")
    s.add_argument("--member", required=True)
    s.add_argument("--role", default="contributor", choices=["lead", "contributor", "reviewer"])
    s = t.add_parser("leave")
    s.add_argument("id")
    s.add_argument("--member", required=True)
    s = t.add_parser("stage")
    s.add_argument("id")
    s.add_argument("to", choices=["clarifying", "claimable", "in_progress", "in_review", "done", "cancelled"])
    s.add_argument("--by", required=True)
    s.add_argument("--reason")
    s = t.add_parser("block")
    s.add_argument("id")
    s.add_argument("--by", required=True)
    s.add_argument("--reason", required=True)
    s.add_argument("--needs", nargs="*", help="形如 m:alice=提供 A100 节点")
    s.add_argument("--depends-on", nargs="*")
    s.add_argument("--unblock-when")
    s = t.add_parser("unblock")
    s.add_argument("id")
    s.add_argument("--by", required=True)
    s.add_argument("--note")
    s = t.add_parser("submit", help="反馈完成，触发验收")
    s.add_argument("id")
    s.add_argument("--by", required=True)
    s.add_argument("--evidence", nargs="*", help="run:<plan_id> 或链接")
    s.add_argument("--deliverable", nargs="*", help="形如 code=https://...")
    s = t.add_parser("accept")
    s.add_argument("id")
    s.add_argument("--by", required=True)
    s.add_argument("--reject", action="store_true")
    s.add_argument("--note")
    s = t.add_parser("promote", help="登记 Aone 建单意图")
    s.add_argument("id")
    s.add_argument("--by", required=True)
    s.add_argument("--scope", required=True)
    sub.choices["task"].set_defaults(fn=cmd_task)

    s = sub.add_parser("search", help="检索知识、任务与快照")
    s.add_argument("query")
    s.add_argument("-k", type=int, default=8)
    s.add_argument("--visibility", nargs="*", default=["team"])
    s.set_defaults(fn=cmd_search)

    o = sub.add_parser("op", help="外部操作意图")
    oo = o.add_subparsers(dest="op_cmd", required=True)
    s = oo.add_parser("list")
    s.add_argument("--state")
    s.add_argument("--kind")
    for name in ("show", "run", "ticket"):
        oo.add_parser(name).add_argument("id")
    s = oo.add_parser("approve")
    s.add_argument("id")
    s.add_argument("--by", required=True)
    s.add_argument("--evidence")
    s = oo.add_parser("supersede")
    s.add_argument("id")
    s.add_argument("--by", required=True)
    s.add_argument("--reason", required=True)
    s = oo.add_parser("recover")
    s.add_argument("--include-approved", action="store_true")
    s = oo.add_parser("record-result")
    s.add_argument("id")
    s.add_argument("--result", required=True)
    s.add_argument("--outcome", default="executed", choices=["executed", "unknown", "failed"])
    s = oo.add_parser("record-observation")
    s.add_argument("id")
    s.add_argument("--observed", required=True)
    o.set_defaults(fn=cmd_op)

    y = sub.add_parser("sync", help="主动拉取外部来源")
    yy = y.add_subparsers(dest="sync_cmd", required=True)
    yy.add_parser("status")
    for name in ("run", "reconcile", "pull"):
        s = yy.add_parser(name)
        s.add_argument("source", choices=["aone", "im", "kb"])
        s.add_argument("--scope", required=True)
        if name == "pull":
            s.add_argument("--id", required=True)
    y.set_defaults(fn=cmd_sync)

    d = sub.add_parser("defects", help="缺陷跟进")
    dd = d.add_subparsers(dest="defects_cmd", required=True)
    dd.add_parser("scan", help="按规则扫描并登记提醒")
    dd.add_parser("check", help="只评估，不写入")
    d.set_defaults(fn=cmd_defects)

    e = sub.add_parser("test", help="测试计划与结果")
    ee = e.add_subparsers(dest="test_cmd", required=True)
    s = ee.add_parser("submit")
    s.add_argument("--plan", required=True)
    s.add_argument("--by", required=True)
    for name in ("collect", "status"):
        ee.add_parser(name).add_argument("plan_id")
    e.set_defaults(fn=cmd_test)

    b = sub.add_parser("baseline", help="基线")
    bb = b.add_subparsers(dest="baseline_cmd", required=True)
    s = bb.add_parser("propose")
    s.add_argument("--run", required=True)
    s.add_argument("--name", required=True)
    s.add_argument("--by", required=True)
    s = bb.add_parser("approve")
    s.add_argument("candidate")
    s.add_argument("--by", required=True)
    s.add_argument("--evidence", required=True)
    s.add_argument("--expect-current", help="确认时看到的当前基线版本；没有当前基线时省略")
    b.set_defaults(fn=cmd_baseline)

    r = sub.add_parser("daily", help="每日同步")
    rr = r.add_subparsers(dest="daily_cmd", required=True)
    s = rr.add_parser("collect")
    s.add_argument("--date")
    s.add_argument("--out")
    s = rr.add_parser("check")
    s.add_argument("--facts", required=True)
    s.add_argument("--draft", required=True)
    s = rr.add_parser("publish")
    s.add_argument("--facts", required=True)
    s.add_argument("--draft")
    r.set_defaults(fn=cmd_daily)

    v = sub.add_parser("views", help="任务全景 / 阻塞清单 / 可认领任务")
    vv = v.add_subparsers(dest="views_cmd", required=True)
    vv.add_parser("show").add_argument("name", choices=["board", "blockers", "claimable"])
    vv.add_parser("rebuild").add_argument("--force", action="store_true")
    v.set_defaults(fn=cmd_views)

    sub.add_parser("schedule", help="定时任务入口").add_argument("action", choices=["tick"])
    sub.choices["schedule"].set_defaults(fn=cmd_schedule)

    c = sub.add_parser("contract", help="接入契约测试（只能指向沙箱项目/测试群）")
    c.add_argument("action", choices=["run"])
    c.add_argument("target", choices=["aone", "nodemanager", "im", "kb", "git"])
    c.add_argument("--scope")
    c.add_argument("--plan")
    c.add_argument("--im-target")
    c.add_argument("--chat")
    c.add_argument("--sample-user")
    c.add_argument("--remote")
    c.set_defaults(fn=cmd_contract)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = args.fn(args)
    except AspeedError as exc:
        print(json.dumps(exc.to_dict(), ensure_ascii=False, indent=2, default=str))
        return 2
    except (FileNotFoundError, FileExistsError, ValueError) as exc:
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    if isinstance(result, str):
        print(result)
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
