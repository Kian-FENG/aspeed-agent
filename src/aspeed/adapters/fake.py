"""文件持久化的 Fake 实现：跨进程共享状态（fcntl 加锁），支持故障注入。

用于 M0 的端到端测试、契约测试基准和本地演练。状态目录由 ASPEED_FAKE_DIR 指定。
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
from pathlib import Path

from ..ids import random_code
from ..snapshots import SourceObject
from ..timeutil import iso, now
from .base import (
    AoneAdapter,
    ForbiddenError,
    IMAdapter,
    KBAdapter,
    NodeManagerAdapter,
    Page,
    PermanentError,
    StaleVersion,
    TransientError,
    UnknownOutcome,
)

FAULTS = {
    "transient": TransientError,
    "permanent": PermanentError,
    "forbidden": ForbiddenError,
}


class FakeState:
    def __init__(self, name: str, root: str | Path | None = None):
        root = Path(root or os.environ.get("ASPEED_FAKE_DIR") or ".aspeed-fake")
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / f"{name}.json"
        self.lock = root / f"{name}.lock"

    @contextlib.contextmanager
    def edit(self):
        with open(self.lock, "a") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            data = json.loads(self.path.read_text()) if self.path.exists() else {}
            try:
                yield data
            finally:
                # 异常时也落盘：调用计数和已消费的故障注入需要持久化
                tmp = self.path.with_suffix(".tmp")
                tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True))
                tmp.replace(self.path)

    def read(self) -> dict:
        with self.edit() as data:
            return json.loads(json.dumps(data))


class _FaultMixin:
    state: FakeState

    def inject(self, method: str, fault: str, times: int = 1) -> None:
        """fault: transient | permanent | forbidden | unknown_after_write"""
        with self.state.edit() as s:
            s.setdefault("faults", {}).setdefault(method, []).extend([fault] * times)

    def calls(self, method: str) -> int:
        return self.state.read().get("calls", {}).get(method, 0)

    def _enter(self, s: dict, method: str) -> str | None:
        s.setdefault("calls", {})[method] = s.get("calls", {}).get(method, 0) + 1
        queue = s.get("faults", {}).get(method) or []
        fault = queue.pop(0) if queue else None
        if fault in FAULTS:
            raise FAULTS[fault](f"注入故障 {fault} @ {method}")
        return fault

    @staticmethod
    def _after(fault: str | None, method: str) -> None:
        if fault == "unknown_after_write":
            raise UnknownOutcome(f"注入故障：{method} 已写入但响应超时")


def _page(items: list[dict], updated_since, page_token, page_size, to_obj) -> Page:
    rows = sorted(items, key=lambda d: (d["updated_at"], str(d["id"])))
    if updated_since:
        rows = [r for r in rows if r["updated_at"] >= updated_since]
    if page_token:
        after = tuple(json.loads(page_token))
        rows = [r for r in rows if (r["updated_at"], str(r["id"])) > after]
    chunk = rows[:page_size]
    token = json.dumps([chunk[-1]["updated_at"], str(chunk[-1]["id"])]) if len(rows) > page_size else None
    return Page([to_obj(r) for r in chunk], token)


class FakeAone(_FaultMixin, AoneAdapter):
    capabilities = {"incremental", "conditional_update", "op_marker", "participants", "query_result"}

    def __init__(self, root=None, statuses: list[str] | None = None):
        self.state = FakeState("aone", root)
        self.statuses = statuses or ["open", "in_progress", "fixed", "verified", "closed", "reopened", "cancelled"]

    # ---- 测试辅助 ----
    def seed(self, scope: str, **fields) -> dict:
        with self.state.edit() as s:
            item = self._new(s, scope, fields)
        return item

    def update_raw(self, scope: str, item_id: str, **fields) -> dict:
        with self.state.edit() as s:
            item = s["items"][scope][item_id]
            item.update(fields)
            self._bump(item)
            return dict(item)

    def delete_raw(self, scope: str, item_id: str) -> None:
        with self.state.edit() as s:
            s["items"][scope].pop(item_id)

    # ---- 内部 ----
    def _new(self, s, scope, fields) -> dict:
        s["seq"] = s.get("seq", 0) + 1
        item_id = fields.pop("id", None) or f"{scope.upper()}-{s['seq']}"
        item = {
            "id": item_id, "type": "task", "title": "", "status": "open", "owner": None, "due": None,
            "severity": None, "participants": [], "description": "", "project": scope,
            **fields, "version": 1, "updated_at": iso(now()),
        }
        s.setdefault("items", {}).setdefault(scope, {})[item_id] = item
        return dict(item)

    @staticmethod
    def _bump(item):
        item["version"] += 1
        item["updated_at"] = iso(now())

    @staticmethod
    def _obj(item: dict) -> SourceObject:
        data = {k: v for k, v in item.items() if k not in ("version", "updated_at")}
        return SourceObject(id=item["id"], type=item.get("type"), version=item["version"],
                            updated_at=item["updated_at"], data=data, link=f"https://aone.example/{item['id']}")

    def _mark(self, s, marker, scope, item_id, action):
        s.setdefault("markers", {})[marker] = {"scope": scope, "id": item_id, "action": action}

    # ---- 契约 ----
    def list_objects(self, scope, updated_since=None, page_token=None, page_size=50) -> Page:
        with self.state.edit() as s:
            self._enter(s, "list")
            items = list(s.get("items", {}).get(scope, {}).values())
        return _page(items, updated_since, page_token, page_size, self._obj)

    def get_object(self, scope, object_id):
        with self.state.edit() as s:
            self._enter(s, "get")
            item = s.get("items", {}).get(scope, {}).get(object_id)
        return self._obj(item) if item else None

    def create_item(self, scope, fields, op_marker):
        with self.state.edit() as s:
            fault = self._enter(s, "create")
            item = self._new(s, scope, dict(fields))
            self._mark(s, op_marker, scope, item["id"], "create")
        self._after(fault, "create")
        return {"id": item["id"], "version": item["version"]}

    def add_participant(self, scope, item_id, account, role, op_marker):
        with self.state.edit() as s:
            fault = self._enter(s, "add_participant")
            item = s["items"][scope][item_id]
            if not any(p["account"] == account for p in item["participants"]):
                item["participants"].append({"account": account, "role": role})
                if role == "lead" and not item.get("owner"):
                    item["owner"] = account
                self._bump(item)
            self._mark(s, op_marker, scope, item_id, "add_participant")
            result = {"id": item_id, "version": item["version"]}
        self._after(fault, "add_participant")
        return result

    def transition(self, scope, item_id, to_status, expected_version, op_marker):
        if to_status not in self.statuses:
            raise PermanentError(f"未知状态 {to_status}")
        with self.state.edit() as s:
            fault = self._enter(s, "transition")
            item = s["items"][scope][item_id]
            if expected_version is not None and item["version"] != expected_version:
                raise StaleVersion(f"期望版本 {expected_version}，实际 {item['version']}")
            item["status"] = to_status
            self._bump(item)
            self._mark(s, op_marker, scope, item_id, "transition")
            result = {"id": item_id, "version": item["version"]}
        self._after(fault, "transition")
        return result

    def find_by_marker(self, scope, op_marker):
        with self.state.edit() as s:
            self._enter(s, "find_by_marker")
            mark = s.get("markers", {}).get(op_marker)
            if not mark or mark["scope"] != scope:
                return None
            item = s["items"][scope].get(mark["id"])
            return {"id": mark["id"], "version": item["version"] if item else None, "action": mark["action"]}


class FakeNodeManager(_FaultMixin, NodeManagerAdapter):
    capabilities = {"dedupe", "cancel", "quota", "results"}

    def __init__(self, root=None, quota: int = 10):
        self.state = FakeState("nodemanager", root)
        self.default_quota = quota

    def finish(self, job_id: str, state: str = "succeeded", cases=None, metrics=None, artifacts=None, log: str = "") -> None:
        """测试辅助：设定作业完成结果。"""
        with self.state.edit() as s:
            job = s["jobs"][job_id]
            job.update(state=state, updated_at=iso(now()), cases=cases or [], metrics=metrics or {},
                       artifacts=artifacts or [], log=log)

    def submit(self, plan, dedupe_key):
        with self.state.edit() as s:
            fault = self._enter(s, "submit")
            if s.get("quota_used", 0) >= self.default_quota:
                raise TransientError("资源配额不足")
            jobs = s.setdefault("jobs", {})
            existing = next((j for j in jobs.values() if j["dedupe_key"] == dedupe_key), None)
            if existing:
                return {"job_id": existing["job_id"], "duplicate": True}
            job_id = f"job-{random_code(8).lower()}"
            jobs[job_id] = {"job_id": job_id, "dedupe_key": dedupe_key, "plan": plan, "state": "queued",
                            "updated_at": iso(now())}
            s["quota_used"] = s.get("quota_used", 0) + 1
        self._after(fault, "submit")
        return {"job_id": job_id, "duplicate": False}

    def find_by_dedupe(self, dedupe_key):
        with self.state.edit() as s:
            self._enter(s, "find_by_dedupe")
            job = next((j for j in s.get("jobs", {}).values() if j["dedupe_key"] == dedupe_key), None)
        return {"job_id": job["job_id"], "state": job["state"]} if job else None

    def status(self, job_id):
        with self.state.edit() as s:
            self._enter(s, "status")
            job = s.get("jobs", {}).get(job_id)
        if not job:
            raise PermanentError(f"作业不存在 {job_id}")
        return {"job_id": job_id, "state": job["state"], "updated_at": job["updated_at"]}

    def results(self, job_id):
        with self.state.edit() as s:
            self._enter(s, "results")
            job = s.get("jobs", {}).get(job_id)
        if not job:
            raise PermanentError(f"作业不存在 {job_id}")
        return {"job_id": job_id, "state": job["state"], "cases": job.get("cases", []),
                "metrics": job.get("metrics", {}), "artifacts": job.get("artifacts", []), "log": job.get("log", ""),
                "link": f"https://nodemanager.example/jobs/{job_id}"}

    def cancel(self, job_id):
        with self.state.edit() as s:
            self._enter(s, "cancel")
            job = s["jobs"][job_id]
            if job["state"] in ("queued", "running"):
                job["state"] = "cancelled"
                job["updated_at"] = iso(now())
            return {"job_id": job_id, "state": job["state"]}

    def quota(self):
        with self.state.edit() as s:
            return {"limit": self.default_quota, "used": s.get("quota_used", 0),
                    "available": self.default_quota - s.get("quota_used", 0)}


class FakeIM(_FaultMixin, IMAdapter):
    capabilities = {"send", "query_result", "identity", "incremental"}

    def __init__(self, root=None):
        self.state = FakeState("im", root)

    def post(self, chat: str, user: str, text: str) -> dict:
        """测试辅助：模拟成员在群里发消息。"""
        with self.state.edit() as s:
            s["seq"] = s.get("seq", 0) + 1
            msg = {"id": f"msg-{s['seq']}", "chat": chat, "user": user, "text": text, "version": 1,
                   "updated_at": iso(now())}
            s.setdefault("chats", {}).setdefault(chat, {})[msg["id"]] = msg
            return msg

    def sent(self) -> list[dict]:
        return list(self.state.read().get("sent", {}).values())

    def send(self, target, text, op_marker):
        with self.state.edit() as s:
            fault = self._enter(s, "send")
            s["seq"] = s.get("seq", 0) + 1
            mid = f"out-{s['seq']}"
            s.setdefault("sent", {})[mid] = {"id": mid, "target": target, "text": text, "marker": op_marker,
                                              "at": iso(now())}
        self._after(fault, "send")
        return {"message_id": mid}

    def find_sent(self, op_marker):
        msg = next((m for m in self.sent() if m["marker"] == op_marker), None)
        return {"message_id": msg["id"]} if msg else None

    def get_message(self, message_id):
        return self.state.read().get("sent", {}).get(message_id)

    def resolve_user(self, im_user):
        return {"im": im_user} if im_user else None

    def list_objects(self, scope, updated_since=None, page_token=None, page_size=50):
        msgs = list(self.state.read().get("chats", {}).get(scope, {}).values())
        return _page(msgs, updated_since, page_token, page_size, lambda m: SourceObject(
            id=m["id"], type="message", version=m["version"], updated_at=m["updated_at"],
            data={k: v for k, v in m.items() if k not in ("version", "updated_at")},
            link=f"https://im.example/{m['chat']}/{m['id']}"))

    def get_object(self, scope, object_id):
        m = self.state.read().get("chats", {}).get(scope, {}).get(object_id)
        if not m:
            return None
        return SourceObject(id=m["id"], type="message", version=m["version"], updated_at=m["updated_at"],
                            data={k: v for k, v in m.items() if k not in ("version", "updated_at")})


class FakeKB(_FaultMixin, KBAdapter):
    capabilities = {"incremental"}

    def __init__(self, root=None):
        self.state = FakeState("kb", root)

    def put(self, space: str, doc_id: str, title: str, body: str, visibility: str = "team") -> dict:
        with self.state.edit() as s:
            docs = s.setdefault("spaces", {}).setdefault(space, {})
            old = docs.get(doc_id)
            doc = {"id": doc_id, "title": title, "body": body, "visibility": visibility,
                   "version": (old["version"] + 1) if old else 1, "updated_at": iso(now())}
            docs[doc_id] = doc
            return doc

    def _obj(self, d):
        return SourceObject(id=d["id"], type="doc", version=d["version"], updated_at=d["updated_at"],
                            data={"title": d["title"], "body": d["body"]}, visibility=d["visibility"],
                            link=f"https://kb.example/{d['id']}")

    def list_objects(self, scope, updated_since=None, page_token=None, page_size=50):
        docs = list(self.state.read().get("spaces", {}).get(scope, {}).values())
        return _page(docs, updated_since, page_token, page_size, self._obj)

    def get_object(self, scope, object_id):
        d = self.state.read().get("spaces", {}).get(scope, {}).get(object_id)
        return self._obj(d) if d else None
