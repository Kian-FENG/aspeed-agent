---
name: aspeed-intake
description: 成员在 IM 中提出问题、引用讨论或提供文档，希望发布为团队任务时使用。检索已有任务与结论、识别重复、起草任务上下文包、保存为提案或待澄清提案。
---

# 问题发布

目标：把一个问题变成**别人读完就能判断要不要参与**的任务上下文包。

## 步骤

1. **确认发起人身份**：把 IM 用户映射为成员 ID（`control/members.yaml`）。映射不到就请对方先登记。
2. **检索已有工作**：
   ```bash
   aspeed task related --query "<问题关键词>"
   aspeed search "<问题关键词>"
   ```
   - 发现**重复**任务：不要新建。告诉发起人已有任务的 ID、阶段、参与者，建议加入（转 `aspeed-claim`）。
   - 发现**相关**任务或决策：在草稿的“已知什么”中引用（`路径@commit`）。
3. **起草**（写到临时文件，例如 `/tmp/draft.md`）：

   ```markdown
   ---
   title: <一句话说清要解决什么>
   stage: claimable            # 信息不完整会自动降为 clarifying
   coordinator: m:<协调人>      # 负责信息完整和依赖协调
   acceptor: m:<验收人>         # 对结果有判断力的人
   goal_refs: [<goals/ 下的目标 ID>]
   skills_needed: [<所需能力>]
   acceptance:
     - {id: AC1, check: "<可检查的条件>", mode: auto, rule: {metric: p99_ms, op: "<=", baseline: perf-main, tolerance: 0.05}}
     - {id: AC2, check: "<需要人判断的条件>", mode: human}
   sources:
     - {ref: "im:msg/<消息ID>", title: "<讨论标题>", link: "<链接>"}
   ---

   ## 为什么做
   关联目标、问题背景、预期价值。

   ## 已知什么
   已验证的事实（附来源）、已有尝试、关键资料。

   ## 还不知道什么
   假设、争议、待验证问题。

   ## 做到什么算完成
   交付物与验收条件的说明。

   ## 如何参与
   可独立认领的子任务、需要的能力。

   ## 当前卡在哪里
   ```
   - 只写有来源支撑的内容；推测放进“还不知道什么”。
   - `auto` 验收项必须有可由程序判定的 `rule`；做不到就用 `human`。
   - 不要编造协调人或验收人：不确定就留空，提案会进入“待澄清”。
4. **发布**：
   ```bash
   aspeed task create --file /tmp/draft.md --as m:<发起人>
   ```
   返回 `stage` 和 `missing`。
5. **回复发起人**（IM）：
   - 已发布为“可认领”：给出任务 ID、一句话摘要、所需能力，邀请感兴趣的人加入。
   - 进入“待澄清”：逐条列出 `missing`，问清楚后用 `aspeed task show <ID> --markdown` 取出全文、补齐，再用新草稿重新发布或请协调人推进到可认领（`aspeed task stage <ID> claimable --by ...`）。
6. **是否建 Aone 单**由协调人决定：`aspeed task promote <ID> --by m:<协调人> --scope <项目>` 只登记建单意图，需按规则确认后执行。

## 不要

- 不要替团队决定方向或优先级；不要把任务指派给具体的人。
- 不要把同一个问题拆成多个重复提案。
