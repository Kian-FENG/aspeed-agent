# aspeed-context

团队共享上下文仓库：知识、任务提案、来源快照和 ASpeed 的控制记录都在这里，用 Git 版本化。

## 数据归属

| 目录 | 内容 | 权威来源 | 谁来写 |
| --- | --- | --- | --- |
| `goals/` `decisions/` `research/` `methods/` | 团队目标、已确认决策、研究结论、方法 | 本仓库 | 人（MR 评审）；ASpeed 只往 `pending/` 提交建议 |
| `tasks/` | 任务上下文包；尚未进入 Aone 的提案以此为准 | 未建单时为本仓库；建单后认领、期限、状态以 Aone 为准 | ASpeed（经 `aspeed` CLI）与人 |
| `raw/<来源>/<范围>/` | IM、知识库、Aone、NodeManager 的版本化快照 | 外部系统 | 只由 ASpeed 同步写入 |
| `tests/` | 测试计划、运行摘要、产物哈希与链接 | NodeManager 及产物存储 | ASpeed |
| `baselines/current/` | 正式性能、精度基线 | 本仓库中经人工批准的记录 | 有 `baseline_approver` 角色的人确认后写入 |
| `control/` | 同步进度、操作意图、执行回执、成员、规则 | 本仓库 | ASpeed（成员与规则由人维护） |
| `views/` `reports/daily/` | 任务全景、阻塞清单、日报 | 由上面的事实生成 | 定时任务重建 |

## 写入规则

- 主分支受保护：禁止强制推送，只允许快进更新。
- 人工 MR 在 CI 中运行 `aspeed validate --context . --base origin/main`，与 ASpeed 使用同一套校验。
- `raw/`、`control/ops/`、`baselines/history/`、`reports/daily/` 中的记录不可删除；来源对象删除时写入 `deleted: true` 的墓碑快照。
- 回答和报告引用本仓库内容时，使用 `路径@commit` 作为来源。
