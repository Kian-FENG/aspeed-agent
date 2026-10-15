---
name: aspeed-ops
description: 处理外部操作意图：查看待确认操作、人工批准、执行、恢复中断的操作，以及 Agent 中介模式下按票据调用外部 SKILL。
---

# 操作审批与执行

所有外部写操作（Aone 建单、加入、流转，IM 提醒，NodeManager 提交）都是**操作意图**：

`proposed（待确认）/ simulated（影子模式）→ approved → leased → executed → verified → recorded`

另有 `failed`、`needs_human`、`superseded`。

## 常用命令

```bash
aspeed op list --state proposed          # 待确认
aspeed op show <op_id>
aspeed op approve <op_id> --by m:<有权限的人> --evidence im:msg/<确认消息>
aspeed op run <op_id>                    # 执行：先查后做、执行、核验、更新快照
aspeed op recover                        # 恢复中断的操作（定时任务会自动执行）
aspeed op supersede <op_id> --by m:x --reason "<为什么不再需要>"
```

- 批准需要 policy 规定的角色；把成员在 IM 中的确认消息 ID 作为证据。
- `needs_human`：说明原因（对象已变化、接入缺少条件更新能力、核验失败），请相关人判断后批准、撤销或人工处理。
- 运行返回 `outcome: unknown` 时**不要重试**：租约过期后 `recover` 会先查询外部结果再决定。

## Agent 中介模式（外部 SKILL 只能由你调用时）

1. `aspeed op ticket <op_id>`：获取执行权和票据。
2. 如果 `lookup_first` 为 true，先按 `lookup` 查询；已生效就把查询结果作为结果回传。
3. 否则按 `method` 和 `args` **原样**调用对应 SKILL。
4. 回传结果：`aspeed op record-result <op_id> --result /tmp/result.json [--outcome unknown|failed]`。
5. 独立查询对象的当前状态，回传观察结果：`aspeed op record-observation <op_id> --observed /tmp/observed.json`。是否生效由程序判定。
