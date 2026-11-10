> 状态: active

# 修复 shadow Provider 中断到 RM 重派的状态衔接

授权：LS 监工根代理批准本项最小源修复；基于 dev a4306e96 隔离实施，不修改 LS 运行态。根代理独占 merge 和真实恢复。

真实复现：独立 Verify 因 Provider CLI 版本不兼容中断，operation suspended、attempt retryable；shadow reconcile 又把 expired 改成 failed / lease_expired_shadow_only，RM pending 降为泛化诊断，WRC 只接纳 expired，导致恢复失联。

- 当前 HEAD 真实 Provider-stop caller 后执行 reconcile -> verify: shadow 变体复现失败，enforce 基线保持通过。
- 新 Provider 中断保持恢复身份，兼容旧 shadow failed 行 -> verify: RM canonical pending、受控 executor、WRC exact requested/completed 链及同 operation 新 attempt 贯通。
- 保留授权、target/contract 和重试限制 -> verify: 普通 restart、错误 attempt/run、无 completed、非重试失败、耗尽上限均不能重派；冻结请求及历史失败事实不改写。
- 隔离验证和交付 -> verify: focused caller/共享合同、相关 mock E2E、dev-verify、sentinel 通过，更新实现文档，提交并归档；根代理最终合并。

边界：不改模型/配置，不伪造 Verify 结果，不扩普通 restart 权限，不调整其他恢复入口。

## 实施与验证

- 仅修复 Provider-stop 的确定性恢复衔接；三处源修改分别负责已知故障识别/新状态保留、RM canonical 提案、WRC 准入。普通失败仍诊断，终态残留 metadata 不重新创建恢复。
- 修复前真实调用链：enforce 1 passed，shadow/历史 shadow 2 failed（expired 被改成 failed）。修复后当前 caller 和拒绝测试 29 passed；旧行不被人工修写为 expired。
- `PYTHONPATH=src python scripts/dev-verify.py plan --base dev` 识别 95 个测试文件。默认阈值会扩大为全套，因此遵循 AGENTS 的按影响闭包验证要求，使用原生 `--paths` 分别对 `src/zf/runtime/task_attempt_recovery.py`、`task_pipeline_attempt_recovery.py`、`task_pipeline_recovery.py` 执行 `dev-verify.py run --base dev --workers 2`；分片选择 66/18/22 文件，合并恰为原 95 文件，未改 selector 或删除覆盖。三组分别 1143/230/240 passed。
- 相关 mock E2E、控制面重载入口、文档契约：`tests/e2e/test_task_pipeline_v4_runtime_noise_mock_e2e.py tests/e2e/test_design199_recovery_mock_e2e.py tests/test_instruction_stack_contracts.py tests/test_cli_start.py` 共 54 passed；随后给 Design199 mock E2E 增加新 shadow 与历史 shadow 两变体，独立重跑该文件 3 passed，覆盖真实 RM tick 自动产生 requested、mock transport completed、控制面重载后的 WRC 幂等重派。
- `ZF_PYTHON=/home/min/workspace/public/zaofu/.venv/bin/python bash scripts/dev-premerge-gate.sh`：53 passed，manual docs currentness ok。`git diff --check` 通过。
- 本地收据位于 `/tmp/zf-shadow-verify-redrive-20261110-2309/`；不声称全套 pytest、真实 Provider E2E 或 LS 业务验收完成。

## 真实恢复交接与未扩大事项

根代理先修环境，再只重载 watcher/Web。`zf start --control-plane-only` 保留现存 worker transport；真实 CLI/控制面执行与 LS canonical 写入均由根代理负责。读取 `_pending_task_attempt_recovery_actions` 后，在 `locks/run-manager-tick` 锁内按 task/operation/attempt 精确选取唯一动作，经 `_execute_controlled_run_action` 执行既有受控链。`applied` 只证明 requested；完成需核对 causal completed、redrive_admitted、新 attempt/placement、新 CLI 实际启动，不能靠普通 restart 自证恢复。

此次 RM/OA 未自愈的原因：RM 收到的原故障虽 retryable/owner 正确，但后续 shadow 状态转换把它降为泛化诊断；WRC 只认 expired，二者缺少合法交接。Agent 的文本建议不应替代这一机械状态修复。

未实施的后续观察（不改变当前恢复授权）：

- HTTP400 的模型/CLI 不兼容被上层归为 generic transport_error，尚需评估结构化永久/环境错误分类；当前只恢复已由根代理修复环境的已知中断，保留重试上限。
- 磁盘 CLI 升级后旧进程继续运行；completion nudge 又触发旧 CLI 的同一错误，需后续核查悬挂 attempt 的 nudge 资格与环境恢复绑定。
- Resident Agent recommendation → action 的独立入口可能丢 operation/attempt 等字段；本轮走 canonical pending 的既有受控执行器，不扩该入口。
