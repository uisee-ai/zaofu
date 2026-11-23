# 新 Plan 接续后停止旧 Candidate 宿主探针

> 状态: done
> 实现: `bf0edafc fix: stop superseded candidate host probes after plan handoff`

授权来自持续监工中的“遇到问题修复问题、记录后恢复”。仅修复已复现的旧恢复继续执行；不改产品语义、不重置无人资格，不替当前 Worker 提交结果。

## 证据与原因

8×8 Run `workflow-ddd320aa0dc7c2ef` 的旧 Reader
`wop-prd-lanes-verify-verify-lane-6-trig-evt-485533a0--daffe72bb2dd`
在新 Plan `evt-294e5e7db2be`、批准 `evt-bb8304ee4509` 后仍产生
`evt-e21da0832206` 等宿主校验完成事件。不是同一命令重试，是失效候选继续消耗执行预算。
`candidate_host_binding` 只查最新 freeze，没有采用 Candidate Plan recovery 的后继权威检查。
当前没有发现新候选污染或新任务阻塞。研究记录：`/tmp/zf-eight-postapproval-research-20261123.md`。

## 验收

- 复现新 task map 后旧候选仍可 preview/apply -> verify: 修复前回归测试失败。
- 共用后继权威检查，覆盖自动 reconcile 与 apply -> verify: 新 Plan 后无旧探针副作用。
- 保留正常恢复与历史证据 -> verify: 不同 Run、非权威事件、候选之前的 Plan 不误伤；已有 receipt 字节不变。
- 当前 Task 接续继续 -> verify: 同一个 Run Manager tick 闭包中的 Task 探针仍可执行，旧 Candidate 不再执行。
- 验证并提交 -> verify: 定向闭包、sentinel 与 mock E2E 通过；记录准确测试层级与实现 commit。

源码以 live `500a3ad2` 为基线；独立 worktree，不改 dev。部署另保留检查点与实际加载版本证据。

## 实施与验证

- `candidate_recovery_superseded` 共用 Run 内后继权威判断，host binding 与 Plan recovery 使用同一规则。
- 回归覆盖旧 preview/apply 拒绝、不同 Run/worker-origin 不误伤、历史 receipt 不变、候选前的 Plan 仍有效、RM真实 tick 内当前 Task 继续执行。
- 修复前两个核心 preview 用例明确 DID NOT RAISE；修复后首轮45 passed。
- dev 基线计划识别439个历史变更文件、1493个测试文件；这是本 worktree 继承的前轮已验证变更，不重复把本次窄修复作为全量验证。
- `dev-verify.py run --base dev --paths` 显式限定本次5个文件，选择58个测试文件的调用/合同闭包；额外声明 host binding、host verification、receipt/consumption 与 Candidate recovery mock。
- 定向自动层：844 passed / 7 skipped；premerge sentinel：53 passed；flow mock：10 passed。未执行本轮全量 pytest 或另一个真实 provider Run。
- 原Run真实账本只读重放：修复代码对旧 operation 返回 `candidate recovery target superseded`。
- 精确命令、计划与回执保存在 `reports/obsolete-candidate-probes/`。
