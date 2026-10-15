"""定时任务入口：外部框架按周期调用 `aspeed schedule tick`。

一次 tick 依次执行：增量同步（到期则对账）→ 缺陷规则扫描 → 恢复未完成操作 →
收集运行中的测试 → 视图重建。单步失败不影响其他步骤，结果逐项返回。
"""

from __future__ import annotations

from .adapters import Adapters
from .flows import defects, testing
from .ops import OpService
from .repo import iter_runs, load_policy, load_sync
from .store import ContextStore
from .sync import SyncEngine
from .timeutil import parse_dt
from .views import render


def tick(store: ContextStore, adapters: Adapters) -> dict:
    out: dict = {"steps": []}

    def step(name, fn):
        try:
            out["steps"].append({"step": name, "ok": True, "result": fn()})
        except Exception as exc:  # noqa: BLE001 - 定时任务需要继续执行其余步骤
            out["steps"].append({"step": name, "ok": False, "error": f"{type(exc).__name__}: {exc}"})

    view = store.view()
    policy = load_policy(view)
    for source, scopes in policy.sources.items():
        for scope in scopes:
            def run_sync(source=source, scope=scope):
                eng = SyncEngine(store, adapters.source(source), scope, source)
                result = eng.run()
                rec = load_sync(store.view(), source, scope)
                hours = policy.source_sync(source).reconcile_every_hours
                if rec and (not rec.last_reconcile_at or (store.view().now - parse_dt(rec.last_reconcile_at)).total_seconds() >= hours * 3600):
                    result["reconcile"] = eng.reconcile()
                return result

            step(f"sync {source}/{scope}", run_sync)
    if policy.defects.scopes:
        step("defects scan", lambda: defects.scan(store))
    ops = OpService(store, adapters)
    step("ops recover", lambda: ops.recover())
    for run in iter_runs(store.view()):
        if run.state in ("submitted", "running"):
            step(f"test collect {run.plan_id}", lambda pid=run.plan_id: testing.collect(store, adapters, pid))
    step("views rebuild", lambda: render.rebuild(store))
    return out
