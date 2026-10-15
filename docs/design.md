# ASpeed-Agent 设计（M0 定稿）

ASpeed 是研发团队共享上下文与任务跟踪的 Agent，服务于 New Lab 的任务驱动工作方式：

**问题进入 → 补齐背景 → 发布任务 → 自主认领 → 协作与测试 → 验证结果 → 更新共同认知**

本文记录已确认的设计和 M0 的实现方式。背景与目标见团队的《任务驱动的研发流程》。

---

## 1. 设计原则

1. **LLM 负责理解和起草，代码负责状态和写入。** 理解自然语言、检索背景、起草上下文包和测试计划由 LLM 完成。所有共享记录的更新和外部写操作只能经 `aspeed` CLI 落地，由 CLI 做校验、状态迁移、幂等和 Git 发布。
2. **事实带版本，回答带来源，报告带截止时间。** 来源失败显示为缺口，不推断为“没有进展”。
3. **幂等由业务意图决定。** 操作 ID = hash(操作类型 + 业务键)。多个沙箱产生同一个意图时，会落到同一条记录。
4. **冲突时重新推导，不做文本合并。** Git 推送被拒后，在新的远端版本上重新执行业务判断。
5. **先影子，后自动。** 每类外部写操作分 `dry_run` / `confirm` / `auto` 三档，在 `control/policy.yaml` 中单独开关。
6. **帮人看清，不替人指派。** Agent 不分配任务，不做个人排名；提醒只发给相关人和约定的协调人。

## 2. 分层架构

```
入口      IM 消息 │ 定时任务（aspeed schedule tick）│ 本地 CLI
            │                                    外部框架负责沙箱，拉取指定版本的代码与上下文
判断层    AGENTS.md + skills/*（LLM）
          意图识别 · 检索 · 起草上下文包/测试计划/日报 · 提出操作
            │  只能通过 aspeed CLI 落地
控制层    src/aspeed（确定性 Python）
          Schema 校验 · 身份权限 · 预算 · 操作状态机 · Git 发布器 · 同步协议 · 视图 · 引用校验
            │
接入层    adapters/base.py 契约 → 已有 SKILL
          脚本直调（script.py，JSON 进/出） │ Agent 中介票据（op ticket）│ M0：Fake + 契约测试
            │
存储层    aspeed-context（Git）
```

ASpeed 不实现沙箱运行框架，也不实现 Aone / NodeManager / IM 的底层 API。

## 3. 仓库

### aspeed-agent（本仓库）

| 路径 | 内容 |
| --- | --- |
| `AGENTS.md`、`CLAUDE.md` | LLM 运行规则 |
| `skills/` | 七个工作流 SKILL（`.claude/skills` 为指向它的软链接，便于本地用 Claude Code 验证） |
| `src/aspeed/models/` | pydantic 模型；`aspeed schema export` 导出 JSON Schema |
| `src/aspeed/gitstore/` | git 封装、读写视图、发布器、发布前校验 |
| `src/aspeed/ops/` | 操作状态机、租约、handler、执行器 |
| `src/aspeed/sync/` | 同步引擎、下游消费者水位线 |
| `src/aspeed/adapters/` | 接入契约、Fake、脚本桥、契约测试 |
| `src/aspeed/flows/` | 任务、缺陷、测试与基线、日报、检索 |
| `src/aspeed/views/` | 任务全景、阻塞清单、可认领任务 |
| `src/aspeed/templates/context/` | aspeed-context 初始化模板 |
| `evals/` | LLM 环节的评测集与确定性打分 |

### aspeed-context（团队知识与控制记录）

| 路径 | 内容 | 权威来源 |
| --- | --- | --- |
| `goals/` `decisions/` `research/` `methods/` | 目标、决策、结论、方法 | Git（人维护，经 MR） |
| `pending/` | Agent 提出的修改建议、实质分歧 | 待人审 |
| `tasks/P-*.md` | 任务上下文包 | 未建单时为 Git；建单后认领/期限/状态以 Aone 为准 |
| `raw/<来源>/<范围>/<对象>.json` | 来源快照（最新观察 + `source_version`），历史版本在 Git 历史中 | 外部系统 |
| `tests/plans/` `tests/runs/` | 测试计划、运行摘要、产物哈希与链接 | NodeManager |
| `baselines/current/` `history/` `candidates/` | 正式基线、历史、候选 | 经人工批准的 Git 记录 |
| `control/ops/<hash前缀>/<op_id>.yaml` | 操作意图与回执 | Git |
| `control/sync/<来源>/<范围>.yaml` | 同步进度 | Git |
| `control/consumers/<名称>.yaml` | 下游处理水位线 | Git |
| `control/members.yaml` `control/policy.yaml` | 成员身份映射与角色、团队规则 | Git（人维护） |
| `views/` `reports/daily/` | 生成的视图与日报 | 由事实生成 |

