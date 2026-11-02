# 上下文、Artifact 与 Handoff

[English](context-handoff-artifacts.en.md) · [运维索引](README.md)

> 目标：Agent、session、provider 或 stage 变化时，不靠聊天记忆猜当前目标、合同、代码
> revision 和证据。完整语义通过 immutable artifact/sidecar 保存，查询和 briefing 只交付
> 当前 attempt 所需切片。

## 三类内容不要混淆

| 内容 | 当前载体 | 作用 |
|---|---|---|
| Current identity/state | Task/Session/TaskAttempt stores、EventLog verdict/ref | 决定哪个 run/generation/attempt/contract current |
| Complete semantic body | artifact、sidecar、accepted package | 保存 plan、Task Map、result、evidence、diagnostics、conversation body |
| Query/context projection | SQLite catalog、StatePacket、Goal Dossier、briefing section | 查找、聚合和有界交付，不成为 authority |

Event preview、文件名、Web badge 或 transcript 片段不能替代 required artifact body 和
digest 校验。

Task 的 Summary、Activity、Evidence、Advanced 与 Agent 资源视图提供同一交付上下文的
不同只读切片；切换页面不会改变 canonical state：

![Task 上下文、Artifact、Handoff 与 Agent 资源动态演示](../assets/task-context-handoff.webp)

## Attempt Source Manifest

每个 provider attempt 应收到显式输入清单：

- current TaskContract 和 revision；
- target/source revision；
- Task Map generation 和 Plan Package；
- stage required reads；
- previous admitted result、negative feedback 或 recovery delta；
- artifact identity、locator、digest、access scope 和 retention；
- actor/role/provider/purpose。

读取正文前必须授权 exact occurrence；知道一个 ref 存在不代表当前 Actor 可以 hydrate。

## 查询当前上下文

Agent-facing Task capsule：

```bash
zf ctx --task TASK-ID --mode implement --json
zf ctx --task TASK-ID --mode check --json
```

Attempt 输入和 read 状态：

```bash
zf attempt inspect ATTEMPT-ID
zf artifact list --attempt ATTEMPT-ID --json
zf attempt missing-reads ATTEMPT-ID
```

读取授权输入的有界正文：

```bash
zf artifact read \
  --attempt ATTEMPT-ID \
  --source SOURCE-ID \
  --artifact ARTIFACT-ID \
  --max-chars 12000
```

该命令只能在已派遣 attempt 的 Worker 上下文中执行，runtime 会注入 attempt-scoped
credential；普通 operator shell 会 fail closed，并追加拒绝审计事件。成功读取会追加 read
evidence。缺 required read 时应先补读或执行同 attempt protocol repair，
不能把“Agent 没读输入”误路由成产品语义 rework。

## Artifact Catalog 与 Lineage

SQLite catalog 加速 object/occurrence/lineage 查询，但不是 current selector：

```bash
zf artifact catalog list --help
zf artifact catalog show --help
zf artifact catalog lineage --help
zf projection status --json
zf projection doctor --projection artifact-catalog --json
```

同一 content digest 可以有多个 occurrence。权限、Task、stage 和 event context 属于
occurrence，不应错误 union 到 content object。Catalog stale/corrupt 时，dispatch-critical
路径仍需 canonical resolver 或 fail closed。

## Handoff 与 Session Continuity

人类可读或 Agent resume packet：

```bash
zf handoff --format md
zf handoff --format state-packet --task TASK-ID --score
```

可靠 handoff 至少包含：

- current Task、Run、attempt、dispatch 和 owner；
- objective、contract、scope、non-goals 和 acceptance；
- target revision、worktree/branch evidence；
- completed work 和 admitted results；
- unresolved feedback、required reads 和 next action；
- refs/digests，而不是复制全部 transcript。

Provider session resume 保留对话连续性，但不拥有 Task truth。Session 被回收、换 Provider
或换 Agent 后，新的 briefing/context envelope 仍必须从 canonical facts 重建，而不是把
旧对话当作 current contract。

## Freshness 与 Currentness

检查一个结果能否接力时，至少核对：

