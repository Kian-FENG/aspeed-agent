"""每类外部操作的执行、先查后做、核验与快照更新。

handler 只描述“怎么做”；状态迁移、租约、幂等和 Git 发布由 executor 统一负责。
"""

from __future__ import annotations

from ..adapters import Adapters
from ..errors import AspeedError
from ..gitstore.view import Workspace
from ..models.testing import TestRun
from ..repo import load_task, save_task
from ..snapshots import SourceObject, make_snapshot, upsert


class VerifyFailed(AspeedError):
    code = "verify_failed"


def _obj_dict(obj: SourceObject) -> dict:
    return {"id": obj.id, "type": obj.type, "version": obj.version, "updated_at": str(obj.updated_at) if obj.updated_at else None,
            "data": obj.data, "link": obj.link, "visibility": obj.visibility}


def _obj_from(d: dict) -> SourceObject:
    return SourceObject(id=d["id"], type=d.get("type"), version=d.get("version"), updated_at=d.get("updated_at"),
                        data=d.get("data") or {}, link=d.get("link"), visibility=d.get("visibility", "team"))


class Handler:
    kind = ""
    adapter = ""
    required_capabilities: set[str] = set()
    notification = False

    def precheck(self, op, adapters: Adapters) -> None:
        pass

    def execute(self, op, adapters: Adapters) -> dict:
        raise NotImplementedError

    def lookup(self, op, adapters: Adapters) -> dict | None:
        raise NotImplementedError

    def verify(self, op, receipt: dict, adapters: Adapters) -> dict:
        raise NotImplementedError

    def check_observation(self, op, receipt: dict, observed: dict) -> dict:
        """Agent 中介模式：由 LLM 查询后回传观察结果，这里用代码判定是否生效。"""
        raise NotImplementedError

    def record(self, ws: Workspace, op, observation: dict) -> None:
        pass

    def ticket(self, op) -> dict:
        raise NotImplementedError


class _AoneHandler(Handler):
    adapter = "aone"

    def _snapshot(self, ws: Workspace, op, observation: dict) -> None:
        if observation.get("object"):
            snap = make_snapshot("aone", op.params["scope"], _obj_from(observation["object"]), ws.now)
            upsert(ws, snap, fresh_read=True)

    def record(self, ws, op, observation):
        self._snapshot(ws, op, observation)


class AoneCreate(_AoneHandler):
    kind = "aone.create"

    def execute(self, op, adapters):
        return adapters.aone.create_item(op.params["scope"], op.params["fields"], op.op_id)

    def lookup(self, op, adapters):
        return adapters.aone.find_by_marker(op.params["scope"], op.op_id)

    def verify(self, op, receipt, adapters):
        obj = adapters.aone.get_object(op.params["scope"], receipt["id"])
        return self.check_observation(op, receipt, _obj_dict(obj) if obj else {})

    def check_observation(self, op, receipt, observed):
        if not observed or observed.get("id") != receipt.get("id"):
            raise VerifyFailed("Aone 中查询不到新建的工作项")
        if observed.get("data", {}).get("title") != op.params["fields"].get("title"):
            raise VerifyFailed("新建工作项标题与意图不一致")
        return {"object": observed}

    def record(self, ws, op, observation):
        self._snapshot(ws, op, observation)
        task_id = op.params.get("task_id")
        if not task_id:
            return
        doc = load_task(ws, task_id)
        if doc and doc.meta.aone is None:
            from ..models.task import AoneLink

            doc.meta.aone = AoneLink(id=observation["object"]["id"], scope=op.params["scope"], linked_at=ws.now, op_id=op.op_id)
            doc.meta.stage = None
            doc.meta.participants = []  # 已随建单写入 Aone，之后以 Aone 为准
            doc.meta.updated_at = ws.now
            save_task(ws, doc)

    def ticket(self, op):
        return {"method": "create_item", "args": {"scope": op.params["scope"], "fields": op.params["fields"], "op_marker": op.op_id},
                "lookup": {"method": "find_by_marker", "args": {"scope": op.params["scope"], "op_marker": op.op_id}}}


