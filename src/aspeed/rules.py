"""验收与阈值规则的判定（纯函数）。"""

from __future__ import annotations

from .models.rules import Rule

_OPS = {
    "<=": lambda a, b: a <= b,
    "<": lambda a, b: a < b,
    ">=": lambda a, b: a >= b,
    ">": lambda a, b: a > b,
}


def evaluate(rule: Rule, metrics: dict[str, float], verdict: str | None = None,
             baselines: dict[str, dict[str, float]] | None = None) -> dict:
    """返回 {passed: True/False/None, value, baseline_value, note}；None 表示证据不足，需人判断。"""
    if rule.test_verdict:
        if verdict is None:
            return {"passed": None, "note": "缺少测试结论"}
        return {"passed": verdict == rule.test_verdict, "note": f"verdict={verdict}"}
    value = metrics.get(rule.metric)
    if value is None:
        return {"passed": None, "note": f"缺少指标 {rule.metric}"}
    if rule.threshold is not None:
        target = rule.threshold
        base_value = None
    else:
        base_value = (baselines or {}).get(rule.baseline, {}).get(rule.metric)
        if base_value is None:
            return {"passed": None, "value": value, "note": f"缺少已批准基线 {rule.baseline}.{rule.metric}"}
        if rule.op in ("<=", "<"):
            target = base_value * (1 + rule.tolerance)
        elif rule.op in (">=", ">"):
            target = base_value * (1 - rule.tolerance)
        else:
            target = base_value
    if rule.op == "==":
        tol = abs(target) * rule.tolerance
        passed = abs(value - target) <= tol
    else:
        passed = _OPS[rule.op](value, target)
    return {"passed": passed, "value": value, "baseline_value": base_value, "note": f"{value} {rule.op} {round(target, 6)}"}