1. run/Task/attempt/operation identity；
2. Task Map generation 和 contract revision；
3. target/source commit；
4. artifact digest/schema；
5. admitted result event/ref；
6. required-read ledger；
7. superseded/terminal 状态。

任何一项不一致时应显示 stale diagnostic、修复引用或创建新的 attempt，不得让迟到结果
覆盖 current 结果。

## 数据与权限边界

- 大 stdout/stderr、provider output、conversation body 和 context package 放 sidecar，不塞入事件预览。
- Artifact read 受 PathGuard、access scope、actor/purpose 和 retention 约束。
- SQLite 可删除重建，不能由 Agent 写入并影响调度。
- Required artifact hydration 失败应 fail closed；可选 UI sidecar 可 degraded 展示。
- 跨 Project 不自动传播正文或 memory。

## 完成定义

一次跨 Agent/Session Handoff 完成需要：接收方能在不读取旧完整 transcript 的情况下，
准确得到 current Goal/Task/Contract/Target、必须读取的输入、已接纳结果、未闭合反馈和下一
动作，并留下 required-read 证据。

## 相关

- [观察一次交付](observe-delivery.md)
- [Skills、Workdir 与 Git Evidence](../05-skills-workdirs-git-evidence.md)

## 输入成本、任务预检与阶段工作记录

Plan Preview 的“任务输入”页按固定 Plan 显示 AC、Task、来源/图片、角色选择和声明缺口；
“完整原文”保留审阅入口。未提供输入、未映射来源或结构正常都不能视为语义覆盖通过。
生产者可用 `zf plan inspect-inputs --bundle <端口文件映射.json> --task <id>` 预检，
每次最多 8192 字节，沿 next_offset 继续；这不修改 Plan 或批准状态。

Task Inputs 的“查看输入成本”对应 `zf artifact costs --attempt <id>`。
正文交付、相同范围重读、声明的 initial/recovery/retry 原因和目录观测分别计量。
`artifact read --reason recovery` 只是用途标注，不放宽必读，不产生完成权。
Provider input/cache/output 与估算、上报、账单费用来自该 operation 的既有 usage；
未观测保持 unknown，不能按字节推算 token 收益。
`costs --compare <前次报告.json>` 只允许相同消费者、材料/选择、合同、Package 和必读政策对照。
索引构建耗时没有在线逐次归因；`scripts/eval-context-inputs.py benchmark` 可运行离线宿主基线。

阶段工作记录使用现有提交凭据；示例文件须位于当前绑定 workdir：

```json
{
  "completed_actions": ["实现重复请求校验并运行指定测试"],
  "pending": ["补充失败路径检查"],
  "next_step": "核对 AC-1 的失败状态并提交正式结果",
  "evidence_files": ["test-output.log"]
}
```

运行 `zf artifact work-record publish --operation <id> --file progress.json --request-id <唯一 ID>`。
记录最多 16 KiB，最多 16 项证据，每项 UTF-8 文本最多 64 KiB；拒绝工作区外路径、
符号链接、非普通文件、.git/.env 路径和已识别的敏感材料。当前身份、canonical lease、
工作区和 manifest 必须有效，此路径不借用只读 capability。
证据与记录先写不可变 sidecar，再由 EventWriter 发布 ref；未发布 sidecar 不会被恢复。
同一请求重试须保持正文和证据 digest 一致，不覆盖旧记录。

`artifact recover`、实际 recovery briefing 和 handoff projection 提供当前有效记录摘要；
`zf artifact work-record show --operation <id>` 返回有界正文页，续页须带首份 sha256 的
`--digest` 和 `--page-start`。记录损坏、Task/输入版本改变或 operation 结束时不可复用。
记录是 worker 未独立验证的陈述，不是正式结果、lease 或当前模型记忆证明。

离线评测可运行 `PYTHONPATH=src python scripts/eval-context-inputs.py retrieval`；
其合成样例仅测显式定位和权限边界，不能证明真实项目语义召回。
工作记录证据用 `artifact work-record show --operation <id> --evidence <从 0 开始的序号>`
读取固定副本；续页同样必须绑定返回的 digest。
