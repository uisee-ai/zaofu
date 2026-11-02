# C2/C3：角色输入入口统一

> 状态: done

用户已批准“拆分成上面说的5项backlog 继续实施”。基线 `1a1989cc`，本批分支 `wip/codex-20261102-context-198-production`。
来源：[设计 198](../../docs/design/198-context-index-write-read-resume.md)。

在首次派发与恢复的共同 briefing 路径给出角色输入范围、必读政策、目录及受控补读命令。Impl/Task Verify 沿用已批准选择；任务评审、Candidate 与 Goal 按实际 profile 解释，保持既有必读义务。

- [x] 同 Task 的 Impl/Verify/任务评审 -> verify: 来自绑定 manifest/profile，Verify 不继承 Impl 回执；首次 briefing 与恢复入口一致。
- [x] Candidate/Goal 与旧版输入 -> verify: 明示候选/目标范围和未知信息，不因角色显示名推断成单 Task，也不擅加 judge selection。
- [x] CLI/实际 dispatch caller -> verify: 可用当前凭据定位必读，目录不扩大 ACL，不重复灌入整份正文。

## 验证与完成记录

实施后记录实际层级、结果、实现提交。真实 Provider 评测、8+8、FTS、delta 不属于本批自动化通过声明。

## 本批实现与评测

已接入 CLI、runtime 调用者与只读 Web；见 [离线评测记录](../../docs/records/2026-11-02-context-198-production-evaluation.md)。
真实 Provider 必须等待第三批、第四批均完成后再启动（用户本轮追加要求）；当前只测离线检索与确定性合同。

## 实际验证

- `scripts/dev-verify.py plan --base dev --json` 无映射错误，514 个受影响测试文件；按影响闭包执行 xdist 8，而未按文件数直接运行全仓测试。
- 最后闭包运行：7297 passed、9 skipped、1 failed；唯一失败为新增 briefing 在 xdist 更长路径下超过原 12 KiB 预算 9 字节。收敛重复提示后，artifact ledger/role inputs、实际 injection/recovery、task pipeline 与 agent-owned mock E2E 调用闭包 **101 passed**，保留原预算门槛。
- 新功能与回归专项 **46 passed**；最终工作记录幂等/证据检查复测 **14 passed**。
- `dev-verify.py run --base dev --skip-tier deterministic --skip-tier web`：serial、sentinel **53 passed**、flow mock E2E **10 passed**；Web typecheck/unit 已由前轮自动验证通过，最终 `npm run build`（含 typecheck / bundle gate）通过。
- Docker `mcp/playwright:latest`：Task Inputs / Plan review 共 **14 passed**，包括真实隔离 API、输入成本、来源未知、导航不完整和迟到响应隔离。
- manual docs currentness、技能三份同步及 git diff 检查通过。无真实 Provider / host E2E；运行中不变更 FMS/ls。
- 影响闭包排除了既有环境传感器 `tests/e2e/test_channel_group_product_mock_e2e.py::test_channel_prd_evaluation_task_workflow_run_and_receipt_continuation`：前批已在 pristine dev 869f7aa0 复现本机 Claude 未登录导致 409。本批不调整该基线，不将此项记为通过。

实现提交：`a33fdd3b feat: add task input production review and recovery accounting`。
