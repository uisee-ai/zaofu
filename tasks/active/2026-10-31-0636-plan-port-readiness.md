> 状态: active

# Plan 工件大小预检与监工返修反馈

来源：Owner 已授权监工遇到问题记录、修复、验证并继续。dl 的 93 任务 Plan 在 Planner/独立 Critic 完成后，因 5,946,608 字节的 execution_spec 超过 sidecar 4 MiB 上限而被 Package 拒绝，事件 evt-d3fa619cec1c；返修到顶事件 evt-c010726fb6bf。前置校验直接 read_bytes，未遵守同一大小边界。

范围：在新 worktree 复现并让 Plan port 前置校验使用既有 sidecar 上限；不增大安全限制。受控 stage-replan-new-generation 支持可审计的 operator 补充反馈，保留原诊断、拒绝无效输入及冲突重放。具体业务修订由 Planner 决定。

实现说明：[Plan 工件字节边界与监工反馈](../../docs/impl/plan-port-readiness-and-operator-feedback.md)。

- 超限引用/内联工件在 producer validate 与 Critic 派发前拒绝 -> verify: 同一字节边界、真实调用者回归及无状态副作用。
- 合法边界工件仍可被 Package hydrate -> verify: 引用和 canonical JSON 的 UTF-8 大小与消费者一致。
- 已授权新代际携带原诊断和监工反馈 -> verify: controlled action 到 Planner rework context 的测试；相同重放幂等、不同反馈重放拒绝、坏输入不重开 Run。
- focused closure / sentinel / deterministic flow E2E 通过后提交归档、合并并清理本 worktree，再在 dl 受控恢复；保留全部原始工件和失败记录，不 push。

验证补充：自动闭包暴露两个旧 delivery 测试夹具缺 Git 基线，在未修改 dev 7c755263 上同样失败（/tmp/zf-workflow-plan-baseline-20261031.log）。补齐隔离仓库的命名分支和空初始提交，不绕过 Git readiness。Generic Workflow 的三项 clean-relative-path 失败属于已复现的既有问题，保留 backlogs/2026-10-31-0532-generic-workflow-artifact-ref.md 的明确触发条件，不弱化路径安全。

最终验证：20 个明确调用闭包模块（含 Package/Plan approval mock E2E）240 passed；dev-verify contract tier 51 passed；run-flow-smoke 10 passed；Ruff 与 git diff --check 通过。此前 69 文件自动闭包 1207 passed、2 skipped（排除已证实的 3 个 Generic 基线失败）。最后一个 handoff 字段使 selector 选择 97 文件并回退全库；已停止过宽全库进程，改由上述 240 项直接闭包覆盖，不声称 full pytest 通过。

证据：/tmp/zf-plan-port-final-focused-20261031.log、/tmp/zf-plan-port-final-smoke-20261031.log、/tmp/zf-plan-port-final-auto-20261031.json、/tmp/zf-plan-port-verification-20261031.json。真实 dl 候选只读复现得到唯一 plan_port_too_large（execution_spec 5946608 > 4194304），记录 /tmp/zf-plan-port-live-repro-20261031.json。
