---
name: aspeed-ask
description: 成员询问背景、进展、决策、谁在做什么、哪里卡住、可以参与什么时使用。回答必须附来源和数据截止时间。
---

# 问答与全景

## 步骤

1. 判断问题类型：
   - 全局状态（“现在都在做什么”“哪里卡住了”“我能做什么”）→ `aspeed views show board|blockers|claimable`
   - 具体任务 → `aspeed task show <ID>`
   - 背景、决策、结论 → `aspeed search "<关键词>"`，再读命中的文件
2. 需要外部系统的最新状态时，只针对相关对象实时拉取：`aspeed sync pull aone --scope <项目> --id <ID>`。
3. 查看数据新鲜度：`aspeed sync status`。

## 回答格式

- 先给结论，再给依据。
- 每个事实后附来源：`[tasks/P-20261014-7K3Q.md@ab12cd]` 或原始链接。
- 结尾注明“数据截至 <时间>”；有同步缺口时明确说出来。
- 找不到依据：说“目前没有记录”，并建议去问谁（参考任务的协调人、相关决策的 decider）或发起一个待澄清提案。
- 提问人想参与时，给出加入方式（转 `aspeed-claim`）。

## 可见性

- 只引用 `visibility: team` 的快照；如果提问所在渠道比原始资料的授权范围更广，不要转述受限内容。