## 4. 核心数据模型

### 任务上下文包（`tasks/P-YYYYMMDD-XXXX.md`）

frontmatter 保存结构化字段；正文固定八个小节：为什么做 / 已知什么 / 还不知道什么 / 做到什么算完成 / 如何参与 / 谁在协作 / 当前卡在哪里 / 结果。

- `stage`：`clarifying → claimable → in_progress → in_review → done`，另有 `cancelled`；支持重新打开（`done → in_progress`、`cancelled → clarifying`）。
- `blocked`：附加标记，记录原因、开始时间、`needs`（需要谁提供什么）、依赖和解除条件，不改变 `stage`。
- 进入 `claimable` 至少需要：为什么做、至少一条验收条件、如何参与（小节或 `skills_needed`）、协调人、验收人。缺任何一项都会留在 `clarifying`，并返回缺失清单。
- `acceptance[].mode = auto` 时必须带可由程序判定的 `rule`（测试结论，或指标与阈值/已批准基线的比较）。
- 建立 Aone 映射（`aone.id`）后，文件中不再保存 `stage` 和参与者，读取时以 Aone 快照为准（`flows/tasks.py: effective`）。

### 操作记录（`control/ops/`）

```
proposed（待确认）─┐
simulated（影子）──┼→ approved → leased → executed → verified → recorded
                   │                │
                   └→ superseded    └→ failed / needs_human
```

- 路径只由 `op_id` 决定（按哈希前缀分片），保证同一意图只有一个文件。
- 发布前校验：状态迁移合法；`op_id / kind / business_key / created_at` 不可修改；进入执行后参数不可修改；历史和尝试记录只能追加；未过期的租约不能被他人接管。

### 同步记录与消费者

- 同步记录：范围、游标（`updated_since`）、重叠窗口、最近成功/尝试/对账时间、对账摘要、租约、缺口。
- 消费者：名称、关注的路径、`through_commit`（已处理到的 Git commit）。

## 5. 关键机制

### 5.1 Git 发布器（`gitstore/publisher.py`）

```
precheck：必须是专用 clone（在主分支、无未提交改动、无未推送提交）
for attempt in 1..N:
    fetch，重置到 origin/main
    mutation(ws)                 # 在最新状态上重新判断业务条件；Abort 表示条件不再成立
    无变化 → noop（不产生空提交）
    发布前校验 → commit（trailer：Run-Id / Actor / Code-Version / Op-Id / On-Behalf-Of / Source-Versions）
    非强制 push；被拒则退避（指数 + 抖动）后重试
超过上限 → Contention
```

冲突处理规则：

| 对象 | 规则 |
| --- | --- |
| RAW 快照 | 按 `source_version` 判断新旧；新的覆盖旧的；无法判断时重新读取；内容未变不写 |
| 视图、日报 | 基于最新输入重新生成；视图只在事实变化时由定时任务重建，IM 请求现场渲染 |
| 任务上下文包 | 每次在最新版本上重新应用变更（例如参与者只增不覆盖） |
| 当前基线 | 批准时检查预期的旧版本（`--expect-current`）；并发变化则拒绝，需要重新确认 |
| 操作记录 | 状态机校验，禁止自由文本合并 |
| 决策与结论 | 人通过 MR 修改；Agent 的建议进入 `pending/` |

### 5.2 操作执行（`ops/executor.py`）

