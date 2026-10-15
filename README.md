# ASpeed-Agent

研发团队共享上下文与任务驱动的 Agent，帮助团队转向 New Lab 工作方式：**问题进入 → 补齐背景 → 发布任务 → 自主认领 → 协作与测试 → 验证结果 → 更新共同认知**。

ASpeed 由两部分组成：

- **判断层**：`AGENTS.md` + `skills/`。由 LLM 理解自然语言、检索背景、起草任务上下文包、测试计划和日报。
- **控制层**：`aspeed` CLI（`src/aspeed`）。负责 Schema 校验、身份权限、操作状态机、幂等、Git 并发发布、同步协议和引用校验。LLM 的所有写入都必须经过它。

设计说明见 [docs/design.md](docs/design.md)。

## 当前状态：M0

- ✅ Git 发布器：多进程并发写入不丢失更新；只做快进更新，不强制推送。
- ✅ 操作执行器：幂等键、租约、先查后做、核验、中断恢复；支持脚本直调与 Agent 中介票据两种方式。
- ✅ 同步引擎：增量同步与重叠窗口、按版本去重、快照与游标原子提交、缺口记录、完整对账（删除与权限变化）。
- ✅ 业务流程：任务发布、认领、阻塞、验收；缺陷规则扫描与提醒去重；测试计划、结果分析与基线批准；日报引用校验；任务视图；检索。
- ✅ 接入契约：Aone / NodeManager / IM / 知识库 / Git 远端，附 Fake 实现、脚本桥和可执行的契约测试。
- ⏳ 真实的 Aone / NodeManager / IM 接入：链路已预留，M1 起逐个接入（见 `config/adapters.example.yaml`）。

## 快速开始

```bash
uv sync
uv run pytest                      # 全部测试，包括多进程并发测试

# 1. 初始化团队上下文仓库（演示用本地 bare 远端；正式使用时换成受保护的远端仓库）
git init --bare -b main /tmp/aspeed-demo/remote.git
uv run aspeed init-context /tmp/aspeed-demo/seed --remote /tmp/aspeed-demo/remote.git
#    编辑 seed/control/members.yaml 与 seed/control/policy.yaml，提交并推送

# 2. 每个运行实例使用自己的专用 clone
uv run aspeed clone-context /tmp/aspeed-demo/remote.git /tmp/aspeed-demo/run1
export ASPEED_CONTEXT=/tmp/aspeed-demo/run1
export ASPEED_FAKE_DIR=/tmp/aspeed-demo/fake      # M0 的外部系统使用 Fake

# 3. 发布与认领
uv run aspeed task create --file draft.md --as m:carol
uv run aspeed task join <ID> --member m:dave --role lead
uv run aspeed views show claimable

# 4. 定时任务与日报
uv run aspeed schedule tick
uv run aspeed daily collect --out /tmp/facts.json
uv run aspeed daily publish --facts /tmp/facts.json
```

运行 `uv run aspeed <命令> --help` 查看全部命令。草稿格式见 `skills/aspeed-intake/SKILL.md`。

## 部署

| 触发 | 运行 |
| --- | --- |
| IM 请求 | 外部框架启动沙箱 → `aspeed clone-context` → Agent 加载 `AGENTS.md` 与 `skills/` → 经 CLI 读写 |
| 定时（如每 10 分钟） | `aspeed schedule tick` |
| 每日 | Agent 执行 `aspeed-daily-sync` SKILL |
| aspeed-context 的 MR | CI：`aspeed validate --context . --base origin/main` |

同一运行实例内，请保持 `ASPEED_RUN_ID` 不变（用于执行租约）。`ASPEED_ACTOR` 记录触发者。

本地可以用 Claude Code 验证：在本仓库启动后，`CLAUDE.md` 会引入 `AGENTS.md`，`.claude/skills` 指向 `skills/`。

## 接入真实系统

1. 为已有 SKILL 写一个满足 `aspeed.adapters.script` 协议（JSON 进、JSON 出）的薄脚本。参考实现：`tests/shims/fake_bridge.py`。
2. 在 adapters 配置中声明实现与能力（`config/adapters.example.yaml`）。
3. 对沙箱项目运行契约测试：`aspeed --adapters <配置> contract run aone --scope <沙箱项目>`。
4. 在 `control/policy.yaml` 中把对应操作从 `dry_run` 逐步切到 `confirm`、再到 `auto`。

如果某个 SKILL 只能由 LLM 调用，改用 Agent 中介模式（`skills/aspeed-ops/SKILL.md`）。

## 目录

```
AGENTS.md / CLAUDE.md    LLM 运行规则
skills/                  七个工作流 SKILL
src/aspeed/              控制层（models / gitstore / ops / sync / adapters / flows / views）
src/aspeed/templates/    aspeed-context 初始化模板
config/                  接入配置示例
evals/                   LLM 环节评测集
docs/design.md           设计说明
tests/                   单元、并发、契约与端到端测试
```
