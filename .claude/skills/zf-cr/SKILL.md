---
name: zf-cr
description: "Read-only ZaoFu code review: scoped files or diffs, architecture and state/event boundaries, or full review for refactor planning. Use for ZaoFu development-readiness and regression reviews; not for implementation or unrelated writing."
---

# ZaoFu Codebase Review

以当前代码、直接调用方和测试建立可复核的审查结论。默认中文。
技能名称和既有 runtime/profile/channel 引用保持 `zf-cr`。

## 范围与只读边界

先读项目 `AGENTS.md`，确认用户目标、当前 checkout 和审查范围。
审查不修改或创建文件、不运行会写缓存/lockfile 的测试、不发 ZaoFu 事件、不改 runtime state。
可用只读查询或确认无外部副作用的纯内存复现验证假设，并禁用字节码/缓存写入。
需要落盘、联网或调用业务副作用的验证，先给方法；有相应授权和隔离条件才执行。
`git status --short`、定向 `rg` 和文件读取足够作为起点。
用户只请求审查时，不自行实施、归档任务、提交或启动服务。
仅在用户明确要求时拆分并行审查；普通审查本地完成。

## 选择模式

| 用户目标 | 模式 | 读取与停止条件 |
|---|---|---|
| 指定文件、diff、函数或回归问题 | scoped | 目标 -> 直接调用方 -> 跨越的共享合同和相关测试；证据链闭合即停止扩展 |
| 一般 codebase review / 开发前建立上下文 | architecture | 读 [架构检查单](references/architecture.md)，建立系统地图后优先检查高风险边界；报告实际覆盖 |
| 明确 full review、全面扫描或 refactor planning | full | 读 [架构检查单](references/architecture.md) 和 [全面审查流程](references/full-review.md)，完成覆盖审计与缺口补查 |

scoped 不默认读取全面流程、全历史或无关子系统。若目标依赖状态/事件/编排边界，
再按需读取架构检查单相关章节；扩展时说明发现了什么依赖。
没有明确目标的一般审查默认 architecture，不擅自声称 full。

## 文档与代码锚点

- `zf.yaml` 是唯一控制面，实际状态位置来自 `project.state_dir`。
- 完整仓库优先使用 `docs/design/00-index.md` 和
  `docs/design/142-layered-runtime-authority-and-orchestration-modes.md`。
  先检查存在性；公开导出缺失时用 `docs/manual/00-index.md`、
  `docs/manual/architecture.md` 及当前代码/测试继续，明确设计上下文缺口。
  doc 44 is only a historical scoring snapshot，不覆盖当前权威。
- `.claude/rules/` 按目标路径读取已有规则；文档不存在不等于运行行为有 bug。
- 文档只能解释意图，必须沿实际 CLI / runtime / Web caller 核实接线。
  文件迁移用符号搜索定位新 owner，不依赖历史行号或文件体积。
- 历史仅用于解释目标变化：先 `git log -20 -- <target>`。
  不无条件扫描全历史；full 模式的扩展条件见全面审查流程。

## 所有模式的核心判据

- Kernel 决定确定性状态迁移；agent/skill/prompt 提供语义判断和意图。
  配置的 orchestrator role agent 不是 Python `Orchestrator` 的替代状态机。
- `EventWriter` / `EventLog` 管 occurrence/order/causation/verdict/ref；
  `TaskStore` / `FeatureStore` / `SessionStore` / `RoleSessionRegistry` 管 canonical current state。
  大语义体与证据由 sanctioned atomic artifact/sidecar writer 持久化并绑定 ref/digest。
- 单一 task `contract`；不要引入第二 task schema 或绕开确定性完成 gate。
- integrations / Web projection 不能直接写 canonical business state；变更必须经过
  token-gated controlled action。检查实际 caller、权限、项目归属和失败路径。
- `skills/` 是源码；provider 目录是分发包。活跃 workdir/worktree 可能有未提交候选，
  不能当可删投影。progress/cost/diagnostics 等按实际 owner 区分，不能统称真相。

## 证据与输出

每个 finding 给出严重度、可定位的 `file:line`、触发条件、实际影响和验证方法。
沿输入 -> 分支 -> 调用方 -> 可观察结果论证；区分 confirmed / inferred / uncovered / known defer。
环境敏感的结论要核对实际前提，例如路径规范化、符号链接和依赖版本；
同时检查测试期望是否符合该前提。仅静态阅读不能代替这些可只读核验的证据。
仅列新问题；已知大文件不是新 bug，未检查的模块不能标已通过。

scoped 输出 findings + 调用链/测试依据 + 未覆盖项即可，不要求系统总览或固定数量建议。
architecture 增加简短系统地图和实际 coverage；full 按全面流程输出完整矩阵。
没有确证问题时直接说明，并限定于已检查范围；不能以“测试存在”代替测试已执行。

## 外部真跑证据（可选）

仅当用户提供或明确指定 `ZF_VALIDATION_PROJECT` 时读取该项目。
先用 [路径解析器](scripts/validation_paths.py) 从显式项目的 `zf.yaml` 解析路径：

```bash
python <skill-directory>/scripts/validation_paths.py "$ZF_VALIDATION_PROJECT"
```

需要当前 ZaoFu Python 环境。解析器只输出路径，不读事件内容、不建目录；
缺少配置或环境时记录 blocker，不猜测 `.zf` 或回退 `/path/to/project`。
然后按需要只读有限事件尾部和相关 task，记录证据范围并避免暴露敏感内容。
不要从当前会话继承的 `ZF_STATE_DIR` 推断另一个验证项目的位置。

## 分发与验证

修改 canonical `skills/zf-cr/` 后同步整个包到 `.codex/skills/zf-cr/` 和
`.claude/skills/zf-cr/`，包括 references/scripts/agents。
`tests/test_review_skill_package.py` 验证包一致性、引用闭包及真实 materialization/briefing。
`zf-tool-skill-parity` 审查的是目标 Agent 的工具/技能兼容，不是本仓库的同步器。

若用户只要可复用命令，先核对本机 `codex exec --help`；使用 ephemeral / read-only /
approval never，并保留用户已有 provider 配置，除非明确要求独立配置。
不要为了生成命令直接启动 review；不得硬编码项目路径。