1. **登记**：`propose` 计算 `op_id`，记录已存在时直接返回（幂等）；初始状态由 policy 决定。
2. **批准**：`approve` 检查成员角色是否符合 `approver_roles`，并记录证据（如 IM 消息 ID）。
3. **获取执行权**：通过 Git CAS 写入租约。免打扰时段的通知类操作、超过每日写入上限的操作会被延后。接入缺少所需能力时（例如 Aone 没有条件更新）降级为 `needs_human`。
4. **先查后做**：此前有过尝试（接管过期租约或重试）时，先按操作标记查询外部结果，已生效则直接记录。
5. **执行**：
   - `UnknownOutcome`：保持 `leased`，等租约过期后由 `recover` 先查后做。
   - `TransientError`：释放执行权，回到 `approved`。
   - `StaleVersion`：转为 `needs_human`。
   - `PermanentError` / `ForbiddenError`：转为 `failed`。
6. **核验**：独立查询对象，由代码判定是否生效 → `verified`。
7. **更新快照**：handler 更新 RAW 快照或任务映射，并在**同一个 commit** 中把记录转为 `recorded`。

外部写成功但 Git 更新失败时，记录停在 `executed` 或 `verified`，由 `aspeed op recover` 补齐，不会重复写外部系统。

**Agent 中介模式**：`op ticket` 签发带固定参数的票据，LLM 原样调用 SKILL；`op record-result` 回传执行结果，`op record-observation` 回传独立查询到的状态，由代码判定是否生效。

### 5.3 同步协议（`sync/engine.py`）

- 增量同步：`updated_since = 游标 − 重叠窗口`，按 `(updated_at, id)` 稳定分页。**每一页的快照和游标推进在同一个 commit 中提交**。
- 无数据变化时不提交；“同步成功”心跳按 `heartbeat_seconds` 限频。因此数据截止时间是保守值，不会高估新鲜度。
- 来源失败时记录缺口、不推进游标；下次成功后关闭缺口。
- 完整对账（带租约）：重新列出全部对象；列表中消失的对象逐个确认，删除写墓碑快照（`deleted: true`），权限收回标记 `visibility: revoked`。
- 实时拉取：`aspeed sync pull` 只针对相关对象。
- 下游消费者用 `git diff <through_commit>..HEAD` 获取待处理变化，并与处理结果在同一个 commit 中推进水位线。

### 5.4 身份、权限与安全

- 成员身份映射与角色在 `control/members.yaml`：`coordinator`、`acceptor`、`baseline_approver`、`tester`。
- 基线批准必须由 `baseline_approver` 确认并附证据，发布前校验也会检查这一点。验收需由任务的验收人或协调人确认。
- IM 和文档内容一律视为数据。写操作必须有明确的发起成员，并通过权限校验。
- 快照带 `visibility`；检索默认只返回 `team` 可见的内容。
- 预算：每日外部写入上限、每人每日提醒上限、免打扰时段。

## 6. 四条业务流程

| 流程 | LLM（skills/） | 代码（flows/） |
| --- | --- | --- |
| 问题发布与认领 | 起草上下文包；识别重复与相关任务；把自然语言映射为（成员, 任务, 意图） | 分配 ID；完整性检查；参与者只增不覆盖；阶段规则；规则自动验收；建单意图 |
| 缺陷跟进 | 判断证据是否充分（判断不了交给验收人）；回答问询 | 规则扫描；提醒去重（缺陷 × 检查项 × 接收人 × 时间窗）；每人上限；升级给协调人；回归通过/失败后登记关闭/重新打开 |
| 测试与基线 | 自然语言 → 结构化计划；解读结果 | 计划 ID = hash(不可变版本 + 计划内容)；提交与收集；产物完整性；失败分类（规则优先）；阈值与基线比较；为产品失败登记建缺陷意图；候选基线；带 CAS 的人工批准 |
| 每日同步 | 按五类撰写，每条引用 `[F#]` | 收集事实（带来源）；引用校验；附来源版本清单、截止时间与数据缺口；影子模式下的动作列入“待决策事项”供评估 |

## 7. 接入（M0：预留链路）

`adapters/base.py` 定义四类契约。M0 提供：

- **Fake**（`adapters/fake.py`）：文件持久化，跨进程共享，支持故障注入（`transient`、`permanent`、`forbidden`、`unknown_after_write`）。
- **脚本桥**（`adapters/script.py`）：把已有 SKILL 包装成“JSON 进、JSON 出”的命令。接入时为每个 SKILL 写一个薄脚本。
- **契约测试**（`adapters/contracts.py`）：把“M0 必须核验的能力”写成可执行检查。

