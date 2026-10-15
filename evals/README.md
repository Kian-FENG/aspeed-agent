# 评测集

LLM 负责的环节（起草上下文包、映射认领意图、生成测试计划、撰写日报）需要回归评测：
每次修改 SKILL、AGENTS.md 或更换模型，都用同一批 golden case 检查输出。

## 结构

```
evals/cases/<类别>/<case>.yaml
  input:   给 Agent 的输入（IM 消息、讨论串、成员 ID 等）
  expect:  对输出的结构化期望（只写必须满足的点，不要求逐字一致）
```

类别：

| 类别 | 被测环节 | 输出 |
| --- | --- | --- |
| `intake` | 讨论 → 任务上下文包草稿 | Markdown（frontmatter + 小节） |
| `claim` | 自然语言 → (成员, 任务, 意图) | JSON |
| `testplan` | 自然语言测试请求 → 结构化计划 | YAML |
| `daily` | 事实清单 → 日报草稿 | Markdown（经引用校验） |

## 运行

1. 用当前的 Agent 运行框架，对每个 case 的 `input` 生成输出，保存为 `out/<类别>/<case>.<ext>`。
2. 打分：`uv run python evals/score.py evals/cases out`

`score.py` 只做确定性检查（Schema 合法、必填字段、期望值、禁止项、引用校验），不依赖 LLM 评审。
后续可以加入 LLM 评审维度（例如“已知什么”是否只包含有来源的事实）。
