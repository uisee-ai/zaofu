# C1：输入成本与交付基线

> 状态: done

用户已批准“拆分成上面说的5项backlog 继续实施”。基线 `1a1989cc`，本批分支 `wip/codex-20261102-context-198-production`。
来源：[设计 198](../../docs/design/198-context-index-write-read-resume.md)。

关联当前 attempt 的授权来源、读取与输出账本、operation usage；新增有界成本报告和对照入口，区分来源/选择/索引/交付字节、重复页、恢复、独立角色、Provider 实测与未知。不得按字节比例伪造 token 归因。

- [x] 当前 attempt 多页/重复/别名 -> verify: 去重口径明确，其他 actor/attempt 不混入；导航不会生成正文回执。
- [x] operation usage 有/无/重复样本 -> verify: 有证据才显示实测 input/cache/output，估算费用与 Provider 上报分离，未知不记 0。
- [x] 同材料及验收范围前后对照 -> verify: 身份/选择/验收绑定不兼容时拒绝收益比较；CLI/API/Task Inputs 可查看报告。

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
