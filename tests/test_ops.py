import multiprocessing as mp

import pytest

from aspeed.adapters.base import Adapters
from aspeed.adapters.fake import FakeAone, FakeIM, FakeNodeManager
from aspeed.errors import PermissionDenied
from aspeed.ops import OpService
from aspeed.repo import load_op
from aspeed.runtime import RunContext
from aspeed.store import ContextStore


def adapters():
    return Adapters(aone=FakeAone(), im=FakeIM(), nodemanager=FakeNodeManager())


def svc(env, run_id=None):
    ctx = RunContext(run_id=run_id) if run_id else RunContext()
    return OpService(ContextStore(env.clone(), ctx), adapters())


CREATE = dict(kind="aone.create", business_key={"task": "P-20261014-AAAA"},
              params={"scope": "proj-x", "fields": {"type": "task", "title": "建单测试"}}, requested_by="m:alice")


def test_propose_is_idempotent(env):
    a, b = svc(env), svc(env)
    r1 = a.propose(**CREATE)
    r2 = b.propose(**CREATE)
    assert r1["created"] and not r2["created"] and r1["op_id"] == r2["op_id"]
    assert r1["state"] == "proposed"  # policy: confirm


def test_approve_requires_role(env):
    s = svc(env)
    op_id = s.propose(**CREATE)["op_id"]
    with pytest.raises(PermissionDenied):
        s.approve(op_id, "m:carol")
    assert s.approve(op_id, "m:alice")["state"] == "approved"


def test_full_lifecycle_records_snapshot(env):
    s = svc(env)
    op_id = s.propose(**CREATE)["op_id"]
    s.approve(op_id, "m:alice", evidence="im:msg/1")
    res = s.run(op_id)
    assert res["state"] == "recorded"
    view = s.store.view()
    op = load_op(view, op_id)
    assert [t.to for t in op.history] == ["proposed", "approved", "leased", "executed", "verified", "recorded"]
    assert view.glob("raw/aone/proj-x/*.json")
    assert FakeAone().calls("create") == 1


def test_unknown_outcome_recovers_without_duplicate(env, monkeypatch):
    s = svc(env, "run-1")
    op_id = s.propose(**CREATE)["op_id"]
    s.approve(op_id, "m:alice")
    FakeAone().inject("create", "unknown_after_write")
    res = s.run(op_id)
    assert res["outcome"] == "unknown"
    # 租约未过期时，其他实例不能接管
    other = svc(env, "run-2")
    assert other.recover() == []
    # 租约过期后接管：先查后做，不重复创建
    monkeypatch.setenv("ASPEED_NOW", "2026-10-14T03:00:00Z")
    out = other.recover()
    assert out[0]["state"] == "recorded"
    assert FakeAone().calls("create") == 1
    op = load_op(other.store.view(), op_id)
    assert [a.outcome for a in op.attempts][-2:] == ["started", "found_existing"]


def test_transient_error_releases_lease(env):
    s = svc(env)
    op_id = s.propose(**CREATE)["op_id"]
    s.approve(op_id, "m:alice")
    FakeAone().inject("create", "transient")
    assert s.run(op_id)["state"] == "approved"
    assert s.run(op_id)["state"] == "recorded"
    assert FakeAone().calls("create") == 2  # 第一次确定未生效


def test_stale_version_needs_human(env):
    aone = FakeAone()
    item = aone.seed("proj-x", title="缺陷", type="defect", status="fixed")
    s = svc(env)
    op_id = s.propose(kind="aone.transition", business_key={"item": item["id"], "to": "closed"},
                      params={"scope": "proj-x", "item_id": item["id"], "to_status": "closed"},
                      requested_by="m:bob", preconditions={"expected_version": item["version"]})["op_id"]
    s.approve(op_id, "m:bob")
    aone.update_raw("proj-x", item["id"], title="被他人修改")
    assert s.run(op_id)["state"] == "needs_human"


def test_missing_capability_downgrades_to_human(env):
    s = svc(env)
    s.adapters.aone.capabilities = {"incremental"}
    op_id = s.propose(kind="aone.transition", business_key={"item": "X-1", "to": "closed"},
                      params={"scope": "proj-x", "item_id": "X-1", "to_status": "closed"}, requested_by="m:bob")["op_id"]
    s.approve(op_id, "m:bob")
    res = s.run(op_id)
    assert res["status"] == "needs_human" and res["missing_capabilities"] == ["conditional_update"]


def test_dry_run_mode_never_executes(env):
    s = svc(env)
    r = s.propose(kind="im.remind", business_key={"k": 1}, params={"to": "m:bob", "im": "im:u002", "text": "hi"},
                  requested_by="schedule:test")
    assert r["state"] == "simulated"
    assert s.run(r["op_id"])["status"] == "skipped"
    assert FakeIM().sent() == []


def test_ticket_mode(env):
    s = svc(env)
    op_id = s.propose(**CREATE)["op_id"]
    s.approve(op_id, "m:alice")
    t = s.ticket(op_id)
    assert t["method"] == "create_item" and t["args"]["op_marker"] == op_id
    # 模拟 LLM 调用 SKILL 并回传结果
    receipt = FakeAone().create_item(**t["args"])
    assert s.record_result(op_id, receipt)["state"] == "executed"
    obj = FakeAone().get_object("proj-x", receipt["id"])
    bad = s.record_observation  # 观察结果由代码判定
    out = bad(op_id, {"id": obj.id, "data": obj.data, "version": obj.version, "type": obj.type})
    assert out["state"] == "recorded"


def _race(path, op_id, q):
    s = OpService(ContextStore(path), adapters())
    q.put(s.run(op_id))


def test_concurrent_runners_execute_once(env):
    s = svc(env)
    op_id = s.propose(**CREATE)["op_id"]
    s.approve(op_id, "m:alice")
    paths = [env.clone() for _ in range(4)]
    q = mp.Queue()
    procs = [mp.Process(target=_race, args=(p, op_id, q)) for p in paths]
    for p in procs:
        p.start()
    for p in procs:
        p.join(60)
    results = [q.get() for _ in procs]
    assert FakeAone().calls("create") == 1
    assert sum(1 for r in results if r.get("state") == "recorded") >= 1
    assert load_op(s.store.view(), op_id).state == "recorded"
