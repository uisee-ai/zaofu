# ZaoFu 验证覆盖表

此表登记用例能力，不保存某个候选的通过状态；每次执行另附目标与结果。
所有命令从 ZaoFu checkout 根目录运行。按影响和合同选择，非要求每次全部运行。

| 场景 | 当前入口（pytest 文件/节点） | 当前证明 | 仍需单独证明 |
| --- | --- | --- | --- |
| 启动到交付 | `tests/e2e/test_agent_owned_verification_mock_e2e.py::test_light_profile_mock_e2e_exact_verify` | 受控提案/提交、transport 派发、真实 Git 产物、实际合同探针、准入及终态 | 浏览器提交、OS 启动、真实模型 |
| 重启后交付 | `tests/e2e/test_generic_workflow_complex_mock_e2e.py::test_generic_workflow_replans_once_and_closes_after_restart` | 新 runtime 实例从持久化事实继续，重复唤醒不重复派发，旧 generation 被拒绝，最终 artifact delivery | OS kill/restart、真实 Provider |
| RM 故障交接 | `tests/e2e/test_design199_recovery_mock_e2e.py` | attempt 过期 → RM → mock respawn → redrive 一次 | 新 worker 后续完成；不能单独签收完整恢复 |
| 双 lane 到候选 | `tests/e2e/test_agent_owned_verification_mock_e2e.py::test_light_profile_mock_e2e_exact_verify` | 两个任务同时已派发，隔离 worktree，各自执行合同命令，候选内容与 Verify 目标一致 | 真实 Provider 的同时运行与宿主资源争用 |
| 启动失败/重复启动 | `tests/test_workflow_runtime_start.py` | starting 不冒充 running、并发请求复用 launcher、项目隔离 | 该测试 mock 子进程，不证明宿主实际启动 |
| 验收探针反例 | `tests/test_project_verification.py` | 缺失/错误产物被指定断言拒绝，错误 target 不执行，Skill 分发后资源完整 | AI judge 的误报/漏报需独立样本评测 |
| Dashboard 启动回执 | `web/tests/workflow-start-receipt.spec.ts` | pending 不显示 started、提案参数保持 | 含浏览器 fixture/route mock，非真实后端交付 |

启动和双 lane 由同一个完整 mock 场景覆盖，执行一次即可；它们不是两次真实 Provider
运行。RM 故障交接与 runtime 重启交付是不同场景，不拼接成一个已执行的端到端案例。

## 确定性基线

```bash
uv run pytest -q --no-cov \
  tests/e2e/test_agent_owned_verification_mock_e2e.py \
  tests/e2e/test_generic_workflow_complex_mock_e2e.py \
  tests/e2e/test_design199_recovery_mock_e2e.py \
  tests/test_task_pipeline_fault_matrix.py \
  tests/test_workflow_runtime_start.py \
  tests/test_project_verification.py
```

上述 fixture 使用临时项目、mock transport/launcher 与真实内部合同。依赖缺失而
skip 或 collection 失败必须列出，不能用汇总退出码把未执行条款算通过。

## 前端与更高层

```bash
cd web
npm run typecheck
node scripts/run-unit-tests.mjs tests/workflowStartModel.test.ts tests/workflowProposalModel.test.ts tests/workflowReviewModel.test.ts tests/kanbanSessionEvents.test.ts
```

浏览器、真实进程和 Provider 的环境与执行方法见项目 Skill。其证据必须有同一候选、
Run、操作与结果绑定。环境尚未准备时列出下一步条件，不发送补造成功事件。

其他能力先从 `docs/manual/reference/capability-coverage.yaml` 和
`docs/manual/reference/command-validation-matrix.yaml` 选路，再核对代码与实际测试。
这三条链不代表 ZaoFu 全功能已覆盖；发布验证仍需各能力的验收闭包。
