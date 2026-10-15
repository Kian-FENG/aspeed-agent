---
name: aspeed-claim
description: 成员用自然语言表达加入、认领、退出任务，报告阻塞或求助，反馈完成或验收结果时使用。
---

# 认领与协作

## 1. 理解意图

把消息映射为 **（成员, 任务, 意图）**：

| 成员说 | 意图 | 命令 |
| --- | --- | --- |
| “这个我来负责” | lead | `aspeed task join <ID> --member m:x --role lead` |
| “我可以帮忙做 kernel 部分” | contributor | `aspeed task join <ID> --member m:x --role contributor` |
| “我帮忙 review” | reviewer | `aspeed task join <ID> --member m:x --role reviewer` |
| “我先退出” | 退出 | `aspeed task leave <ID> --member m:x` |
| “卡住了，需要 A100” | 阻塞 | `aspeed task block <ID> --by m:x --reason "..." --needs "m:y=协调 2 台 A100"` |
| “节点到了” | 解除阻塞 | `aspeed task unblock <ID> --by m:x --note "..."` |
| “做完了，MR 在这” | 反馈完成 | `aspeed task submit <ID> --by m:x --evidence <链接或 run:<plan_id>> --deliverable code=<链接>` |
| （验收人）“通过 / 还差 X” | 验收 | `aspeed task accept <ID> --by m:x [--reject --note "..."]` |

- 任务不明确时，用 `aspeed task related --query "..."` 找候选，**列出候选请成员确认**，不要猜。
- 成员 ID 必须来自 `control/members.yaml` 的映射。
- 一条消息里有多个意图时逐个执行。

## 2. 执行后的回复

- `join` 返回 `via: aone` 时表示已登记 Aone 操作意图：说明“已提交加入 Aone 工作项的请求，核验后生效”，并给出 `op_id`。
- 多人同时加入是安全的：每个人的参与意图都会保留。
- `submit` 的结果：
  - `auto_accepted`：规则判定全部通过，任务已完成。
  - `auto_rejected`：列出未通过的验收项与数值。
  - `awaiting_acceptor`：通知验收人，附交付物和证据链接。
- `block` 后：在回复中点名 `needs` 里的人，说明需要他提供什么。

## 不要

- 不要代替验收人做主观判断。
- 不要在成员没有明确表达时替他认领。
