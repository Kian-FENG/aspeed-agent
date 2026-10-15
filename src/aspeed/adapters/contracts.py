"""接入契约测试：把方案中“M0 必须核验的能力”写成可执行检查。

同一套检查先在 Fake 上通过，接入真实 SKILL 后直接复用：
    aspeed contract run aone --scope <沙箱项目>
注意：检查会在目标系统中创建测试对象，只能指向沙箱项目、测试群或测试空间。
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import traceback
from pathlib import Path
from typing import Callable

from ..ids import random_code
from .base import AoneAdapter, IMAdapter, KBAdapter, NodeManagerAdapter, StaleVersion


class Recorder:
    def __init__(self, target: str):
        self.target = target
        self.results: list[dict] = []

    def check(self, capability: str, name: str, fn: Callable[[], str | None]) -> None:
        try:
            detail = fn()
            self.results.append({"target": self.target, "capability": capability, "check": name, "status": "pass",
                                 **({"detail": detail} if detail else {})})
        except SkipCheck as exc:
            self.results.append({"target": self.target, "capability": capability, "check": name, "status": "skip",
                                 "detail": str(exc)})
        except Exception as exc:  # noqa: BLE001 - 契约测试需要记录所有失败
            self.results.append({"target": self.target, "capability": capability, "check": name, "status": "fail",
                                 "detail": f"{type(exc).__name__}: {exc}",
                                 "trace": traceback.format_exc(limit=3)})

    def manual(self, capability: str, name: str, how: str) -> None:
        self.results.append({"target": self.target, "capability": capability, "check": name, "status": "manual",
                             "detail": how})


class SkipCheck(Exception):
    pass


def _expect(cond, message: str) -> None:
    if not cond:
        raise AssertionError(message)


def check_aone(adapter: AoneAdapter, scope: str, transition_to: str = "in_progress",
               accounts: tuple[str, str] = ("contract-a", "contract-b")) -> list[dict]:
    r = Recorder("aone")
    tag = random_code(6)
    created: dict = {}

    def create():
        res = adapter.create_item(scope, {"type": "task", "title": f"[契约测试] {tag}"}, f"contract-{tag}-create")
        _expect(res.get("id"), "create_item 未返回 id")
        created.update(res)
        obj = adapter.get_object(scope, res["id"])
        _expect(obj is not None, "创建后无法查询到对象")
        return f"id={res['id']}"

    r.check("创建与更新", "create_item + get_object", create)

    def version():
        obj = adapter.get_object(scope, created["id"])
        _expect(obj.version is not None or obj.updated_at is not None, "对象缺少版本与更新时间")
        return f"version={obj.version} updated_at={obj.updated_at}"

    r.check("来源版本或更新时间", "对象携带版本", version)

    def paging():
        ids = []
        for i in range(2):
            adapter.create_item(scope, {"type": "task", "title": f"[契约测试] {tag}-{i}"}, f"contract-{tag}-p{i}")
        for _ in range(2):
            seen, token = [], None
            while True:
                page = adapter.list_objects(scope, page_token=token, page_size=1)
                seen += [o.id for o in page.items]
                token = page.next_token
                if not token:
                    break
            ids.append(seen)
        _expect(len(ids[0]) == len(set(ids[0])), "分页出现重复对象")
        _expect(ids[0] == ids[1], "两次完整分页顺序不一致")
        _expect(created["id"] in ids[0], "分页结果缺少新建对象")
        latest = adapter.get_object(scope, created["id"])
        if latest.updated_at:
            since = adapter.list_objects(scope, updated_since=str(latest.updated_at), page_size=200)
            _expect(created["id"] in [o.id for o in since.items], "增量查询未包含边界时间的对象")
        return f"{len(ids[0])} 个对象"

    r.check("查询与分页", "稳定分页与增量查询", paging)

    def participants():
        a, b = accounts
        adapter.add_participant(scope, created["id"], a, "contributor", f"contract-{tag}-pa")
        adapter.add_participant(scope, created["id"], b, "contributor", f"contract-{tag}-pb")
        obj = adapter.get_object(scope, created["id"])
        names = [p.get("account") for p in obj.data.get("participants", [])]
        _expect(a in names and b in names, f"参与者被覆盖：{names}")

    r.check("参与者维护", "追加参与者不覆盖已有参与者", participants)

    def transition():
        obj = adapter.get_object(scope, created["id"])
        res = adapter.transition(scope, created["id"], transition_to, obj.version, f"contract-{tag}-t1")
        after = adapter.get_object(scope, created["id"])
        _expect(after.data.get("status") == transition_to, "状态未变化")
        if obj.version is not None and after.version is not None:
            _expect(after.version != obj.version, "状态变化后版本未变化")
        return f"version {obj.version} → {res.get('version')}"

    r.check("状态流转", "按当前版本流转", transition)

    def conditional():
        if "conditional_update" not in adapter.capabilities:
            raise SkipCheck("不支持条件更新：无法防止并发覆盖的写操作将降级为建议或人工执行")
        try:
            adapter.transition(scope, created["id"], transition_to, -1, f"contract-{tag}-t2")
        except StaleVersion:
            return "旧版本被拒绝"
        raise AssertionError("使用过期版本更新未被拒绝")

    r.check("状态流转", "条件更新拒绝过期版本", conditional)

    def lookup():
        res = adapter.find_by_marker(scope, f"contract-{tag}-create")
        _expect(res and res.get("id") == created["id"], "无法按操作标记查询结果")

    r.check("操作结果查询", "按操作标记查询", lookup)
    return r.results


def check_nodemanager(adapter: NodeManagerAdapter, plan: dict | None = None) -> list[dict]:
    r = Recorder("nodemanager")
    tag = random_code(6)
    plan = plan or {"intent": "契约测试", "suites": [{"name": "smoke"}]}
    job: dict = {}

    def submit():
        res = adapter.submit(plan, f"contract-{tag}")
        _expect(res.get("job_id"), "submit 未返回 job_id")
        job.update(res)

    r.check("提交任务", "submit", submit)

    def dedupe():
        again = adapter.submit(plan, f"contract-{tag}")
        found = adapter.find_by_dedupe(f"contract-{tag}")
        _expect(again.get("job_id") == job["job_id"], "重复提交产生了新作业")
        _expect(found and found.get("job_id") == job["job_id"], "find_by_dedupe 未找到作业")

    r.check("重复提交识别", "相同去重键返回同一作业", dedupe)
    r.check("查询状态", "status", lambda: adapter.status(job["job_id"]).get("state"))

    def results():
        res = adapter.results(job["job_id"])
        for key in ("cases", "metrics", "artifacts"):
            _expect(key in res, f"results 缺少 {key}")

    r.check("读取产物", "results 结构", results)
    r.check("超时或取消处理", "cancel", lambda: adapter.cancel(job["job_id"]).get("state"))
    r.check("资源配额", "quota", lambda: str(adapter.quota()))
    return r.results


def check_im(adapter: IMAdapter, target: str, chat: str | None = None, sample_user: str | None = None) -> list[dict]:
    r = Recorder("im")
    tag = random_code(6)
    sent: dict = {}

    def send():
        res = adapter.send(target, f"[ASpeed 契约测试 {tag}] 可忽略", f"contract-{tag}")
        _expect(res.get("message_id"), "send 未返回 message_id")
        sent.update(res)
        found = adapter.find_sent(f"contract-{tag}")
        _expect(found and found.get("message_id") == res["message_id"], "无法按操作标记查询已发送消息")
        _expect(adapter.get_message(res["message_id"]), "无法查询已发送消息")

    r.check("消息发送和结果查询", "send + find_sent + get_message", send)

    def identity():
        if not sample_user:
            raise SkipCheck("未提供 sample_user")
        _expect(adapter.resolve_user(sample_user), "无法解析成员身份")

    r.check("身份信息", "resolve_user", identity)

    def read_scope():
        if not chat:
            raise SkipCheck("未提供可读取的 chat")
        a = [o.id for o in adapter.list_objects(chat, page_size=200).items]
        b = [o.id for o in adapter.list_objects(chat, page_size=200).items]
        _expect(a == b, "两次读取的对象 ID 不稳定")
        items = adapter.list_objects(chat, page_size=200).items
        _expect(all(o.version is not None or o.updated_at for o in items), "消息缺少版本信息")
        return f"{len(a)} 条"

    r.check("授权读取范围 / 稳定对象 ID / 版本信息", "list_objects", read_scope)
    return r.results


def check_kb(adapter: KBAdapter, space: str) -> list[dict]:
    r = Recorder("kb")

    def read():
        items = adapter.list_objects(space, page_size=200).items
        for o in items[:5]:
            again = adapter.get_object(space, o.id)
            _expect(again and again.id == o.id, f"对象 {o.id} 无法单独读取")
            _expect(o.version is not None or o.updated_at, f"对象 {o.id} 缺少版本")
        return f"{len(items)} 篇"

    r.check("授权读取范围 / 稳定对象 ID / 版本信息", "list_objects + get_object", read)
    return r.results


def check_git_remote(remote: str, branch: str = "main") -> list[dict]:
    """在临时分支上验证非强制推送会被拒绝；受保护分支与身份控制需人工确认。"""
    r = Recorder("git")
    tmp = Path(tempfile.mkdtemp(prefix="aspeed-contract-"))
    scratch = f"aspeed-contract-{random_code(6).lower()}"

    def git(cwd, *args):
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)

    def concurrent():
        a, b = tmp / "a", tmp / "b"
        for p in (a, b):
            res = git(tmp, "clone", "-q", "--single-branch", "--branch", branch, remote, str(p))
            _expect(res.returncode == 0, f"clone 失败: {res.stderr}")
        for p, name in ((a, "a"), (b, "b")):
            (p / f"contract-{name}.txt").write_text(name)
            git(p, "add", "-A")
            git(p, "-c", "user.name=aspeed", "-c", "user.email=aspeed@localhost", "commit", "-qm", f"contract {name}")
        first = git(a, "push", "-q", "origin", f"HEAD:refs/heads/{scratch}")
        _expect(first.returncode == 0, f"推送临时分支失败: {first.stderr}")
        second = git(b, "push", "-q", "origin", f"HEAD:refs/heads/{scratch}")
        _expect(second.returncode != 0, "非快进推送没有被拒绝")
        return "非快进推送被拒绝"

    try:
        r.check("并发更新检测", "非强制推送遇到远端变化时被拒绝", concurrent)
    finally:
        git(tmp / "a", "push", "-q", "origin", "--delete", scratch)
        shutil.rmtree(tmp, ignore_errors=True)
    r.manual("受保护分支", f"{branch} 禁止强制推送", "在代码托管平台确认分支保护规则：禁止 force push 与删除")
    r.manual("身份控制", "提交身份", "确认运行实例使用专用账号推送，并在提交 trailer 中记录 Run-Id 与 On-Behalf-Of")
    r.manual("校验与恢复", "CI 校验", "为 aspeed-context 配置 CI：aspeed validate --context . --base origin/main")
    return r.results
