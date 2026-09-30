# S8 Candidate / Product Acceptance 恢复测试窄调研

> 文档定位（2026-09-30）：历史调研，正文“当前/建议/尚未实现”仅适用于各段所记时点，不是最新执行队列。现行方案见 [ZaoFu_TestKit.md](../03_plans_and_steps/ZaoFu_TestKit.md)，执行仍按既有暂停/跳过边界；本次不重跑或改写历史结果。
>
> 当前对齐入口：`tests/e2e/test_zaofu_testkit_s8_e2e.py`。已有本地 candidate/Product Acceptance 测试；不要把回调/fixture 当作真实远端 merge 或新 /issue-merge。

日期：2026-09-10。本文只依据当前工作树源码和测试做事实审查，目标是为 ZaoFu 自身 TestKit 设计 Candidate 与 Product Acceptance 的重启/重放测试；不测试生成产品，不调用远端服务。

## 已确认的接口事实

- `src/zf/runtime/candidates.py::CandidateRebuilder.rebuild` 从 EventLog 和 task-index 恢复已批准任务，写入 candidate manifest，并通过 `locked_path(manifest_path)` 串行化同一 candidate 的重建。
- Candidate 的规范真值仍来自 EventLog 与 task-index；branch、worktree、manifest 是可重建产物。测试不能直接编辑 manifest 来制造通过。
- Candidate rebuild 会执行本地 git 命令并更新 candidate ref；测试必须使用临时 git 仓库，禁止触碰当前 checkout 或远端。
- `src/zf/runtime/product_acceptance.py::bind_product_acceptance_report` 会根据 plan artifact package 解析 spec，校验 workflow/task-map/candidate/target commit 身份，然后以 immutable sidecar 写入报告；同一内容的 sidecar identity 应由 digest 保持稳定。
- `tests/e2e/test_zaofu_testkit_s8_e2e.py` 已分别覆盖 Product Acceptance 缺失/完整/replay 和一次 Candidate 创建，但尚未把 Candidate branch 重启重放作为独立 case。

## 本次测试设计推导

重启边界用“新 `CandidateRebuilder` 实例 + 相同 state_dir/git 仓库”模拟，不重启远端服务。第一次 rebuild 的 commit、manifest lineage 和 candidate ref 是 baseline；第二次只读恢复后再次 rebuild，必须满足：

1. candidate branch 仍可解析，manifest 状态为 `updated`；
2. commit 与 commit lineage 不因重复重放而漂移；
3. source task ref、source commit 和 candidate commit 绑定保持一致；
4. EventLog 允许记录新的尝试事实，但不得产生错误 candidate/conflict/quality_failed；
5. Product Acceptance report 的 digest/ref replay 继续由已有 binding case 独立证明。

## 判定与边界

- 通过：两次重建结果均为 `updated`，同一 candidate commit/lineage 可复现，manifest 和事件身份完整，临时仓库之外没有写入。
- 产品失败：同一输入重放产生不同 commit、错误 candidate 状态或损坏 lineage。
- harness failure：临时 git、依赖或清理失败；不能归因于 candidate 逻辑。
- 本 case 不声称证明远端 PR/merge、并发 CandidateRebuilder 或完整 workflow crash-point；这些仍保留为矩阵缺口。
