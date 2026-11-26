# 候选 Reader 独立执行命令的证据

在本 attempt 的 `contract`、`target` 和输入索引中核对命令、环境和候选 SHA。合同允许本地执行、同时没有适用的共享收据时，用 `probe_receipts` 登记本轮实际探针。`reusable: false` 约束复用，不能据此推断必须有 `candidate-host-execution`。合同明确指定宿主、容器或媒体时，沿 briefing 指定的授权入口采证。

1. 在 Reader 获准的工作目录用 `git rev-parse HEAD` 核对冻结 SHA。独立执行每条合同原命令，保留实际工作目录、原生调用 ID、退出码与输出。Codex 的 JavaScript 适配器可按下面的形式透传完整结果，`command` 和 `candidateCwd` 分别取自本次合同和实际工作目录。

   ```javascript
   const result = await tools.exec_command({cmd: command, workdir: candidateCwd});
   text(result);
   ```

   若命令仍在运行，沿返回的 session ID 等到进程退出，保留实际的分段输出和最终结果。输出超长时按合同允许的入口保存完整输出。仅传 `result.output` 会丢掉退出码。外层调用完成、测试输出中的 OK 或作者自述均不能还原内层进程状态。

2. 用 briefing 的 CLI 和 state dir 定位本路 PostToolUse 事件。以下为可替换角色和 ID 的只读命令。

   ```text
   zf events --actor <role-instance> --type codex.hook.post_tool_use --last 20 --index --json --state-dir <state-dir>
   zf events --id <hook-event-id> --json --state-dir <state-dir>
   zf events --id <dispatch-event-id> --json --state-dir <state-dir>
   ```

   当前索引按 actor 和类型过滤，可能包含同角色的其他 attempt。核对 hook 的 `causation_id` 是否指向本次派发，并比对派发中的 `operation_id`、`attempt_id`、目标及命令工作目录。可用时同时核对 session、turn 和原生调用 ID。`task_id` 可能为空。读取选中事件的内联输出，或沿获准的 `refs.raw_output` 核对内容与摘要。hook 缺少退出码时，结合本次授权可查、与该调用匹配的原生完成结果。索引摘要不能充当输出。当前查询没有按 operation/attempt 直接过滤的参数。`artifact handoff-evidence` 只用于已绑定的前序交接。

3. 逐条填写冻结的 `command_id`、`command`、`command_digest` 和 `target_commit`。`exit_code` 来自进程完成结果。`evidence_refs` 引用实际查到并核对的命令事件、获准的原生完成记录或其他当前合同认可的持久证据，在 `requirement_results` 中说明输出实际证明的断言。只写真实可回读的引用，原生调用 ID 本身不自动成为可提交的引用。独立探针的 `host_execution_receipt_ids` 留空。读者的工作记录可保存证据位置，但不能代替原生执行事实。

标准输出和标准错误有各自的断言时，逐项区分证据。冻结测试若分别捕获两条流并断言其内容，可引用该测试在本目标实际执行且通过的回执及输出摘要，同时标明测试名和对应断言。该摘要属于测试回执。断言要求某条流的原始字节时，使用合同允许的分流探针或独立产物，引用对应流的定位符及按原始字节计算的摘要。合并日志无法证明单独的 stderr 字节；即使预期 stderr 为空，也不能用合并日志的引用配上空串摘要。缺少已授权的分流证据时，将原始流声明保留为未知，单列已有测试证明的行为。

缺少必需的退出码或可审计输出时，先核对当前授权的绑定记录。如果仍缺失且命令允许安全重跑，仅补跑这条并完整采集；否则报告具体证据缺口与采集负责人。
