# 任务上下文包

每个任务一个文件 `P-YYYYMMDD-XXXX.md`。frontmatter 保存结构化字段，正文固定八个小节：

为什么做 / 已知什么 / 还不知道什么 / 做到什么算完成 / 如何参与 / 谁在协作 / 当前卡在哪里 / 结果

阶段：`clarifying 待澄清 → claimable 可认领 → in_progress 进行中 → in_review 待验收 → done 已完成`，
另有 `cancelled 已取消`；`blocked` 是附加标记，保留原阶段。

建立 Aone 映射（`aone.id`）后，认领、期限和状态以 Aone 为准，文件中不再保存 `stage`。

请通过 `aspeed task ...` 命令修改任务，它会校验字段、处理并发并记录来源。
