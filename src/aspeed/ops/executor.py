"""操作执行器：意图 → 校验 → 租约 → 执行（先查后做）→ 核验 → 更新 Git 快照。

每一步都是一次 Git 发布（CAS）。任何一步中断，recover 都能从记录的状态继续，
且不会重复执行外部写操作：
- 同一业务意图对应同一 op_id（幂等键）；
- 只有租约持有者能执行；租约过期后接手者必须先按操作标记查询；
- 外部写成功但 Git 更新失败时，记录停在 executed/verified，由 recover 补齐。
"""

from __future__ import annotations

from datetime import timedelta

from ..adapters import (
    Adapters,
    ForbiddenError,
    PermanentError,
    StaleVersion,
    TransientError,
    UnknownOutcome,
)
from ..errors import Abort, NotFound, PermissionDenied
from ..gitstore.view import TreeView, Workspace
from ..ids import digest
from ..models.common import Lease
from ..models.op import TRANSITIONS, Approval, Attempt, OpRecord, Transition
from ..repo import iter_ops, load_op, load_policy, load_roster, save_op
from ..store import ContextStore
from ..timeutil import local_date, local_time, parse_dt
from .handlers import HANDLERS, VerifyFailed

INITIAL_STATE = {"dry_run": "simulated", "confirm": "proposed", "auto": "approved"}


def make_op_id(kind: str, business_key: dict) -> str:
    return "op-" + digest({"kind": kind, "key": business_key}, 20)


def _move(op: OpRecord, ws: TreeView, to: str, by: str, note: str | None = None) -> None:
    op.state = to
    op.updated_at = ws.now
    op.history.append(Transition(at=ws.now, to=to, by=by, note=note))


def propose_in(ws: Workspace, kind: str, business_key: dict, params: dict, requested_by: str, by: str,
               on_behalf_of: str | None = None, preconditions: dict | None = None, note: str | None = None
               ) -> tuple[OpRecord, bool]:
    """在一个 mutation 内登记操作意图；已存在则原样返回（幂等）。"""
    if kind not in HANDLERS:
        raise ValueError(f"未知操作类型 {kind}")
    op_id = make_op_id(kind, business_key)
    existing = load_op(ws, op_id)
    if existing:
        return existing, False
    mode = load_policy(ws).op(kind).mode
    op = OpRecord(op_id=op_id, kind=kind, business_key=business_key, params=params, requested_by=requested_by,
                  on_behalf_of=on_behalf_of, preconditions=preconditions or {}, mode=mode, state=INITIAL_STATE[mode],
                  created_at=ws.now, updated_at=ws.now, note=note)
    op.history.append(Transition(at=ws.now, to=op.state, by=by, note=f"mode={mode}"))
    save_op(ws, op)
    return op, True


