import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "evals"))

import score  # noqa: E402


def test_scorer_pass_and_fail(tmp_path):
    out = tmp_path / "out"
    (out / "intake").mkdir(parents=True)
    (out / "claim").mkdir()
    (out / "testplan").mkdir()
    (out / "intake" / "p99-jitter.md").write_text(
        "---\ntitle: P99 抖动\ncoordinator: m:alice\nacceptor: m:bob\nstage: claimable\n---\n\n"
        "## 为什么做\n\nSLA 130ms。\n\n## 已知什么\n\nP99 从 110 涨到 160。\n\n## 还不知道什么\n\n是否由调度改动引起。\n")
    (out / "claim" / "join-as-helper.json").write_text(json.dumps({"member": "m:dave", "task": "P-20261014-7K3Q", "role": "lead"}))
    (out / "testplan" / "regression-main.yaml").write_text(
        "intent: x\nbuild: {id: 'git:3f2a9c1'}\nsuites: [{name: perf}]\n"
        "checks: [{metric: p99_ms, op: '<=', baseline: perf-main, tolerance: 0.05}]\n")
    code = score.main(str(ROOT / "evals" / "cases"), str(out))
    assert code == 1  # claim 的 role 不符
    assert score.score_claim({"json": {"role": "contributor"}}, '{"role": "contributor"}') == []
