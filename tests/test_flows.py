import multiprocessing as mp

import pytest

from aspeed.adapters.base import Adapters
from aspeed.adapters.fake import FakeAone, FakeIM, FakeNodeManager
from aspeed.errors import ValidationFailed
from aspeed.flows import daily, defects, search, tasks, testing
from aspeed.ops import OpService
from aspeed.repo import iter_ops, load_task
from aspeed.store import ContextStore
from aspeed.sync import SyncEngine
from aspeed.views import render

DRAFT = """---
title: 推理服务 P99 延迟优化
stage: claimable
coordinator: m:alice
acceptor: m:bob
skills_needed: [cuda, profiling]
goal_refs: [G-2026H2-perf]
acceptance:
  - {id: AC1, check: "P99 不超过基线 5%", mode: auto, rule: {metric: p99_ms, op: "<=", baseline: perf-main, tolerance: 0.05}}
sources:
  - {ref: "im:msg/42", title: "群聊讨论"}
---

## 为什么做

线上 P99 抖动影响 SLA。

## 如何参与

可独立认领：kernel 融合、调度参数扫描。
"""


def adapters():
    return Adapters(aone=FakeAone(), im=FakeIM(), nodemanager=FakeNodeManager())


def test_create_complete_and_incomplete(env):
    store = ContextStore(env.clone())
    ok = tasks.create_task(store, DRAFT, "m:carol")
    assert ok["stage"] == "claimable" and ok["missing"] == []
    partial = tasks.create_task(store, "---\ntitle: 只有标题\n---\n", "m:carol")
    assert partial["stage"] == "clarifying" and len(partial["missing"]) == 5
    with pytest.raises(ValidationFailed):
        tasks.create_task(store, "---\ntitle: x\nstage: done\n---\n", "m:carol")


def _join(path, task_id, member, q):
    store = ContextStore(path, max_attempts=30)
    q.put(tasks.join(store, OpService(store, adapters()), task_id, member, "contributor"))


def test_concurrent_joins_keep_everyone(env):
    store = ContextStore(env.clone())
    task_id = tasks.create_task(store, DRAFT, "m:carol")["id"]
    q = mp.Queue()
    procs = [mp.Process(target=_join, args=(env.clone(), task_id, m, q)) for m in ("m:alice", "m:bob", "m:carol", "m:dave")]
    for p in procs:
        p.start()
    for p in procs:
        p.join(60)
    members = {p.member for p in load_task(store.view(), task_id).meta.participants}
    assert members == {"m:alice", "m:bob", "m:carol", "m:dave"}


def test_stage_rules_block_and_accept(env):
    store = ContextStore(env.clone())
    ops = OpService(store, adapters())
    task_id = tasks.create_task(store, DRAFT, "m:carol")["id"]
    assert tasks.join(store, ops, task_id, "m:dave", "lead")["stage"] == "in_progress"
    tasks.block(store, task_id, "m:dave", "缺少 A100 节点", needs=["m:alice=协调 2 台 A100"])
    assert "缺少 A100" in render.render_blockers(store.view())
    assert tasks.load_task(store.view(), task_id).meta.stage.value == "in_progress"  # 阻塞不改变阶段
    tasks.unblock(store, task_id, "m:dave")
    with pytest.raises(ValidationFailed):
        tasks.set_stage(store, ops, task_id, "done", "m:dave")
    # 无测试证据：自动规则无法判定 → 提交验收人
    res = tasks.submit(store, ops, task_id, "m:dave", evidence=["https://code.example/mr/1"])
    assert res["result"] == "awaiting_acceptor" and res["stage"] == "in_review"
    assert tasks.accept(store, task_id, "m:bob", accepted=True)["stage"] == "done"
    assert tasks.set_stage(store, ops, task_id, "in_progress", "m:dave")["stage"] == "in_progress"  # 重新打开


def test_promote_creates_aone_link(env):
    store = ContextStore(env.clone())
    ops = OpService(store, adapters())
    task_id = tasks.create_task(store, DRAFT, "m:carol")["id"]
    tasks.join(store, ops, task_id, "m:dave", "lead")
    op = tasks.promote(store, ops, task_id, "m:alice", "proj-x")
    assert op["state"] == "proposed"
    ops.approve(op["op_id"], "m:alice")
    assert ops.run(op["op_id"])["state"] == "recorded"
    doc = load_task(store.view(), task_id)
    assert doc.meta.aone and doc.meta.stage is None
    eff = tasks.effective(store.view(), doc)
    assert eff["stage"] == "in_progress" and eff["owner"] == "m:dave"
    # 关联后加入走 Aone 操作
    j = tasks.join(store, ops, task_id, "m:carol")
    assert j["via"] == "aone" and j["state"] == "approved"  # policy: auto
    assert ops.run(j["op_id"])["state"] == "recorded"
    eff = tasks.effective(store.view(), load_task(store.view(), task_id))
    assert "m:carol" in {p["member"] for p in eff["participants"]}


