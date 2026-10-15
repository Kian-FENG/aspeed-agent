"""可恢复的主动拉取协议。

- 每个 (来源, 范围) 一份同步记录，不以某个沙箱的本地状态作为共享进度。
- 增量：重叠时间窗口 + 稳定分页 + 按对象版本去重。
- 每页的快照与游标推进在同一个 commit 中提交：快照持久化后游标才前进。
- 无数据变化时不提交；“同步成功”心跳按 heartbeat_seconds 限频。
- 完整对账（带租约）发现漏同步、删除与权限变化；删除写墓碑快照。
- 来源失败记录为缺口（gap），报告据此展示数据缺口，而不是“没有进展”。
"""

from __future__ import annotations

from datetime import timedelta

from ..adapters import AdapterError, ForbiddenError, SyncSource
from ..errors import Abort
from ..ids import digest
from ..gitstore.view import TreeView, Workspace
from ..models.common import Lease
from ..models.report import SourceStatus
from ..models.sync import Gap, SyncRecord
from ..repo import iter_raw, iter_sync, load_policy, load_sync, save_sync
from ..snapshots import make_snapshot, upsert
from ..store import ContextStore
from ..timeutil import iso, parse_dt

MAX_GAPS = 20


class SyncEngine:
    def __init__(self, store: ContextStore, source: SyncSource, scope: str, source_name: str | None = None):
        self.store = store
        self.source = source
        self.name = source_name or source.source_name
        self.scope = scope
        self.run_id = store.ctx.run_id

    def _record(self, ws: TreeView) -> SyncRecord:
        rec = load_sync(ws, self.name, self.scope)
        if rec is None:
            cfg = load_policy(ws).source_sync(self.name)
            rec = SyncRecord(source=self.name, scope=self.scope, overlap_seconds=cfg.overlap_seconds)
        return rec

    def _apply(self, ws: Workspace, objects, stats: dict) -> None:
        for obj in objects:
            outcome = upsert(ws, make_snapshot(self.name, self.scope, obj, ws.now))
            if outcome == "incomparable":
                fresh = self.source.get_object(self.scope, obj.id)
                if fresh is not None:
                    outcome = upsert(ws, make_snapshot(self.name, self.scope, fresh, ws.now), fresh_read=True)
                    stats["refetched"] = stats.get("refetched", 0) + 1
            stats[outcome] = stats.get(outcome, 0) + 1

    # ---- 增量同步 ----
    def run(self) -> dict:
        view = self.store.view()
        rec = self._record(view)
        cfg = load_policy(view).source_sync(self.name)
        since = None
        if rec.cursor.get("updated_since"):
            since = iso(parse_dt(rec.cursor["updated_since"]) - timedelta(seconds=rec.overlap_seconds))
        token, total, pages, commits = None, {}, 0, []
        try:
            while True:
                page = self.source.list_objects(self.scope, updated_since=since, page_token=token, page_size=cfg.page_size)
                pages += 1
                last = page.next_token is None
                stats: dict = {}

                def persist(ws, page=page, last=last, stats=stats):
                    stats.clear()
                    self._apply(ws, page.items, stats)
                    r = self._record(ws)
                    changed = bool(ws.changed)
                    newest = max((str(o.updated_at) for o in page.items if o.updated_at), default=None)
                    if newest and (not r.cursor.get("updated_since") or parse_dt(newest) > parse_dt(r.cursor["updated_since"])):
                        r.cursor["updated_since"] = iso(parse_dt(newest))
                        changed = True
                    if last:
                        heartbeat_due = not r.last_success_at or (ws.now - parse_dt(r.last_success_at)).total_seconds() >= cfg.heartbeat_seconds
                        if changed or heartbeat_due or any(g.open for g in r.gaps):
                            r.last_success_at = ws.now
                            r.last_attempt_at = ws.now
                            for g in r.gaps:
                                g.open = False
                    save_sync(ws, r)

                res = self.store.publish(persist, f"sync: {self.name}/{self.scope} 第 {pages} 页",
                                         source_versions=f"{self.name}:{self.scope}")
                if res.status == "committed":
                    commits.append(res.commit)
                for k, v in stats.items():
                    total[k] = total.get(k, 0) + v
                if last:
                    break
                token = page.next_token
        except AdapterError as exc:
            self._gap(f"{type(exc).__name__}: {exc}")
            return {"source": self.name, "scope": self.scope, "status": "gap", "error": str(exc), "pages": pages,
                    "stats": total, "commits": commits}
        return {"source": self.name, "scope": self.scope, "status": "ok", "pages": pages, "stats": total, "commits": commits}

    def _gap(self, error: str) -> None:
        def mutation(ws):
            r = self._record(ws)
            r.last_attempt_at = ws.now
            r.gaps = (r.gaps + [Gap(at=ws.now, error=error)])[-MAX_GAPS:]
            if r.lease and r.lease.holder == self.run_id:
                r.lease = None
            save_sync(ws, r)

        self.store.publish(mutation, f"sync: {self.name}/{self.scope} 记录缺口")

    # ---- 完整对账 ----
    def reconcile(self) -> dict:
        def acquire(ws):
            r = self._record(ws)
            if r.lease and parse_dt(r.lease.until) > ws.now and r.lease.holder != self.run_id:
                raise Abort(f"对账租约由 {r.lease.holder} 持有")
            secs = load_policy(ws).leases.sync_seconds
            r.lease = Lease(holder=self.run_id, acquired_at=ws.now, until=ws.now + timedelta(seconds=secs))
            r.last_attempt_at = ws.now
            save_sync(ws, r)

        res = self.store.publish(acquire, f"sync: {self.name}/{self.scope} 开始对账")
        if res.status == "aborted":
            return {"status": "skipped", "reason": res.reason}
        try:
            listed, token = [], None
            while True:
                page = self.source.list_objects(self.scope, page_token=token, page_size=200)
                listed += page.items
                token = page.next_token
                if not token:
                    break
        except AdapterError as exc:
            self._gap(f"对账失败 {type(exc).__name__}: {exc}")
            return {"status": "gap", "error": str(exc)}

        summary: dict = {}

        def apply(ws):
            summary.clear()
            stats: dict = {}
            self._apply(ws, listed, stats)
            seen = {str(o.id) for o in listed}
            deleted = revoked = 0
            for snap in list(iter_raw(ws, self.name, self.scope)):
                if snap.object_id in seen or snap.deleted or snap.visibility == "revoked":
                    continue
                try:
                    fresh = self.source.get_object(self.scope, snap.object_id)
                except ForbiddenError:
                    fresh, revoked_now = None, True
                else:
                    revoked_now = False
                if fresh is not None:
                    upsert(ws, make_snapshot(self.name, self.scope, fresh, ws.now), fresh_read=True)
                    stats["missed_in_listing"] = stats.get("missed_in_listing", 0) + 1
                    continue
                tomb = snap.model_copy(update={"observed_at": ws.now})
                if revoked_now:
                    tomb.visibility, revoked = "revoked", revoked + 1
                else:
                    tomb.deleted, deleted = True, deleted + 1
                tomb.content_hash = digest({"data": tomb.data, "deleted": tomb.deleted, "visibility": tomb.visibility})
                upsert(ws, tomb, fresh_read=True)
            summary.update(stats, listed=len(listed), deleted=deleted, revoked=revoked)
            r = self._record(ws)
            r.lease = None
            r.last_reconcile_at = ws.now
            r.last_success_at = ws.now
            r.reconcile_summary = dict(summary)
            for g in r.gaps:
                g.open = False
            save_sync(ws, r)

        res = self.store.publish(apply, f"sync: {self.name}/{self.scope} 完成对账")
        return {"status": "ok", "summary": summary, "commit": res.commit}


def pull_object(store: ContextStore, source: SyncSource, scope: str, object_id: str) -> dict:
    """IM 请求需要最新状态时，只针对相关对象实时拉取。"""
    obj = source.get_object(scope, object_id)
    if obj is None:
        return {"status": "not_found", "object_id": object_id}
    outcome: dict = {}

    def mutation(ws):
        outcome["result"] = upsert(ws, make_snapshot(source.source_name, scope, obj, ws.now), fresh_read=True)

    res = store.publish(mutation, f"sync: 实时拉取 {source.source_name}/{scope}/{object_id}")
    return {"status": outcome.get("result"), "commit": res.commit, "version": obj.version}


def source_status(view: TreeView) -> list[SourceStatus]:
    policy = load_policy(view)
    out = []
    for rec in iter_sync(view):
        cfg = policy.source_sync(rec.source)
        stale = not rec.last_success_at or (view.now - parse_dt(rec.last_success_at)).total_seconds() > cfg.stale_after_hours * 3600
        out.append(SourceStatus(source=rec.source, scope=rec.scope, cursor=rec.cursor, last_success_at=rec.last_success_at,
                                stale=stale, open_gaps=[f"{iso(g.at)} {g.error}" for g in rec.gaps if g.open]))
    return out
