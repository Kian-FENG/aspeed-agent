"""测试计划、运行摘要与基线。"""

from datetime import datetime
from typing import Literal

from pydantic import Field

from .common import MemberId, Model, SourceRef
from .rules import Rule


class BuildRef(Model):
    id: str = Field(description="不可变版本标识，如 git:<sha> 或镜像 digest")
    label: str | None = None


class Suite(Model):
    name: str
    cases: list[str] = []
    config: dict = {}


class MetricCheck(Rule):
    pass


class TestPlan(Model):
    __test__ = False

    id: str | None = None
    intent: str
    build: BuildRef
    suites: list[Suite] = Field(min_length=1)
    checks: list[MetricCheck] = []
    expected_artifacts: list[str] = []
    resources: dict = {}
    defects: list[str] = []
    tasks: list[str] = []
    requested_by: str
    sources: list[SourceRef] = []
    created_at: datetime | None = None


class Artifact(Model):
    name: str
    sha256: str | None = None
    link: str | None = None
    size: int | None = None


class Failure(Model):
    case: str
    message: str
    category: Literal["product", "environment", "scheduling", "flaky", "unknown"]
    rule: str | None = None


class CheckResult(Model):
    metric: str | None = None
    rule: dict
    value: float | None = None
    baseline_value: float | None = None
    passed: bool | None = None
    note: str | None = None


class TestRun(Model):
    __test__ = False

    plan_id: str
    op_id: str | None = None
    job_id: str | None = None
    state: Literal["submitted", "running", "completed", "failed", "cancelled"]
    submitted_at: datetime | None = None
    completed_at: datetime | None = None
    artifacts: list[Artifact] = []
    missing_artifacts: list[str] = []
    cases: dict = {}
    failures: list[Failure] = []
    metrics: dict[str, float] = {}
    checks: list[CheckResult] = []
    verdict: Literal["pass", "fail", "inconclusive"] | None = None
    verdict_reason: str | None = None
    defects: list[str] = []
    build: str | None = None
    link: str | None = None


class BaselineCandidate(Model):
    id: str
    name: str
    from_run: str
    build: str | None = None
    metrics: dict[str, float]
    proposed_by: str
    proposed_at: datetime
    status: Literal["candidate", "approved", "rejected"] = "candidate"


class Baseline(Model):
    name: str
    version_id: str
    metrics: dict[str, float]
    from_run: str
    build: str | None = None
    approved_by: MemberId
    approved_at: datetime
    evidence: str
    previous: str | None = None
