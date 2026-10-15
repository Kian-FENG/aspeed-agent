---
name: aspeed-test
description: 成员用自然语言请求测试（回归、性能、精度验证），查询测试结果，或申请/批准基线时使用。
---

# 测试与基线

ASpeed 负责**目标、配置和结果判定**；NodeManager 负责资源调度、排队、执行和产物。

## 1. 自然语言 → 结构化计划

写到临时文件（如 `/tmp/plan.yaml`）：

```yaml
intent: "<成员的原话>"
build: {id: "git:<完整 commit sha 或镜像 digest>", label: "<可读名>"}   # 必须是不可变版本标识
suites:
  - {name: perf, cases: [llama-70b-p99], config: {batch: 8}}
checks:                                    # 可由程序判定的阈值
  - {metric: p99_ms, op: "<=", baseline: perf-main, tolerance: 0.05}
  - {metric: accuracy, op: ">=", threshold: 0.92}
expected_artifacts: [metrics.json, junit.xml]
defects: [<要验证的缺陷 ID>]               # 验证修复时填写
tasks: [<关联任务 ID>]
sources: [{ref: "im:msg/<ID>"}]
```

- 版本说不清（“最新的”“昨天那个”）时，先问清楚具体的 commit 或镜像，不要猜。
- 阈值来源不明时，优先引用已批准基线（`baselines/current/`），不要编数字。

## 2. 提交与收集

```bash
aspeed test submit --plan /tmp/plan.yaml --by m:<请求人>   # 同一版本 + 同一计划 = 同一个回归意图，重复提交不会重复执行
aspeed op approve <op_id> --by m:<有权限的人>               # policy 要求确认时
aspeed op run <op_id>
aspeed test collect <plan_id>                               # 定时任务也会自动收集
```

结果中的失败分类：`product`（产品回归，会登记建缺陷意图）、`environment`、`scheduling`、`flaky`、`unknown`。
回复时说明结论（pass / fail / inconclusive）、原因、失败分类，并附 NodeManager 链接。**inconclusive 不是 pass。**

## 3. 基线

```bash
aspeed baseline propose --run <plan_id> --name <基线名> --by m:x
aspeed baseline approve <candidate_id> --by m:<批准人> --evidence im:msg/<确认消息> [--expect-current <确认时看到的当前版本>]
```

- 测试结果只是候选；正式基线必须由有 `baseline_approver` 角色的人在 IM 中**明确确认**，并以那条消息作为证据。
- 你永远不能自行批准基线。
- 返回 `aborted`（当前基线已变化）时，把新旧对比发给批准人重新确认。