def test_defect_scan_dedupes_and_caps(env, monkeypatch):
    aone = FakeAone()
    aone.seed("proj-x", type="defect", title="无人认领", status="open")
    aone.seed("proj-x", type="defect", title="已逾期", status="in_progress", owner="dave", due="2026-10-09")
    aone.seed("proj-x", type="defect", title="快到期", status="in_progress", owner="carol", due="2026-10-15")
    aone.seed("proj-x", type="defect", title="已修复待验证", status="fixed", owner="carol", due="2026-10-20")
    store = ContextStore(env.clone())
    SyncEngine(store, aone, "proj-x").run()
    first = defects.scan(store)
    checks = sorted(c["check"] for c in first["created"])
    assert checks == ["due_soon", "missing_owner", "missing_verification", "overdue", "overdue_escalate"]
    assert all(c["state"] == "simulated" for c in first["created"])  # 默认 dry_run
    second = defects.scan(store)
    assert second["created"] == [] and second["already_reminded"] == 5
    # 第二天：新窗口再次提醒，但受每人每日上限约束
    monkeypatch.setenv("ASPEED_NOW", "2026-10-15T02:00:00Z")
    third = defects.scan(store)
    assert {c["check"] for c in third["created"]} >= {"overdue"}


def test_regression_flow_and_baseline(env):
    store = ContextStore(env.clone())
    ad = adapters()
    ops = OpService(store, ad)
    plan = {"intent": "主干性能回归", "build": {"id": "git:abc123", "label": "main@abc123"},
            "suites": [{"name": "perf", "cases": ["p99"]}],
            "checks": [{"metric": "p99_ms", "op": "<=", "threshold": 120}], "expected_artifacts": ["metrics.json"]}
    sub = testing.submit(store, plan, "m:bob")
    again = testing.submit(store, plan, "m:bob")
    assert sub["plan_id"] == again["plan_id"] and not again["created"]
    ops.approve(sub["op_id"], "m:bob")
    assert ops.run(sub["op_id"])["state"] == "recorded"
    job_id = store.view().read_yaml(f"tests/runs/{sub['plan_id']}.yaml")["job_id"]
    FakeNodeManager().finish(job_id, cases=[{"name": "p99", "status": "passed"}], metrics={"p99_ms": 110},
                             artifacts=[{"name": "metrics.json", "sha256": "ab"}])
    res = testing.collect(store, ad, sub["plan_id"])
    assert res["verdict"] == "pass"
    cand = testing.propose_baseline(store, sub["plan_id"], "perf-main", "m:bob")
    assert cand["current"] is None
    with pytest.raises(Exception):
        testing.approve_baseline(store, cand["candidate"], "m:bob", "im:msg/9", None)  # 无批准权限
    ok = testing.approve_baseline(store, cand["candidate"], "m:alice", "im:msg/9", None)
    assert ok["version_id"]
    # 预期旧版本不符：拒绝
    stale = testing.approve_baseline(store, cand["candidate"], "m:alice", "im:msg/10", None)
    assert stale["status"] == "aborted"


def test_failure_classification(env):
    store = ContextStore(env.clone())
    ad = adapters()
    ops = OpService(store, ad)
    plan = {"intent": "回归", "build": {"id": "git:def456"}, "suites": [{"name": "func"}]}
    sub = testing.submit(store, plan, "m:bob")
    ops.approve(sub["op_id"], "m:bob")
    ops.run(sub["op_id"])
    job_id = store.view().read_yaml(f"tests/runs/{sub['plan_id']}.yaml")["job_id"]
    FakeNodeManager().finish(job_id, cases=[
        {"name": "a", "status": "failed", "message": "AssertionError: output mismatch"},
        {"name": "b", "status": "failed", "message": "CUDA error: device lost"},
    ])
    res = testing.collect(store, ad, sub["plan_id"])
    cats = {f["case"]: f["category"] for f in res["failures"]}
    assert cats == {"a": "product", "b": "environment"} and res["verdict"] == "fail"
    assert len(res["defect_ops"]) == 1  # 只为产品失败登记建缺陷意图


def test_daily_report_cites_sources(env, monkeypatch):
    store = ContextStore(env.clone())
    ops = OpService(store, adapters())
    tasks.create_task(store, DRAFT, "m:carol")
    first = daily.collect(store.view())
    assert any("第一份日报" in g for g in first.gaps)
    assert any("新提案" in f.text for f in first.facts)
    daily.publish(store, first)
    monkeypatch.setenv("ASPEED_NOW", "2026-10-15T02:00:00Z")
    task_id = tasks.create_task(store, DRAFT.replace("P99", "吞吐"), "m:carol")["id"]
    tasks.join(store, ops, task_id, "m:dave", "lead")
    fs = daily.collect(store.view())
    texts = [f.text for f in fs.facts if f.category == "progress"]
    assert any("新提案" in t for t in texts)
    bad = "## 新增进展\n\n- 一切顺利\n\n## 阻塞与求助\n\n- 无\n\n## 可认领任务\n\n- 无\n\n## 待决策事项\n\n- 无\n\n## 测试与基线变化\n\n- 无\n"
    assert daily.check_draft(bad, fs)["errors"]
    with pytest.raises(ValidationFailed):
        daily.publish(store, fs, bad)
    res = daily.publish(store, fs)
    text = store.view().read_text(res["path"])
    assert "数据来源与截止时间" in text and "[F1]" in text


def test_views_rebuild_only_on_change(env):
    store = ContextStore(env.clone())
    tasks.create_task(store, DRAFT, "m:carol")
    assert render.rebuild(store)["status"] == "committed"
    assert render.rebuild(store)["status"] == "noop"
    assert "推理服务" in store.view().read_text("views/claimable.md")


def test_search_finds_tasks(env):
    store = ContextStore(env.clone())
    tasks.create_task(store, DRAFT, "m:carol")
    hits = search.search(store.view(), "延迟 优化")
    assert hits and hits[0]["path"].startswith("tasks/") and hits[0]["commit"]
