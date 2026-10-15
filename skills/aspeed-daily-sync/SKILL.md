---
name: aspeed-daily-sync
description: 每日生成团队同步：新增进展、阻塞与求助、可认领任务、待决策事项、测试与基线变化，每项附来源。
---

# 每日同步

日报用来**逐步替代周会中的状态播报**：让周会只讨论方向和待决策事项。

## 步骤

1. 收集事实（代码完成，每条事实带来源与编号 F1、F2……）：
   ```bash
   aspeed daily collect --out /tmp/facts.json
   ```
   输出中的 `template` 是确定性版本，可以直接发布。
2. 撰写（可选，让日报更好读）：只用事实清单里的信息，按以下五个小节写，**每条要点末尾标注引用**，如 `[F3]` 或 `[F3][F5]`：
   ```markdown
   ## 新增进展
   ## 阻塞与求助
   ## 可认领任务
   ## 待决策事项
   ## 测试与基线变化
   ```
   - 可以合并相关事实、按重要性排序、用一句话点明“需要谁做什么”。
   - 没有内容的小节写 `- 无`。
   - 不要写来源清单或截止时间，程序会附加。
3. 校验与发布：
   ```bash
   aspeed daily check --facts /tmp/facts.json --draft /tmp/draft.md   # 修正所有 errors
   aspeed daily publish --facts /tmp/facts.json --draft /tmp/draft.md
   ```
   不提供 `--draft` 时发布模板版本。
4. 发到团队群：正文 + 报告路径。报告中的“数据缺口”必须原样保留。

## 不要

- 不要添加事实清单之外的结论。
- 不要点名批评；阻塞写成“需要谁提供什么”。
