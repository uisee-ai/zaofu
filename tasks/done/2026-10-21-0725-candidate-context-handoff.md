> 状态: done

# Candidate 验证上下文与修复提交交接

来源：[设计 183](../../docs/design/183-runtime-result-authority-and-recovery-closure.md)。用户授权：继续执行剩余修复和断点验证。

1. 产品 reporter 显式接受当前 candidate reader 输入 -> verify: 原三条 Vitest 真正运行断言，收据保留 flow-discovery/candidate_verify 身份；旧 attempt、错 manifest、脏代码、冒充 task Verify 全部拒绝。
2. 修复写入隔离产品分支，再经受控新代绑定新提交 -> verify: 新 target 必须是原提交后代、ref 精确匹配，保留合同与 plan 分母；旧 snapshot 不变，错误输入不重开 Run。
3. 新 operation 独立执行原命令 -> verify: 当前 HEAD、operation/attempt/manifest 与收据一致，失败如实记录，不以动作收据代替产品通过。

Q01–Q03 外部接入仍归原 active 任务。本任务不得虚构任务 Verify 完成、业务全量完成或真实服务接入。

## 实现与验收

- 产品 `d7d6a94 feat: bind candidate verification to current reader context`，位于独立产品 worktree/分支，未移动原 worker 或 main refs。
- Harness `0944d27 feat: bind approved reader recovery to committed repair targets`；`e23d5f2 fix: preserve compact reader target lineage during recovery`；`6eaf6be fix: preserve bound reader targets during worktree checkout`。
- 提交方法 `3bc073f fix: clarify immutable templates for reader result submission`；retry 合同 `5f2f180 fix: retain full result contracts in legacy reader retries`。技能源与两份分发副本一致。
- TDD：candidate context、repair target、compact legacy target、真实 Orchestrator checkout、legacy retry 合同先 RED 后 GREEN。产品12项回归+66项行为、typecheck/build通过；Harness聚焦100、compact68、checkout80、直接caller156、提交方法158、retry闭包167分别通过（存在重叠，不相加称唯一用例数）。最终serial10、sentinel51、flow-smoke10通过；未宣称含20项已复现基线失败的full suite全绿。
- 现场：e429 operation在固定d7d6a94独立运行四条原命令，规则25、UI7、契约4和三视口browser通过；12项上下文回归另通过。receipt身份真实一致，32份原字节证据已绑定evt-beefef8d27f9。provider未提交导致transport retry后，保留原收据身份/时间，使用当前retry envelope复用，无重跑或伪造新收据。
- 正式提交evt-e9b7ef9cc2e6 → 准入evt-82ee9700cece → operation结算evt-fecc21f5074b。全局结论是有效abstained（完整分片输入不足），不是产品通过；本任务关闭的是上下文/目标/提交交接缺陷。

完整证据与剩余270项未验证AC、Q01–Q03接入条件见[恢复检查点](../../docs/records/2026-10-21-loshu-recovery-checkpoint.md)和[继续活动的恢复任务](../active/2026-10-21-0316-loshu-resume-q01-q03.md)。
