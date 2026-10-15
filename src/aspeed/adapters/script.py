"""脚本直调桥接：把已有 SKILL 包装成“JSON 进、JSON 出”的命令，供确定性代码调用。

协议（每次调用启动一次命令）：
  stdin : {"method": "<方法名>", "args": {...}}
  stdout: {"result": ...}  或  {"error": {"type": "transient|permanent|unknown_outcome|forbidden|stale_version", "message": "..."}}

方法名与 base.py 中的抽象方法一致；对象类返回值使用
{"id","type","version","updated_at","data","link","visibility"} 结构。
接入真实 Aone / NodeManager / IM 时，只需为对应 SKILL 写一个满足此协议的薄脚本。
"""

from __future__ import annotations

import json
import shlex
import subprocess

from ..snapshots import SourceObject
from .base import (
    AdapterError,
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

ERRORS = {
    "transient": TransientError,
    "permanent": PermanentError,
    "unknown_outcome": UnknownOutcome,
    "forbidden": ForbiddenError,
    "stale_version": StaleVersion,
}


class ScriptBridge:
    def __init__(self, command: str | list[str], timeout: float = 60.0, capabilities: list[str] | None = None):
        self.command = shlex.split(command) if isinstance(command, str) else list(command)
        self.timeout = timeout
        if capabilities is not None:
            self.capabilities = set(capabilities)

    def call(self, method: str, **args):
        payload = json.dumps({"method": method, "args": args}, ensure_ascii=False)
        try:
            proc = subprocess.run(self.command, input=payload, capture_output=True, text=True, timeout=self.timeout)
        except subprocess.TimeoutExpired as exc:
            # 写类方法超时视为结果未知；读类方法由调用方重试
            raise UnknownOutcome(f"{method} 超时") from exc
        if proc.returncode != 0 and not proc.stdout.strip():
            raise AdapterError(f"{method} 调用失败: {proc.stderr.strip()[:500]}")
        try:
            resp = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise UnknownOutcome(f"{method} 返回无法解析") from exc
        if "error" in resp:
            err = resp["error"] or {}
            raise ERRORS.get(err.get("type"), AdapterError)(err.get("message", "未知错误"))
        return resp.get("result")

    @staticmethod
    def _obj(d: dict | None) -> SourceObject | None:
        if not d:
            return None
        return SourceObject(id=d["id"], type=d.get("type"), version=d.get("version"), updated_at=d.get("updated_at"),
                            data=d.get("data") or {}, link=d.get("link"), visibility=d.get("visibility", "team"))

    def _page(self, result: dict) -> Page:
        return Page([self._obj(i) for i in result.get("items", [])], result.get("next_token"))

    def list_objects(self, scope, updated_since=None, page_token=None, page_size=50):
        return self._page(self.call("list_objects", scope=scope, updated_since=updated_since,
                                    page_token=page_token, page_size=page_size))

    def get_object(self, scope, object_id):
        return self._obj(self.call("get_object", scope=scope, object_id=object_id))


class ScriptAone(ScriptBridge, AoneAdapter):
    def create_item(self, scope, fields, op_marker):
        return self.call("create_item", scope=scope, fields=fields, op_marker=op_marker)

    def add_participant(self, scope, item_id, account, role, op_marker):
        return self.call("add_participant", scope=scope, item_id=item_id, account=account, role=role, op_marker=op_marker)

    def transition(self, scope, item_id, to_status, expected_version, op_marker):
        return self.call("transition", scope=scope, item_id=item_id, to_status=to_status,
                         expected_version=expected_version, op_marker=op_marker)

    def find_by_marker(self, scope, op_marker):
        return self.call("find_by_marker", scope=scope, op_marker=op_marker)


class ScriptNodeManager(ScriptBridge, NodeManagerAdapter):
    def submit(self, plan, dedupe_key):
        return self.call("submit", plan=plan, dedupe_key=dedupe_key)

    def find_by_dedupe(self, dedupe_key):
        return self.call("find_by_dedupe", dedupe_key=dedupe_key)

    def status(self, job_id):
        return self.call("status", job_id=job_id)

    def results(self, job_id):
        return self.call("results", job_id=job_id)

    def cancel(self, job_id):
        return self.call("cancel", job_id=job_id)

    def quota(self):
        return self.call("quota")


class ScriptIM(ScriptBridge, IMAdapter):
    def send(self, target, text, op_marker):
        return self.call("send", target=target, text=text, op_marker=op_marker)

    def find_sent(self, op_marker):
        return self.call("find_sent", op_marker=op_marker)

    def get_message(self, message_id):
        return self.call("get_message", message_id=message_id)

    def resolve_user(self, im_user):
        return self.call("resolve_user", im_user=im_user)


class ScriptKB(ScriptBridge, KBAdapter):
    pass
