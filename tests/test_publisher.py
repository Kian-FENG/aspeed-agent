import multiprocessing as mp
import subprocess

import pytest

from aspeed.errors import Abort, DirtyWorkspace, ValidationFailed
from aspeed.store import ContextStore


def _increment(ws):
    current = int(ws.read_text("counter.txt") or "0")
    ws.write_text("counter.txt", f"{current + 1}\n")
    return current + 1


def test_publish_commit_and_noop(env):
    store = ContextStore(env.clone())
    r1 = store.publish(_increment, "计数 +1", op_id="op-test")
    assert r1.status == "committed" and r1.value == 1
    msg = subprocess.run(["git", "-C", str(store.root), "log", "-1", "--format=%B"], capture_output=True, text=True).stdout
    assert "Op-Id: op-test" in msg and "Run-Id:" in msg
    r2 = store.publish(lambda ws: ws.write_text("counter.txt", "1\n"), "无变化")
    assert r2.status == "noop"


def test_rederive_on_remote_change(env):
    a, b = ContextStore(env.clone()), ContextStore(env.clone())
    a.publish(_increment, "a")
    # b 的本地 clone 落后；发布时应在最新远端上重新计算，而不是覆盖
    r = b.publish(_increment, "b")
    assert r.value == 2


def test_abort_leaves_no_commit(env):
    store = ContextStore(env.clone())
    head = store.git.head()

    def mutation(ws):
        ws.write_text("x.txt", "x")
        raise Abort("条件不满足")

    r = store.publish(mutation, "abort")
    assert r.status == "aborted" and store.git.head() == head and store.git.is_clean()


def test_validation_blocks_bad_record(env):
    store = ContextStore(env.clone())
    with pytest.raises(ValidationFailed):
        store.publish(lambda ws: ws.write_yaml("control/members.yaml", {"members": [{"id": "bad id"}]}), "bad")
    assert store.git.is_clean()


def test_refuses_dirty_clone(env):
    path = env.clone()
    (path / "local.txt").write_text("人的未提交改动")
    with pytest.raises(DirtyWorkspace):
        ContextStore(path).publish(_increment, "x")
    assert (path / "local.txt").exists()


def _worker(path, n, q):
    store = ContextStore(path, max_attempts=50)
    store.publisher.backoff_base = 0.01
    values = [store.publish(_increment, "并发计数").value for _ in range(n)]
    q.put(values)


def test_concurrent_publishers_never_lose_updates(env):
    workers, per = 4, 5
    paths = [env.clone() for _ in range(workers)]
    q = mp.Queue()
    procs = [mp.Process(target=_worker, args=(p, per, q)) for p in paths]
    for p in procs:
        p.start()
    for p in procs:
        p.join(120)
        assert p.exitcode == 0
    values = sorted(v for _ in procs for v in q.get())
    assert values == list(range(1, workers * per + 1))
    final = ContextStore(env.clone()).view().read_text("counter.txt")
    assert final.strip() == str(workers * per)
