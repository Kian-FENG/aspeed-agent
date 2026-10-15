"""测试与基线：结构化计划 → NodeManager 执行 → 结果分析 → 候选基线 → 人工批准。

ASpeed 负责目标与配置、结果判定；NodeManager 负责调度、排队、执行与产物。
"""

from __future__ import annotations

import re

from ..adapters import Adapters
from ..errors import Abort, NotFound, PermissionDenied, ValidationFailed
from ..ids import digest, safe_name
from ..models import Baseline, BaselineCandidate, TestPlan, TestRun
from ..models.testing import Artifact, CheckResult, Failure
from ..ops.executor import propose_in
from ..repo import baseline_path, load_baselines, load_plan, load_policy, load_roster, load_run, plan_path, run_path
from ..rules import evaluate
from ..store import ContextStore

IDENTITY_FIELDS = ("build", "suites", "checks", "expected_artifacts", "resources")


def normalize_plan(data: dict, requested_by: str) -> TestPlan:
    try:
        plan = TestPlan.model_validate({**data, "requested_by": requested_by, "id": None})
    except Exception as exc:
        raise ValidationFailed("测试计划不符合 Schema", errors=str(exc)) from exc
    identity = {k: plan.model_dump(mode="json")[k] for k in IDENTITY_FIELDS}
    # 同一不可变版本 + 同一计划内容 = 同一次回归意图
    plan.id = "TP-" + digest(identity, 12)
    return plan


def submit(store: ContextStore, data: dict, requested_by: str) -> dict:
    plan = normalize_plan(data, requested_by)
    out: dict = {}

    def mutation(ws):
        if not ws.exists(plan_path(plan.id)):
            ws.write_yaml(plan_path(plan.id), plan.model_copy(update={"created_at": ws.now}).dump())
        op, created = propose_in(ws, "nm.submit", {"plan": plan.id}, {"plan_id": plan.id, "plan": plan.dump()},
                                 requested_by, store.ctx.run_id, on_behalf_of=requested_by)
        out.update(plan_id=plan.id, op_id=op.op_id, state=op.state, created=created)

    res = store.publish(mutation, f"test: 登记测试计划 {plan.id}", on_behalf_of=requested_by)
    return {**out, "commit": res.commit}


def classify(message: str, policy) -> tuple[str, str | None]:
    for category in ("environment", "scheduling"):
        for pattern in policy.failure_rules.get(category, []):
            if re.search(pattern, message or ""):
                return category, pattern
    return "product", None


def analyze(plan: TestPlan, job_state: str, results: dict, policy, baselines: dict) -> dict:
    names = {a.get("name") for a in results.get("artifacts", [])}
    missing = [a for a in plan.expected_artifacts if a not in names]
    cases = results.get("cases", [])
    counts: dict[str, int] = {}
    failures = []
    for c in cases:
        counts[c.get("status", "unknown")] = counts.get(c.get("status", "unknown"), 0) + 1
        if c.get("status") == "flaky":
            failures.append(Failure(case=c["name"], message=c.get("message", ""), category="flaky"))
        elif c.get("status") in ("failed", "error"):
            cat, rule = classify(c.get("message", ""), policy)
            failures.append(Failure(case=c["name"], message=c.get("message", ""), category=cat, rule=rule))
    metrics = {k: float(v) for k, v in (results.get("metrics") or {}).items()}
    checks = []
    for rule in plan.checks:
        r = evaluate(rule, metrics, None, {n: b.metrics for n, b in baselines.items()})
        checks.append(CheckResult(metric=rule.metric, rule=rule.dump(), value=r.get("value"),
                                  baseline_value=r.get("baseline_value"), passed=r["passed"], note=r.get("note")))
    if job_state != "succeeded" and not cases:
        cat, _ = classify(results.get("log", ""), policy)
        verdict, reason = "inconclusive", f"作业状态 {job_state}，判定为{'环境' if cat == 'environment' else '调度'}问题" \
            if cat != "product" else f"作业状态 {job_state}，无测试结果"
    elif missing:
        verdict, reason = "inconclusive", f"缺少产物 {missing}"
    elif any(f.category == "product" for f in failures) or any(c.passed is False for c in checks):
        verdict, reason = "fail", "存在产品失败或指标未达阈值"
    elif any(f.category in ("environment", "scheduling") for f in failures) or any(c.passed is None for c in checks):
        verdict, reason = "inconclusive", "存在环境/调度失败或缺少判定所需的基线"
    else:
        verdict, reason = "pass", "全部用例通过且指标满足阈值"
    return {
        "artifacts": [Artifact.model_validate({k: a.get(k) for k in ("name", "sha256", "link", "size")}) for a in results.get("artifacts", [])],
        "missing_artifacts": missing, "cases": counts, "failures": failures, "metrics": metrics, "checks": checks,
        "verdict": verdict, "verdict_reason": reason,
    }