| 接入对象 | 检查项 |
| --- | --- |
| Aone | 创建与更新、版本或更新时间、稳定分页与增量查询、追加参与者不覆盖、状态流转、条件更新拒绝过期版本、按操作标记查询结果 |
| NodeManager | 提交、重复提交识别、状态、产物结构、取消、资源配额 |
| IM / 知识库 | 发送与按标记查询、身份解析、读取范围内对象 ID 与版本稳定 |
| Git 远端 | 非快进推送被拒（在临时分支上验证）；受保护分支、身份控制、CI 校验需人工确认 |

运行：`aspeed --adapters <配置> contract run aone --scope <沙箱项目>`。

## 8. 部署形态

| 触发 | 运行内容 |
| --- | --- |
| IM 请求 | 外部框架启动沙箱 → `aspeed clone-context`（partial clone）→ Agent 加载 AGENTS.md 与 skills，经 CLI 读写 |
| 定时（如每 10 分钟） | `aspeed schedule tick`：增量同步（到期则对账）→ 缺陷扫描 → 恢复操作 → 收集测试 → 重建视图 |
| 每日 | Agent 运行 `aspeed-daily-sync`：collect → 撰写 → check → publish → 发群 |
| aspeed-context 的 MR | CI 运行 `aspeed validate --context . --base origin/main` |

环境变量：`ASPEED_CONTEXT`、`ASPEED_ADAPTERS`、`ASPEED_RUN_ID`（同一运行实例内保持不变）、`ASPEED_ACTOR`、`ASPEED_GIT_NAME/EMAIL`、`ASPEED_BRANCH`。

## 9. New Lab 转型落地

| 阶段 | Agent 能力 | 团队习惯变化 | 观察指标（只看团队层面） |
| --- | --- | --- | --- |
| ① 看见（2–3 周） | 只读同步、任务全景、日报；提醒为影子模式 | 新事项一律以上下文包发布 | 上下文包完整率；日报被纠错率 |
| ② 任务市场 | 自然语言发布与认领、建单、阻塞求助 | 周会去掉状态播报，只讨论方向和待决策事项；任务公开认领，协调人只补信息、不派活 | 认领中位时长；自主发起任务占比；周会中状态播报时长 |
| ③ 闭环 | 缺陷提醒正式开启、规则自动验收、基线批准流程 | 技术决策写入 `decisions/`，注明由谁判断、依据和异议 | 有明确下一步的缺陷占比；阻塞解除时长；验证闭环率 |

推进方式：每个阶段开始前，由协调人在 `control/policy.yaml` 中调整对应操作的模式。

需要防止的问题：Agent 变成监工；出现个人排行榜；提醒过多被静音；上下文包变成填表负担（由 Agent 起草、人确认，必填字段保持最少）。

## 10. 里程碑

| 里程碑 | 内容 | 状态 |
| --- | --- | --- |
| M0 | 控制层基础设施；只依赖 Git 的流程；Fake 接入；契约测试；SKILL 与评测骨架 | 本次交付 |
| M1 | 为 Aone SKILL 写脚本桥并通过契约测试；只读同步；缺陷扫描（影子模式）；日报接入 Aone | |
| M2 | Aone 写入：建单、认领、流转；核验与对账；按团队意愿把提醒从影子模式切到确认/自动 | |
| M3 | NodeManager：按发布记录触发回归、测试计划、结果分析、基线批准 | |
| M4 | IM 入口与知识库快照；带引用的问答；判断力地图（基于贡献记录的“谁可能最了解”，仅作建议） | |

## 11. 已知限制与后续优化

- 检索使用 BM25（中文按字与二元组切分）。语料增大后可加入向量索引，索引作为产物存放，不进 Git。
- 预算统计和提醒计数需要遍历 `control/ops/`。操作量很大时可增加按日的计数索引。
- RAW 快照体量：目前只快照授权范围内的对象。增长后可用 sparse checkout，或把 `raw/` 拆成独立仓库。
- 回归的自动触发（轮询发布记录或版本清单）放在 M3；M0 只支持按不可变版本手动或由自动化提交。
- 已关联 Aone 的任务退出参与，需要在 Aone 中操作（M2 增加 `aone.remove_participant`）。
