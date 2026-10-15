from typing import Literal

from pydantic import model_validator

from .common import Model


class Rule(Model):
    """可由程序判定的验收/阈值规则。

    - {test_verdict: pass}：关联的测试运行整体通过
    - {metric, op, threshold}：指标与固定阈值比较
    - {metric, op, baseline, tolerance}：与已批准基线比较，tolerance 为相对容差
    """

    test_verdict: Literal["pass"] | None = None
    metric: str | None = None
    op: Literal["<=", "<", ">=", ">", "=="] | None = None
    threshold: float | None = None
    baseline: str | None = None
    tolerance: float = 0.0

    @model_validator(mode="after")
    def _shape(self):
        if self.test_verdict:
            return self
        if not (self.metric and self.op) or (self.threshold is None and not self.baseline):
            raise ValueError("规则需为 test_verdict，或包含 metric、op 以及 threshold/baseline 之一")
        return self