class AoneAddParticipant(_AoneHandler):
    kind = "aone.add_participant"

    def execute(self, op, adapters):
        p = op.params
        return adapters.aone.add_participant(p["scope"], p["item_id"], p["account"], p["role"], op.op_id)

    def lookup(self, op, adapters):
        obj = adapters.aone.get_object(op.params["scope"], op.params["item_id"])
        if obj and any(x.get("account") == op.params["account"] for x in obj.data.get("participants", [])):
            return {"id": obj.id, "version": obj.version}
        return None

    def verify(self, op, receipt, adapters):
        obj = adapters.aone.get_object(op.params["scope"], op.params["item_id"])
        return self.check_observation(op, receipt, _obj_dict(obj) if obj else {})

    def check_observation(self, op, receipt, observed):
        names = [x.get("account") for x in observed.get("data", {}).get("participants", [])]
        if op.params["account"] not in names:
            raise VerifyFailed("Aone 参与者列表中没有该成员")
        return {"object": observed}

    def ticket(self, op):
        p = op.params
        return {"method": "add_participant", "args": {"scope": p["scope"], "item_id": p["item_id"], "account": p["account"],
                                                       "role": p["role"], "op_marker": op.op_id}}


class AoneTransition(_AoneHandler):
    kind = "aone.transition"
    required_capabilities = {"conditional_update"}

    def execute(self, op, adapters):
        p = op.params
        return adapters.aone.transition(p["scope"], p["item_id"], p["to_status"], op.preconditions.get("expected_version"), op.op_id)

    def lookup(self, op, adapters):
        return adapters.aone.find_by_marker(op.params["scope"], op.op_id)

    def verify(self, op, receipt, adapters):
        obj = adapters.aone.get_object(op.params["scope"], op.params["item_id"])
        return self.check_observation(op, receipt, _obj_dict(obj) if obj else {})

    def check_observation(self, op, receipt, observed):
        if observed.get("data", {}).get("status") != op.params["to_status"]:
            raise VerifyFailed(f"Aone 状态不是 {op.params['to_status']}")
        return {"object": observed}

    def ticket(self, op):
        p = op.params
        return {"method": "transition", "args": {"scope": p["scope"], "item_id": p["item_id"], "to_status": p["to_status"],
                                                  "expected_version": op.preconditions.get("expected_version"), "op_marker": op.op_id}}


class IMRemind(Handler):
    kind = "im.remind"
    adapter = "im"
    notification = True

    def execute(self, op, adapters):
        return adapters.im.send(op.params["im"], op.params["text"], op.op_id)

    def lookup(self, op, adapters):
        return adapters.im.find_sent(op.op_id)

    def verify(self, op, receipt, adapters):
        return self.check_observation(op, receipt, adapters.im.get_message(receipt["message_id"]) or {})

    def check_observation(self, op, receipt, observed):
        if not observed:
            raise VerifyFailed("查询不到已发送的消息")
        return {"message_id": receipt["message_id"]}

    def ticket(self, op):
        return {"method": "send", "args": {"target": op.params["im"], "text": op.params["text"], "op_marker": op.op_id},
                "lookup": {"method": "find_sent", "args": {"op_marker": op.op_id}}}


class NMSubmit(Handler):
    kind = "nm.submit"
    adapter = "nodemanager"

    def precheck(self, op, adapters):
        from ..adapters import TransientError

        if adapters.nodemanager.quota().get("available", 1) <= 0:
            raise TransientError("NodeManager 资源配额不足，稍后重试")

    def execute(self, op, adapters):
        return adapters.nodemanager.submit(op.params["plan"], op.op_id)

    def lookup(self, op, adapters):
        return adapters.nodemanager.find_by_dedupe(op.op_id)

    def verify(self, op, receipt, adapters):
        return self.check_observation(op, receipt, adapters.nodemanager.status(receipt["job_id"]))

    def check_observation(self, op, receipt, observed):
        if not observed or observed.get("job_id") != receipt.get("job_id"):
            raise VerifyFailed("NodeManager 中查询不到作业")
        return {"job_id": receipt["job_id"], "state": observed.get("state")}

    def record(self, ws, op, observation):
        plan_id = op.params["plan_id"]
        rel = f"tests/runs/{plan_id}.yaml"
        if ws.exists(rel):
            return
        run = TestRun(plan_id=plan_id, op_id=op.op_id, job_id=observation["job_id"], state="submitted",
                      submitted_at=ws.now, build=op.params["plan"].get("build", {}).get("id"),
                      defects=op.params["plan"].get("defects", []))
        ws.write_yaml(rel, run.dump())

    def ticket(self, op):
        return {"method": "submit", "args": {"plan": op.params["plan"], "dedupe_key": op.op_id},
                "lookup": {"method": "find_by_dedupe", "args": {"dedupe_key": op.op_id}}}


HANDLERS: dict[str, Handler] = {h.kind: h for h in (AoneCreate(), AoneAddParticipant(), AoneTransition(), IMRemind(), NMSubmit())}
