from aspeed.adapters.fake import FakeAone
from aspeed.repo import load_raw, load_sync
from aspeed.store import ContextStore
from aspeed.sync import SyncEngine, pull_object, source_status


def test_incremental_sync_pages_and_dedupes(env, monkeypatch):
    aone = FakeAone()
    for i in range(5):
        aone.seed("proj-x", title=f"缺陷 {i}", type="defect")
    store = ContextStore(env.clone())
    store.publisher.backoff_base = 0.01
    eng = SyncEngine(store, aone, "proj-x")
    res = eng.run()
    assert res["status"] == "ok" and res["stats"]["created"] == 5
    view = store.view()
    assert len(view.glob("raw/aone/proj-x/*.json")) == 5
    rec = load_sync(view, "aone", "proj-x")
    assert rec.cursor["updated_since"] and rec.last_success_at
    # 无变化：不提交
    head = store.git.head()
    again = eng.run()
    assert again["commits"] == [] and store.git.head() == head
    # 有变化：只更新变化对象
    aone.update_raw("proj-x", "PROJ-X-2", status="in_progress")
    monkeypatch.setenv("ASPEED_NOW", "2026-10-14T02:05:00Z")
    third = eng.run()
    assert third["stats"].get("updated") == 1
    assert load_raw(store.view(), "aone", "proj-x", "PROJ-X-2").data["status"] == "in_progress"


def test_failure_records_gap_and_keeps_cursor(env, monkeypatch):
    aone = FakeAone()
    aone.seed("proj-x", title="a")
    store = ContextStore(env.clone())
    eng = SyncEngine(store, aone, "proj-x")
    eng.run()
    cursor = load_sync(store.view(), "aone", "proj-x").cursor
    aone.inject("list", "transient")
    assert eng.run()["status"] == "gap"
    rec = load_sync(store.view(), "aone", "proj-x")
    assert rec.cursor == cursor and rec.gaps[-1].open
    assert source_status(store.view())[0].open_gaps
    monkeypatch.setenv("ASPEED_NOW", "2026-10-14T02:10:00Z")
    eng.run()
    assert not any(g.open for g in load_sync(store.view(), "aone", "proj-x").gaps)


def test_stale_observation_never_overwrites_newer(env):
    aone = FakeAone()
    item = aone.seed("proj-x", title="a")
    store = ContextStore(env.clone())
    aone.update_raw("proj-x", item["id"], title="b")  # version 2
    pull_object(store, aone, "proj-x", item["id"])
    from aspeed.snapshots import SourceObject, make_snapshot, upsert

    old = SourceObject(id=item["id"], type="task", version=1, updated_at=None, data={"title": "a"})
    out = {}
    store.publish(lambda ws: out.setdefault("r", upsert(ws, make_snapshot("aone", "proj-x", old, ws.now))), "旧观察")
    assert out["r"] == "stale"
    assert load_raw(store.view(), "aone", "proj-x", item["id"]).data["title"] == "b"


def test_reconcile_detects_deletion(env):
    aone = FakeAone()
    a = aone.seed("proj-x", title="a")
    aone.seed("proj-x", title="b")
    store = ContextStore(env.clone())
    eng = SyncEngine(store, aone, "proj-x")
    eng.run()
    aone.delete_raw("proj-x", a["id"])
    res = eng.reconcile()
    assert res["summary"]["deleted"] == 1
    snap = load_raw(store.view(), "aone", "proj-x", a["id"])
    assert snap.deleted
    rec = load_sync(store.view(), "aone", "proj-x")
    assert rec.lease is None and rec.last_reconcile_at


def test_multi_page_cursor_advances_per_page(env):
    aone = FakeAone()
    for i in range(5):
        aone.seed("proj-x", title=f"t{i}")
    store = ContextStore(env.clone())

    def small_pages(ws):
        policy = ws.read_text("control/policy.yaml")
        ws.write_text("control/policy.yaml", policy.replace("page_size: 50", "page_size: 2"))

    store.publish(small_pages, "测试：小分页")
    aone.inject("list", "transient")  # 第一页即失败：不推进游标
    assert SyncEngine(store, aone, "proj-x").run()["status"] == "gap"
    assert load_sync(store.view(), "aone", "proj-x").cursor == {}
    res = SyncEngine(store, aone, "proj-x").run()
    assert res["pages"] == 3 and res["stats"]["created"] == 5 and len(res["commits"]) == 3
