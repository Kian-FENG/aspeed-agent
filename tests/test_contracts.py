import sys
from pathlib import Path

from aspeed.adapters import contracts
from aspeed.adapters.fake import FakeAone, FakeIM, FakeKB, FakeNodeManager
from aspeed.adapters.script import ScriptAone, ScriptNodeManager

SHIM = [sys.executable, str(Path(__file__).parent / "shims" / "fake_bridge.py")]


def _assert_ok(results):
    bad = [r for r in results if r["status"] == "fail"]
    assert not bad, bad


def test_fake_aone_contract(env):
    _assert_ok(contracts.check_aone(FakeAone(), "sandbox"))


def test_script_bridge_aone_contract(env):
    adapter = ScriptAone(SHIM + ["aone"], capabilities=list(FakeAone.capabilities))
    results = contracts.check_aone(adapter, "sandbox")
    _assert_ok(results)
    assert {r["capability"] for r in results} >= {"查询与分页", "参与者维护", "状态流转", "操作结果查询"}


def test_aone_without_conditional_update_is_reported(env):
    a = FakeAone()
    a.capabilities = {"incremental"}
    results = contracts.check_aone(a, "sandbox")
    assert any(r["status"] == "skip" and "降级" in r["detail"] for r in results)


def test_nodemanager_contract(env):
    _assert_ok(contracts.check_nodemanager(FakeNodeManager()))
    _assert_ok(contracts.check_nodemanager(ScriptNodeManager(SHIM + ["nodemanager"])))


def test_im_kb_contract(env):
    im = FakeIM()
    im.post("chat-perf", "im:u001", "P99 又抖了")
    _assert_ok(contracts.check_im(im, "im:u002", chat="chat-perf", sample_user="im:u001"))
    kb = FakeKB()
    kb.put("space", "doc-1", "调优手册", "……")
    _assert_ok(contracts.check_kb(kb, "space"))


def test_git_remote_contract(env):
    results = contracts.check_git_remote(str(env.remote))
    _assert_ok(results)
    assert any(r["status"] == "pass" for r in results)