class OpService:
    def __init__(self, store: ContextStore, adapters: Adapters | None = None):
        self.store = store
        self.adapters = adapters
        self.run_id = store.ctx.run_id

    # ---- 登记与批准 ----
    def propose(self, kind: str, business_key: dict, params: dict, requested_by: str, on_behalf_of: str | None = None,
                preconditions: dict | None = None, note: str | None = None) -> dict:
        def mutation(ws):
            op, created = propose_in(ws, kind, business_key, params, requested_by, self.run_id, on_behalf_of,
                                     preconditions, note)
            return {"op_id": op.op_id, "state": op.state, "mode": op.mode, "created": created}

        op_id = make_op_id(kind, business_key)
        res = self.store.publish(mutation, f"op: 登记 {kind} {op_id}", op_id=op_id, on_behalf_of=on_behalf_of)
        return {**res.value, "commit": res.commit}

    def approve(self, op_id: str, by: str, evidence: str | None = None) -> dict:
        def mutation(ws):
            op = self._load(ws, op_id)
            if op.state not in ("proposed", "simulated", "needs_human", "failed"):
                raise Abort(f"{op_id} 当前状态 {op.state}，无需批准")
            roles = load_policy(ws).op(op.kind).approver_roles
            member = load_roster(ws).get(by)
            if member is None:
                raise PermissionDenied(f"{by} 不是登记成员")
            if roles and not set(roles) & set(member.roles):
                raise PermissionDenied(f"{by} 没有批准 {op.kind} 的角色（需要 {roles}）")
            op.approval = Approval(by=by, at=ws.now, evidence=evidence)
            _move(op, ws, "approved", by, evidence)
            save_op(ws, op)
            return {"op_id": op_id, "state": op.state}

        res = self.store.publish(mutation, f"op: 批准 {op_id}", op_id=op_id, on_behalf_of=by)
        return res.value or {"op_id": op_id, "status": res.status, "reason": res.reason}

    def supersede(self, op_id: str, by: str, reason: str) -> dict:
        def mutation(ws):
            op = self._load(ws, op_id)
            if "superseded" not in TRANSITIONS[op.state]:
                raise Abort(f"{op_id} 当前状态 {op.state}，不能撤销")
            op.lease = None
            _move(op, ws, "superseded", by, reason)
            save_op(ws, op)
            return {"op_id": op_id, "state": op.state}

        res = self.store.publish(mutation, f"op: 撤销 {op_id}", op_id=op_id, on_behalf_of=by)
        return res.value or {"op_id": op_id, "status": res.status, "reason": res.reason}

    # ---- 执行 ----
    def run(self, op_id: str) -> dict:
        lease = self._acquire(op_id)
        if lease.get("status") != "leased":
            if lease.get("state") in ("executed", "verified"):
                return self._finish(op_id)
            return lease
        op = load_op(self.store.view(refresh=False), op_id)
        handler = HANDLERS[op.kind]
        receipt = None
        if lease["prior_attempts"]:
            receipt = handler.lookup(op, self.adapters)  # 先查后做
            if receipt:
                return self._executed(op_id, receipt, "found_existing")
        try:
            handler.precheck(op, self.adapters)
            receipt = handler.execute(op, self.adapters)
        except UnknownOutcome as exc:
            # 保持 leased：租约过期后 recover 会先查询再决定
            self._attempt(op_id, "unknown", str(exc))
            return {"op_id": op_id, "state": "leased", "outcome": "unknown", "detail": str(exc)}
        except TransientError as exc:
            self._release(op_id, "deferred", str(exc))
            return {"op_id": op_id, "state": "approved", "outcome": "deferred", "detail": str(exc)}
        except StaleVersion as exc:
            self._fail(op_id, "needs_human", "precondition_failed", f"对象已变化，需重新判断：{exc}")
            return {"op_id": op_id, "state": "needs_human", "detail": str(exc)}
        except (PermanentError, ForbiddenError) as exc:
            self._fail(op_id, "failed", "failed", str(exc))
            return {"op_id": op_id, "state": "failed", "detail": str(exc)}
        return self._executed(op_id, receipt, "executed")

    def recover(self, include_approved: bool = False, limit: int = 50) -> list[dict]:
        view = self.store.view()
        results = []
        for op in iter_ops(view):
            if len(results) >= limit:
                break
            expired = op.state == "leased" and op.lease and parse_dt(op.lease.until) <= view.now
            if expired or op.state in ("executed", "verified") or (include_approved and op.state == "approved"):
                results.append(self.run(op.op_id))
        return results

    # ---- Agent 中介模式 ----
    def ticket(self, op_id: str) -> dict:
        lease = self._acquire(op_id)
        if lease.get("status") != "leased":
            return lease
        op = load_op(self.store.view(refresh=False), op_id)
        t = HANDLERS[op.kind].ticket(op)
        return {
            "op_id": op_id, "kind": op.kind, "adapter": HANDLERS[op.kind].adapter, "lease_until": lease["lease_until"],
            "lookup_first": lease["prior_attempts"], **t,
            "instructions": (
                "调用对应 SKILL，参数必须与 args 完全一致，不得改写。"
                + ("此前已有执行尝试：先用 lookup 查询，已生效则直接回传查询结果。" if lease["prior_attempts"] else "")
                + f"完成后运行 aspeed op record-result {op_id} --result <结果JSON文件>；"
                "随后独立查询对象当前状态，运行 aspeed op record-observation 回传。"
            ),
        }

    def record_result(self, op_id: str, result: dict, outcome: str = "executed") -> dict:
        if outcome == "executed":
            return self._executed(op_id, result, "executed", require_holder=False, finish=False)
        if outcome == "unknown":
            self._attempt(op_id, "unknown", str(result))
            return {"op_id": op_id, "state": "leased", "outcome": "unknown"}
        self._fail(op_id, "failed", "failed", str(result))
        return {"op_id": op_id, "state": "failed"}

    def record_observation(self, op_id: str, observed: dict) -> dict:
        op = self._load(self.store.view(), op_id)
        if op.state != "executed":
            raise Abort(f"{op_id} 当前状态 {op.state}，不是 executed")
        handler = HANDLERS[op.kind]
        try:
            observation = handler.check_observation(op, op.receipt, observed)
        except VerifyFailed as exc:
            self._fail(op_id, "needs_human", "failed", f"核验失败：{exc}", from_states=("executed",))
            return {"op_id": op_id, "state": "needs_human", "detail": str(exc)}
        self._verified(op_id, observation)
        return self._record(op_id)

    # ---- 内部步骤 ----
    def _load(self, view: TreeView, op_id: str) -> OpRecord:
        op = load_op(view, op_id)
        if op is None:
            raise NotFound(f"操作 {op_id} 不存在")
        return op

    def _acquire(self, op_id: str) -> dict:
        def mutation(ws):
            op = self._load(ws, op_id)
            policy = load_policy(ws)
            expired = op.state == "leased" and op.lease and parse_dt(op.lease.until) <= ws.now
            if op.state in ("executed", "verified"):
                raise Abort("继续核验", state=op.state)
            if not (op.state == "approved" or expired):
                raise Abort(f"{op_id} 当前状态 {op.state}，不可执行", state=op.state)
            handler = HANDLERS[op.kind]
            adapter = getattr(self.adapters, handler.adapter, None) if self.adapters else None
            missing = handler.required_capabilities - set(getattr(adapter, "capabilities", set()))
            if missing:
                op.lease = None
                _move(op, ws, "needs_human", self.run_id, f"接入缺少能力 {sorted(missing)}，降级为人工执行")
                save_op(ws, op)
                return {"status": "needs_human", "op_id": op_id, "missing_capabilities": sorted(missing)}
            if handler.notification and _in_quiet_hours(ws, policy):
                raise Abort("免打扰时段，稍后执行", state=op.state, deferred=True)
            if not expired and _writes_today(ws, policy.timezone) >= policy.budget.max_external_writes_per_day:
                raise Abort("今日外部写入已达上限", state=op.state, deferred=True)
            prior = bool(op.attempts)
            if expired:
                _move(op, ws, "leased", self.run_id, f"接管过期租约（原持有者 {op.lease.holder}）")
            else:
                _move(op, ws, "leased", self.run_id)
            op.lease = Lease(holder=self.run_id, acquired_at=ws.now, until=ws.now + timedelta(seconds=policy.leases.op_seconds))
            op.attempts.append(Attempt(run_id=self.run_id, at=ws.now, outcome="started", detail="takeover" if expired else None))
            save_op(ws, op)
            return {"status": "leased", "op_id": op_id, "prior_attempts": prior, "lease_until": str(op.lease.until)}

        res = self.store.publish(mutation, f"op: 获取执行权 {op_id}", op_id=op_id)
        if res.status == "aborted":
            return {"status": "skipped", "op_id": op_id, "reason": res.reason, **(res.value or {})}
        return res.value

    def _attempt(self, op_id: str, outcome: str, detail: str) -> None:
        def mutation(ws):
            op = self._load(ws, op_id)
            op.attempts.append(Attempt(run_id=self.run_id, at=ws.now, outcome=outcome, detail=detail))
            op.updated_at = ws.now
            save_op(ws, op)

        self.store.publish(mutation, f"op: 记录尝试 {op_id} {outcome}", op_id=op_id)

    def _release(self, op_id: str, outcome: str, detail: str) -> None:
        def mutation(ws):
            op = self._load(ws, op_id)
            if op.state != "leased" or not op.lease or op.lease.holder != self.run_id:
                raise Abort("租约已不属于本实例")
            op.lease = None
            op.attempts.append(Attempt(run_id=self.run_id, at=ws.now, outcome=outcome, detail=detail))
            _move(op, ws, "approved", self.run_id, detail)
            save_op(ws, op)

        self.store.publish(mutation, f"op: 释放执行权 {op_id}", op_id=op_id)

    def _fail(self, op_id: str, to: str, outcome: str, detail: str, from_states=("leased",)) -> None:
        def mutation(ws):
            op = self._load(ws, op_id)
            if op.state not in from_states:
                raise Abort(f"状态已变为 {op.state}")
            op.lease = None
            op.attempts.append(Attempt(run_id=self.run_id, at=ws.now, outcome=outcome, detail=detail))
            _move(op, ws, to, self.run_id, detail)
            save_op(ws, op)

        self.store.publish(mutation, f"op: {to} {op_id}", op_id=op_id)

    def _executed(self, op_id: str, receipt: dict, outcome: str, require_holder: bool = True, finish: bool = True) -> dict:
        def mutation(ws):
            op = self._load(ws, op_id)
            if op.state != "leased":
                raise Abort(f"状态已变为 {op.state}")
            if require_holder and op.lease and op.lease.holder != self.run_id:
                raise Abort("租约已被他人接管；接管者会先查询外部结果")
            op.receipt = receipt
            op.lease = None
            op.attempts.append(Attempt(run_id=self.run_id, at=ws.now, outcome=outcome))
            _move(op, ws, "executed", self.run_id)
            save_op(ws, op)

        res = self.store.publish(mutation, f"op: 已执行 {op_id}", op_id=op_id)
        if res.status == "aborted":
            return {"op_id": op_id, "status": "skipped", "reason": res.reason}
        return self._finish(op_id) if finish else {"op_id": op_id, "state": "executed", "receipt": receipt}

    def _finish(self, op_id: str) -> dict:
        op = self._load(self.store.view(), op_id)
        if op.state == "executed":
            handler = HANDLERS[op.kind]
            try:
                observation = handler.verify(op, op.receipt, self.adapters)
            except VerifyFailed as exc:
                self._fail(op_id, "needs_human", "failed", f"核验失败：{exc}", from_states=("executed",))
                return {"op_id": op_id, "state": "needs_human", "detail": str(exc)}
            except (TransientError, UnknownOutcome) as exc:
                return {"op_id": op_id, "state": "executed", "detail": f"核验暂不可用：{exc}"}
            self._verified(op_id, observation)
        return self._record(op_id)

    def _verified(self, op_id: str, observation: dict) -> None:
        def mutation(ws):
            op = self._load(ws, op_id)
            if op.state != "executed":
                raise Abort(f"状态已变为 {op.state}")
            op.observation = observation
            _move(op, ws, "verified", self.run_id)
            save_op(ws, op)

        self.store.publish(mutation, f"op: 已核验 {op_id}", op_id=op_id)

    def _record(self, op_id: str) -> dict:
        def mutation(ws):
            op = self._load(ws, op_id)
            if op.state != "verified":
                raise Abort(f"状态为 {op.state}", state=op.state)
            HANDLERS[op.kind].record(ws, op, op.observation or {})
            _move(op, ws, "recorded", self.run_id)
            save_op(ws, op)
            return {"op_id": op_id, "state": "recorded", "receipt": op.receipt}

        res = self.store.publish(mutation, f"op: 快照已更新 {op_id}", op_id=op_id)
        if res.status == "aborted":
            return {"op_id": op_id, **(res.value or {}), "reason": res.reason}
        return {**res.value, "commit": res.commit}


def _in_quiet_hours(view: TreeView, policy) -> bool:
    qh = policy.budget.quiet_hours
    if not qh:
        return False
    t = local_time(view.now, policy.timezone).strftime("%H:%M")
    return (qh.start <= t or t < qh.end) if qh.start > qh.end else (qh.start <= t < qh.end)


def _writes_today(view: TreeView, tz: str) -> int:
    today = local_date(view.now, tz)
    return sum(
        1
        for op in iter_ops(view)
        for a in op.attempts
        if a.outcome == "started" and local_date(a.at, tz) == today
    )
