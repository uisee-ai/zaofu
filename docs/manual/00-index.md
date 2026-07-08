# ZaoFu 使用手册

本手册是 ZaoFu 的**对外使用文档**,自包含、面向所有用户,按"架构总览 → 入门 →
核心 → 观测与计划 → 集成 → 评估"分层组织。

新用户建议先读 [架构总览](architecture.md) 建立整体模型,再从
[01 快速开始](01-quickstart.md) 上手。

## 0. 架构总览

- [架构总览](architecture.md) — 三层架构、kernel truth/state、`zf.yaml` 控制面、核心技术点与任务生命周期(自包含,新用户先读)

## 1. 入门

- [01 快速开始](01-quickstart.md) — 安装、`zf init` preset、preflight 与 `zf start --dry-run`

## 2. 核心使用

- [02 zf.yaml 控制面与运行态](02-zf-yaml-control-plane.md) — 唯一控制面的配置结构
- [03 CLI 操作手册](03-cli-operations.md) — 日常操作命令流程
- [04 Harness 运行流程](04-harness-runtime.md) — 三层架构、任务链路、stuck/orphan/recovery、签收口径
- [05 Skills、Workdir 与 Git Evidence](05-skills-workdirs-git-evidence.md) — 技能分层、worktree 隔离、证据链
- [07 故障排查](07-troubleshooting.md) — 常见故障场景与诊断命令
- [09 ZaoFu CLI 命令参考](09-zaofu-cli-usage.md) — CLI 全量命令参考(reference)

## 3. 观测、计划与诊断

- [06 Web、观测与 E2E](06-web-observability-e2e.md) — `zf web` Dashboard、运行中观测、E2E 入口
- [08 New Task、Agent 与 Squad](08-new-task-agent-squad.md) — Web 新任务入口与 assignment intent
- [13 Plan、Task Map 与 Orchestrator 调度手册](13-plan-task-map-orchestrator-dispatch.md) — 计划到调度的链路
- [14 Delivery Trace 使用手册（zf trace）](14-delivery-trace-usage.md) — 交付追踪与 drift 报告
- [12 Supervisor Inspection 使用手册](12-supervisor-inspection-usage.md) — supervisor projection、attention candidates 与 bounded invocation 信号

## 4. 飞书集成与 Channel 协作

- **[19 Feishu AI-Native 直连 Bridge 使用手册](19-feishu-ai-native-direct-bridge.md) — 当前主入口:`zf feishu bridge --watch` 直连飞书,群/单聊流式问答 + 点按钮审批闭环,无需 OpenClaw / 公网 webhook**
- [15 Channel 协作使用手册](15-channel-collaboration.md) — `zf channel say`、@mention 触发回复、channel.* 事件链(当前可用范围)
- [11 Feishu Automation / Kanban Sync 专题](11-feishu-automation-kanban-sync.md) — 自动化文档与 Kanban 表同步

> OpenClaw 转发链路(旧 `zf bridge openclaw-feishu`)已废弃,统一改用直连方案(19)。
> 历史迁移说明见 git 历史,不再保留在本手册。

## 5. Autoresearch 与真实 E2E 评估

- [10 Autoresearch 使用手册](10-autoresearch-usage.md) — 场景注入、loop、self-repair
- [Autoresearch Orchestrator 使用手册](autoresearch-orchestrator.md) — 外层 supervisor 长程评估
- [Autoresearch Campaign 使用手册](autoresearch-campaign.md) — 批量验收场景
- [16 真实 Codex Provider Preflight](16-real-codex-provider-preflight.md) — `zf doctor provider --backend codex` 预检
- [18 Product Fanout 真实 E2E 手册](18-product-fanout-real-e2e.md) — 维护者 / QA 验证向