def collect(store: ContextStore, adapters: Adapters, plan_id: str, requested_by: str = "schedule:test-collect") -> dict:
    view = store.view()
    run, plan = load_run(view, plan_id), load_plan(view, plan_id)
    if run is None or plan is None:
        raise NotFound(f"没有已提交的测试运行 {plan_id}")
    if run.state == "completed":
        return {"plan_id": plan_id, "state": "completed", "verdict": run.verdict}
    status = adapters.nodemanager.status(run.job_id)
    if status["state"] in ("queued", "running"):
        def mark_running(ws):
            r = load_run(ws, plan_id)
            if r.state != "running" and status["state"] == "running":
                ws.write_yaml(run_path(plan_id), r.model_copy(update={"state": "running"}).dump())

        store.publish(mark_running, f"test: {plan_id} 运行中")
        return {"plan_id": plan_id, "state": status["state"]}
    results = adapters.nodemanager.results(run.job_id)
    out: dict = {}

    def mutation(ws):
        policy = load_policy(ws)
        current = load_run(ws, plan_id)
        if current.state == "completed":
            raise Abort("已由其他实例处理")
        a = analyze(plan, status["state"], results, policy, load_baselines(ws))
        state = "completed" if status["state"] == "succeeded" else ("cancelled" if status["state"] == "cancelled" else "failed")
        done = current.model_copy(update={**a, "state": state, "completed_at": ws.now, "link": results.get("link")})
        ws.write_yaml(run_path(plan_id), TestRun.model_validate(done.model_dump()).dump())
        defect_ops = []
        # 产品失败：登记建缺陷意图（验证运行除外——由缺陷跟进按规则重新打开）
        if a["verdict"] == "fail" and not plan.defects and policy.defects.scopes:
            for f in a["failures"]:
                if f.category != "product":
                    continue
                op, _ = propose_in(ws, "aone.create", {"plan": plan_id, "case": f.case}, {
                    "scope": policy.defects.scopes[0],
                    "fields": {"type": "defect", "title": f"[回归失败] {f.case} @ {plan.build.label or plan.build.id}",
                               "description": f"{f.message}\n\n测试运行：aspeed-context/{run_path(plan_id)}\n结果：{results.get('link')}",
                               "severity": "major"}}, requested_by, store.ctx.run_id, note="回归产品失败")
                defect_ops.append(op.op_id)
        out.update(plan_id=plan_id, state=state, verdict=a["verdict"], reason=a["verdict_reason"],
                   failures=[f.dump() for f in a["failures"]], defect_ops=defect_ops)

    res = store.publish(mutation, f"test: 收集结果 {plan_id}")
    return {**out, "commit": res.commit} if out else {"plan_id": plan_id, "status": res.status, "reason": res.reason}


def propose_baseline(store: ContextStore, plan_id: str, name: str, by: str) -> dict:
    cand_id = f"{safe_name(name)}--{plan_id}"
    out: dict = {}

    def mutation(ws):
        run = load_run(ws, plan_id)
        if run is None or run.verdict != "pass" or not run.metrics:
            raise ValidationFailed("只有结论为 pass 且有指标的运行可以作为候选基线")
        rel = f"baselines/candidates/{cand_id}.yaml"
        if not ws.exists(rel):
            ws.write_yaml(rel, BaselineCandidate(id=cand_id, name=name, from_run=plan_id, build=run.build,
                                                 metrics=run.metrics, proposed_by=by, proposed_at=ws.now).dump())
        current = load_baselines(ws).get(name)
        out.update(candidate=cand_id, metrics=run.metrics, current=current.version_id if current else None,
                   current_metrics=current.metrics if current else None)

    res = store.publish(mutation, f"baseline: 候选 {cand_id}", on_behalf_of=by)
    return {**out, "commit": res.commit}


def approve_baseline(store: ContextStore, candidate_id: str, by: str, evidence: str, expect_current: str | None) -> dict:
    """正式基线必须由有权限的人确认；expect_current 是确认时看到的当前基线版本（并发变化则拒绝，需要重新核验）。"""
    view = store.view()
    member = load_roster(view).get(by)
    if not member or "baseline_approver" not in member.roles:
        raise PermissionDenied(f"{by} 没有基线批准权限")
    if not evidence:
        raise ValidationFailed("需要人工确认证据（例如 IM 消息 ID）")
    out: dict = {}

    def mutation(ws):
        rel = f"baselines/candidates/{candidate_id}.yaml"
        data = ws.read_yaml(rel)
        if not data:
            raise NotFound(f"候选基线 {candidate_id} 不存在")
        cand = BaselineCandidate.model_validate(data)
        if cand.status != "candidate":
            raise Abort(f"候选基线状态为 {cand.status}")
        current = load_baselines(ws).get(cand.name)
        seen = current.version_id if current else None
        if seen != (expect_current or None):
            raise Abort(f"当前基线已变为 {seen}（确认时为 {expect_current}），请重新核验后再确认", current=seen)
        version_id = f"{ws.now.strftime('%Y%m%dT%H%M%SZ')}-{cand.from_run}"
        baseline = Baseline(name=cand.name, version_id=version_id, metrics=cand.metrics, from_run=cand.from_run,
                            build=cand.build, approved_by=by, approved_at=ws.now, evidence=evidence, previous=seen)
        ws.write_yaml(baseline_path(cand.name), baseline.dump())
        ws.write_yaml(f"baselines/history/{safe_name(cand.name)}/{version_id}.yaml", baseline.dump())
        ws.write_yaml(rel, cand.model_copy(update={"status": "approved"}).dump())
        out.update(name=cand.name, version_id=version_id, previous=seen)

    res = store.publish(mutation, f"baseline: 批准 {candidate_id}", on_behalf_of=by)
    if res.status == "aborted":
        return {"status": "aborted", "reason": res.reason, **(res.value or {})}
    return {**out, "commit": res.commit}
