# delivery route 终局基线：有限只读测试设计调研

> 文档定位（2026-09-30）：历史调研，正文“当前/建议/尚未实现”仅适用于各段所记时点，不是最新执行队列。现行方案见 [ZaoFu_TestKit.md](../03_plans_and_steps/ZaoFu_TestKit.md)，执行仍按既有暂停/跳过边界；本次不重跑或改写历史结果。
>
> 当前对齐入口：`tests/testkit/issue_route.py`。已存在正式 route 驱动及后续修复；K596 hook 来源已另有完成记录，旧宿主诊断不代表重启后的认证状态。

> 2026-09-14；research 后台任务。仅静态读取当前源码、测试与已有 `cli_workflow_coverage_research.md`；未运行测试、CLI、服务、容器、Provider 或网络。以下是待实现并实跑的方案，不是通过报告，不登记新 Issue。范围以 issue 默认 route 为主，终局是机械证据闭合，不评生成产品质量、不执行远端 merge。

## 1. 最小真实路径与当前缺口

- 直接延伸 `tests/conformance/test_zaofu_testkit_s9_fixrun.py::test_runtime_issue_fix_run_dispatches_implementation_to_provider`：它已经用 `_service/_execute` 发起真实 `workflow-request → workflow-submit → workflow.invoke.requested → Orchestrator.run_once → fanout.child.dispatched`，但止于首次派发（同文件约 411 行）。
- `_service` 实际加载 `IssueFlow(backend=mock, lanes=1) + ZfConfig`；`_execute` 调真实 `ControlledActionService._execute_action`，绕过的是 Web transport/token 接入层，不是 workflow 的 request/proposal/submit 准入。不得称其为 HTTP 或 CLI 授权闭环（`tests/test_control_actions_workflow_request.py:32,68`）。
- 官方默认 issue 是 `topology: light`，不是 v4 task-pipeline。源码 `src/zf/core/config/workflow_profiles.py::expand_issue_flow/_expand_product_flow_light/_flow_kernel_lane_pipeline`（1142、1390、958 行）给出固定单 lane；`tests/test_light_flow.py::test_light_pipeline_materializes_candidate_chain` 验证共享 light 编译规则。
- 正常链为：`issue-lanes-impl(task_map.ready) → candidate.ready → issue-lanes-verify → test.passed → flow.goal.closed → issue-lanes-final → goal.closure.synthesized → run.goal.completion.claimed → run.goal.completed`。具体 child event/role 从加载后的 stage 与实际 manifest 读取，不抄旧事件名。`candidate.ready` 在此位于 verify **之前**；per-lane streaming fixture 不能替代该链。
- `judge.passed` 是 Goal Closure 的兼容投影，`authority=compat_projection` 不构成成功 run terminal；`goal_closure_runtime.py::process_goal_closure_result` 和 `goal_completion_gate.py::maybe_complete_run_goal` 为实际 owner，`tests/test_goal_closure_runtime.py::test_passed_compat_projection_is_not_run_terminal` 为反例。

## 2. 建立一个有限 fixture，保留正式准入

1. 临时独立 repo，初始 commit 包含一个可直接用本机 Python 标准库运行的回归脚本和小源文件；固定 local git user 与 `main`，不设置 remote。复用 S9 `_git/_init_product_repo` 的本地模式，但避免为了两行断言引入嵌套 pytest。首次 request 前完成 fixture 的源码、配置和 baseline。
2. 从 `_service` 提取其两份 YAML 的原形，仍调用真实 `load_config`；在 `ZfConfig.spec` 声明 custom `project.state_dir`、`goal.enabled: true`、`runtime.workdirs.enabled: true/mode: worktree` 与 `runtime.git.candidate_base_ref: main`，配置真实 `quality_gates.<name>.required_checks`。禁用自动 ship 的两项开关并保持 issue 默认 `delivery_policy=report_only`；不使用 `allow_unverified_candidate`、关闭结果准入、换成简化 stage 的手法。
3. 回归命令使用绝对 `sys.executable` 加 repo 相对脚本，例如 `<python> tests/verify_issue.py`，显式超时、只访问临时 repo。命令在配置中声明后，light 合成会继承为 `verification` 和 `validation.commands[id=light-verification-N, owner=impl_self_check, tier=task_non_smoke]`；不要在提交后直接覆写 TaskContract（`light_flow.py:55,443`）。
4. 若需要“已有看板 Task”分支，在 request 前通过 `TaskStore.add` 建 Task，契约包含真实 `behavior/verification/verification_tiers/owner_instance` 与有效 spec 引用或明确允许的 `spec_skip_reason`。最小主例可以保留 S9 的无显式 Task 输入，让正式 submit/light/task-map materialization 负责生成；不要再手造 writer Task 或 task-map。对已有 Task 的 revision/binding 更新必须由 `TaskContractAuthorityService` 承接（`workflow_start_inputs.py::ensure_workflow_managed_task`）。
5. proposal 必须回传其真实 `intake_ref/request_id/proposal_ref/proposal_digest`；预期 `proposal_ready` 后尚无 invoke，submit 后恰好一个 invoke，且 route、需求 revision、origin digest 与 request projection 一致。可保留 fixture 的 `allow_missing_env=True`，但报告必须注明它豁免环境预检，不能用于证明依赖存在。
6. 用 `_RecordingTransport` 只收录 `send_task(role, briefing_path, prompt, context)`；主循环每次重新读 ledger、有限步调用 `run_once`，每一阶段首先断言真实派发记录与收到 briefing，才允许测试注入该 worker 的结果。`tests/e2e/generic_workflow_complex_support.py:76` 无 Provider/进程调用。

## 3. 正常输入与 artifact identity

- 入口：runtime 的 `light_flow.py::maybe_synthesize_light_task_map` 生成且去重 task-map，开启 goal 时自行补 `run.goal.started`（168–329 行）。必需 ports 由 `_light_required_plan_ports` 指定 `issue_spec/goal_claim_set/task_map/planning_result`；draft matrices 不可伪称 ready，真实 artifact package/admission 应保留。
- Writer：读取实际 child workdir，只改批准 scope 中的小文件，提交到该 worker 本地分支；注入 `dev.build.done` 的合法 worker 事实。必须取真实 `fanout_id/child_id/child run_id/dispatch_id/role_instance/source_branch/workdir/source_commit`，工作流 correlation 使用 invoke 的 run identity；child attempt `run_id` 不可误当 workflow run id。
- Writer/verify 共用的 currentness 字段以 `src/zf/runtime/task_contract_snapshot.py::TASK_CONTRACT_CURRENTNESS_FIELDS` 为准：workflow/task、authority/request/contract revision、execution_owner、origin digest、task-map generation、base_commit/task_ref、plan package id/ref/digest；另带真实 contract snapshot ref/digest。不可硬编码 `trace-1`、`F-11111111` 或 SHA。
- Candidate：由真实 `TaskRefManager`/fanout aggregation/`CandidateRebuilder` 建候选；不能测试端写 candidate-ready。`candidates.py::_run_quality_gates/_quality_checks` 在候选 worktree 真实运行命令、写 quality stdout/stderr 证据；它优先 TaskContract 命令，配置只作适用 fallback（1026、1573 行）。应对实际候选树独立 rerun 一次有限回归，确认真实 target 与命令结果。
- Verify：等 `candidate.ready` 驱动 reader 派发，hydrate runtime 生成的 contract/target snapshot；`verification_result_template` 可提供 schema 形状，但实际 `probe_receipts/requirement_results` 必须由这次受审 SHA 的真实命令结果填充，记录 command id、target_commit、evidence refs、acceptance id/owner/tier。参考 `fanout_briefing_runtime.py:238–455`，不照搬 `_complete_verify` 的 `mock://lane-verify/*`。
- Reader 输入：读取真实派发生成的 attempt-source manifest 与 required-read policy，调用 `read_attempt_artifact` 并 `seal_read_ledger` 生成收据；复用 `generic_workflow_complex_support.py::_prepare_reads` 的真实读取/封存 API，但**不复用它新建替代 manifest/policy 的部分**。`goal_closure_bridge.py::_goal_closure_read_contract` 要求 pinned claim-set、closure fact、plan package/ports 和已准入上游结果。
- 结果入口随当前派发协议：若签发 semantic submit，使用 `SemanticResultSubmitService.submit` 或真实 `zf result submit` 加当前 role/credential/operation 与签发 scratch；若允许 legacy child event，则 `EventWriter.append` 后经真实 `run_once` 走结果准入。不要直接调 admission/aggregate helper 代替 caller；不要在 blocking submit 模式用旧 terminal 偷渡（`result_submit.py::_authorized_semantic_result`、`tests/test_result_submit.py::test_blocking_operation_rejects_legacy_terminal_until_secure_submit`）。
- Thin Judge：等真实 `flow.goal.closed` 和 final child dispatch，按 `fanout_briefing_runtime.py:153–231` 形状填 `goal-closure-result.v1`，目标/代际/需求/planning ref、claim-set 与 closure-fact ref/digest 来自派发。`goal_coverage` 按真实 mandatory claims 构造，supporting/input refs 指向已经准入的 verifier envelopes；先完成受控 reads，再交 worker 语义结果。模板中的 passed 只是示例，不是证据。
- 最后持续驱动真实 runtime，让 `goal_closure_runtime`、`goal_completion_gate::_evaluate_active_claims` 与 canonical Task settlement 产生终局。禁止注入 `task.done/run.goal.completed/judge.passed/candidate.ready/test.passed/flow.goal.closed` 来补缺口；这些在本例都应是 Kernel 输出。

## 4. 独立 oracle 与有限 cases

| case | 输入/扰动 | 必须核验 |
|---|---|---|
| I-01 正常 | 一个回归、一个 writer commit、真实 verify、合法 judge | run 范围内实际阶段按序派发；候选 SHA 存在且含 writer 修改；quality 的 command/exit/ref 对应真实执行；每个 admitted envelope 的 digest 可独立 hashlib 重算；一个 completion claim、一个 `run.goal.completed`；对应生成 Task 最终 done；无 ship/remote effects。 |
| I-02 真实质量失败 | writer 合法提交但保留回归失败 | `candidate.quality.failed` 与非零命令证据；无伪 ready/verify/judge/completed。若正常 recovery 会重派，按真实最大重试预算注入同类合法失败，直到真实 blocked/exhausted 或需外部决策；不要把单个 quality failure 叫 run 终局。 |
| I-03 合法语义失败 | 实际 final child 对当前 claims 报 `blocked`，给外部输入原因；或 verify 报失败与合法 rework items | `goal.closure.blocked`/attention 或有界 rework 的因果链；无成功 terminal。`goal.closure.blocked` 本身是暂停/升级，不等于 `run.goal.blocked`；是否自动 run 终局必须实跑确认。 |
| I-04 缺依赖 | 缺 Git baseline；另在 reader 派发后缺一个必需 artifact/受控 read receipt | 前者复用 S9 环境失败 node；后者真实 hydrate/admission 拒绝，错误定位 ref/read，不能有 downstream completion。缺少 artifact 是输入依赖失败，不模拟 Provider 未安装。 |
| I-05 重复 | 相同 submit、相同合法结果事件、终局后重放全部输入 | submit 不多 invoke；每个 operation/child 不多派发/准入/候选 effect；成功 terminal 唯一。同 credential 第二次 submit 允许明确 `duplicate_submit`，不能要求返回成功（`result_submit.py:630`）。 |
| I-06 乱序 | 将未来 verify/judge 的无 operation 结果先于派发；或重试后重放旧 attempt/target 的结果 | 真实准入拒绝/隔离旧事实，不提前解锁；恢复正确顺序后达到真实终局。不要事先造 future canonical operation；可由单独基线录得的旧完整 payload 改为异 run 输入做隔离 case。 |
| I-07 同目录重建 | 分别在 writer 事实已持久化未消费、candidate ready 后、judge 已准入未完成 claim 时丢弃实例，重新 load config/EventLog/Orchestrator | 同 state dir、同 immutable refs 保留，新的 transport 继续驱动；不依赖旧 Python 对象，不重复派发/候选 effect，最终 SHA/claims 与 I-01 等价；已完成后再重建仍唯一 terminal。 |

计数按 `(workflow_run_id, generation, operation/child identity)`，不能只看全局 event type。每例保存 ledger 顺序、TaskStore 终态、候选 git ref/SHA、sidecar digest 与实际命令回执；不要用同一 helper 生成 expected 和 actual。

## 5. 可复用精确 nodes 与安全界限

- `tests/test_control_actions_workflow_request.py::test_request_is_proposed_before_explicit_submit`：proposal/submit/replay 入口；`::test_workflow_start_active_request_is_immutable_and_exact_replay_is_noop`：request revision；`::test_submit_and_reject_bind_exact_current_proposal`：精确 proposal。`::test_terminal_workflow_request_replay_fails_closed` 手写 blocked，仅作 replay 合同，不计 run 失败闭环。
- `tests/conformance/test_zaofu_testkit_s9_fixrun.py::test_runtime_issue_fix_run_without_git_baseline_is_environment_failure`：基线缺失。S9 其它 local merge/helper cases 会跑 local git clone/fetch/merge 与子进程 pytest，不能整组拷贝到默认 route driver，也不是正式 candidate/goal 调用链。
- `tests/test_lane_stage_streaming_runtime.py::_complete_writer/_complete_verify/_fail_verify`：仅复用 payload/contract 模板思路；其 `_state/_start` 手造 tasks/map/入口、关闭部分 schema，且硬编码身份与 mock refs，不作正式准入 fixture。
- `tests/test_call_result_admission.py::test_goal_closure_admission_accepts_current_identity_and_rejects_stale`、`::test_goal_closure_admission_accepts_integrated_task_commit_input`：复用 identity 负例逻辑；该组直接建 operation/fact，仅是局部 oracle，不能移植为 route 已走到 final 的证据。
- `tests/test_light_flow.py::test_entry_inherits_workflow_task_verification`、`::test_entry_uses_config_quality_checks_without_matrix_commands`、`::test_entry_mints_run_goal_when_goal_enabled`：固定 fixture 前提；`::test_entry_minted_goal_completes_on_judge_passed` 是旧式局部 completion，不能顶替 Thin Judge 新链。
- 允许 fake：LLM 的编辑/语义判断输出、Provider transport、外部 sink 的记录/禁止调用。不能 fake：loader/route selection、request/proposal digest、TaskContract authority、dispatch、git task/candidate tree、command exit、快照 hash/target binding、source-read ledger、call-result admission、rework budgets、completion gate、canonical stores。
- Host 边界：recording transport 本身无 host 调用，但 worktree/candidate 真实执行 git 与 quality shell，环境 setup_script 也可执行；temporary fixture 必须为空 setup_script、只含本地有限命令，auto ship/outbound sidecars 关闭。`tests/conftest.py:43,59` 会校验 import provenance 并隔离真实 workspace registry；仍需隔离 cwd、ZF_PROJECT_ROOT/ZF_STATE_DIR、token 与 Provider credential 环境。

## 6. 需实跑确认的阻碍与 PRD 必要差异

- `_service` 默认 goal 关闭，现有 S9 只放 README baseline，没有 verification。若不补配置，缺终局/候选质量命令是 fixture 缺项，不能静态报生产 bug（`GoalConfig.enabled=False`，`schema.py:1943`）。
- 当前 blocking result/artifact package/required-read 合同可能使简单 event 注入在正式 route 被拒；先看实际 briefing/request/manifest，再填合同。缺 `base_commit`、命令不入 snapshot、计划 package 或 admitted input 丢失，需保存 runtime 错误再定位，禁止 mock gate=True。
- issue quality floor 的旧兼容 judge gate 需要 `repro_ref/regression_refs/test_refs` 之一（`orchestrator_module_parity.py::_reject_flow_judge_evidence_gap/_QUALITY_FLOOR_REF_GROUPS`）；Thin Judge 兼容投影是否完整保留这些输入以及是否影响当前 completion 顺序需实跑追踪，不预判为 bug。
- `goal_completion_gate.py::maybe_complete_run_goal` 捕获异常并 fail closed；因此“无异常”不是成功。需区分没有 claim、claim blocked、sidecar/admission 被拒与真正 run terminal，有限步耗尽必须出可定位失败证据。失败恢复是否自行产生 run terminal 尚未由现有 nodes 证明。
- PRD standard 保留 `topology=fanout` 默认两 scan readers→planner→plan-critic synth→task-map/package admission，后半段可共用候选/verify/judge driver；不能切成 `PrdFlow(topology=light)` 冒充 `delivery:prd:standard`（`workflow_profiles.py::expand_prd_flow:1484–1709`）。
- PRD 须填 `requirement_spec` 代替 `issue_spec`，保留 scan/source-index/plan ports 与准入；默认 `post_verify_discovery=product_completeness`，另有 discovery stage；默认 `quality_floor=product-demo/delivery_policy=report_and_demo`。若配置启用 blocking product acceptance，须真实 spec/report refs 和 binding（`_controller_metadata`、`goal_closure_bridge::_goal_closure_read_contract`）。这些是新增有限输入，不可通过取消阶段/降低 policy 宣称 standard 完整成功。

## 7. I-02 补充：候选门禁不能被 worker 的绿色自测替代

主线程定向源码调研（2026-09-14）：`TaskRefManager._validate_workdir`（task_refs.py:1213–1254）在交付时检查 SHA/HEAD 与未提交修改；`normalize_impl_self_check`（impl_self_check.py:33–112）检查报告身份和声明命令回执；`CandidateRebuilder._run_quality_gates`（candidates.py:1026–1199）在独立 merger worktree 重新执行 TaskContract 命令。已有 `tests/test_candidates.py::test_candidate_quality_gate_failure_blocks_updated_result` 和 `::test_task_contract_quality_failure_records_failed_task_id` 直接建立任务和批准事实，证明局部门禁，但不是正式 Issue 入口整链。上述仅检查，不修改生产代码。

当前正式 Issue 的 broken-worker case 真正执行 exit=1，但可能被 self-check 提前拒绝；它不能独自完成 I-02。补充一个固定不可信 worker 输入：提交仍有缺陷的 answer.txt；只在 worker 自测执行期间把测试脚本临时替换为绿色输出，记录实际执行脚本的摘要和真实 exit=0；随后原字节恢复脚本，交付前目标源码／脚本与提交一致。不得修改 merger worktree、生产 gate、退出码或注册命令，也不把该回执说成“提交树已通过”。这个输入专门模拟不可信或错误归属的 worker 自测证据。

预期：真实 writer 结果可进入正式候选尝试，独立候选树含原始回归脚本和错误 answer；实际质量命令 exit=1，产生 `candidate.quality.failed`，失败关联真实 Task；无 ready、verify/final 派发或 Goal completed。独立重跑同一候选命令应同样失败，主分支不变；保留实际脚本摘要与提交脚本不同的证据。若 handoff 提前拒绝，则保留真实拒绝，但不宣称已测到候选失败边界。此例不证明完整 retry/cap 终局，后续预算测试仍单列。

## 8. 联调中的进程清理夹具时序（非生产缺陷）

`/tmp/zf-issue-route-final-integration-59kl_9tm` 出现唯一普通失败：`test_runner_timeout_cleans_descendant_after_pytest_leader_exits` 的 child.pid 不存在。内层正式报告位于 `/tmp/pytest-of-uisee/pytest-2815/test_runner_timeout_cleans_des0/out/testkit-run.json`：exit=-15、timed_out=true、cases 为空、session 未完成、stdout/stderr 均为空。此时目标 stubborn child 尚未建立，不能判定进程组清理失效，也不能简单靠复跑把不稳定用例算绿。

源码复核：`tests/testkit/runner.py:208–230` 先启动 pytest，再以固定 timeout 等待；旧测试把 2 秒同时当作 pytest 导入／收集／创建 child 的预算，父进程还在 child 安装 SIGTERM 忽略之前就写 PID。`tests/testkit/cleanup.py::terminate_process_group` 的真实流程为 SIGTERM→按组存在性等待→SIGKILL，即使 leader 退出仍检查后代。两个 readiness 竞态都在测试前置条件，不改生产或 runner 的超时语义。

拟修订：仅本测试包装 Popen，返回真实 pytest 进程前先有界等待 child 自己安装 handler 后写就绪 PID；之后 runner 仍真实执行 2 秒 timeout 和清理。包装器启动失败时清理自己创建的组，最长准备时间 30 秒；不 mock wait、信号、PID、退出码或被测 cleanup。增加 0/2.5 秒的固定 child 准备延迟，后者明确超过原 2 秒窗口，以证明测试不依赖机器恰好足够快。原失败报告保留，不登记 ZaoFu 生产 Issue。

## 9. K524 后续 Refactor 正式入口差异（2026-09-24，只读定位）

PRD standard 的 5 项完整入口用例已在 K524 通过；下一模块不能直接把 PRD 的 stage 名
替换后宣称 Refactor 完成。当前 `expand_refactor_flow_v3` 编译出的实际链为：flow-scan →
flow-plan/plan-critic → flow-lanes-impl → candidate → flow-lanes-verify →
flow-verify-bridge → flow-module-parity-scan → module.parity.closed → flow-final-judge。
默认三个 scan roles（contract/runtime/verification）由 `_SCAN_CHILDREN_V1` 决定，应读取
配置，而不是照旧 smoke 的两 reader。

RefactorFlow 必须声明 `flowProfile: refactor-flow/v3`、`assembly`，mock backend 经
roleDefaults/laneRoleTemplate 传入（没有 PRD 的顶层 backend 参数）。默认 quality_floor
是 release_candidate、delivery_policy 是 report_only、verify_rescan 是 module_parity；
不能删 bridge/parity 或制造 task_map.ready 绕过。正式 request 需核对 source_root 与
target_root；现有目标选择/输入编译 helper 继续为 authority，不直接覆盖 runtime。

`tests/e2e/test_refactor_pipeline_smoke.py` 是自定义 cj-min 图，还手动追加 run.goal.started
和 task_map.ready；因此它的绿色结果不证明上述正式编译路径或 Kernel 终态交付。
当前 `Orchestrator._bridge_refactor_plan_ready_to_task_map` 已经有 plan.ready 到初始
task_map.ready 的 bridge，下一步应走真实 caller，而不是复用旧 smoke 的人工补桥。

`examples/prod/controller/refactor-lane-v3.yaml` 比裸编译默认更强：ship_candidate、blocking
Product Acceptance、全角色 semantic submit，并默认 standard tier。后续记录必须区分
编译默认链与这个具体模板；模板 ship 若要求远端动作，不越过当前 remote-write 禁令。
本节只确认测试设计路线，未证明 Refactor formal terminal 已通过，不登记新 Issue。

## 10. K526 catalog 强策略合同核对（2026-09-24）

按 zf-cr 做测试开发前定向核对；AGENTS/规则与设计索引已核验，索引缺 doc142 且该文件
仍不存在，以当前代码/测试为据；不扩展为全仓审查。K525 已验收编译默认链，本节只补
它没有证明的强策略。控制面仍是 zf.yaml，state 由 project.state_dir 解析；worker 只能
提交语义，EventWriter/Store/Kernel 继续管理准入和终态。

- `examples/prod/controller/refactor-lane-v3.yaml`：六类 semanticSubmitProfiles blocking、
  artifactPackageMode/productAcceptanceMode blocking、deliveryPolicy ship_candidate。
  采用其 spec 策略，测试缩为单 lane/单文件 parity scope，路径/Provider 换为隔离夹具；
  不声称真实 Provider 或完整两 lane catalog 环境验收。
- `result_submit.SemanticResultSubmitService.submit`：credential、operation scratch、
  runtime identity 与 read ledger；implementation 输入为 target_commit/changed_files/
  self_check 等语义，verification/goal closure 使用自己的 profile；不能追加旧 event
  绕过 blocking，`_compatibility_projection` 负责兼容事实。
- `plan_artifact_package.prepare_plan_artifact_package`：Refactor blocking 额外必需
  product_acceptance_spec，inline plan_ports 可由 Kernel 物化并绑定真实 Run/generation；
  planner/critic 结果中的 plan_ports 由 refactor_artifacts 的继承白名单保留。
- `product_acceptance.validate_product_acceptance_spec/bind_product_acceptance_report`：
  spec 要 entrypoints、mandatory journeys/assertions；report 必须绑定真实 spec digest、
  generation、candidate 和受审 SHA，证据来自该候选树实际命令，不能复用单测虚构 SHA。
- `goal_completion_gate._apply_delivery_request` 调 `ShipService.ship`，结果为
  run.delivery.settled/failed；测试仅独立临时 Git、local_only，不触碰本仓库分支或远端。

缺口与实跑停止条件：先确认六类签名提交及 spec 入包，再确认 verifier 的 acceptance
绑定抵达 closure/delivery；缺失 acceptance 必须拒绝。任何失败先区分夹具输入与生产
合同接线，原红证据保留。完成需实际 Kernel terminal、local ship 与外围 owner-request
同 Run/ref/digest 对齐；不能以单独调用 helper 或单独绿色合同测试替代全链。

## 11. K527 浏览器同 Run 接续与深链阻断（2026-09-24）

- 原四流程脚本的 fake headless 只处理 synthesis/channel/task/workflow 规划，role 为
  mock `cat`，不消费正式 worker operation；原报告明确是 invocation handoff。新测试
  不把该脚本的绿色外推成完成，而先隔离 General 单流程接上 K523 的真实 Kernel。
- `WorkflowStartService.propose` 产生待批准 proposal；真实 Web 点击走 token-gated
  workflow-start，原始 Task/Run 身份从 action response 与 slim event API 精确获取。
  `GeneralRoute.prepare` 只准备安装方案，不再强制同时启动另一个 Run。
- `GeneralRoute.dispatch/emit` 继续使用真实 manifest、read ledger、role credential、
  operation scratch 与 SemanticResultSubmitService；脚本只负责 worker 语义。
  terminal 写入与 Task settlement 相隔下一 reactor tick，不得提前断言 done。
- `prod_flow_terminal_audit` 复用为单 Run 后端观察器；它按最新 terminal 观察，不用于
  多 Run 串联的身份 oracle。新测试另行断言唯一 invoke/terminal、同 Run refs/digest。
- Web `_run_detail` 保持归档合同，未归档 General Run 可 404；独立 dossier API 已为
  200。本轮生产阻断是 ObservabilityPage 只认历史 obs_run_id，而路由已转为 run_id。
  修复 canonical 参数读取后 dossier 不依赖归档详情成功；见 ZF-TEST-088 与
  `docs/impl/run-dossier-deep-links.md`。

未覆盖边界：安装及待启动 Task/proposal 是 Given、transport 为 recording，不宣称
完整 zf start/tmux/live-provider；PRD/Issue/Refactor 浏览器后续执行、外部 owner 回执
仍待继续。doc142 仍缺失，本轮依 zf-cr 定向核对源码/测试，不声明完整架构审查。

## 12. K528 三类交付流程接续（2026-09-24）

1. `WorkflowStartService.preview` 对 delivery 比 General 更严格：要求有效
   `workflow.kind_routes`、受支持 verification_tiers、execution owner 与 source precedence。
   单 Kind 的历史 conformance fixture 有编译后 stages，但没有 operator catalog；本轮
   在临时 zf.yaml 注册其已有 entry（Issue issue-lanes-impl、PRD prd-scan、Refactor
   flow-scan），不改变 stage/gate 策略。Task 标明 workflow ownership、e2e tier 与实际
   `docs/intake/bug.md`；不设置 skip reason 或降低预检。
2. `PrdRoute/RefactorRoute.continue_to_writer` 只消费已有 invoke；旧 `plan_to_writer`
   仍先正式 request/submit 再调用它。浏览器 runner 不再调用后者，不产生第二个 Run。
3. General 为 artifact report；Issue light/PRD default/Refactor v3 default 另核验真实
   candidate ref/commit、该树上的 regression probe、独立 verify SHA、原始 Task 与
   delivery Task done。三条代码链沿各自同一 Run 走到 Kernel terminal 和 owner-request。
4. 四个 Docker case 各自隔离项目/API/端口与 workspace，不是同一 live 多 Kind runtime
   的排队/资源竞争证明。worker semantics 与 recording transport 仍是 deterministic，
   下一缺口为真实 `zf start`/EventWatcher/transport 接续及外部收件，不可用本轮替代。

检出：三类新夹具缺事件账本、缺 catalog、缺 Task 合同，以及 tier 误用 task_non_smoke
（它是 validation command tier，不是 Task verification_tiers）均是测试初始化错误；
正式 Web/Kernel 的拒绝符合合同，非新增生产缺陷。红/绿证据详见 Issue 台账 K528。

## 13. K529 live runtime 与脚本 worker 接线（2026-09-24）

本轮按 zf-cr 定向复核 `cli/start.py`、`watcher.py`、`transport.py`、`backend.py`、
`spawn_coordinator.py`、`result_submit.py` 与既有 General/CLI host tests。doc142 仍缺失；
不声明全仓架构审查。源配置、Task ownership 与 terminal settlement 均以当前代码为准。

- 正式 `zf start` 创建 WorkflowRuntimeCoordinator、真实 TmuxTransport/SpawnCoordinator，
  EventWatcher 推送新事件并做 idle catch-up；独立子进程启动，不替换入口/时钟/调度器。
- mock backend 的正式 argv 是 `cat`。仅临时 PATH 的同名 executable 换为 stdin 脚本；
  它收到 `build_task_prompt` 的实际 briefing 路径后才等待该路径/role 的精确 dispatch。
  不能只轮询 EventLog 然后替 transport 产结果；negative stdin 测试守住这一边界。
- 脚本只复用 General worker 语义和输入读取 helper，未构造 Orchestrator；结果走正式
  `zf result submit --operation ... --scratch`，credential 来自 SpawnCoordinator 的环境，
  不使用测试侧固定 credential。收据留 stdin、PID、briefing SHA 与 admitted event id。
- 父测试把自身进程 `Orchestrator.run_once` 替换为断言失败；先启动 runtime，再通过
  浏览器批准 proposal，证明是 live EventWatcher 消费，不是测试侧补 tick 或只启动 catch-up。
- General 父 Task 也必须在 Given 中使用 workflow ownership；未预留时被普通调度领取是
  有效行为。Task done 与 terminal 非同一写入，需等实际下个事件消费；owner request 必须
  精确绑定 terminal，不能把 supervisor 通知混入交付计数。
- 完整 CLI stop/start 后等两次真实 watcher heartbeat，核对关键业务事件和交付字节不变，
  最终清理 CLI、tmux/worker PIDs、API、Docker 与临时端口。只做 local owner-request，
  隔离外部通知环境；不代表外部收件、真实模型语义或四类共用 runtime 的竞争已验证。

## K565 定向补充：受控首次worker死亡与真实恢复（2026-09-25）

本阶段用General五阶段作为shared worker/watchdog的真实恢复控制，不冒充Issue
首次死亡现场复现。复用已有LiveGeneralRuntime和K541的正式CLI start/授权proposal；
父进程禁止Orchestrator.run_once，语义由tmux中的脚本worker通过正式CLI提交。
启动所有角色后选择实际scope stdin收据，使用已有testkit-worker-gate在提交前停住；
不修改生产liveness阈值/频率、retry预算或任务状态来促成恢复。

`_inject_fanout_recovery_briefing` 重用active child原briefing，用真实transport重送，
再写worker.recovery.injected；这不必创建新dispatch/run。当前testkit-transport按
dispatch ID保存的是最新物理收据，故必须在故障前把首次收据另存入控制实验报告，
不能把恢复后该路径的新PID当成首次进程。process record本身按PID/时间独立，不覆盖。

首轮host已通过真实runner.failed/respawn，随后测试误用所有角色最新process record
选到verifier而不是scoper；该失败是夹具选择错误（K565-F1），不是生产缺陷。
修正必须按相同role、新PID、晚于首次started_at精确筛选，并验证其真实stdin收据与
recovery.injected的run/briefing/correlation一致，不只检查“存在新进程”。

故障发送使用已有tmux_process_cleanup._Pidfd/_identity；检查目标/proc/environ
具有本测试的ZF_STATE_DIR和角色，birth identity前后相同后才发SIGKILL，关闭pidfd。
不使用广播或数字PID降级。原型首轮用owned receipt PID直接发送，已收尾，后续证据
必须来自上述绑定版本。生产事件不包含真实退出signal时，控制报告记录注入signal，
不能向历史或生产事件补造退出原因。

zf-cr本次仅补充runtime/watchdog/恢复briefing、TestKit host/cleanup取证边界；
沿用K564锚点与历史map，HEAD未移动，doc142/backlogs仍缺失。真实Provider、Issue
light特殊重派、OS crash、全部架构不在该General控制实验的证明范围。

### K565 恢复交接重叠的定向复核

第二轮host的原始账本证实：scoper的恢复注入evt-4200ee977ad6在07:47:05.610 UTC，
随后07:47:06.378的dispatch_lost仍引用该次spawn的launch-artifact，继而retry-1。
旧briefing先提交成功，新的retry再消费相同capability产生duplicate_submit；这是恢复
交接与丢派发扫描重叠，不是首次worker死亡原因。正式owner-request于07:47:34.965
才出现，比run.goal.completed晚2.56秒；host夹具需等待该terminal.id对应request，
不能把早读projection的pending和重复派发混为一类。

检查范围：orchestrator_lifecycle的_capture_logs/_respawn_instance_with_lease/
_inject_fanout_recovery_briefing，fanout_dispatch_liveness，orchestrator_fanout与
plan_synth_runtime丢派发扫描，fanout_recovery_runtime的同一账本快照。run_once先
watchdog、后replay；恢复注入已落账但扫描只认agent活动，不认同dispatch交接。
reader与synth共有该机械缺口，不能只修General夹具或让worker吞掉duplicate_submit。
旧attempt结果归属问题还需单独核验，不能仅凭retry后的event字段称为越权结果准入。

确定性测试通过真实run_once创建fanout，再调用生产恢复注入，使用recording transport；
同runtime和重新构造runtime均出现额外dispatch_lost。首轮28项12绿16红中，4红为
上述生产行为，12红是新负例夹具误读synth dispatch缺省child_id，修正为正式synth身份。
原红报告/tmp/zf-k565-handoff-red-20260925.xml保留，不把装配错误算生产缺陷。

最小修复计划：仅在最新loss信号为launch-artifact时，识别其后与原dispatch精确匹配的
worker.recovery.injected（role/fanout/child/run/stage/briefing/trace）；该交接已重送
briefing，本轮不再次retry。不同身份、发送失败、交接早于launch、后续新launch或
provider.turn.closed仍保留原有恢复；不把交接当结果、心跳或永久存活证明，原timeout
与stuck门禁不改。分别覆盖reader/synth与重建，再回到真实SIGKILL host和K564联调。
doc142与backlogs目录仍缺失；规则文件.claude/rules/backlogs.md存在且已读取。
这是zf-cr定向supplement，不是全仓审查；生产阻断将独立登记ZF-TEST-110并保留红绿。

修复与验收更新：legacy synth dispatch实际未携带child_id，生产恢复也保留空值；
不能强行填synth再宣称协议匹配。共同helper按实际dispatch值比较，其余非空身份必需。
修复后完整相关16文件508 passed；正式premerge47绿/4既有结构红（本次fanout调用
接线增加4行也明确记账），mock E2E10绿。来源绑定host三例全部通过，SIGKILL后只
恢复一次、无dispatch_lost/duplicate_submit，五个worker提交、六个准入及本地同Run
交付，37个跨case登记PID全部不存在，三个tmux session均退出。台账后S0/S9加新模块
127绿，所有报告与边界见steps的K565 done。

结果身份的进一步边界：durable_call_fanout._prepare_reader_fanout_retry_operation
明确以同operation/request进行infra redrive；既有reader恢复合同保留原active_attempt/
lease并更新dispatch_id。因此原现场的旧attempt字段与retry run字段不同，本身不足以
证明越权准入，不能把它另造为已经修复的stale-result bug。此次确认和修复的是两个
恢复入口向同一工作重复交接；K530首次退出原因仍无可追补证据，保持open。

## K564 定向补充：首次 worker 退出证据的验证边界（2026-09-25）

复核 K531 的 `observe_worker`，已有 PID+纳秒文件名、启动时间、异常 traceback、
EOF 和原子替换写入；六个 live worker 入口复用它，不应重做实现。现有
`test_original_worker_failure_is_not_overwritten_by_replacement` 以同进程 monkeypatch
模拟异常和替换，尚未验证独立进程退出及不可捕获的 SIGKILL。补真实子进程控制即可，
不向生产 watchdog 加新的推断策略。Python `Popen.wait` 的负返回值是父进程观察到的
signal；worker 的 finally 不会在 SIGKILL 执行，旧 running 记录必须保留为未记录退出，
不能补写 EOF 或从磁盘 running 推断存活。方法依据沿用本目录
`ordinary_case_evidence_research.md` 的 Python signal/subprocess 生命周期边界。

原 K530 账本重新回读：失败 evt-339075f7d1a2（verify-lane-0，
2026-09-23T22:46:04.695311+00:00）含空 pane_pid/current_command/current_path，
reason 为 no_liveness_evidence；4.107 秒后 evt-7d72ced75f3c 为 watchdog respawn，
其 causation_id 为 null。只能证明同角色账本时间/顺序关系，不能声称存在显式失败→
respawn 因果引用，更不能反推 EOF 或证明误判。源码 `_capture_logs` 先记录失败再
调用 `_respawn_instance`；新合同将验证顺序及失败快照不被 replacement 改写。

zf-cr 覆盖矩阵：worker 入口/记录 writer、Orchestrator watchdog/respawn、Composite
快照转发、现有 worker/watchdog/stuck tests 与原 K530 事件为已检查；真实模型、OS
crash、全部架构为未覆盖。doc142/backlogs 缺失；历史由 fanout/runtime、Task Pipeline、
TestKit 与后续大量证据记录构成，当前 HEAD 142a62ac、共享脏树保留。只读
`codex login status` 仍为 Not logged in，K547 不能用离线证据补绿。

## 14. K530 Issue light 的真实 CLI/schema/transport 接续（2026-09-24）

沿用 zf-cr 的定向核对，仅补 `cli/events.py`、EventWriter/schema registry、Issue
dispatch/manifest 和前序 live helper 的接线边界；doc142 缺失仍不外推为全仓审查。

- Issue 编译默认 semantic_result_submit_mode=off，worker 依正式 briefing 通过
  `zf emit --payload-file -` 提交完整语义；不能擅改为 General 的 blocking scratch
  模式，也不能在父测试通过 EventWriter 代提交。CLI 返回 canonical event id 仅证明
  事实入账，必须关联后续 Kernel admission.source_event_id 才算准入通过。
- Issue writer dispatch 的 task_id 位于 payload，verify 则可有顶层 task_id；两种
  形状均绑定同一 Active task。按收到的 briefing 路径和 role 精确选 dispatch，不能
  从最新事件猜 Task，也不能轮询到 dispatch 就绕过真实 transport 自动执行。
- 旧 Issue fixture 的裸 EventWriter 未启用 schema registry，live CLI 暴露了报告缺
  summary/failure_class。仅修夹具完整性，并在旧 happy-path 回归加入正式 registry
  验证；生产 EventSchemaD 正确拒绝，不关闭 gate，不计为生产问题。
- 独立 runtime 在浏览器提交之前启动，父进程 run_once fail guard 持续开启；writer
  真实 worktree commit 后 candidate build/quality、verify 的 exact SHA probe、Judge
  引用已准入报告，直至父子 Task done 与 terminal/dossier/receipt/owner 同 Run。
- General/Issue 共享 worker stdin 与 runtime lifecycle helper；按 ZfConfig.kind 修改
  session，pane-grid 的 owned PID/日志由 binding 解析。正式 stop/start 后等两次
  watcher heartbeat，关键业务事件和交付字节不变；最终 simulation.done 和专属
  CLI/tmux/API/container/端口清理。保留临时审计证据，不操作用户的 8001/runtime。

仍未覆盖 PRD/Refactor live transport、多 Kind 同 runtime 竞争、外部 owner 收件。
当前 gate 证明脚本 worker 的真实接线与证据闭环，不证明真实模型语义或整个目标完成。

最终联调另检出一次 watchdog→respawn→retry；严格收据集合保留红。首次 worker 的
退出原因没有足够日志，不能推断 EOF；推测性 EOF 常驻补丁已撤回。retry briefing
缺 Active task 已从原始文件证实，但不在本轮正常路径修复范围内；Issue 台账单列 open。
后续未复现与固定代码正常路径绿色都不构成该观察项的修复证明。

## 15. K531 reader retry 身份缺口与首次 worker 证据（2026-09-24）

zf-cr 定向检查 `fanout_retry_briefing.py`、`fanout_briefing_runtime.py`、
`fanout_execution_boundary.py`、Orchestrator retry caller 与既有 reader/writer tests。
doc142 仍缺失，不扩为全仓审查。确认初次 reader briefing 使用 `fanout_task_id`，
已有 Task 时首行固定身份；light retry renderer 却仅为 writer 写首行，因此有 Task
的 reader retry 违反同一 worker 协议。blocking reader 走初次 renderer，不受此分支影响。

当前代码复现：真实 `run_once(candidate.ready)` → reader dispatch → 有界 timeout →
第二次 transport，dispatch 已保留 task_id、briefing 首行却丢失。红报告
`/tmp/zf-k531-retry-red-20260924.xml` 为 5 failed/1 passed，含实际 caller 和四种已有
身份形状；无身份的 reader 保持不虚构 Task。最小修复位于现有独立 renderer，不向
超大 orchestrator 模块加逻辑、不改 watchdog/retry 预算或结果准入。

worker 异常取证缺口独立处理：测试 worker 启动/结束按 PID+时间记录，捕获 Python
异常的原始 traceback 与 EOF；文件不因 respawn 后新进程覆盖。SIGKILL 不会执行 finally，
残留 running 只表示未记录退出，不能等同仍活着或证明 watchdog 误判。
此证据辅助后续根因调查，不改变事件账本或声明 K530 watchdog 已修复。

## 16. K532 首次失败诊断路径定向核验（2026-09-24）

按 zf-cr 定向读取实际 make_transport → CompositeTransport → TmuxTransport，
以及 watchdog `_emit_worker_runner_failed` 和 Task Pipeline 的 snapshot caller。
确认底层已有 lifecycle_snapshot，composite 缺转发，真实失败事件因此保存空诊断。
最小修复应只转发原对象，不增加判死策略；无可选接口保持 None，不拿其他角色快照替代。
direct/composite 同一个 Orchestrator caller 红绿对照足以证明诊断路径接线。

联调另发现 readiness 在活性检查后预算耗尽直接返回，绕过统一 failure 记录。
受控单调时钟重现后只改为 break，保持超时拒绝；不通过扩大时间预算掩盖竞态。

原始 K530 首次失败事件早于 retry briefing AssertionError，后者不能解释前者。
首次 worker 日志已被历史重派覆盖，不能从后续正常 run 追补不存在的原因。
保留 open，后续新异常依 PID/启动记录与真实 snapshot 继续定位；不循环重复无变化
的真实 Provider，不做全仓审查。doc142 路径仍缺失，当前代码/测试为核验依据。

## 17. K533 PRD live 接续前的协议核对（2026-09-24）

按 zf-cr 定向检查 PrdRoute/IssueRoute、plan_synth_runtime、synth renderer、
build_task_prompt 与 result_submit。PRD default 既有合同为七个 child、一个 synth、
九个 admitted result（包含 Kernel aggregate）。child 继续用正式 CLI emit；plan
critic 单独走签名 `zf result submit --scratch`，不能把两条语义提交协议合并猜测。

synth 提示是 `Read the fanout synthesis briefing ... Use the ...`，正式 renderer
首行为 `# Fanout Synth:`，不是 Active task；按现有 fanout/run 身份校验而不放宽 child
首行约束。critic 只修改预置 scratch 的 verdict/summary，不重新生成 planner 产物；
使用已准入 child report 与 Source Manifest/read ledger，结构产物仍由 Kernel 合并。

物理 worker 仅初始化只读路径/EventLog/固定 probe 与 main 基线，不构建 Orchestrator。
收到精确 stdin 后查对应 dispatch，再选择同 workflow_run_id 的 invoke；没有 stdin
不得根据账本主动提交。与 Issue 共用真实 CLI/Web/Docker 生命周期和独立清理收据。
这证明真实接线及机械证据闭环，不外推真实模型语义、四流共享资源竞争或外部收件。

live 接续的协议校正：PRD 提案必须显式传 target_root；多阶段真实 transport 使用
独立的浏览器预算（150 秒 Task poll、210 秒 test），默认 recording 路线仍为 60 秒，
不更改生产 watchdog/timeout。正式 synth canonical event 由 result-submit 写入，
其 canonical/admission origin 均不是 Kernel 身份证明；应核对 CLI admitted_event_id、
canonical payload 的 semantic_submit_admission_event_id 与正式 admission.id 三者相等，
同时检查 admission 的 zf-cli actor、blocking mode 与同 Run/operation 身份。Kernel 自动
scan aggregate 的 read_ledger_ref 为空，不能虚构 worker 读取账本；实际八次物理提交
仍逐一核验 envelope/control-result/read-ledger 的 ref/SHA。原始 host 红报告分别保留。

## 18. K534 Refactor 默认 live 接续定向核对（2026-09-24）

沿 zf-cr 定向核验 RefactorRoute/PrdRoute、既有 Refactor conformance 与 K528 浏览器
入口。doc142 仍缺失，本轮不重做全仓历史/架构扫描。控制权保持独立 zf start/Watcher，
worker 只在精确 stdin 后提供语义并经 CLI 回报；source/target 为互斥临时目录。

默认 v3 合同：flow-scan 三 reader → flow-plan planner/signed synth → impl/candidate/
verify → verify-bridge → module-parity → Judge，九 child 加一 synth、十一准入（含
Kernel scan aggregate）。source-root 只读、target Git/probe 真实；新增 worker 复用
Refactor 的 plan_result 和既有 PRD synth scratch 提交流程，不自行写 task-map.ready、
terminal 或 canonical Store。空/无关 stdin 的拒绝纳入共享负向合同。

默认 delivery_policy 为 report_only，与 StrongRefactorRoute 的 catalog blocking
profiles/Product Acceptance/ship_candidate 不同。默认 live 绿灯只关闭对应子项，
强策略 live、四类共享 runtime 竞争和外部 owner 收件继续明确未覆盖。上一轮 K533
真实 PRD 现场（第六轮）及当前代码为接续依据；K530 原始首次死亡原因无新证据，保持 open。

首次 Refactor live `/tmp/zf-k534-refactor-first-20260924.xml` 为 1 passed/200.45s，
Docker 浏览器 1 passed/126.55s，无 unexpected/skipped/flaky。账本实证九 child、
一 synth、十一 admission、唯一 terminal；正式 stop/start 形成两次 loop.started，
无重复业务结算/交付或源目录改写，restart_verified/cleanup_ok 均 true。未发现新的
生产阻断或需要标 fixed 的夹具缺陷；不为凑红证据制造问题。前序联调单独记录。

## 19. K535 catalog 强策略 live 的提交/交付边界（2026-09-24）

zf-cr 定向核对 `StrongRefactorRoute.publish_result`、`PrdRoute.submit_semantic`、
`zf.cli.result._run_submit`、`result_submit.credential_from_environment`、
`SpawnCoordinator` 和强策略 conformance。doc142 仍缺失；沿已验证的 K534 路径补该
缺口，不重做全仓审查。模板 blocking profiles/package/acceptance/ship_candidate 不改。

worker 只复用已存在的业务语义构造，submit_semantic 换成实际 CLI 子进程；角色凭据由
transport 签发并通过 ZF_RESULT_SUBMIT_TOKEN_FILE 注入，不读写/重置真实 Provider 配置，
不在日志或事件里输出秘密。十次物理提交全部绑定 CLI admission ID，Kernel scan
aggregate 为第十一次结果；不能统一要求 origin=kernel（K533 已证明这是错误 oracle）。

候选期 main 必须仍为 base；ship 后 main 必须是 ship.final_commit。因此终态审计不能
继续调用含 pre-ship main==base 断言的 candidate helper；应读 canonical candidate SHA，
另证 ship source→landed ancestry、final_tag、声明 probe 和产品验收 ref/digest/Run/代际。
source 保持不变、远端策略 local_only；重启防重放集合须新增 delivery/ship 事件。

首次 host 红 `/tmp/zf-k535-strong-first-20260924.xml` 为 1 failed/41.97s；正式预检
以 `git_delivery_baseline_dirty: zf.yaml` 拒绝浏览器批准，未进入 fanout/ship，cleanup_ok
为 true。只读 diff 确认污染来自测试加入 kind_routes 与 LiveGeneralRuntime 加入唯一
tmux_session；这不是生产缺陷。修复在 runtime 配置完成、提案前，仅将隔离 tmp_path
项目的 zf.yaml 精确提交为 Given 并更新 route.base；检查空 index/最终 clean，不放宽
ship_candidate 门禁、不提交主仓。后续独立目录验证，原红现场保留。

第二轮虽完成产品验收/ship/terminal 与 restart/cleanup（1 passed/196.80s），首次
module-parity worker 的独立 PID 记录却保留 `KeyError: attempt_source_manifest`。
dispatch 本身已有完整 source manifest，`EventWriter._append_with_projectors` 先追加
账本、再更新 fanout projection；TestKit `PrdRoute.complete_reader` 在看到 dispatch 后
又读取尚未更新的 child manifest，产生跨进程窗口。watchdog 的空 agent 进程树与后续
同 briefing 重注入相符，不是误判证据。修改范围仅夹具：reader 使用精确 dispatch
嵌套 payload 的深拷贝，继续逐项读 ledger、校验 sidecar SHA、正式 CLI signed submit。
新增确定性“事件已可见/投影仍旧”红绿合同；正常 host 禁止 runner failure/respawn。
不把该现场倒推为 K530 首次死亡根因，原现场无首次 traceback，仍 open。

## 20. K536 两 lane 确定性主链（2026-09-24）

上一轮为有实物/报告的 progress，整体仍 active。本轮 zf-cr 定向核对 catalog 的
RefactorFlow、lane_pipeline_materialize 的 affinity_stage_slots/candidate_integration/
wait_for_all、共享 writer self-check 和 StrongRefactorRoute 验收报告。doc142 缺失
仍保留，不重做全仓架构扫描。catalog 的完整第二段 ZfConfig、profile_sources 和
semantic-control 部署没有被现有 flow_spec 夹具涵盖，必须单独保留未覆盖声明。

双任务分别有互斥 allowed_paths、独立 claim/验收命令/affinity、独立 worktree；共享
writer 只把固定 answer/probe 名变为有原默认值的字段，未改生产协议。隔离 Popen guard
仅增加本轮声明的第二 lane 与组合 product probe 精确命令，不允许 Provider/网络/远端。
组合 probe 在 broken Given 必须失败，在候选必须实际验证两份文件；每个 verifier
报告追加该 probe ref/hash，不能拿单 lane probe 冒充整体产品验收。

首次 full 红 `/tmp/zf-k536-two-lane-full-first-20260924.xml`：1 failed/1 error/5.77s。
候选真实使用 scoped cherry-pick，不保证每个 writer SHA 是其祖先；现场 git cherry
明确 `- <writer SHA>`，应核对 rev-list --cherry-pick --right-only 无剩余、实际内容及
candidate 的两项质量命令，而不是改生产 Git 策略。teardown 的 git remote 列举也被
严格 guard 拒绝，改用已有 git config --local --list 只读检查；不放宽禁止 remote 命令。

## 21. K537 双 lane live 接线（2026-09-24）

沿 zf-cr 定向检查 K536 route、K535 StrongRefactorWorker、IssueWorker 初始化、实际
LiveGeneralRuntime 和公共 browser host。双 lane worker 只替换 factory 与复用的语义
类，仍以精确 stdin/dispatch 激活，签名凭据由 transport 环境提供；不调用测试 route
构造器重建 runtime，不在 worker 中再次初始化 source/target Git。

双 lane 的 Task/worktree/verification 集合必须显式核验：十二次物理提交＋一次 Kernel
scan aggregate，共十三准入，impl/verify affinity 保持一致且 verifier pin 同候选 SHA。
终态产品报告现在含单 lane probe 和实际组合 probe 两份 refs，两者 digest 均校验，
用组合 probe 的命令/退出码/stdout/commit 裁决产品结果。父 Task 的 scope 和 verification
必须对应两份文件及组合命令，不能保留单 lane 的需求描述。原单 lane 断言保持原合同。

沿用 K535 临时 config baseline 固化、源码只读、正式 stop/start、Watcher heartbeat、
关键事件/交付 bytes 幂等与清理；正常路径不许 runner failure/respawn。当前浏览器预算
保持原值，若真实运行超出先查进度再处理，不修改生产 watchdog。完整 catalog 第二段
配置部署、跨 Kind 竞争和外部收件仍与此项分开，doc142 缺失继续如实记录。

首次双 lane live `/tmp/zf-k537-two-lane-first-20260924.xml` 为 **1 passed/241.86s**；
Docker 浏览器 **1 passed/162.42s**，unexpected/skipped/flaky=0。十二份物理 worker
收据、十三准入、两个 dev/verify lane affinity/worktree/Task 与同候选、组合产品
验收和实际 main/tag 两文件全部核对通过；restart_verified/cleanup_ok=true。
worker.runner.failed/respawned/discriminator.failed 为 0，未发现需修复的新阻断。
预算没有调整；浏览器总耗时包含批准、终态、reload/legacy 链接，不能据总耗时直接
推断 Task poll 超出 150 秒。前序 live/自动闭包另行记录，不外推完整 catalog 部署。

## 22. K538 完整 catalog 部署接线调研（2026-09-24）

zf-cr 补充审查：AGENTS、design index、路径规则及当前源码已读；doc142 仍缺失。
复用前序 Git/架构地图，只补 control_plane / skills_materialization / runtime_orchestration
缺口，不声明全仓审查。上一轮已取到 K537 自动闭包最终成功，属于有效进展。

- `core/profile/flows.py:flow_path` 从 YAML catalog id 找模板；
  `profile/apply.py:materialize_flow_assets` 是 CLI bootstrap 使用的部署 caller，复制
  profile_sources、启用 skills 的依赖闭包，并将 skill source 重定位到项目本地。
- `config/loader.py:load_config` 在 envelope 前加载完整 profile_sources，随后
  `kind_envelope.py` 合并 uses 并记录 profile/file digest；运行时不另读第二控制面。
  `tests/test_config_profiles.py` 与 `test_project_profile.py` 是直接调用方回归。
- 模板第二文档保留 orchestrator 角色和 semantic_control/plan_candidate/shadow/25%，
  strict skills、task_contract_required、split_quality blocking 和 admission_replan；
  既有两 lane 夹具没有启用这些字段，不能仅看强策略 Flow 文档即视为完整部署。
- `orchestrator_agent_operations.py` 根据 Run/revision 稳定采样；未采样应有 skipped
  事件，采样应经真实 checkpoint transport、source/read ledger、signed result 和
  Kernel decision.shadowed。shadow 不得产生 authoritative plan adoption。

隔离部署探针已证实：正式 materialize 复制 common/profiles.yaml 和 32 个 skill 目录，
load_config 得到 13 个角色、两 lane、相对本地 skills、完整 blocking 和 shadow 策略。
部署探针单独只证明安装/加载；随后已在隔离双任务 Git fixture 中消费该部署结果，
仅替换 source/target/需求参数、custom-state 和 local_only，继续真实 Kernel。
最终保留 codex backend、on_demand/resident/strict 配置；ReadyRecordingTransport 提供
显式就绪协议，不启动真实驻留 Provider。原 mock backend 与 on_demand 不相容，以及
缺 wait_ready 导致 activation 错误的两次红证据已保留，Issue 中分别登记 K538-F1/F2 fixed。
完整五节点 `/tmp/zf-k538-full-catalog-fourth-20260924.xml` 通过，配置 caller＋既有
双 lane 联调 `/tmp/zf-k538-config-integration-closeout-20260924.xml` 为 50 passed。
本层覆盖正常/反序重建、shadow requested 或未采样 skipped、请求对应 decision.shadowed
且无 decision.applied；不声称每次随机 Run 必然采样，不外推完整 host 部署。
四类共享竞争、外部收件、K530 原因仍保持未覆盖。

### K538 收尾联调追加：采样 worker 的时序合同

自动影响闭包 `/tmp/zf-k538-dev-verify-closeout-20260924.json` 为 failed：80 passed/
1 failed，失败包 `/tmp/zf-model-evidence-5lv2h5i9/b2d5231332530f193294b0c3-825762/latest.json`。
实物账本顺序为 operation.requested → checkpoint.requested → operation.started；
`orchestrator_agent_transport.dispatch_orchestrator_agent_operation` 在 send 之前先 activate，
而 `prepared_operation_from_checkpoint_event` 明确只接受 requested（或限定 retry）。
故 tick 之后重建 prepared 是夹具误用，不需要修改生产状态机。

新增独立确定性回归使用已有 admitted-package Given 与正式 risk_level=critical 的
risk_override，保持原 25% 配置、不替换采样函数。它每次必经真实 shadow dispatch，
首红 `/tmp/zf-k538-sampled-red-20260924.xml` 1 failed/3.56s。修复方向是在 tick 前
读取已持久化 prepared context，tick 后仍要求实际 transport 收据才提交签名结果；
Kernel 的 requested/running/准入/shadow 权限不变。完整双 lane 正常链仍使用原自然采样，
不能把之前未采样的绿灯当作该分支已被验证；最终收尾须再跑影响闭包。

确定性新增回归第二步又核实了 package Given 的 schema 边界：旧 `_candidate` helper
生成的 admitted payload 没有 `mode`，严格 catalog 的 EventWriter 记录 discriminator.failed，
其后 checkpoint decision 合法拒绝为 plan_package_stale。只给该测试 helper 增加可选
`package_mode` 与对应 event mode，默认调用不变；不修改生产 writer/准入。新回归与原
helper 四项调用方合计 `/tmp/zf-k538-sampled-green-second-20260924.xml` 5 passed，
明确检查无 discriminator.failed、无 decision.rejected、一个 shadowed、无 authoritative
applied，并在重建/重复 tick 后保持单次结果。夹具故障统一记 K538-F3 fixed。

## 23. K539 同项目多 Kind 竞争入口调研（2026-09-24）

### K593-C Codex role-local TOML职责补查（2026-09-28）

`SpawnCoordinator`同时协调Provider启动、Claude锁清理、Codex配置准备和会话观察。
工作树Claude改动与末尾TOML trust block helper无交集；后者只有json/Path依赖，
写入目标由`_install_codex_project_hook_trust`传入role-local config。将该完整职责
原样移到`codex_runtime_hook_trust.py`，保留coordinator导入接口，不搬动旧RPC、认证、
update prompt和Claude分支，是可验证的独立拆分。CLI start和Orchestrator respawn
均仍经同一spawn；新模块不是无调用方库。原字节比较也确认所有既有脏改动保留。

局部zf-cr范围：读取AGENTS/rules/index、完整git历史命令、config、spawn调用链、
旧测试及结构哨兵。历史热点集中TestKit台账与runtime/Web；doc142缺失，backlogs/tasks
没有精确proposed/backlog裸状态候选。本轮不宣称全仓审计，不推断未读子系统正确。
保护边界为配置state_dir、既有RoleSessionRegistry和EventLog所有权、无新状态机。

新的caller合同检查真实spawn所传CODEX_HOME、合法TOML、旧hash替换/无关表保留、
重复调用和全局配置字节不变；hash计算兼容性保留为独立传感器。扩展实跑检出10个
静态向量差异和0.155/0.144版本不匹配；对应源码及测试与HEAD完全一致，独立进程
仍10 failed/8 passed/1 host deselected，未修改基线或据此声称原生Provider可用。
原始红绿证据见`attachments/k593c-hook-trust-20260928/`；后续原生hash重新校验须有
可靠Provider证据，不以本次配置搬移的绿灯替代。实现说明见
[Codex hook trust边界](../../../../docs/impl/codex-runtime-hook-trust-boundary.md)。

### K593-B 结构门禁最小拆分补查（2026-09-28）

当前结构门禁仍检出040；不提高cap或新增债务豁免。已核对干净的
`control_actions_workflow_request.py`：1016行中最后两个方法分别取消异步synthesis
operation和拒绝proposal，不负责request创建、submit或start。两方法只消费
service的state_dir/writer/actor及`_failed`，外部服务仍通过既有action router调用；
它们的操作service与proposal函数均为局部import，不依赖请求模块其它全局符号。
可以按“请求取消/提案拒绝”职责原样移入`WorkflowDecisionActionsMixin`，由原
`WorkflowRequestActionsMixin`继承，不改action名字、事件序列、状态/摘要校验或
错误返回。不是`/issue-merge`/Feishu功能开发。

拆分前既有cancel/reject选择集为3 passed/21 deselected（2.91s）；已有Web实际action
入口在`test_web_workflow_proposals.py`，最终联调应跑该整文件而非仅名称过滤。
补充重建service后的cancel幂等与错误proposal摘要拒绝，验证移动后仍走实际router、
EventWriter和operation projection。结构证明为原文件降到1000行以内、新模块有实际
src调用方；040其它八个源文件仍开放，不能据此标整项fixed。共享脏文件本轮不拆。

- 正式 `cli/flow.py:draft_multi_kind_project_spec` 构建一个项目容器，组合 Issue/PRD/
  Refactor 配置与分 Kind schema/role namespace；自身不生成请求或 invoke。
  `tests/test_workflow_profiles.py::TestExpansion` 中的 multi-kind 合同核对了
  路由/角色/触发匹配，但不能代替四个未完成 Run 的实际资源竞争。General 需经已有
  proposal/config-apply 接入同一容器，不能复用四个互相隔离的 route 实例来冒充。
- `WorkflowRunAdmissionConfig` 默认为 serial/max_active_runs=1；concurrent 为显式
  opt-in（2–8），且仍受 `run_isolation` 前置约束。先验证配置原有序列语义，再按
  合法隔离配置验证并发；不把抬高容量当解决资源冲突。
- `runtime/run_admission.py` 使用 append-order EventLog 重建 active/FIFO/terminal，
  `admit_workflow_invoke` 在锁内准入或排队，reconcile 在正式终态后补位；不能自己写
  queue/state 文件或手工 terminal 事件放行。`tests/test_run_admission.py` 是直接调用方。
- 下一层必须有同一 source/config/state/runtime、四个不同业务 Run、交错未完成窗口、
  容量与引用隔离断言、正式结果释放容量、重建/重放不重复；只有四条独立路线绿灯
  不足以关闭此缺口。外部 owner 收件仍是独立验收，不由本地事件或 shadow 证明。

本节为入口调查，未实现或执行共享竞争测试；不计完成量。原 K530 与生产结构门禁仍 open。

### K539 首次接线：显式 General 入口

首次共享排队 `/tmp/zf-k539-shared-queue-first-20260924.xml` 1 failed；尚未产生
General invoke。`workflow_delivery._resolve_submit_pattern_id` 在多 stage 且没有
workflow kind route 时必须拒绝猜测，预检 STOP/workflow_route_unresolved 正确。
`generic_workflow_templates` 的 evidence-synthesis-v1 entry 是 `scope`，现有
`build_flow_submit_preview(pattern_id=...)` 支持正式显式选路；只让 General 测试
helper 接受可选入口，共享夹具传 scope，独立夹具保留原默认，不改生产预检。
下一验证为正式 config-apply/submit 是否保留该选择，以及四 Run 的真实排队/释放。

### K539 终态阻断调研：claim 的 immutable 合同所有权

修正显式入口透传与“原 stage 集合＋General 五 stage”的精确断言后，四 Run 已真实
排队。General verifier 完成后红于 run.goal.completed 缺失，账本明确记录三类合同
不匹配。`workflow_submission_binding.py:pin_submitted_run_contract` 写每 Run 快照并
轮换兼容 active pointer；`goal_completion_gate.py:_evaluate_active_claims` 却仅在
循环外 load_run_contract 一次。因此最后排队 Refactor 的合同污染在途 General。
`artifact_delivery_result.load_bound_run_contract` 已提供受限 sidecar 路径、完整 body
digest 与结果 semantic digest 验证，可复用而不新增状态所有者；绑定读取错误必须
fail closed，不能回退最新合同。直接调用方两合同反例确认此因果，登记 ZF-TEST-092。

### K539-B：复用单流程工人时的事件身份

第一段修复和前序联调均通过后，新增共享 worker view；它不执行各独立 route 的
构造器，只代理同一 shared runtime 的 tick/reopen，工人输出复用已验证方法。
事件按实际 Run/trace/correlation 筛选；candidate 子服务的 quality 事件没有这些
字段而使用 `pdd_id`，因此只对 candidate 事件额外按 invoke 的 goal_id 精确关联。
首轮 `/tmp/zf-k539-shared-delivery-first-20260924.xml` 1 failed/28.58s：真实候选
quality 已 passed，但夹具漏该身份导致误报缺事件。不是生产 quality 未执行。
共享 probe 补齐前序 helper 预期 stdout，三类使用正式命名空间角色并签发公开测试
凭据；不得直接重写 operation/candidate 成功结果，后续四终态仍须实际执行验证。

最终 `/tmp/zf-k539-shared-delivery-fourth-20260924.xml` **1 passed/104.64s**。General、
Issue、PRD、Refactor 四个 Run 均在同一 EventLog/Orchestrator 中实际走到 terminal；
candidate quality 的无 run_id 事件按同 Run `candidate.ready.pdd_id + candidate_head_commit`
绑定，所有 Task/operation/candidate/owner sidecar 归属与重建 bytes 通过。下一段只做
显式隔离 concurrent，不把 serial 证据外推并行。

### K539-C 待验证的正式并发入口风险（只读调查，尚未裁定缺陷）

`workflow_proposal.py` effective snapshot body 含 request_id/revision/source path；
不同请求即使渲染配置相同，其整个 sidecar SHA 也会不同。当前 `run_isolation.py`
却直接比较 invoke.effective_config_digest。另 intake 接收 scope，但
`workflow_delivery.py` submit/invoke payload 未检索到对应 scope 透传；target_root
常为绝对路径，而 isolation 的 scope 归一化要求显式相对路径。
下一步须用正式请求复现并分别确认前置输入，不能伪造同 digest/补写 scope 到 invoke
来制造 concurrent 正向。先保留红证据，再判断最小接线修复及安全负例；既有
`tests/test_run_admission.py` 的手工 digest Given 不能单独证明公开入口可用。

首次正式入口负例 `/tmp/zf-k539-concurrent-missing-scope-red-20260924.xml` 1 failed：
断言预期先缺 scope，但实际更早得到 `concurrent effective config digest mismatch`。
当时把不同 snapshot digest 当作不同配置，是错误判断，已于 K539-C1 校正：两个
snapshot 的 `config` body 完全相同，仅 request identity/provenance 不同。旧红和
随后错误断言通过的报告保留，但后者不再作为合法拒绝的证明。

### K539-C1 配置内容身份与不可变绑定（2026-09-24）

定向 `zf-cr` 复核 `workflow_proposal.py`、`workflow_submission_binding.py`、
`run_admission.py`、`run_isolation.py` 与 `sidecar_refs.py`：配置控制面仍为 zf.yaml，
snapshot 是必需不可变证据，排队/准入只由 EventWriter 写事件；不新增状态源。
仓库 142 号设计文档与 backlogs 目录缺失，本轮不据其假定内容实施；历史显示早期
fanout/flow/goal 能力演进到近期 TestKit 审计，本轮只处理当前并发配置身份边界。
Web、外部 Provider 和全仓历史未作完整审查，不把此定向审查声称为全仓验收。

已确认：完整快照 SHA 应验证不可变来源，跨请求可执行配置比较则应使用完整 `config`
body 的稳定 JSON SHA，不忽略任何 config 字段。读取失败、空/损坏 descriptor、SHA
不符、错误 schema 或非 mapping config 均保持 fail-closed。历史无 descriptor 事件
保留 raw digest 路径，但显式损坏的 descriptor 不可借此回退。

实际 `config.run_contract.request_bound` 仅携带 descriptor，`run.admission.*` 仅携带
raw digest；必须先验证 snapshot，再关联同一个 SHA 的纯摘要事件。不认识的摘要仍
作为 divergence 拒绝，事件顺序不能影响结论。生产范围仅 run_isolation，既有
Run Contract/scopes/task/worktree/capacity 门禁不放宽；登记 ZF-TEST-093。

下一段审查发现 intake 接受 scope，但 submit/invoke 丢失该字段。须通过正式 intake
复现后修复透传并证明 concurrent 正向；不能把本段“缺 scope 正确排队”当作 K539-C
完成。测试 helper 只允许设置请求输入，不补写 canonical invoke/admission/terminal。

### K539-C2 scope 正式传递与准入（2026-09-24）

`workflow_intake.build_flow_intake` 与 `workflow_requests._bind_effective_manifest`
均保存 scope；`workflow_delivery.build_flow_submit_preview` 构造 payload 时遗漏，
`_submit_payload_to_workflow_invoke` 又使用显式字段白名单，因此两处均需接线。
正式 scoped General 与 Issue 请求复现 invoke.scope=None，登记 ZF-TEST-094；没有
以 monkeypatch 修改 canonical invoke 来绕过复现。

最小接线后 6 项正式入口用例通过，互斥 scopes 的两个 Run 都由真实 Kernel 准入和
派发，第三个请求命中容量门禁；缺范围、相同/父子范围与越界均排队，重建不重复。
直接 requests/controlled-action/admission/config/S0 联调 95 passed。前序十四文件
CLI/API/交付闭包 138 passed/5 failed；五红是现存 024/025/026，独立进程加载 HEAD
原 workflow_delivery（其他模块不变）仍相同五红，排除本轮 scope 增量所致。094 已 fixed，
原红集成保留，不外推完整 concurrent 交付已完成。
此外 PRD/Refactor 的绝对 target_root 会被旧 scope gate 视为非法范围，这一边界
尚未处理；不以删除该门禁绕过，下一步需区分项目根身份和相对工作范围的正式合同。

### K539-C3 双 Run 交错结果与终态（2026-09-24，实施前调查）

复核 SharedKindRoute 与 SharedKindWorker：worker view 代理一个 runtime/log/store，
Issue 的 writer→candidate→verify→Judge 使用原正式签名提交方法；General 使用既有
五 stage 的 immutable inputs/read ledger 与 artifact verifier。两者无需第二个配置或
runtime。plan/proposal/admission/terminal 由原生产入口建立，不允许为测试直接造成功。
本轮沿用 C1 的架构/历史图（HEAD 未移动），只补结果交错与终态路径，未覆盖领域仍显式保留。

新的测试须让两个 Run 同时 active，并交错 General reader 和 Issue writer/verifier
事实后重建，再分别完成 General-first/Issue-first。完成 gate 应读各 claim 的 immutable
合同；dossier/receipt 应以原 Run/terminal ID 绑定。候选只属于 Issue，不可借另一
Run 的 operation settled 或通用绿回执。两个终态后重复 tick 不应重派或改变回执 bytes。
这补齐 C2 的准入后缺口，不代替三类代码 Run 共享写资源、真实 Provider 或外部收件。

### K539-C4 正式身份拒绝与重放（2026-09-24）

`zf-cr` 定向补充审查沿用 §23/C1 的全局地图：正式 workflow-request/submit →
`durable_call_workflow._workflow_proposal_binding_error` → light/reader admission →
`runtime_authority_bridge._emit_workflow_invoke_rejected` → `run_admission` 事件投影。
ControlledAction 的现有 Task 绑定先由 workflow_task_request_rotation 拒绝；CLI
apply_flow_submit 入口没有该前置动作，须验证后续 Kernel collision fence，不能替它
写入伪造 invoke。不改变语义决策、canonical store 所有权或不可变合同。

正式故障矩阵首次 3 passed/1 failed：不同配置、缺失/篡改 effective snapshot 正确
排队；Run Contract 损坏正确拒绝，但重建后同一 source_event_id 新增第二个
workflow.invoke.rejected。GeneralRoute.reopen 清空内存 cursor 后重放真实事件；生产
handler 对 run.admission.rejected 有源事件去重，workflow.invoke.rejected 却无持久
去重。直接 caller 的普通重放/两次拒绝写入间中断探针均红，登记 ZF-TEST-095。

最小修复范围仅拒绝事件 writer：以 state_dir 内锁保护查找/追加，按 source_event_id
复用原拒绝事实；锁外仍调用原 admission 拒绝函数，补齐中断窗口，不能简单 early return。
不同 source 的同 Task 拒绝仍独立，不变更原门禁判断或放宽 scope/config/contract。
验收范围：正式 C4、拒绝直接 caller、workflow invoke/light/admission/事件合同及已有
General＋Issue 并发终态；其他全局领域/OS/Provider/外部收件未由本审查覆盖。

dev-verify 对 authority mixin 的继承关系选出 192 文件并建议全仓 deterministic；这与
本轮单一拒绝写入边界不相称，按 AGENTS 的影响闭包规则执行上述定向 caller/mock E2E
与 premerge，不执行建议的全仓套件，不修改 planner 或声称自动全量已通过。

C4 Task collision 第二轮调查：Generic 的 bootstrap Task 仅保存 anchor request_id，
并不必然有 workflow_task_request_binding 读取的 revision/origin 三字段。受控 submit
把它当作初次绑定，覆写在途父 Task；CLI submit 的 claim 也先于 admission 改写 Task。
所以仅等待 concurrent task_id collision 排队太晚，原测试的 CLI queued 预期不充分。
正式入口应在任何 Task binding/claim 前检查 EventLog 中活动 Run 的父 Task 归属；
同 Task、不同 Request 必须 STOP/409，不写第二 invoke、不改变原 Task。登记 096，
两入口的当前工作树红测先保留。旧 revision/origin/terminal rotation 检查继续保留，
新检查仅补 bootstrap 缺 binding 的活动归属，不手动补写 Task binding 来绕过复现。

### K539-C5 项目根与请求写范围（2026-09-24，实施前定向审查）

沿用 §23/C1–C4 地图和未移动的 HEAD，按 zf-cr 补查 run_isolation、run_contract、
workflow_refactor_preflight、light_flow、task_map、task_pipeline_dispatch 与正式共享
夹具。142 文档/backlogs 仍缺失；不重扫未变化历史、不触碰真实运行项目。

确认 source_refs.target_root 被 _scope_values 当成写范围，正式 PRD/Refactor 的绝对
项目根因此误报 invalid work scope。Run Contract 的 root_binding 和 preflight 已
区分项目/源/目标根；并发路径应单独校验 target 属于当前 project，并保持 scope 是
显式项目相对路径，不能把绝对 scope、异项目目标或目标本身当作写授权。

另 light_flow.synthesize_light_task_map 对绝对 target_root 生成 allowed_paths=["**",
"README.md"]，没有消费 invoke.scope；task_pipeline_dispatch 从 Task contract 取实际
workdir_write_scopes。因此仅准入正例不证明 writer 被隔离，需正式 Issue writer 探针。
fanout PRD/Refactor 的 task_map 来自签名 plan，仍需核对 plan scope 与请求范围关系。

覆盖：正式 request/proposal/submit→admission/replay、light→Task→writer；未覆盖：
代码双 Run 终态、外部收件、Provider/OS。先跑正常项目根、缺/越界/重叠范围和 light
写权限红证据，再修阻断。下一审查停止条件是明确实际 writer 权限的所有生产消费点，
不以增加看似互斥的夹具字段替代真实门禁。

C5 下游补查：签名 PRD/Refactor planner 将 allowed_paths/scope 扩为 answer.txt 与
docs/general，正式 plan/critic 都接受并真的派出 impl。两例红见 plan-scope-red；
说明之前只有同 task_map 内部路径互斥检查，没有核对已准入请求的范围。登记 099。
最小接线应在 writer_fanout_admission.load_writer_task_map 的 all_items 规范化后、
Task/operation 创建前，依 admitted.source_event_id 从 EventLog 读取原 invoke.scope。
只读校验不改 plan 内容，不覆盖 agent 判断；已有异常→plan admission cancel/返工
通道负责处理。并发下缺失 source binding 或非法范围 fail-closed；串行旧入口不变。
范围只证明明确相对前缀/子树包含，不能把任意 glob 解释成扩大后的权限。

C5b 双代码夹具调研与修正：旧 SharedKindWorker 三类代码都写 answer.txt，不能用于
互斥代码证明。新 ScopedCodeWorker 用两个正式 objective、两个文件/独立 probe/
source-index；保留 task-contract candidate gate。IssueRoute 的 writer 已支持
answer_file/probe_file，但 candidate 内容断言仍写死 answer.txt，已改为同一属性，
没有删除该断言。原默认 Issue 三个终态场景和注册复验 5 passed。

最终 code-concurrent-third 1 passed/154.99s：PRD/Refactor 同时 active/两个工作树，
先提交双方 writer 后重建，再对各自 candidate 的唯一改动运行验证。排队 Issue 在
首个代码 Run 真终态后获准，所有四类本地收据和 operation 身份通过且重建稳定。
首轮 probe stdout 夹具红、第二轮主动中断以及原 impact 注册红均保留；未重跑整批。
下一未覆盖是外部接收端实收，不能再用本地 owner-visible 请求充当完成证据。

### K540 外部 owner 回执：adapter→隔离接收端（2026-09-24，定向调研）

检查 `owner_visible_delivery.py`、`owner_visible_autodeliver.py`、`cli/feishu.py`、
`FeishuHttpTransport.send_card/send_message/list_recent` 与现有 Mock 测试。生产边界是：
Kernel 只写 `owner.visible_message.requested`，Feishu sidecar 在运行时边缘发送；
`owner.visible_message.delivered` 携带 source_event_id、target、receive_id、delivery_id
和 provider `feishu_message_id`。真实 HTTP adapter 已支持发送响应 message_id 与接收端
GET 最近消息，但现有套件没有把二者串起来。

本阶段不使用真实凭据（当前环境 FEISHU_APP_ID/SECRET/TOKEN、owner target 均缺失），
不向未知 owner 发送消息。新增隔离 HTTP receiver：真实 FeishuHttpTransport 请求形状
写入独立接收端，随后用同 adapter 的 `list_recent` 独立读回，核对 message_id、chat、
正文与 delivered 事件一致；第二次 delivery 不重复 POST。该证据证明 adapter/receipt
合同和幂等，不证明真实 Feishu 网络、权限或人类阅读。真实 provider 收件需在有明确
owner target/凭据后另跑显式 real_provider tier。

实施纠正：最初草稿 request_func 内存 receiver 仍是 HTTP stub，且 chat id 前缀写成
oc- 导致 preflight 正确拒绝；owner-receipt-first 53 passed/1 fixture failed 不作为
实收证据。已替换为独立 multiprocessing spawn HTTPServer，只监听 127.0.0.1 随机
端口，生产 adapter 不替换 urlopen/transport；parent 限制 connect 到该唯一端口并
清空代理/真实凭据，全部为公开测试凭据。以 General 真 Kernel terminal 驱动原
run_standard_tick_services 自动 delivery；接收记录通过独立 GET 查询。列入 S8
host_tests，默认确定性套件不隐式启动监听器。list_recent 仅规范化纯文本，card 正文
应独立读取 receiver 的原始 JSON，不据其空 content 误判生产丢失卡片。

### K541 watcher 硬退出后的控制面接管（2026-09-24，定向审查）

最新用户禁止继续 Feishu 联调，K540 留作历史未收尾；不再沿该分支设计或修复。
按 zf-cr 补查 `cli/start.py` 的 `_acquire_lock`、`_initialize_start_transport`、
`_requeue_inflight_for_start`、startup catch-up，`process_guard.py` 及真实 live helper。
普通 start 会重建 tmux session 并 reconcile 在途工作；既有 `--control-plane-only`
由生产 source_repair_restart_actuator 使用，保留原 worker session/在途状态。这是两种
不同的正式恢复入口，测试应分别验证，不能只通过“保留 worker”便关闭完整重启接管。

flock 在 owner SIGKILL 后由 OS 释放，但 loop.lock 与 watcher.pid.json 的旧字节还在；
新 SingleOwnerProcessGuard 必须按旧 PID 已死重建 epoch。测试不得先清锁或改 PID。
旧 worker 由 tmux 拥有，不随 watcher PID 死亡。TestKit 在 stdin 收据后、语义提交前
用私有 state 下两份同步标记暂停，保留正式派发/结果准入，父测试禁止 run_once。
仅杀 Popen 所有的 watcher，不杀 tmux server/宿主机/其他项目；以存活 worker 原 PID、
正式 start 新 PID/epoch、同一 Run 终态/签名结果/本地 receipt 检验恢复。完整 start
则允许正式替换 worker，但不允许丢失在途工作或凭重复调用伪造成功。

覆盖矩阵：guard/CLI/transport 的源码路径已查；真实在途进程接管待测；整机断电、
内核崩溃、真实 Provider 网络/配额、K530 原现场原因不在本次两条 host 链证明范围。
已有 064 replay 与 040 premerge 现状复核仍为 2 passed/1 strict xfailed、47 passed/4 failed；
它们不阻止开发独立 host 检测，不再重复跑无变化的红链。

K541-F1 夹具入口纠正：首次 preserve-workers host 已跨 SIGKILL 产生同 Run 的 Kernel
terminal 与五次签名结果，但通用 workflow-submit 创建的是 backlog bootstrap Task，
无法用它证明已有 managed Task 的 done。原红 `/tmp/zf-k541-preserve-first-20260924.xml`
为 1 failed/113.86s，takeover.json 确认 cleanup_ok=true；不是已确认的生产恢复缺陷。
对照 test_testkit_browser_live_runtime 与 test_workflow_start_service 的正式入口后，
保留 config-apply 安装 General，先通过 TaskStore 建 managed Task，再使用
WorkflowStartService.propose 与独立 CLI workflow start --apply 的 token 授权入口。
保留 Task done 断言，禁止事后强改 Task、补 terminal 或由父测试执行 Kernel。
此修正只解决夹具归属；两种真实接管尚待分别验证，不预先标 fixed/done。

managed 入口复验已到 Task done/local receipt，后续两次红是同一 K541-F2 计数夹具
错误：正式 scope 包含五份物理 worker 结果之外的一份 Kernel fanout aggregate。
managed.xml（1 failed/53.58s）与 managed-v2.xml（1 failed/89.11s）的 admission
集合逐条核对均为五个 worker 加一个 aggregate，无重复派发/准入证据。修正为精确
六事件，其中五项逐一绑定 worker 收据，第六项必须 origin=kernel、schema 为
fanout-aggregate-result.v1 且 operation_id 等于 scope child 的 parent_operation_id；
所有六项同 Run。不能仅把计数改大或用包含关系容忍未知多余事件。

K541-F3 证据身份漏洞：takeover-both 初跑虽 2 passed/190.69s，但回读 full-start
发现 retry 与原 dispatch 复用同 briefing_path，文件中的 run_id 已变为 retry-1；
run_worker 仅按 path/role 找事件，在新 dispatch 尚未 append 的窗口取到旧事件并
覆盖旧收据。真实 Kernel 已通过 task.attempt.superseded 关联替换，但测试物理收据
不能证明新身份，因此该绿灯不得作为 K541 完成依据。需先加直接红测试覆盖
“只有旧事件→新事件随后 append”和“新旧事件已并存”，再按实际 briefing 顶层
run_id 精确匹配；旧收据保持原 PID/hash/无 submission，新收据关联 retry_of_run_id、
同 operation 的 attempt superseded 链。此项为 TestKit 夹具修复，不是生产 Bug。

F3 直接红 2 failed/1.28s，按 raw briefing run_id 等待精确 dispatch 后 worker/guard
26 passed/1.44s。最终 takeover-bound 两个 host 参数 2 passed/157.11s：full-start
六份物理 handoff（旧 scope 无提交、新 scope 加四后续），preserve 五份 handoff；
每次五 worker 加一 Kernel aggregate 准入，Task done/本地交付、两次真实 heartbeat
不变性通过。独立回读两份 takeover.json 均 pass/cleanup_ok=true，owned_pids 均已退出。
旧 takeover-both 绿报告因 F3 证据不足不作最终验收；共享 browser caller 联调单独进行。

#### K541-F4/F5：投影读取边界与生产误归因更正

定向检查 `core/events/writer.py::_append_with_projectors`：先追加可被 worker 读到的
dispatch，再执行 fanout projector。GeneralRoute.result 与 IssueRoute.child 原先从
fanout manifest 取身份/refs，因此独立 worker 可能抢在投影前执行；同一路径也不能
保证它读到的投影仍属于收到的 dispatch。不可变 handoff 与可变 projection 不能混用。

General 首错 PID 1005741 是首次 scope，不是 retry；原始 dispatch 的 source manifest
已完整，retry nested payload 也保留 refs。ZF-TEST-101 曾被标 production fixed，
此归因已撤回为 misclassified；撤回上一 dispatch handoff keys 复制的两个生产 hunk
及人为缺字段的 stub 测试，保留 completed writer/project-root 等既有改动。
确定性反例仅在真实 General dispatch 之后替换指定 manifest 读取，模拟投影落后/
下一代投影；旧 helper **2 failed/2.31s**，只消费 dispatch 后直接闭包
**30 passed/18.02s**。没有关闭 SHA、read ledger 或结果准入 gate。

`/tmp/zf-k541-f4-host-final-20260924.xml`：**3 passed/1 failed/483.40s**。
两个接管参数 99.422s/88.119s，General browser 137.845s；四个现场均 cleanup_ok，
独立回读 owned PID 均已退出。Issue verifier 首次 PID 1120299 失败于
`assert child["target_commit"] == commit`，原 dispatch 顶层与 nested target_commit
均与唯一 candidate.ready 相同；之后 watchdog 观察到空进程树并 respawn，最后到达
terminal。瞬时投影未捕获，不能具体断言是下一代或生产重派丢失，也不关闭历史 K530。

F5 用真实 writer→candidate→verify，再对 manifest 注入空/错代 target，旧 helper
两项都在现场同一断言处失败（`verify-projection-red` **2 failed/5.30s**）；
dispatch 优先后 `verify-projection-green` **2 passed/16.73s**。初稿误把 writer
当 verifier 两次 KeyError 与全去投影导致 legacy writer 缺 nested payload 的
29 failed 均为新增夹具编写错误，原报告保留，不能当生产证据。最终 helper 保留
legacy/light 事件缺少的投影字段，仅以实际 handoff/top-level 字段覆盖当前派发身份。
共享 Issue/PRD/Refactor 调用方 **30 passed/595.06s**；Issue browser 修正后
`/tmp/zf-k541-f5-issue-host-20260924.xml` **1 passed/300.32s**，browser-gate
verdict=pass、restart_verified=true、cleanup_ok=true，事件中
`worker.runner.failed=0`、`worker.respawned=0`，终态/owner receipt 绑定同一 Run。
F5（TestKit）因此 fixed；真实 Provider、整机 OS crash、外部 owner 实收和 K530 首次
watchdog 根因仍未证明，不能由该本地脚本 worker 结果关闭。

验证范围更正：此前 dev-verify 的 207 是文件数，阈值回退误跑全仓，首层为
46 failed/16930 passed/20 skipped/96 xfailed/4 subtests passed；后续 tier 未执行。
其 receipt started_at_epoch=1790234944.9169796、status=failed，不沿用旧80项绿报告。
本轮 plan --paths 仅得7个文件，但包含禁止继续的 external_owner_receipt，故不用该
计划执行；改为明确直系调用方。Feishu、issue-merge 和降级项均未运行。

### K542 writer 排队批次独立进程恢复（2026-09-24，定向调研）

按 zf-cr 补证据模式，只检查本项入口与持久化边界，不重复全仓架构审查。
doc 142 在当前 checkout 仍缺失；以当前代码及测试为准，不从历史设计推定实现。

| 边界 | 已检查路径/入口 | 缺口及验收 |
| --- | --- | --- |
| 原测试 | `test_zaofu_testkit_s3_writer_batch_resume.py` | 原来只有同进程对象重建；抽出真实 queued timeout 夹具，保留全部旧断言 |
| 恢复入口 | `cli/recover.py::_run_workflow`、`control_actions_workflow_resume.py::_workflow_batch_resume` | 两者调用 `apply_workflow_resume`；新增独立 CLI dry-run/apply/replay，不把本地 CLI 说成 Web token-gate |
| 持久化合同 | `workflow_resume.py`、`workflow_resume_apply.py::_apply_batch_checkpoint`、`_apply_batch_task_map_ready` | 从旧 cancellation/TaskRef 重建 completed task 集合；gap-only marker 只能包含 TASK-2，保留 TASK-1 |
| 候选输入 | `writer_completed_tasks.py::completed_writer_task_ids`、writer/candidate caller | 新进程仅加载 zf.yaml/ledger/Git/workdir，候选必须同时含 a.txt/b.txt，旧 TASK-1 不重派 |
| 前序调用方 | S3 CLI lifecycle/full-preflight、candidate reader batch、writer completed/resume tests | 精确闭包联调，不运行全仓、真实 Provider 或 Feishu |

实施步骤：①生成真实 checkpoint 后退出解释器；②另起 CLI 进程 dry-run/apply/replay；
③新 Orchestrator 进程只派发 TASK-2 后退出；④再新进程完成 TASK-2 并核对 candidate；
⑤两次独立重放检查 dispatch/candidate 事件身份、内容和 transport send 不变。
保留每阶段 PID 与事件/commit 证据。脚本语义和 recording transport 仍是明确 Given；
本项不证明真实 worker 崩溃、OS crash、Provider 网络恢复或完整 shutdown/start preflight。
最终版本的精确影响闭包 `/tmp/zf-k542-impact-final-20260924.xml`：**56 passed/74.86s**，
包括同进程控制、独立进程正常退出和 `os._exit(73)` 两支、candidate reader batch、
完整 preflight 拒绝合同、completed input/queue checkpoint/event-first/control-action
与两个 writer/candidate caller；不是完整 S3 或全仓。前序 CLI lifecycle 四场景
`/tmp/zf-k542-cli-lifecycle-20260924.xml`：**4 passed/42.395s**。

K542-F1（TestKit）已修正：新 process harness 首版也对 clean-exit 调用了
`os._exit(0)`；最终版只有 abrupt 的 checkpoint/dispatch 调用 `os._exit(73)`，
clean 正常结束解释器。修正时已有联调进程加载旧 entry，故终止该批并检查无遗留 PID；
`/tmp/zf-k542-impact-20260924-lj57sk_x/pytest-testkit.json` 保留
`session_finished=false`，不是绿色证据。初轮未参数化控制报告
`/tmp/zf-k542-process-first-20260924.xml` 2 passed 仅证明当时的正常跨进程链。
无新增生产 Bug、无生产代码修改；正式 TestKit 收尾结果见 steps.md K542。

### K543 原生 CLI 停启恢复（2026-09-24，定向调研）

按 zf-cr 定向补证据：已有 K515 是真实 Provider 被杀＋lease 时钟输入的 Run Manager
恢复；K469 的 CLI stop/start 使用 dry-run tmux 与有界 watcher，不能代替独立 CLI 进程。
`cli/stop.py::run` → `GracefulShutdown.execute_fast` 会中断 operation、清理 worker、
记录 loop.stopped 并终止 watcher。`shutdown.py::_kill_watcher` 使用 loop.lock/guard PID，
所以不可把 stop 子进程接进原来由 pytest 自身充当 watcher 的夹具，否则会杀父测试。
采用独立 `zf start --simulation` 子进程，父测试只读 ledger/TaskAttempt/transcript。

主要步骤 → verify：
1. 隔离配置/Plan admission Given → 使用既有 blocking fixture，仅将两角色配置为真实
   Claude/tmux；不修改认证、权限或默认模型。已有 auth status 明确 loggedIn=true。
2. 启动实际 CLI → 观察同 attempt 的真实 assistant transcript 且尚未 settled，才执行
   另一个 CLI 进程的 stop --fast；确认旧 watcher/worker PID 消失、operation suspended。
3. 独立 recover inspect/dry-run/apply/replay → 唯一 operation checkpoint，dry-run 不改
   账本，首次 applied=1、再次=0；不直接写 resume/terminal 或人工代交结果。
4. 完整新 start（不跳过 preflight）→ 新 attempt 接续同 operation，由真实 Claude
   完成 impl/verify/candidate；精确 Git 内容、Task done、session/attempt 身份均核验。
5. 再次 stop/start → 不重派、不重复 candidate；最终 simulation.done、正式 stop 与
   scoped cleanup、来源前后摘要、JUnit/TestKit 报告留证；联调 K469/K542/原生观察器。

边界：先验 impl 中断，不以一支外推 verify、全部 graceful 模式、网络断流、OS crash 或
原生多 writer 批次。Feishu、issue-merge、降级项不进入配置或测试。

#### K543 第二轮宿主更新窗口（进行中证据）

恢复后 impl 已提交正式结果，verify 三次激活失败的原事件分别为
2026-09-24T10:28:08/18/29 UTC，failure_class=provider_launch_not_submitted；三份
诊断 pane 的具体错误均为 `env: claude: 没有那个文件或目录`。18:29 后的只读检查显示
认证仍 loggedIn=true；CLI 软链接新建时间 10:28:57 UTC，运行中 impl PID 1171444 的
`/proc/1171444/exe` 指向 deleted 旧安装，pane 显示更新已安装、重启生效。
这些证据支持宿主 CLI 安装替换窗口，不支持“未登录”或新增生产缺陷的归因。

`orchestrator_dispatch.py::_dispatch_recent_failure_cooldown_active` 是 3 次失败后
120 秒 backoff，不是永久终态。10:30:34 UTC runtime 自行成功派发 verify；未调用额外
recover/start，也未人工提交结果。事件与三个 launch-attempt 副本保存在
`/tmp/zf-k543-launch-evidence-20260924-f1DwXBbJ`。保留本轮至原定预算/最终判定，
不为了抹掉中间失败另起测试，也不把该宿主现象登记成 ZaoFu fixed。

#### K543 第二轮终态与启动 cwd 生产阻断（ZF-TEST-102）

第二轮实际到达 candidate，随后 binding transcript 断言失败，完整报告 fail/798.22s；
cleanup 与 source stable 均 true。初步把 logical binding 与 physical Provider ID 分开
就算通过的方案已撤回：`RoleSessionRegistry.activate_task_stage_session` 明确声明 binding
是 conversation authority；已有 native acceptance 也要求两者一致，不能降低 oracle。
第三轮未声明 opt-in，只 skip，外层 fail；第四轮误启动较弱断言版本后主动中断（41.59s），
stop wait 超时由 final fallback 回收，均不计为验收。弱断言已撤回，所有旧报告保留。

定向追踪：K510 的 `workflow_role_workdir._role_spawn_cwd` 已接
`task_pipeline_affinity.task_stage_spawn_cwd`，但 `cli/start.py` 完整启动仍独立使用通用
WorkdirManager，未消费 Task-stage placement。真实 CLI dry-run 两分支复现为 cwd 错误
和缺 Task workspace 仍启动（2 failed）。最小修复让完整启动复用该已有验证器：有 resume
obligation 时优先正确 cwd，没有 Task binding 则保留旧 role 逻辑；无效 binding/目录拒绝。
原样两例 2 passed，Provider transcript 未被归档/改写；后续闭包与真实重跑分别留证。

第五轮正式收尾：`/tmp/zf-k543-native-fifth-20260924-0k1fvs2c/testkit-run.json`
1 passed/375.733681s、verdict=pass、source stable、cleanup完整。同operation不同attempt，
新旧impl均为同一Claude session，verify独立session；dispatch与归档binding一致，两真实
transcript独立回读28/25条assistant。候选精确内容/Task done通过，第三次完整start的
startup catch-up后无重复dispatch/candidate。4份来源证据digest回读一致，214个观测PID均
退出、专属tmux/临时root已清理。无需重跑原native三链；144项影响闭包与S0 19项保留。
仅验收impl在途fast-stop子项，verify在途/graceful及网络/OS/原生writer批次继续开放。

### K544 verify 在途独立 CLI 恢复（2026-09-24，定向补充）

zf-cr 只读核对：`cli/stop.py::run` 选择 `GracefulShutdown.execute_fast`，
后者先中断 operation 再清理 worker/watcher；`cli/start.py` 的 resume obligation 已
复用 Task-stage cwd。K469 已有 impl/verify × fast/graceful 离线 caller，但 dry-run tmux
不能证明真实会话恢复。K543 独立 CLI 只在 impl 首条 assistant 后停止，缺 verify 路径。
当前 doc142/backlogs 目录缺失，未补造架构/待办结论；历史热点集中 TestKit、start、
orchestrator/transport。此次以现有代码、K469、102 detector 与原生证据交叉核对。

补充覆盖矩阵：stop/start/recover 入口→K543共享夹具（已有）；verify pinned target/
成功 impl 保留→新增 K544 原生路径（待验）；same-session/cwd→离线拒绝与原生严格断言；
network/OS/graceful/native writer→本阶段未覆盖。无 kernel/Provider mock 注入独立解释器。
实际实现使用两 stage 共用 helper，保留 K543 原 nodeid，verify 另一个显式 opt-in 节点。
先记录 impl 成功 attempt、唯一 settlement 和 target 的精确 README 内容，恢复后逐项
不变，派发必须恰为 impl/verify/verify。不能以新的 impl 实现或新 Provider session
代替 verify 接续；candidate 与再次 startup-catch-up 均继续严格验收。

K544 首轮失败保留：`/tmp/zf-k544-native-verify-20260924-n_ye_xfq/testkit-run.json`，
1 failed/290.503991s，verify 已派发但在 stop 前精确内容断言失败；cleanup/source stable
均通过，148 个观测 PID 已回收。根因是测试误读 `target_ref`（工作树 base commit），
不是生产丢失 impl：`task_pipeline_dispatch.py` 默认将 target_ref=workspace.base_commit，
`task_pipeline_targets.py` 从已准入 TaskRef 获取 source_commit 写 `target_commit` 与
target snapshot；正式 briefing 同样优先 target_commit。原生记录中两字段确实不同。
修正测试为 target_commit，同时用 base-commit/commit-1 不同值构造离线 detector：
仅改 new.target_commit 时旧 oracle 不拒绝，`/tmp/zf-k544-f1-red-20260924.xml`
1 failed/0.91s。归类 K544-F1 测试框架缺陷，不新增生产 ZF-TEST 编号，不改 src/。

K544 第二轮收尾：`/tmp/zf-k544-native-verify-second-20260924-q6p1d6o5/testkit-run.json`
1 passed/498.029855s，source stable、cleanup完整。成功impl attempt/settlement不变，
派发恰为impl/verify/verify；verify同operation/binding/session/workdir/target_commit，
旧attempt superseded，新attempt成功。精确candidate内容、Task done、归档、第三次
完整start不重复派发/候选均通过。独立回读四份证据digest、两transcript39/28条assistant、
248个已退出PID和消失的临时root；无activation.failed/continuity downgrade。
修正后的离线target/briefing/恢复/报告调用方63 passed/23.16s，精确dev-verify此前75
passed/48.81s，102/offline observer已接MAINLINE，不重跑无关真实normal/impl。

下一缺口调研：`GracefulShutdown.execute` 与fast共享operation中断/正式恢复，但额外
保存角色pane日志、memory .snapshotted及last-shutdown；该快照是诊断副本而非canonical
恢复来源。后续graceful用例除接续链外应检查快照来源/中断reason/停止命令，不能把
诊断快照当第二份真相，也不因fast两stage通过就关闭graceful矩阵。

### K545 graceful两stage原生链（2026-09-24，定向调研与步骤）

`shutdown.py::execute` 在transport退出、usage尾部对账后保存last-shutdown，再写
loop.stopped/终止watcher并改session stopped。因此诊断snapshot的session应仍是
shutdown_requested，events应为停止后完整账本的前缀，而不是与最终文件强求全等。
memory通过MemoryStore预置，shutdown复制现有memory并留下.snapshotted；snapshot
不具备生产restore caller。`tests/integration/test_phase3_flow.py::TestGracefulShutdown`
已有离线复制合同；K469有两stage×两模式状态链，K479有真实tmux清理/隔离，未覆盖
原生Provider在graceful后接续。只增加共享NativeCLI模式和两显式native参数节点。

测试验收：先在真实GracefulShutdown离线运行上检测snapshot前缀/operation原因/日志/
memory不变，再运行impl、verify原生两支。停机必须是无--fast的独立CLI，首个snapshot
与日志digest在JUnit固定；完整启动消费canonical状态，仍核验同session/operation、
新attempt、成功impl保留、候选精确内容与再次重启幂等。final兜底清理保留fast，不把
该兜底当graceful验收。未修改生产代码，不包含Feishu或/issue-merge测试。

K545首轮生产缺陷补充：snapshot/memory已通过，但缺活动角色日志。由代码看
`_save_transcripts`仍用role.name；PaneGridLayout.resolve与物理绑定均使用instance_id，
Composite.for_role fallback不会改写传给adapter的参数。隔离真实shutdown/Composite
的三组复现为name=id通过，单/双不同instance失败（1 passed/2 failed），不归因为
测试“应该忽略日志”。ZF-TEST-103最小两行修复用instance_id捕获和存档，防多实例覆盖；
detector/前序caller/mock E2E 60 passed，真实tmux三stop模式3 passed。首轮报告保留，
不先行关闭原生链；premerge仍4个既有040红，planner全量扩围未执行。

K545修复后串行原生完成：impl
`/tmp/zf-k545-native-graceful-impl-fixed-20260924-pneq1hcs/testkit-run.json`
1 passed/487.074128s；verify
`/tmp/zf-k545-native-graceful-verify-fixed-20260924-cfihbveq/testkit-run.json`
1 passed/457.013850s。两支source SHA同为
`5da3788a06cd01f95aa81f81358b60f93f8659b2d66d303b652a4e4ce605f5aa`，
各四份报告digest独立核对一致；快照events分别43/44、157/158，memory原值及
impl-1.log/verify-1.log检查通过。分别274/224个owned PID、私有tmux、临时root已清理。
两支均同session/operation接续，新attempt，唯一candidate/Task done，第三次start幂等；
verify支的成功impl attempt/settlement与target_commit保持不变。两stage transcript回读
impl场景34/32条assistant，verify场景24/28条；无activation.failed/continuity downgrade。
ZF-TEST-103至此具有离线红绿＋真实调用链绿，不关闭网络/OS/native writer与结构门禁。

### K546 原生 blocking writer 批次恢复（2026-09-24，调研）

已按 zf-cr 复核当前入口与历史：HEAD仍为既有TestKit收尾提交，当前大量未提交实现；
早期fanout/controller→generic workflow→Task Pipeline→TestKit为主要演进脉络，高变动
集中steps/coverage、orchestrator、start、writer准入。doc142与backlogs当前不存在，
不据历史文档补造权威。此次只深入writer/Task Pipeline/CLI caller，不做全仓审计或改造。

关键路由证据：`task_pipeline_fanout.py::admit_blocking_task_pipeline_generation`在blocking
模式消费legacy fanout派发，转换为generation；`task_pipeline_reconciler.py`按blocked_by
等待canonical terminal；`task_pipeline_integration.py`逐Task集成，terminal模块在所有
Task完成后冻结candidate。故K542的queued_wait_timeout/batch checkpoint不直接适用于
blocking。K546以同一task-map两任务（第二依赖第一）的原生批次作为下一实际验收，不
宣称替代legacy原生排队超时。恢复仍由CLI选择interrupted operation checkpoint。

覆盖矩阵：raw planner输入/plan CLI→复用K467夹具并支持多Task；full start/独立进程
stop/recover→K543 NativeCLI；已完成Task保留→新增attempt/settlement/集成receipt快照；
同一批候选完整性→a.txt与b.txt随机内容；会话→四个Task-stage独立session且中断stage
必须原session续接；terminal restart→比较canonical事件ID。未覆盖网络/配额、OS crash、
legacy原生排队超时、跨map真实接管和最终结构门禁。先离线mock语义联调，然后串行原生
显式opt-in；原生运行中不使用父进程monkeypatch、不代提交成功结果/恢复/终态事件。

调研结束后实施：只复用现有规划夹具和脚本worker提交方法；native wrapper单独观察
真实CLI/Provider，不将离线结果当native绿。禁测范围、8001、脏树和无提交边界保持。

夹具接线发现（K546-F1）：原K467 direct-runtime fixture未声明stage.task_map和aggregate
成功/失败；CLI loader/workflow-inspect均正确拒绝，不是生产阻断。补齐实际路径及
candidate_integration/candidate.ready/integration.failed后保留完整preflight。离线minimal
run-contract会在recover增加WARN审计；在准入前build/write实际加载配置合同后，整个
账本dry-run/replay字节不变检查通过，不过滤config事件。workflow graph核对
`graph.py::_terminal_policy`从聚合声明选择终态，`inspection.py`要求有producer；无需
虚构judge角色。新的loaded-config inspection与两任务offline共2 passed/11.92s。

前序调用方：审批/提交/legacy writer/CLI stop和observer共57 passed/76.56s；补充
blocking recovery/candidate reader/跨map/提示词调用方33 passed/39.50s。S0＋新增base内容
断言21 passed/43.63s。原生首轮6.9282s在preflight终止，source/cleanup正常，报告保留；
修复后第二轮已真实通过Plan审批、两Task generation准入，结果仍待正式终态验收。

### K546-B 连续性诊断的语义边界（2026-09-24）

第二轮已结束为1 failed/1245.661539s，不是持续运行。JUnit保留事件24的唯一downgrade，
其前19为TASK-1 done，20/21为task_stage_session_archived导致的suspended，其后28
为TASK-2首次impl派发，29才是fast-stop；恢复派发33复用同provider_session_id，
最终50为candidate.ready，四个独立binding均archived，cleanup/source通过。候选双文件
内容断言已先于失败断言执行；third-start未运行，所以不能把局部结果重解释为整条绿。

源码链：`task_pipeline_runtime.py::_activate_task_stage_binding`按Task-stage创建并激活
binding，`role_sessions.py::activate_task_stage_session`说明role entry只是物理槽的当前
provider投影，conversation identity由binding持有。`SpawnCoordinator.spawn`按该session
在精确cwd下有无transcript决定resume；`role_lifecycle_runtime.py`仅以previous=suspended
且is_resume=false发provider_session_resume_not_proven。故旧Task归档后新Task fresh
session与中断同Task恢复丢session是两种行为，不应为通过测试改生产或强制跨Task复用。

覆盖补充：runtime/orchestration、state/session、provider lifecycle三个边界已定向读取；
doc142/backlogs仍缺失，历史Git map与上节一致，不做全仓完成宣称。测试判据要求停机前
旧Task done+slot归档、不同Task/binding/session与下次派发匹配；恢复和第三次start后的
downgrade一律拒绝，且保留assert_replacement的同operation/session/cwd与新attempt。
新11负例和合法/无诊断情形连同批次offline14 passed，前序63 passed；记录K546-F2
测试框架缺陷fixed，不登记生产Issue。之后只跑该单native节点，再做S0收尾。

K546-C原生结果：第三轮`/tmp/zf-k546-native-writer-third-20260924-81__n4k0/testkit-run.json`
1 passed/1128.7909s，独立CLI三次start、两Task四stage（含一次impl重派）正式完成。
唯一downgrade确实在TASK-1 done/归档到TASK-2首次impl派发之间，恢复和terminal replay
无新增；不是删除失败证据，也未强行要求跨Task会话复用。精确双文件candidate、四个
archived binding/独立session、TASK-1完整evidence保留，5 dispatch/6 settlement
（4 stage和2 integration）/2 Task done/1 candidate，第三次start这些事件ID不变。
source前后SHA d6fc0d227507cd36ebe6bb00d9c04158b1ab1c1b0eedf3e1efb93c3d0d933e71，
四个报告digest及四个transcript摘要独立回读相符，494个PID退出且临时root消失。
精确dev-verify自动层14 passed/15.32s，S0 19 passed/35.60s，Ruff/diff-check通过。

下一legacy队列调研入口：`orchestrator_fanout.py::_check_fanout_timeouts`使用实际stage
timeout与fanout.started时间调用`close_expired_queued_wait`，只有存在eligible queued且
无pending/assigned/dispatched child才生成queued_wait_timeout，不消耗semantic attempt。
K542夹具用替换`_active_provider_turn`维持旧slot忙，不能原样用于native；正式Claude
`Orchestrator._active_provider_turn`以Task/dispatch和finished heartbeat锚定轮次，Codex
另有prompt/stop生命周期。因此K547先研究可真实维持/释放队列的配置或进程边界，再写
native夹具，不能用手工cancel/成功事件替代自然超时。该调研尚非实现或验收证据。

### K547 legacy queue 的 Provider turn 实际入口（2026-09-24）

zf-cr续查：`lifecycle_evidence_queries._active_task_for_instance`会排除terminal fanout
child；`Orchestrator._heartbeat_worker_finished_current_dispatch`由已准入的进展结果判
finished。因此Claude的`_active_provider_turn`在dev.build.done后结束，不能用结果之后的
sleep制造占槽。`writer_slot_reconciler`和writer completion立即释放并尝试派发下一项，
K542替换的active-turn lambda并非这条原生Claude行为。此处尚未发现生产错误。

Codex不同：`provider_turn_liveness.active_codex_turn`由真实
codex.hook.user_prompt_submit到同session/turn的stop或provider.turn.closed重建，优先于
Task finished检查。结果提交并不关闭Provider turn，因而可以真实保留TASK-2 queued；
`hook_recv`从registry或role-local transcript解析actor，正式CLI保留session_id/turn_id。
关闭另一session/turn不得解锁本轮。该路径不需要替换runtime方法，runtime重建也能从账本
恢复占槽事实。`close_expired_queued_wait`仍须无active child且实际stage deadline已过。

覆盖矩阵：runtime/slot/timeouts、Provider hook/turn/registry、CLI recover三个入口已定向
核对；Git/规则沿用K546同HEAD审计，doc142/backlogs依然缺失。尚未覆盖native Codex执行，
2026-09-24只读`codex login status`返回`Not logged in`；不登录、不复制凭证、不伪造hook
声称native。下一补充为正式hook CLI的离线输入→真实time/queue/checkpoint/CLI恢复，外部
语义和transport明确模拟；认证恢复后再单独原生。Claude资源竞争的替代真实场景仍需研究，
不从Codex认证缺失推断所有Provider都无法测，也不把离线测试替代原生验收。

K547-A执行：正式hook-recv独立CLI成功绑定actor/cause；Claude立即释放与Codex保持
排队两个控制通过。Codex关闭错误session/turn不解锁，重建仍保持；10秒deadline实际
10.212778s自然到期，检查点记录TASK-1完成、仅TASK-2待恢复。CLI dry-run/apply/replay
0/1/0，零effect账本字节不变，匹配stop后重建完成双文件candidate及两次replay。
首轮新夹具漏创建cwd（3 failed/1.85s）已修正为K547-F1 fixed；新3 passed/25.22s，
加elapsed断言与K542/K546、hook/turn/CLI lifecycle **53 passed/82.55s**，报告
`/tmp/zf-k547-hook-queue-impact-20260924.xml`。新文件已注册S3/MAINLINE，未启动native。

下一非阻断路线的host只读探针：`unshare --user --map-root-user --net true`因uid_map
Operation not permitted退出；`docker info --format '{{.ServerVersion}}'`为29.7.1。
这只证明隔离工具前置，不证明网络故障发生或恢复。K548应选择只作用于自有进程的
容器/透明转发故障边界，不改宿主防火墙/路由、共享认证或8001；先离线本地目标证明
切断/恢复和日志不含凭证，再接真实Provider。实际入口/安全路由仍需调研，不能先称可行。

## K548：进程独享网络故障边界（2026-09-24，定向调研）

zf-cr supplement：沿用已核对的同 HEAD 历史与权威清单，只检查网络故障链，不重跑
全仓审查。AGENTS/设计索引/三份规则已回读；doc142仍缺失，以实际 caller 为准。

| 范围 | 源码/现场 | 结论及尚未证明项 |
|---|---|---|
| tmux 环境 | `tmux.py::_ensure_provider_update_environment` | 会写 server 全局 update-environment 名单；不用于网络注入 |
| pane 启动 | `transport.py::_agent_env_prefix`、`spawn_coordinator.py` | runtime 环境走 argv，任意网络 env 并非角色参数；不新增生产配置 |
| Provider 配置 | `provider_session_config.py::provider_session_environment` | 仅 effort；Claude CLI help支持 `--settings` 额外配置，真实优先级尚须实测 |
| 当前上游 | 只读输出环境/用户 settings 的 endpoint scheme/host/port | 两者相同 HTTP 网关，非 HTTPS；不读取/输出 apiKeyHelper 内容或凭据 |
| 恢复 caller | `NativeCLI`、原生 writer batch、`assert_replacement` | 可复用独立 start/stop/recover 和严格 session/attempt/candidate oracle；尚未接网络 |

因此选**固定单一上游 TCP relay**，只监听127.0.0.1随机端口，上游数值IP/端口在创建时
固定。不接受客户端指定目的地，不改变HTTP/TLS内容；只保留连接编号、时间、字节数量、
故障代次与断开数量。故障关闭自己的socket、拒绝自己的新连接；恢复只开放新连接，旧
socket不能复活。线程、连接、连接等待和生命周期均有上限，退出必须回收。对本机HTTP
网关通过仅测试进程的`--settings`追加base URL/loopback NO_PROXY；不改用户文件、不
复制认证、不关闭TLS验证。未来HTTPS endpoint不能直接替换hostname，否则证书不匹配，
需另证安全路由；不提前宣称泛Provider支持。

下一验收顺序：本地socket/TLS字节透传与断开、两个relay/直接连接隔离、限额/清理 →
真实Claude专属进程的通路和断流证据 → 完整native CLI恢复。工具绿灯不是Provider恢复，
CLI支持某flag也不是环境覆盖成功；每个进程必须有实测非零上/下行计数及断流时间证据。

K548-A实证：独立Claude print经relay首请求8551上行字节被切断，故障期重连被拒，
第三连接8527/2980上下行和真实随机marker结果通过；正式source-bound报告
`/tmp/zf-k548-native-probe-20260924-yqz9t4xe/testkit-run.json`，1 passed/8.339907s。
后续完整CLI首轮117.304822s失败，relay accepted=0；进一步定位
`spawn_coordinator.py::spawn`追加生产hooks `--settings`，与wrapper重复。精确argv
子进程先1红，测试侧合并唯一settings、保留hooks/其余env后前序62绿，Issue K548-F1 fixed。
原生前后都必须观察通路，不能因为print模式通过就假设interactive启动也相同；原native红
不追改，待修复后单节点复验。私有tmux socket和options-only exec包装未改生产状态机。

K548-B最终：合并settings后还需把唯一全局flag放在`auth status`之前（第二轮6.882239s
preflight红，未进入Provider）；正式只读auth对照及62项调用方通过。第三轮native
`/tmp/zf-k548-native-cli-third-20260924-oirz2cya/testkit-run.json`
1 passed/802.497941s，source稳定；真切2条已双向传输连接，故障期3条重连被拒，
11.376753s后restore，完整stop/recover/start→同session新attempt→双stage候选→第三
start幂等、248 PID/root/socket清理通过。source/phase/JUnit与两transcript SHA独立回读。
因此当前HTTP网关短断流已不是假EOF/合成SDK证明；DNS/TLS故障、真实额度耗尽、长任务
和OS crash仍未由此证明。下一段先研究429/配额在真实CLI与runtime层的分类，禁止主动
耗尽真实账户配额；受控HTTP响应与真实服务配额事实必须在报告中分开。

## K549：真实客户端与受控HTTP限流/配额响应（2026-09-24）

zf-cr定向补充，规则/历史/存储边界沿用K548同HEAD审计；不扩展全仓或降级项。

| 已读入口 | 当前证据 | 缺口 |
|---|---|---|
| `stream_query_lifecycle.drain_messages` | 捕获包含rate_limit_event的SDK解析异常→RATE_LIMITED | K492为假的CLI输出，不证明真实HTTP429 |
| `StreamJsonTransport._publish_stream_turn` | 非成功结果→api_blocked/health=false；RATE_LIMITED分类特判 | RESULT_ERROR只用泛化reason，实际HTTP错误细节可能丢失，需真实入口复现 |
| `_record_background_failure` | 查询真实transcript API错误后分类 | SDK异常分支与ResultMessage错误分支不相同，不能只测分类helper |
| `provider_stop.classify_provider_stop` | 文本rate+limit、auth、context等 | 无独立quota taxonomy；不得自己假设应新增状态 |
| `Orchestrator._recover_provider_stop` | 依据分类进行cooldown/suspend/恢复，health是只读投影 | 错分类可能选择错误恢复动作，需实际事件链证明 |
| `test_zaofu_testkit_s5_stream_interruptions` | 真SDK/进程/锁/事件排空，CLI被替换 | 可复用方法，但K549必须用已安装CLI消费真实HTTP响应 |

有限计划：不扩通用API mock系统，只做loopback `/v1/messages`固定Anthropic错误形状
（429 rate_limit_error、400 invalid_request_error额度不足）和受控成功SSE。未知路径拒绝，
请求body有界读取但不保存，只记序号/status/byte_count/时刻；无forward能力。独立
CLAUDE_CONFIG_DIR、禁用用户/项目settings来源、假API key、无工具/MCP、禁用非必要流量，
不复制账号凭据。先直接已安装CLI核对响应协议，再接生产SDK/transport/drain caller。
默认不会推断具体版本的重试次数，先有界实测，若用例超时应保留timeout而不是强称限流
识别成功。真实外部配额事实与受控响应两层分开，前者不通过耗尽账户制造。

K549实际结果：已安装CLI两例通过；真实SDK/StreamJsonTransport/事件排空中，429一次
后200产生agent.text，持续429在4秒预算后agent.timeout且PID退出。额度400使CLI真实
transcript写billing_error、apiErrorStatus=400；后台失败事件保留kind/status，却分类成
transport_error。真实Kernel handler立即requeue、Task回backlog，health无需operator。
精确生产检测首次5绿1红（`/tmp/zf-k549-http-detector-first-20260924.xml`），登记104 open。
不新增生产taxonomy或修src；先把正确处理要求变成严格检测，错误之外的前置/cleanup不豁免。

restore补充依据：`StreamJsonTransport.spawn`会重新准备已停止角色，registry保留session；
失败后新send_task通过真实SDK resume执行，不需要手写成功事件。实际额度错误和持续
429两支经server.restore→显式spawn/send_task后新PID/同session/200文本通过，旧账本
前缀和Task状态不变。其范围只是transport复用，非正式TaskPipeline准入、充值、自动
Kernel恢复或完整candidate闭环；不得沿用K548的原生候选链结果替代本次HTTP判据。

## K550：未结算旧 impl 的原生跨 map 接管（2026-09-24，定向调研）

zf-cr supplement，沿用同HEAD的repository/history审计，回读规则/索引；doc142仍缺。
上一K549为已完成检测增量，不重复其host/长原生链。当前范围只补跨map原生缺口。

| 已读caller | 确认边界 | 尚未证明 |
|---|---|---|
| K468 `test_zaofu_testkit_s3_live_map_replacement` | 新map用TaskStore/Plan artifact/EventWriter及正式generation service准入；旧live lane等待 | inert transport，没有真实Provider |
| K471/K483 old verify handover | 新writer和旧reader分槽，真实tmux/Run Manager释放旧reader；迟到结果拒绝 | semantic worker仍由测试代交 |
| K510 `NativePipelineRecovery` | 真assistant之后pidfd杀自有Claude，lease通过Store注入时钟；正式watcher/Run Manager恢复 | 同operation，不是新map |
| `task_pipeline_recovery.reconcile_task_pipeline_redrives` | 只接受expired + run_manager +匹配request/completed的redrive | 跨map原生最终slot/binding/candidate要实测 |
| `TmuxSession.terminate_window` | ownership校验后TERM、有界等待、KILL和hard teardown | SIGSTOP旧Provider的实际退出要PID检查，不能只看完成事件 |
| `SemanticResultSubmitService._authorized_semantic_result` | 非running operation先拒绝，未消费binding后才进入授权/语义 | late probe必须核验拒绝代码且账本/Task/Attempt不变 |

选择旧impl单writer路线先闭合：真实assistant/未结算→pidfd SIGSTOP精确旧进程→新map
Given准入→真实slot-wait且旧lease仍sent→仅加速旧lease→正式恢复/新map派发→原生
impl/verify/candidate。暂停只作故障注入，不把假CLI结果或手写恢复事件当证明；新map
上游计划语义/审批仍为Given。本轮不是长任务、verify跨map或OS crash全矩阵。
复用原生夹具的可选recovery factory与全部终态判据；新observer另文件，禁止改生产
state machine。先离线caller通过再原生；失败调查、必要阻断修复和Issue标记按原授权。

K550-A补充：`task_pipeline_runtime.reconcile_task_pipeline_runtime`传所有attempt给
Reconciler，`used_roles`在角色选择处先排除sent旧attempt，因此live阶段不保证发出
`stage.waiting`。`read_task_pipeline_projection`反而过滤旧map attempt，不可充当该
调度输入证明。新增透明wrapper调用原Reconciler后记录实际idle-role blocker，不修改
其输出；观察到旧sent/running且无新派发后才注入过期时钟。新dispatch后立即校验旧PID
已退出（在watcher停止/测试finally之前），随后用真实submit入口只探测旧operation
非running状态门禁并核对账本/Task/Attempt不变；不声称验证完整旧语义或凭据。
第一轮observer+旧恢复6 passed/2.25s；正式RunManager/consumer/redrive/候选同实例及
重建两支补齐后5 passed/6.14s。该离线层明确模拟物理进程和语义结果，原生尚未验收。

K550原生首轮44.455884s失败：SIGSTOP使Claude成为stopped job，pane回shell；
TmuxTransport.is_alive返回False。`_activate_task_stage_binding`以alive为canonical
ownership检查前提，新map在旧operation suspended、无RM授权时重新绑槽/派发。
不是已证明的合法generation-fence恢复；interrupted和attempt.failed的recovery_owner
仍为run_manager，不能代替completed。严格RM/PID门禁保留。离线必须在expire后、RM
tick之前加一次调度wake，并让shell/probe-error的snapshot不伪装成已激活旧cwd，才能
直接复现绕过：2绿4红。修复将已有binding纳入检查而不依赖host liveness；候选13绿，
需与原有inert跨map合同联调，旧测试若直接绕过RM应升级为正式授权而非保留漏洞。

K550最终（2026-09-25）：K468/K471改走正式RM授权，K483真实tmux调用方保留，
跨map12绿、自动影响闭包240绿/1缺历史归档skip、mock E2E/model5绿。第二轮原生
`/tmp/zf-k550-native-second-20260924-v77lbdaw/testkit-run.json` 1 passed/1238.71s，
三个RM因果回执和旧PID在shutdown前退出通过；新map impl/verify、唯一精确candidate、
Task done、双绑定归档、两次Orchestrator重建与最终11个观测PID清理全部通过。
生产周期未被替换：15:54:25首次RM tick，15:59:29正式request，15:59:34 completed/
redrive，15:59:40新impl，16:08:10新verify。外层supervisor周期300秒解释约5分钟等待；
900秒仅RM idle刷新，不能把二者混用。四份source/phase/JUnit摘要独立回读匹配，
源摘要d747557653a99eb3dcb8da46f2b205c0b0920827f10926dcf07a2551826bb69a稳定。
105 fixed；保留首轮red。late probe仅测operation状态门禁，上游Plan为Given；
不外推旧verify跨map、OS crash、长任务或最终发布绿灯。原生及S0句柄均已终态。

## K551：旧 verify 跨 map 原生接管（2026-09-25）

zf-cr定向supplement，沿用同HEAD历史/仓库地图，重读AGENTS、rules和当前索引；
doc142仍缺，依据当前代码及K471/K483，未做新的全仓审查。
`test_zaofu_testkit_s3_old_verify_replacement._expired_old_verify_handover`证明旧impl
先成功、旧verify在途时新writer先行，旧pinned commit不变；新impl结算后reader占槽，
之后才过期/恢复。`test_zaofu_testkit_s5_real_tmux_map_recovery`接真实tmux物理替换，
但语义仍由测试提交。`TaskPipelineReconciler.reconcile`按stage选择pool，used_roles
包含所有active attempts，reader无idle role时标no_compatible_idle_role；不能复用
impl_ready等待来证明verify_ready占槽。`native_map_replacement`当前只支持旧impl，
共享native夹具又硬编码3次派发和唯一dev.build.done；这是夹具扩展点，不是生产故障。

有限实现：现有observer显式stage=verify；只在两次真实派发、verify有assistant后暂停，
保存旧impl成功attempt/settlement及旧verify target/workdir HEAD；观察真实新impl结算、
verify_ready无idle reader后才注入旧lease时钟。复用严格RM三个回执/停机前PID退出与
非running状态拒绝；允许四次派发和两个impl结果，但最终候选只接受新map的实现。
不代交任何native结果，不强行释放reader，不缩短生产RM周期。离线同实例/重建先验证；
真实Tier有3个完整新旧stage工作量，采用已有显式1800秒上限，不追溯修改旧报告。
覆盖边界：Plan/map Given、精确旧Provider SIGSTOP不是OS crash，迟到probe仅状态门禁；
不是全部Provider长任务或容量矩阵。此调研完成后才编辑测试。

### K551-B：实现提交身份阻断定向补充（2026-09-25）

前一统计turn只是状态报告，无新增验收；本轮先取原生终态，再推进离线复现。
原生首轮实际1440秒预算，1446.965983秒失败且0候选，不能沿用计划1800秒叙述。
临时项目已清理，原JUnit没有完整dev.build.done/TaskRef/waiting payload，不冒称重读
已删除账本。新impl transcript实际提交b99ef91，顶层target、自检source/target和receipt
均为7位。沿用zf-cr同HEAD仓库/history map，定向读取typed_result_admission、
result_submit、task_refs、task_pipeline_targets、impl_self_check及直接测试；doc142
仍缺。未重做全仓审计，尚未覆盖新原生终态、OS crash/容量等独立缺口。

代码链：typed admission只比自报字段相等；compatibility projection原样保留短source；
TaskRefManager用rev-parse将其解析完整OID；prepare_verify_target的self-check准入
要求dev.build.done.source_commit与TaskRef精确相等，因此进入waiting_for_impl_result。
`/tmp/zf-k551-106-red-20260925.xml`首次1 failed/1.15s，短SHA确实被正式submit准入。
补完整因果oracle后，仅临时撤回本轮15行修复并原样恢复的
`/tmp/zf-k551-106-causal-red-20260925.xml` 1 failed/1.27s，保存短source→完整TaskRef→
waiting_for_impl_result。真实worktree/commit/read ledger/submit/reducer参与，未改返回值。
这证明独立生产准入缺口，并与原生现象一致，不替代修复后K551原生全链验收。

选择在contract-bound成功implementation的typed admission前置完整小写40/64位hex
检查；不解析可漂移符号引用，不改不可变语义正文/receipt，不放宽下游前缀匹配。
无snapshot legacy、非semantic、failed/blocked仍按既有边界；现有单测target-1占位
改成完整OID数据。中间“只拒绝hex短串”候选会漏HEAD，两支测试已检出并撤回，不能
作为最终绿色版本。强校验最终直接73绿；后续尚需caller/前序/原生验证。

### K551-C：通用审批页与检测器的实际边界（2026-09-25）

第二轮不是再次短SHA卡死：旧impl以完整SHA结算，新impl首个Bash却调用python读取
briefing，在Claude allowlist下显示“This command requires approval / Do you want
to proceed?”。原生helper只识别trust/onboarding；生产marker也只识别shell-syntax
审批。初期以为统一marker已覆盖现场是错误推断，旧32绿不能证明，已明确撤回。

zf-cr沿用同HEAD已有地图/锚点，定向检查provider_interactive_prompt、native watcher、
TmuxTransport.wait_ready、pane_probe和prompt matrix。classification在kernel边缘
属机械观察，不能自动授权。采用双文本条件返回既有permission_confirmation；将
测试helper接该分类器以便原生流程发现交互时快速失败，保留权限拒绝而非代按确认。
未改provider config、角色allowlist和工作者语义结果。角色已有Read/Write/Edit及git/zf
工具使用指令，worker选择python属于本轮agent行为；该分类修复不保证worker今后遵从。

停测前准确核对pytest PID后发送SIGINT，原watcher停止/清理，报告578.839371秒失败，
case575.53秒，非1800秒耗尽。独立核对四份来源摘要、native_processes审批原文、
cleanup全true及旧root/PID消失。精确原样2红→直接56绿、自动闭包106绿，107 fixed。
6支106提交身份与native事件保留7绿，确保未用审批修复削弱前序。第三轮仍需完整
新map候选与RM/PID证据；只分类/快失败不是端到端恢复成功。

### K551-D 终态补记（2026-09-25）

第三轮 `/tmp/zf-k551-native-old-verify-third-20260925/testkit-run.json` 已终态：
1 passed/903.459897s，exit=0、verdict=pass、session_finished=true。四次实际派发
为旧 impl/旧 verify/新 impl/新 verify；四次 settlement 为旧 impl/新 impl/新 verify/
integration，旧 verify 未结算。旧 operation 接管观测为 requested，非 running。
reader 实际 no_compatible_idle_role、RM request/completed/redrive、新 verify
严格有序；旧 4 个观测 PID 在 watcher shutdown 前退出。late submit 仅证明
operation_not_running 状态门禁与无状态变化，不外推完整迟到凭据/语义矩阵。
两个 dev.build.done 完整 source_commit 与对应 TaskRef 一致；唯一新 map candidate、
精确 README、Task done、两次 Orchestrator 重建不增加 dispatch/candidate 均通过。
来源/phase/JUnit 四摘要一致，source SHA
639e938dd8711989b8e330be04b677cf20a0e4036a44242c82db883ea2021c68；双 binding
archived，transcript 45/41 assistant；最终 10 个观测 PID 与临时 root 均不存在。
106 已标 fixed，107 也获得本次原生通过证据；两份旧失败报告保留。
最后 S0 v2 为19 passed/25.18s，premerge 47 passed/4 既有040红。
只关闭旧 verify 跨 map 场景；OS crash、长任务/容量、legacy 原生队列和最终统一验收不关闭。

## K552：容量与长运行定向补充（2026-09-25）

zf-cr 定向 supplement，沿用同 HEAD 142a62a 的历史/仓库地图，重读 AGENTS、
code/backlogs/docs rules 与 design index；142 文档仍缺失，不虚构其内容。
本次只覆盖 performance_cost_context、runtime_orchestration、tests_docs 的容量子域，
不是一次完整架构审查；无生产状态写入、无新 Provider 启动。

| 原要求 | 当前证据/入口 | 缺口与下一动作 |
|---|---|---|
| PERF-04 | s3_fanout 两 child；K539 同 runtime 双代码/四 Run 交付 | 有功能基线但无较大 child 批次；以2控制/32采样运行真实 fanout admission、dispatch、fanin、重建 |
| PERF-06 | fanout_hardening、K450 fanout model 有预算0/1/2的少量 child timeout | 批量 timeout 后重试/终态/恢复扫描有界缺口；有限规模复用实际 run_once，不手写 terminal |
| PERF-05 | S4 1000-cycle restart、K404 两小时单VU Web soak | 非长时间实际工作循环的内存/FD/tmux/端口/Task 全资源证据；保持未完成 |
| PRV-13 | context_hard_cap FakeReader/UsageReport、_start_recycle spy；context_recycle_e2e fake transport | 不能声称原生长任务；后续把实际预算/回收入口与资源监测接起来 |

生产入口：Orchestrator.run_once → reader fanout dispatch → EventWriter 账本与
FanoutManifestProjector → child 输入准入/fanin/synth；timeout sweep 消费配置预算和
manifest/dispatch 时间，retry 生成新 run_id。transport 仅记录派发，mock 结果显式作为
worker 输入；独立 oracle 比较预先定义的 role 集合、每 child 次数、结果与派发身份，
不是从生产输出反推预期数量。容量测试允许源事件重复投递，但不得重复产生派发/汇总。
新测试先走正式 TestKit 留证，再与原有 fanout/恢复模块联调；生产异常只登记检出，
阻断整体目标才修。32 是有限工程采样、不是生产 SLA 或物理32 Provider 并发证明。
legacy Codex 认证、OS crash、Provider 长任务仍需各自独立验收；本轮不访问外部项目，
不执行可能修改配置/commit 的 tests/longhorizon 驱动，不重复两小时 Web soak。

### K552-A 实测与证据边界（2026-09-25）

2/32 reader 经真实dispatch/fanin、逆序mock结果重复投递、中途重建及terminal重建通过。
8 reader timeout分别用注入时钟与真实3秒预算：逐child两dispatch/新run_id，耗尽后
唯一failed/timeout/aggregate；3次重建/6次tick不增加这些工作且无active reader fanout。
mock Provider idle为真实setter的Given；不是物理Provider或全局RunManager queue证明。

初次S3 177 passed/4既有xfail，但证据文件预算耗尽，外层harness_failure。回读失败
fixture有305文件/1173667字节，超默认128文件。局部为32-child设置512文件上限，
保留512 entry/4MiB case/64MiB run/no-follow/digest合同。撤销中途取消采集的错误方案；
不是省略raw evidence变绿。首轮helper误导入、plain pytest未判opt-in均为测试夹具
问题，F1/F2/F3已修正；失败报告保留，未新增生产Issue。

最终精确六文件TestKit `/tmp/zf-k552-targeted-evidence-20260925` **70 passed**，
25.981671s、verdict=pass/source stable；全部四例registered且errors=[]。
修正后普通pytest新四例+S0 **23 passed/45.37s**；最后单独S0 **19 passed/25.41s**，
另39个完整恢复caller已过。中途取消采集版本的4 passed/16.44s不是最终验收证据。
两次宽范围S3复跑被主动SIGINT终止，不作为全阶段绿色依据；不再重复无变化全S3。
PERF-04是有限采样；PERF-06仅reader fanout批量超时，持续压力/其他恢复队列仍开放。
下一补充入口：PERF-05实际mock工作循环资源曲线与PRV-13预算/回收，须与原生长任务
分别定义有限验收，不能从大量合成heartbeat或Web soak外推。

### K552-B：预算调用链 supplement 与实测（2026-09-25）

zf-cr 定向只读调研沿用同HEAD仓库地图，重读规则和index；142仍不存在，未假设其内容。
本次 performance_cost_context 覆盖 `housekeeping.apply_agent_usage_event`、
`CostTracker.record_usage`、`Orchestrator.run_once/_run_budget_sweep`、
`workflow_budget_guard`、`task_attempt_operation_settlement`、`workflow_task_lifecycle`。
原PRV-13明确允许fake usage；不需要为该确定性预算合同启动真实Provider。

- usage原生报价 `total_cost_usd` 优先；unknown mock model无报价只产生unpriced记录，
  不能从150000token虚构USD。初稿1失败是测试预期错误，不是生产budget绕过。
- run_once先消费pushed usage，再budget sweep，再dispatch；操作终态写入账本后还需
  后续tick消费。既有test_workflow_budget_guard直接调用guard/settlement，新的7个
  caller测试不用这两个内部调用。运行中operation/attempt是复用Given，不冒充真实派发。
- token/USD分别4→10、0.004→0.01；显式注入operation时钟start+0.25→start+2；
  unknown-model fail-closed与fail-open分离；global dispatch对照0.009/0.01。
- 独立预期：一次budget、operation block、attempt failure、run blocked、Task状态转换；
  三次重建后重复usage仍只2条费用/原终态身份，transport终止一次，不产生task.done。
- 250次空闲重建草稿删除，不能用于PERF-05。实际持续工作、RSS/FD/进程/tmux/端口
  及任务残留仍uncovered；原生长任务和run-wide完整准入也不由本轮证明。

正式8文件TestKit `/tmp/zf-k552-budget-evidence-20260925/testkit-run.json`：
96 passed/11.990774s，source stable、7例ordinary注册无错误。S4 registry/matrix同步；
K552-F4 fixed，无生产修改/新生产Issue。本轮仅预算子阶段收口，不把短测试当资源soak。

### K552-C：持续 Task Pipeline 与资源生命周期定向调研（2026-09-25）

沿用 zf-cr supplement 的同 HEAD 仓库地图；复核原方案 PERF-05、
`planning_runtime_at`、`TaskStore.get/list_all`、`FileLock`、
`task_pipeline_terminal._release_archived_task_stage_slot`、
`TmuxTransport.terminate` 与 `TmuxSession.terminate_window`。
只检查上述实际调用链，不宣称全仓或 Doc142 审查（当前 checkout 仍缺该文件）。

- 同一 Orchestrator 从原始 Plan 经 controlled approval、真实 tmux/mock-cat、签名
  impl/verify 结果和实际 Git 验证持续完成依赖 Task；仅终态后重建，不每轮重建掩盖增长。
- TaskStore.list_all 仅活动 Task；归档后的完成判定必须用 get。FileLock 故意保留
  inode，不能把 `*.lock` 存在算泄漏；独立 nonblocking flock 才能检测未释放锁。
- Linux /proc 提供 RSS、FD、socket；缺 PID/传感器报错，不返回假零。工作进程与
  bootstrap shell 均纳入观测，最终 tmux/全部 PID 必须不存在。
- 3 Task 接线 1 passed/35.68s；12 Task 复现 1 passed/180.64s，均不是长时验收。
  首次 source-bound 32 Task 在第12个 Task 后失败，报告
  `/tmp/zf-k552-workloop-bound-20260925/testkit-run.json`（fail/195.608746s）。
  PID 在3秒内未消失，但 finally 全部退出；不能把这一裸红替换成小样本绿灯。
- 随后诊断 `/tmp/zf-k552-workloop-diag32b-20260925.xml` 1 failed/30.36s：
  TASK-LOOP-001 的 verify shell PID 1628738 为 `Z (zombie)`，PPid 1627567。
  它已经退出而未被 tmux 父进程回收，不能误称活 worker；整个 session 清理后消失。
  更早 diag32 1 failed/17.25s 的临时 Python monkeypatch 没有进入 pytest 重载模块，
  没有取得诊断；保留失败，不拿它支持进程状态归因。
- 判据精化：当前 Task 不得有 running worker，记录 zombie；**前一 Task 的全部 PID
  必须在下一 Task 完成时消失**，防止 zombie 累积；最终 session 清理后全部 PID 消失。
  不提高3秒等待，不允许无界 zombie，不改生产终止逻辑。
  传感器直接4例（活进程拒绝、真实 zombie 与 wait 后回收、socket/FD/锁、缺 PID）
  `/tmp/zf-k552-workloop-probe-20260925.xml` 4 passed/0.22s。

32 Task 来源绑定 v2 **1 passed/847.484776s**，source stable；8-selector前序联调
**69 passed/73.591762s**。host四摘要独立核对，129 PID与root消失；完整证据见steps
K552-C done和coverage.latest_k552_workloop。两次zombie都在下一Task完成前回收。
RSS102296→峰258128→final251524KiB，FD恒12；Task间隔约13→42s。
下一定向调研区分测试保留/账本缓存/运行时对象与时延增长，不提高上限来声称稳定。
RSS只设安全上限，不据此宣称无长期泄漏。
未启动 Web/API listener，不能以 socket 空集证明 Web 端口生命周期。
真实 Provider 长任务、OS crash、104 修复、K530、040与最终统一验收仍未覆盖。

### K552-D 定向补充：分量与真实 listener 生命周期（2026-09-25）

zf-cr延续同HEAD地图，仅复核 `EventLog.read_all` 的 `_READ_ALL_CACHE/_READ_ALL_FOLD`、
`EventIndex.load/observe/flush`、`browser_app.create_app`、生产 `_tail_events`、
TestKit `terminate_process_group` 和现有 browser truth-chain finally。

- EventLog进程级缓存保留全事件对象，index另保存完整dict和旧mtime文件缓存；它们的
  条目数有界不等于字节恒定。真实账本增长可导致必要缓存增长，但尚不能解释RSS全部增量。
- 新state分量传感器不跟随symlink、排除`.tmp`，测顶层目录字节与文件数；5例直接测试
  通过。来源绑定3Task `/tmp/zf-k552-workloop-attribution-3-20260925/testkit-run.json`
  1 passed/36.606547s：state563459→1223741 bytes，RSS108492→115900KiB。
  这是测量接线，不是堆归因；32Task来源绑定归因样本正在执行，运行中不改源码。
- 现有browser host以随机端口启动真实uvicorn，并用finally检验进程/port；未覆盖
  测试进程硬退出时独立session API逃出外层pytest进程组的组合。新小矩阵复用API
  factory与已有cleanup，不引入浏览器、不重复UI构建或既有长Web soak。
- 生产SSE入口 `/api/stream` 使用 `_tail_events`，发connected后检查disconnect并返回；
  用真实流连接/关闭与子进程socketFD集合观测验证，不能用TestClient模拟代替listener。
- outer runner启动pytest为新session，并在finally执行process-group TERM/KILL；
  新API子进程留在这一个测试group，正常路径以精确PID终止；单进程uvicorn不用workers。
  需ready-synchronized外层timeout实跑验证，而不是只看到命令或finally就声称完成。

未证明任何新生产缺陷；D1b堆归因、D2/D3真实边界与完整PERF-05继续开放。

### K552-D 最终阶段收口（2026-09-25）

listener host 阶段已按预绑定随机端口重新执行，避免“释放端口后再 bind”的竞态。真实
`browser_app.create_app` + uvicorn factory 由外层 TestKit 进程组托管；SSE 连接建立后，
反复断开时 API 进程的 socket inode 集合必须回到 ready 基线。正常退出、调用方断言异常、
API `SIGKILL`、inner pytest timeout 和 runner `SIGKILL` 五类均通过，端口在清理后可再次
绑定。inner pytest 的原始未完成状态仍保持 `harness_failure`，不会因 finally 清理而变绿。

本轮发现并修正两个测试框架边界：一是 runner 只有 timeout 才清理会让正常异常退出留下
descendant，现改为所有 process.wait 路径 finally 清理 process group；二是新增 host 文件
必须进入 coverage matrix 的显式 host_tests，否则 S0 fail-closed。两者均为 TestKit/注册
问题，不是 ZaoFu 生产问题，未写入 ZaoFu_issue.md。

证据：listener source-bound 5 passed/16.253694s；五文件前序联调 source-bound
56 passed/89.140125s；修正登记后的 S0 19 passed/31.698390s。D1a 32-task state 分量
曲线已通过；D1b 的 Python 分配与 EventLog/index 缓存归因仍未完成，不能据相关性宣称
无泄漏或完成 PERF-05。

补充回归：新增 host 文件首次接入后，`tests/testkit/test_parallel.py` 的 xdist 合并
合同仍硬编码旧 S0 的 11 项，导致前序联调出现 **141 passed/1 failed**。按当前真实
collection 动态核对 nodeid 集合后修正；定点 parallel/case-evidence 回归 **8 passed**。
这是测试合同随注册面漂移的问题，不是 ZaoFu 生产 Issue；原始失败报告保留在
`/tmp/zf-k552-runner-callers-20260925.xml`。

最终选定 12 文件 caller 复验 `/tmp/zf-k552-runner-callers-fixed-20260925.xml`：
**142 passed/163.78s**；结构哨兵 **47 passed/4 既有 ZF-TEST-040 failed/8.07s**。

### K552-D1b 初步接线与失败保留（2026-09-25）

复核 `EventLog.read_all` 后明确：精确 key 缓存、fold 缓存会共享事件对象，不能把引用数
相加当唯一字节数；缓存数量先检查 `>8` 后再插入，实际瞬时可为 9，不能把 `<=8` 当
正确 oracle。`EventIndex.load` 的文件缓存还保留解析字典，而 `close()` 只 flush 不清空。
因此只观测指定 state 的缓存数量/事件引用与分配点，不修改生产缓存或创建因果结论。
单帧 tracemalloc 记录当前/峰值、跟踪器自身开销、完整分域汇总及有限 top 分配文件。
它只归因分配位置，不代表对象 owner；JSON/stdlib 分配不能全部算作 ZaoFu 或测试端。

首版 `/tmp/zf-k552-workloop-allocation-3-20260925/testkit-run.json` 为
**harness_failure/147.571824s**：缩进错误让临时目录提前释放，源码在执行中又被改动，
source stable=false；已发送精确 pytest SIGINT，不作为任何验收依据。此为本轮夹具错误，
不是生产 bug。`K552-D-F6` 作用域已修正，小样本 v2 **1 passed/42.170971s**，但该版
还只有全局缓存计数，后续 scoped/控制组版本另行验收。首轮原始失败证据完整保留。

首轮自己的 bootstrap shell PID1705333 最终通过精确 PID TERM/KILL 退出，所有五个记录
PID 独立检查已消失；没有终止共享 tmux server。失败夹具重建的独占临时 root 已移动到
该报告目录的 `retained-simulation-root` 留存，原运行路径清除，非删除证据。
修正版本还用 pytest finalizer 确保断言失败时也停止本轮 tracer，不接管外部已启动 tracer。
传感器最终 **8 passed/0.47s**，包含缺 tracer 拒绝、scope 隔离、1 MiB 分配/释放正负对照。
D1b 仍需同实际工作循环做完整源绑定采样与终态对照；小样本不能替代 32 Task/长期验收。

### K552-D1b 最终有限接线（2026-09-25）

按缓存实现继续细化观测：`_READ_ALL_CACHE` 的 key 含 mtime/size，`_READ_ALL_FOLD` 与
`_INDEX_FILE_CACHE` 是进程级共享缓存；不同 state 的 key 必须隔离。缓存引用共享对象，
不能把 `len` 或引用总数换算成 retained bytes。传感器现只在显式 opt-in 时调用
`tracemalloc`，按 `src/`、`tests/`、other 分域汇总，并记录 tracer 自身开销；外部已有
tracer 时拒绝覆盖，测试 finalizer 确保异常仍停止本轮 tracer。

实际工作循环结果：2-task traced source-bound `/tmp/zf-k552-workloop-allocation-2-v3-20260925`
通过 1/41.921002s；2-task untraced control + sensors `/tmp/zf-k552-workloop-untraced-control-20260925`
通过 9/26.594871s；8-task traced `/tmp/zf-k552-workloop-allocation-8-20260925`
通过 1/306.531701s。8-task 的 RSS 102356→174416→176300 KiB，traced current
16506509B、peak32854092B、tracer overhead11043536B；分域 src1324763、tests50424、
other15131414B。指定 state 的 read/fold/index cache 7/1/4，事件引用4355、index字典
2115。所有 source/JUnit/phase 摘要及 PID/root 清理均独立核验。

这些结果仅说明测量路径可执行并提供分配位置/缓存计数；JSON/解释器/测试框架分配占比高，
无法由 traceback 推断对象 owner。D1b 的终态 GC/对象释放对照和长期 32 Task 追踪成本仍
开放，不能把 allocation probe 当 PERF-05 或无泄漏证明。

### K552-E 终态对象释放定向调研（2026-09-25）

zf-cr supplement 沿用同 HEAD 地图，当前仍缺 doc142，不声称完整架构审查。核对
`Orchestrator.__init__` 的 EventLog/Writer/TaskStore 所有权、`planning_runtime_at`
fixture 的 monkeypatch 生命周期，以及 EventLog/index 的进程级缓存。使用 weakref
跟踪原实例及两次重建，不用强引用列表来制造泄漏；`EventLog.close()` 只 flush，不能
当作缓存释放动作。`EventWriter` 本身持有 log，若释放 runtime 时仍为 finally 保留
writer，就无法证明 log 被释放，因此必须在现有真值留证和物理清理之后才开始释放实验。

顺序：terminal_held → held_after_gc → test_buffers_released → runtime_released。
held 控制必须证明最新 runtime 仍活着，最后再去掉引用并检查三代 runtime/log/index
weakref；用同 state 缓存计数与五个文件 SHA-256 不变作为反篡改控制。只丢弃已经序列化
进 JUnit 的本地冗余证据列表，JUnit 属性和事件文件不删除；不清空任何生产全局缓存。
观测器、pytest/JUnit、config/transport 和进程级缓存仍可能持有其他对象，不能从这个
局部生命周期实验推广全部资源无泄漏。初稿作用域错误在 py_compile 阶段检出并修正，
未运行该草稿、未产生新生产问题。

### K552-E 终态对象释放验收结果（2026-09-25）

四时点已接入原 S4 显式 host 工作循环。先保存选定真值事件/TaskAttempt/资源曲线到
JUnit、追加 simulation.done 并清理 tmux/PID，然后才释放本地冗余副本和 runtime。
两次重建前后的实例只通过 weakref 追踪，持有控制的最新实例在 GC 后仍存活；最终
三代 runtime/EventLog/EventIndex 的九个 weakref 均为空。没有清空任何生产全局缓存。

- 两 Task：`/tmp/zf-k552-e-gc-2-20260925/testkit-run.json`，1 passed/42.826792s，
  runtime_released 时 GC collected=432，read/fold/index 缓存数前后7/1/5。
- 八 Task：`/tmp/zf-k552-e-gc-8-20260925/testkit-run.json`，1 passed/318.206039s，
  GC collected=468，缓存数前后7/1/4；引用总数及索引事件字典数也保持不变。
- 未追踪控制：`/tmp/zf-k552-e-control-2-20260925/testkit-run.json`，
  1 passed/25.365697s，不启动 tracer，不执行 GC 实验，保留原工作循环判据。
- 前序调用方：`/tmp/zf-k552-e-eventlog-callers-20260925.xml`，26 passed/1.76s；
  S0：`/tmp/zf-k552-e-s0-20260925/testkit-run.json`，19 passed/44.721418s。

两组释放前后 events.jsonl、event_index.json、kanban.json、task_attempts.json、
role_sessions.yaml 的五个 SHA-256 完全相同。两 Task 的10个/八 Task 的33个观测PID
及临时root清理完成；不是通过删除账本或缓存制造内存下降。八 Task 工作循环 RSS
baseline102496/采样峰173528/物理清理后175064KiB；该时点早于对象释放实验。
gc.collect 返回全进程本次收集数，不能把432/468全部归因于指定runtime，更不能把
RSS仍高直接判为泄漏。JUnit/phase及source-before/after保留原报告，运行期源码稳定。

本结果取代上一节“终态释放实验未完成”的阶段状态，仍不证明32 Task追踪、所有对象
owner或retained bytes因果、长期无泄漏/吞吐SLA、原生Provider长任务及完整PERF-05。
没有确认新的ZaoFu生产bug，因此不新增或修改生产Issue；原生legacy排队、OS crash、
104修复回归、K530、040和最终统一验收继续开放。架构核对仅为zf-cr定向supplement，
doc142仍缺失，不宣称完整架构审查。

### K552-F 追踪预算失败与采样成本对照（2026-09-25）

原32Task+allocation报告 `/tmp/zf-k552-workloop-allocation-32-20260925/testkit-run.json`
为 **1 failed/1213.067858s**；内部1200秒deadline在下一工作循环检测，外层1800秒
未触发。17 done、1 in_progress、14 backlog，36派发、53结算、无candidate，未进入
终态重建/GC段。17个资源采样的FD均12；RSS baseline102636/采样峰270480/
物理清理后279636KiB。最终traced current50343879/peak79124961/overhead33812432B。
73个观测PID和临时root均消失，JUnit仍保留选定真值与曲线；failure-bundle四文件及
source-before/after/phase/JUnit摘要可回读，来源稳定。外层临时打印脚本exit0不改变
TestKit的exit1/verdict=fail。不能把17Task部分结果当32Task通过或无泄漏证明。

本轮zf-cr supplement只核对workloop、workloop_resources与EventLog/index计数边界，
不扩大为全仓审查。CPython3.11.15的 `tracemalloc.py::take_snapshot` 调用_get_traces
复制trace集合，`Snapshot.statistics` 分组再排序；`start(1)`从设置后持续跟踪Python
分配，不是只在take_snapshot瞬间启用。因此仅从运行变慢不能断言快照采样就是主因。
此前口头“每Task快照导致超时”的根因断言撤回；尚无同源开/关及成本分段的充分因果证据。

测试侧新增显式 `ZF_MOCK_WORKLOOP_ALLOC_SAMPLE_EVERY`：默认1，合法范围1..Task数，
tracing关闭时不读取这个可选参数。每Task仍记录RSS/FD/socket/锁/文件字节和业务断言，
分配快照只在间隔或最后Task采集，`allocation_sampled=false`明确缺测，不填零冒充测量。
baseline/final/四GC阶段不降采样，tracer仍全程开启；此开关不是解决超时的已验证修复。
观测器另返回 `observation_seconds`，只计算自身复制/汇总/缓存观测时间，不代表持续
tracer对整个工作循环的额外开销。0/负数/超过Task数/非整数均在创建资源前拒绝。

- 间隔2、两Task：`/tmp/zf-k552-workloop-allocation-2-sampled-20260925/testkit-run.json`，
  **1 passed/42.927501s**。回读采样标记为false/true，四阶段对象释放/缓存与SHA不变。
- 带计时的默认间隔1、两Task：`/tmp/zf-k552-f-timed-probe-20260925/testkit-run.json`，
  **1 passed/43.473654s**。两Task快照0.211136/0.405469s，合计0.616606s；baseline
  0.001265s、final0.328236s、四GC时点0.326338/0.304015/0.295577/0.284254s。
  这些开销不足以支持小样本的总时差都来自快照，更不能据此推断32Task会在1200s内完成。
- 传感器/输入拒绝/EventLog fold/index调用方：
  `/tmp/zf-k552-f-sampling-callers-20260925.xml` **26 passed/1.33s**。

下一步保留长样本目标，先按同源开/关与分段开销验证持续追踪成本，形成可解释预算后
才重跑；不继续试不同间隔盲跑20分钟，不提高既有内存/业务/1200秒断言，不清空缓存。
未确认生产bug，因此ZaoFu_issue.md不变。完整PERF-05及其他原生/门禁缺口继续开放。

### K552-F1 同源开关与分段诊断结论（2026-09-25）

zf-cr仅做定向supplement，doc142仍缺失。复核后撤回checkpoint短时开tracer草稿：
真实CPython实验先分配2MiB，再start(1)，后分配1MiB；旧块get_object_traceback=None，
新块有trace，current=1048633B。只在观测前start再stop无法度量之前工作循环的存量，
因此没有运行或保留该模式，也不以“tracer无泄漏”替代产品内存证据。

改为保留持续追踪，仅在既有caller增加单调时钟累计分段：loop run_once、签名提交、
资源观测；逐Task保留累计快照，失败的调用也在finally累计。setup、PID等待、证据
序列化、清理、终态重建不包含在分段内，报告明确写出而不造总和等式。

同源两Task、默认sample_every=1，每组单次：

| 组 | TestKit耗时 | loop run_once（各6次） | 签名提交 | 资源观测 |
| --- | --- | --- | --- | --- |
| 关闭追踪 | 24.779791s | 21.212603s | 0.287074s | 0.020096s |
| 持续追踪 | 43.062818s | 34.826533s | 0.646947s | 0.780425s |

报告分别为`/tmp/zf-k552-f-control-timed-20260925-071342/testkit-run.json`和
`/tmp/zf-k552-f-traced-timed-20260925-xvxzu6hs/testkit-run.json`；均pass/exit0，
source SHA均为`2b91abfdc1c97ab6c1ee896267334c0317496362f86275f0f39d74bd315d3701`。
各9个PID和root已消失；四份绑定摘要回读通过，追踪组四GC阶段/九weakref/五文件摘要
仍通过。资源累计字段单调，计时小计小于全程耗时。测试侧直接12 passed，自动dev-verify
确定性层12 passed/5.21s，plan无errors/warnings且broad_python=false；显式host不混算。

此对照支持把成本调查指向持续追踪下的运行路径，而不是仅减少快照频率；单对样本有
环境抖动、控制组不执行终态GC等边界，不能据总时差做唯一因果结论，也不能外推32Task。
原32Task失败及73PID/root清理再次回读确认，不改预算、不重跑相同20分钟盲测。
F1只完成诊断留证，F2完整长样本与PERF-05继续开放；下一优先其他可执行非降级主干。

最终关联联调：EventLog fold/index/instruction三文件
`/tmp/zf-k552-f-callers-20260925-0725.xml`，22 passed/0.69s；S0
`/tmp/zf-k552-f-s0-20260925-072122/testkit-run.json`，19 passed/27.721485s，
pass/exit0/完整session，无collection/timeout/evidence错误（本次S0不绑定source）。
JSON/Ruff/py_compile/定向diff-check/HTML注释配对通过；未执行全仓pytest。

### K553 长工具任务/检查点恢复定向调研与离线接线（2026-09-25）

本轮 zf-cr 为定向 supplement；doc142 缺失，不声称全仓架构审查。实际检查
`_blocking_runtime_at` 的 Given Task/Plan 准入、`NativeCLI` 的独立进程 start/stop/
recover、`assert_replacement` 的同 operation/session/workdir 与新 attempt 身份，
以及原生 pipeline 的 allowlist、source 和 cleanup。保持已有恢复入口，不手写
operation/成功事实。长任务输出放在 worker worktree，不写 canonical runtime state。

缺口不是再运行一次 README 短任务。新增测试专属分块 checksum 负载：每块用真实
SHA-256 计算消耗 60 CPU 秒，三块；原子写完才成为检查点，恢复验证已存在的连续前缀，
不重写已完成块。不使用 sleep、假 heartbeat 或父测试代交。有限合成计算负载不证明
模型长期推理、长上下文或整机 OS crash，更不关闭完整 PERF-05。

| 面 | 实际入口/判据 | 边界 |
| --- | --- | --- |
| 工具 | immutable 临时脚本、精确 Bash allowlist；真实 Claude transcript 含命令 | 不绕过权限/不修改共享配置 |
| 在途 | 首块已落盘、第二块 progress、PID 属于 pane 后代；/proc CPU ≥59.99秒 | progress 是诊断，不作为 Kernel 真值 |
| 恢复 | 原正式 CLI stop/recover dry-run/apply/replay/start | 原 operation/session/workdir，新 attempt |
| 内容 | candidate 三块独立重算摘要；首块字节、mtime/inode不变，新块 invocation 不同 | 不只看退出码或候选文件存在 |
| 联调 | checkpoint负例、短计算硬中断/新进程续算、原 CLI/graceful/批次/recovery/S0 | 先离线，原生独立验收 |

离线已有证据：新脚本/独立 oracle/CLI lifecycle 首轮47 passed；精确 dev-verify plan
仅选择三测试文件，自动层19 passed/6.63s，host/native明确排除；前序五文件联调
`/tmp/zf-k553-offline-callers-20260925.xml` **90 passed/60.43s**。
S0曾检出matrix未登记新离线文件（18 passed/1 failed），K553-F1已修复；后续S0
19 passed/28.00s。新19项中包含真实短CPU子进程被kill后续算与终态重跑、十一类
伪造/损坏检查点、缺块、非法时长及配置不改原准入身份。Ruff/编译通过。
这是测试接线缺陷，不是ZaoFu生产bug。原生结果尚未取得，不提前记主项done。

### K553-B 实际停机子进程缺陷与修复（2026-09-25）

原生首轮1 failed/259.16s发现正式fast-stop退出后真实Bash工具仍运行。独立host复现
不用Provider：pane内父进程通过start_new_session启动tool，核验SID/PPID，独立stop
返回0但tool存活。原红`/tmp/zf-k553-108-detached-red-20260925.xml`1 failed/5.53s。
根因在TmuxTransport.shutdown仅kill_session；pane PGID也覆盖不到新SID。按原launch
记录逐角色terminate的草稿不能解决独立CLI和新SID，已撤回，不当作修复证据。

最终修复在实际TmuxSession.kill_session入口：精确session列live panes，读取/proc
构建后代快照，birth/parent复核后pidfd固定身份，再有界TERM/KILL。只向已证明的后代
发信号，读取进程元数据与向进程发信号严格区分；非Linux、采样前已脱离父链以及采样
后新fork不外推覆盖。当前Python没有原生pidfd wrapper，因此调用libc同名接口，不
退回裸PID/PGID广播。ZF-TEST-108已标fixed，并更新worker-respawn实现说明。

验证：清理/身份/错误负例与tmux调用方48 passed/2.23s；实际host的三种stop模式×
正常/忽略TERM以及旧session隔离控制9 passed/24.42s；tmux/transport/shutdown/CLI
直接调用方104 passed/1 host deselected/14.55s。premerge47 passed/4既有040 failed，
不提高结构cap。dev-verify因tmux高扇出扩为184文件全tests，遵守用户禁止全仓要求未
执行自动广域层，上述精确闭包替代；不是宣称全仓通过。

原生续跑环境：首次Claude自主更新子进程与stop重叠，之后PATH包装器和新2.1.282
安装不完整；不能归因为认证，旧2.1.281二进制的auth status仍loggedIn=true。只通过
显式ZF_CLAUDE_EXECUTABLE选择现有二进制，在临时bin内wrapper exec，并对子进程
设置DISABLE_AUTOUPDATER；不改共享安装、链接、认证或tmux server环境。

fixed2原生轮已实际stop/recover/start、三块60CPU秒完成、impl结算、verify派发。
一次verification-evidence.txt越界写被正确拒绝后，主线程提前SIGINT；759.41s/exit2
只表示人为中止，不证明永久阻断或生产bug。普通JUnit在KeyboardInterrupt后tests=0，
此前口头“保留完整红证据”不成立，不能拿此轮作为最终验收。下一轮改用TestKit持续
phase报告+来源绑定，只澄清verifier用stdout/正式scratch；scope和权限不扩大。
来源绑定轮`/tmp/zf-k553-native-long-bound-20260925`已完成，1800秒case/1860秒outer，
1 passed/685.946729s，source stable/exit0/pass；注册/新增oracle/S0此前46 passed/63.07s。

### K553-C 原生终态回读（2026-09-25）

四份来源附件摘要匹配；JUnit记录原生三块各60 CPU秒、同operation/session/workdir恢复、
新attempt成功及独立verify、唯一candidate、三次start和完整清理。第一块checkpoint的
bytes/mtime/inode不变，后两块使用新invocation，工具脚本digest不变。ZF-TEST-108在
正式stop后即检查旧工具退出，因而不是finally掩盖遗留进程。只关闭这个原生长工具子项，
不外推OS crash、完整PERF-05、模型长期推理或K547原生排队。

### K552-F2a 函数级观测成本诊断（2026-09-25）

既有两Task工作循环在cProfile和持续tracemalloc下完成：
`/tmp/zf-k552-f2-profile-bound-20260925-f16Gfc/pytest.prof`，pytest 1 passed/49.98s，
profile总51.104s。source.json中pytest_exit=0，前后files_sha256相同，均为
`e975bc1da84e4c228e23700ea1b7dceba4ca34d2c6a201b33c502847034b6404`。
这是自定义诊断源码前后观测，不是正式TestKit的完整来源、phase和cleanup绑定验收。

run_once九次累计44.153s；prepare_task_pipeline_dispatch九次31.919s；
RoleSessionRegistry._load 250次17.599s；yaml.safe_load 415次18.068s；
_wait_until_target_gone八次8.906s；tracemalloc._group_by八次2.030s。
累计时间互有嵌套，不能相加；cProfile和tracemalloc本身改变执行成本，不能把这里的
绝对时间当生产基准。它支持检查role-session重复YAML解析的调用归属，而非继续只
调整快照采样间隔；尚不能证明32Task唯一根因或生产泄漏，也未授权用优化生产造绿。

下一步先核对registry的加载与调用关系，再决定不丢历史分配的观测方案；32Task的
1200秒失败、RSS和业务断言保持原样，不将此诊断等同F2或完整PERF-05完成。

### K552-F2b 调用归属与宿主能力核对（2026-09-25）

对 `src/zf/core/state/role_sessions.py` 的 `_load`、构造函数和 mutation 调用点做了
只读核对，并回读同源 pstats caller map。`_load` 250 次中，构造函数 148 次累计
10.082s，`record_heartbeat` 56 次累计 4.069s，`update_instance_meta` 15 次累计
1.080s；主要构造调用方为 `role_lifecycle_runtime._role_lifecycle_registry`（30 次）、
`orchestrator._apply_housekeeping`（28 次）、`worker_state_runtime._set_worker_state`
（24 次）和 `lifecycle_evidence_queries._runtime_active_role_configs`（18 次）。
这把下一假设收窄到 registry YAML 重复解析的调用归属，但不构成唯一根因或无泄漏证明。

同一 profile 中 `yaml.safe_load` 415 次/18.068s，`tracemalloc._group_by` 8 次/2.030s；
两者和 `run_once` 均有嵌套累计，且仪器改变绝对耗时，不能拿来做生产性能基线。
因此不直接缓存/改写 canonical registry，也不重复只调整采样间隔的 32Task 盲测。

宿主探针显示 Codex 未登录；`/dev/kvm` 存在但没有 qemu-system-x86_64、virsh 或
cloud-hypervisor。K547 原生 Provider 队列和 OS crash takeover 继续记录为环境未就绪，
不把离线替身或设备存在当作通过。

### K554 统一证据收口接线调研（2026-09-25）

#### K566 增量收口前的定向复核（2026-09-25）

本次沿用同一HEAD的既有架构/历史地图，只补读testkit的`__main__`、
`requirement_audit`、`native_acceptance`、`source_observation`与`premerge_run`。
doc142仍缺失，不声称完整架构审查。当前MAINLINE已新增K564 worker/watchdog与K565
handoff入口，旧406节点报告不足以证明扩展选择；复用原900秒mainline入口一次联调。
正式premerge脚本有独立source-before/after及stdout/stderr/JUnit绑定，不能只将K565裸
JUnit填入premerge-run格式，故同一冻结输入执行一次正式入口并保留040。

K565的三项host同属一个完整报告；现有`_bound_mainline(expected_nodes=...)`要求报告
全部节点精确匹配，不应把同一报告拆成三个只含单节点的声明。新增一个包含三个精确
节点的需求行。K565收尾报告亦声明其全部127节点，不修改或回填历史附件。原K563清单
中的失败/缺项原样继承，当前mainline/premerge引用更新；这仍不是原始需求的完整分母。

同时回读K552-F2旧32Task与两Task报告：旧JUnit尚无后加的timing/observation_seconds，
缺字段不可当作零成本。既有pstats只能支持运行期YAML加载热点，不支持“降采样足以
通过1200秒”的结论。维持持续tracer、32Task、RSS/业务预算和原红，不改canonical
registry缓存，也不再运行无新方法的32Task尝试。此项与最终长稳验收仍开放。

首次收口实际检出K566-F1：`native_acceptance._bound_mainline`直接split("::")，
把S0参数`[TestClaudeRespawn::test_missing_probe]`拆进classname。127个runner/phase
节点与127个JUnit节点本来相符，仅3个带节点字符串的参数误判。锁定pytest实现
`_pytest/junitxml.py::mangle_test_address`先partition("[")，只拆前缀，再原样拼回参数；
`record_testreport`把最后一段作name、前段作classname。现采用相同边界，仍做完整
身份集合相等检查。真实子pytest覆盖function/class、普通与含`::`及嵌套方括号参数，
并重绑被篡改JUnit摘要验证参数/类名差异仍被拒绝。红2 failed/4 passed，修复6 passed；
不通过移除closeout需求、修改旧报告或忽略参数来求绿。无ZaoFu生产修改。

五文件直接闭包由dev-verify规划并实际执行：native_acceptance、native_run、premerge_run、
requirement_audit、source_observation，133 passed/57.13s，plan无errors/warnings、
broad_python=false；回执`/tmp/zf-k566-devverify-run-20260925.json`。同一旧manifest修复后
只读重审为19 pass/5 fail/6 missing，24份报告与100附件双向（源和归档）摘要一致；
不需要重跑旧host来验证节点映射修复。新合同已随原文件自动接入MAINLINE，但首轮478
发生在该修复前，故最后一次完整联调必须含六项新合同，不能直接沿用478。

`tests/testkit/native_acceptance.py`的ROLES仅有三条早期原生链、MAINLINE和premerge；
其`_bound_mainline`已校验源码清单前后摘要、runner/phase节点与exit一致性、JUnit身份。
K553/K546/K548均由同一个run_pytest(bind_source=True)产出正式testkit-run.json和四附件，
但不属于旧三条原生pipeline节点，不能强塞旧native-run schema或假装旧五项已覆盖它们。
现有MAINLINE包括测试侧oracle，不会自动运行显式host/native，因此也不能代替这些报告。

方法：保持旧五项兼容，抽取参数化的精确节点校验能力给测试侧只读证据审计使用。需求
清单由agent写明id/nodeids/ref/SHA；运行器不判断业务质量、不补造缺项，所有通过项必须
有完整phase和source/JUnit绑定。跨报告source不一致独立列出，不能用同一HEAD掩盖dirty
源码漂移。缺证据和生产红灯分开；Feishu、/issue-merge不进入当前执行清单。该清单只是
显式列出的需求集合，不具备自动推断“原目标全覆盖”的能力，输出必须保留此边界。

K554已落地：新的requirement-audit CLI直接复用参数化的`_bound_mainline`，保留旧默认
MAINLINE行为；传入明确nodeids时要求精确节点集合。逐份核验原报告SHA、来源清单自
摘要/前后一致、phase结果/selection/exit、JUnit节点和pass一致性；缺项不读伪报告，
必须附原因。不同来源的通过报告仍无法得到统一通过。记录原字节、16份来源附件副本，
不修改输入或旧结论。新14例含真实runner/CLI、篡改、同HEAD不同源码和缺证据控制。

精确七文件调用方/registry联调`/tmp/zf-k554-impact-20260925.xml`151 passed/59.79s；
真实有限清单回读`/tmp/zf-k554-requirement-audit-final-20260925/requirement-audit.json`
为3 pass/1 fail/5 missing、四种source、总gate1。它不检查未列需求、不推断语义覆盖、
不授权发布，也不要求修复所有已检出的非阻断生产bug；原五项/premerge和完整需求
清单仍须继续衔接，不能把新增审计器本身作为端到端目标完成证明。

### K555 原五项格式衔接调研（2026-09-25）

zf-cr定向supplement：旧`_bound_native`验证固定role/node、原生session/outcome/recovery/
cleanup和runner/phase/JUnit；`_bound_premerge`验证完整脚本命令、exit、stdout/stderr、
五类JUnit及PREMERGE_SCOPE（比SOURCE_SCOPE多web/docs），并返回共同范围摘要。
因此不能把两种报告按普通testkit-run解析，也不能拿完整premerge范围摘要与backend
范围直接比较。K515五份原报告都仍可读取且外层SHA匹配，不需要重跑Provider。

MAINLINE registry此后新增很多测试，旧192项报告只能作为历史，不能缩回旧选择集来
宣称当前MAINLINE完成。四流程K529/533/534/537记录为普通JUnit与browser-gate，本轮
先核对是否缺四来源附件；缺失时保持不可验证，不能事后capture_source写回历史。
doc142仍缺失，此为测试侧报告边界定向审查，不是全架构审计。

### K555 历史选择与当前验收分离（2026-09-25）

定向复核发现历史K514有完整source/phase/JUnit和192个节点，报告观察pass；用现在
MAINLINE校验会正确拒绝缺少后加模块。新增显式selection=historical后，由manifest列出
完整精确节点快照；不得从被测报告自取预期、不缩回当前registry。复用已有外层SHA和
phase/JUnit/source校验，status=historical与observed_verdict分开；即使历史全绿也保持
总gate非绿。额外selection摘要的草稿撤回，避免与显式nodeids重复契约。

S1和MAINLINE是不同执行集合，各含审计器一次，未发现同集合重复。当前格式的正/负
测试与历史错节点/错stage/phase/JUnit反例、native/premerge及旧调用方合计168 passed，
来源见coverage/steps K555。前序整体git历史以TestKit追溯记录为高频修改，生产主干
经历workflow/recovery到Task Pipeline/Provider演进；历史done不能替代当前脏源码证据。

K529/530/533/534/537目录只有独立JUnit和browser-gate等历史附件，没有找到正式
source-before/source-after/pytest-testkit/testkit-run绑定。route.source_root字节断言
只保护测试产品输入，不是ZaoFu执行源码指纹；普通SOURCE_SCOPE也不含web。因此后续
四流程需要执行时覆盖backend/tests/web来源的新报告，不得事后补capture_source。

K556先复验当前MAINLINE和premerge；不扩大到全仓pytest，不重跑未受影响的原生链。
K555报告的历史/缺项与跨source差异继续公开，不能拿新增行数计算完成百分比。

### K556 当前主干复验与台账输入边界（2026-09-25）

当前MAINLINE先329绿1红，唯一失败为S9独立tests行协议。只读枚举发现108/107/106/103
已有检测器写在内联文字中，未按既有协议登记。只规范这些路径，S9七例通过；第二次
完整MAINLINE330项通过且来源稳定。正式premerge51例仍47绿4既有040红，脚本完整、
来源稳定且未超时；不修非阻断结构债务、不抬cap。报告与manifest路径见steps/coverage。

SOURCE_SCOPE包含src/tests/scripts/skills/config但不含awesome-zaofu台账，premerge额外
包含web/docs。两轮源码摘要相同不能证明台账也未变。执行中checkpoint及结束后所读
Issue台账摘要相同，但没有执行前自动绑定，不能补造为完整来源证明。K557应把显式
测试输入和web来源作为机械证据范围接入，而非放宽审计器接受普通历史JUnit。

K556局部清单为7 pass/2 fail/6 missing；当前mainline/premerge共同SOURCE_SCOPE相同，
但旧native/扩展报告仍有六种来源，未证明全目标同源验收。独立核验9份报告和40份附件
SHA；首次红灯和K555历史清单均保留，不把报告有效性与生产发布资格混淆。

### K557 来源范围接线方案（2026-09-25，实施前调研）

已定向读取run_pytest、capture_source、_bound_mainline/_bound_premerge、CLI及两个live
浏览器文件。现有capture_source已支持scope参数，runner却固定默认范围；直接复用此
参数即可，不另造runner。旧SOURCE_SCOPE保持不变以便历史报告可验证；新增明确
acceptance范围包含实际S9输入Issue台账，browser范围再包含web。manifest显式选择
来源profile，校验器不得根据报告自选较小范围。各报告对共同范围的文件摘要须一致，
尤其不能把两个不同web版本通过缩到backend范围当成同源。

live两文件已有同Run身份、真实CLI/tmux、Docker DOM/重载、重启幂等与finally清理断言；
General一例、Issue/PRD/Refactor默认/强策略/双lane五例可复用，不重写业务链。
新增显式browser-live CLI按一个case运行，默认mainline不会偷偷启动Docker。Docker
镜像已存在，digest为2e0e475ff29c41edb2c764193a194fcfd3d96668c5515dbd0b100e701e1e251b。
离线反例先验证web/台账漂移、声明范围降级与跨报告web冲突，再实跑host。
浏览器gate等详细证据目前位于pytest临时目录；后续host运行应使用专属basetemp保留，
不能只靠stdout中的临时路径宣称永久证据闭包。doc142缺失边界仍保留。

### K557 General 重复入口诊断与恢复边界（2026-09-25）

读取首轮browser-truth/browser-gate/API日志和原始events.jsonl：只有一个Web POST；
第57行原invoke，58–64行是Web回执和Supervisor诊断，第65行是zf-stall-redispatch；
间隔0.133258秒，正常admission到第73行才发生。第二invoke被既有operation replay
防重挡住，五个reader/终态仍完成，不把多余入口扩大成双派发或双Run。

已读stall_detector的候选/有界重发/升级、tick_services的正式caller和
_replay_unconsumed_invokes、durable_call_workflow与浏览器原oracle。仅在重发函数
返回None会误走dispatch.silent_stall升级，所以应在检测器入口划分恢复所有权。
外部invoke已由60秒宽限的原事件重放负责；stage-local停滞恢复不复制该入口。既有
宽限和accepted/rejected裁决继续由原实现负责，不加新的状态文件、时钟或配置开关。

验证设计：原源码下独立复现诊断/其他Kernel事件到达但入口未消费的误报；正式tick
caller不写重发或错误升级；61秒参数下真实Orchestrator消费漏掉的原入口，检查两个
reader/唯一fanout/唯一invoke；非法pattern被真实拒绝，122秒后已裁决事件不再消费。
随后与原直接调用方、Event/Store/Workflow/General mock及browser-live分层联调。
不改浏览器断言，不靠增大等待或重复绿灯覆盖原红。文档142仍缺失，未称完整架构审查。

### K557 默认 Refactor 超时的时间线与独立对照（2026-09-25）

zf-cr定向读取browser spec、live host caller、LiveGeneralRuntime、scripted worker及
原红EventLog。浏览器live-delivery等待Task done为150秒，测试总预算210秒；Python
caller等待Run终态180秒，随后独立等待Task done。二者不是同一个终态条件。
原红`/tmp/zf-k557-refactor-20260925-EUDYZb/run`：唯一invoke于03:56:54.058310Z，
Run completed于03:59:22.152090Z（148.094秒），Task done于03:59:35.630446Z
（161.572秒）。Playwright从03:56:49.726Z执行156.042秒后失败；最后Task投影为
judge pending/verify passed，无browser errors。不能把较早Run完成当成Task已完成。
各reader约3秒就提交，部分提交到admission耗时约9–16秒；没有双Run/重复dispatch证据。
原报告四附件SHA核验通过、source stable；21个登记PID回读均不存在，cleanup=true。
worker过程文件保留running字样不是存活证明，以/proc和正式cleanup为准。

该轮与MAINLINE时间重叠。为排除本会话并行干扰，在确认MAINLINE三个PID均已退出后，
不改任何源码、断言或预算，做一次独立对照：
`/tmp/zf-k557-refactor-serial-20260925-tNri2h/run`，1 passed/202.340075秒。
Playwright125.292秒，invoke→Run终态98.940秒、invoke→Task done106.795秒；
正式restart/cleanup=true。两轮browser来源摘要相同（9d32ed72…ccef）。
这证明当前链能在原预算内完成，不证明并行资源竞争是唯一根因，不将原红改成pass或
把跨运行波动隐藏成自动retry；无生产修复。后续host场景串行执行，保留超时缺项。

### K557 双lane反证与后续测量边界（2026-09-25）

Strong独立通过，双lane独立失败（目录和结果见steps K557-C）。后者在最终judge
提交约13秒后才发completion claim；ship完成仍不等于Run终态。无MAINLINE并发仍
超时，反证“只因为MAINLINE竞争”的归因。K557-F2已登记，原150秒断言保持。
曾提出预算分层草稿；两次patch上下文失败，后续两次应用的草稿均完整撤回，未执行
扩大预算后的浏览器验收。证据不足以认定150秒是错误合同；最终源码和等待阈值
未改变，不将扩大预算当成性能修复。最终两源文件与原红source-before摘要核验。

下一定向测量入口：src/zf/runtime/watcher.py::EventWatcher.run（逐轮poll_once，
然后on_tick，再sleep）；src/zf/cli/start.py::_on_event/_on_batch_consumed/_on_tick，
以及Orchestrator.run_once。start固定poll_interval=1.0/tick_interval=5.0，但同步
回调自身耗时不包含在这个sleep里，所以不能按配置数值解释所有9–26秒延迟。
应在隔离host诊断中分别计时上述真实入口、housekeeping和Web读取，不替换状态
转换或伪造事件；保留原预算下的红灯检测。当前仅定位到需测量边界，未作生产归因。

### K558 低粒度计时入口（2026-09-25，实施前边界）

已有原红ledger含orchestrator.decision.recorded及trigger_lag_s；worker.state.changed、
fanout.child.completed等多条out_of_scope/no_action仍逐条经过完整run_once。
双lane/api/tasks响应从约0.1秒增至1–2秒，不能先忽略Web读取成本。只凭event timestamp
不能判断CPU、等待、嵌套扫描各占多少，需独立计时而非直接关闭某种wake。

定向检查Watcher.run/poll_once、start._process_watched_event/_process_watched_batch
和Orchestrator.run_once：同步回调处理每条wake，batch补非wake，然后periodic catchup。
run_once即使no_action仍可能capture logs、handoff、dispatch、progress投影；保持这些
生产调用不变。诊断wrapper仅包装选定函数，perf_counter测wall、thread_time测当前线程
CPU，thread-local parent id区分嵌套，JSONL每次返回/异常时flush，结果放live-browser/
runtime-timing-N.jsonl（不是业务账本）。CLI仍执行真实main；stop路径不换wrapper。
这是有仪器的诊断运行，开销非零，不能用其耗时宣称生产SLA或“同源无仪器已通过”。
包装版支持异常记录与排他建文件，输出失败令诊断失败，不静默吞错；没有性能阈值修改。
### K558 实测结果（2026-09-25）

计时入口已接入 S1/MAINLINE，并完成一次原双lane、原 150 秒等待预算的仪器运行：
`/tmp/zf-k558-refactor-two-lane-timing-20260925-0WrPxj/run/testkit-run.json` 为
`1 failed/203.745618s`，浏览器仍在 150 秒内观察不到 Task `done`；source stable、清理成功。
`runtime-timing-1.jsonl` 聚合结果为：

- `run_once`：105 次，wall 146.940s，thread CPU 39.452s；
- `_capture_logs`：106 次，wall 115.846s，thread CPU 20.692s；
- `_react_to_events`：106 次，wall 20.731s；`_dispatch_ready`：105 次，wall 0.873s；
- `poll_once`：14 次，wall 112.877s。

因此当前最强线索是 `_capture_logs` 内部的 tmux/外部 I/O 等待，而不是 dispatch 本身；
嵌套累计时间不可相加，wall 减 thread CPU 也包括调度影响，不能直接当作 I/O 实测值。
但它同时承载 worker 存活检查、死亡计数、respawn、日志写入和 stuck detector，计时数据
不足以授权降频、缓存或删除。K557-F2 继续保持 open；后续必须在不改变 watchdog/stuck/
respawn 语义的前提下做更细的分段观测。

### K559 分段结果与主干回归（2026-09-25）

K559 在 `/tmp/zf-k559-refactor-two-lane-timing-20260925-KDg3Jk/testkit-run.json` 同样
使用浏览器 150 秒 Task 等待，单次结果为 `1 passed/237.955198s`，restart/cleanup 和
50 个登记 PID 清理均通过；但 K558 同预算为 1 failed，故仍按波动性结果处理。
本轮将 `_capture_logs` 进一步拆到 `is_alive` 1380 次/80.488s、`capture_log` 1380 次/
22.851s，嵌套 tmux `_run` 约 102.112s；状态查询仅约 4.290s。随后 MAINLINE 复跑
`/tmp/zf-k559-mainline-20260925-HC1ACx/testkit-run.json`，`375 passed/283.205s`，
exit=0、session 完整、source stable。两轮浏览器的 src/web 摘要一致，但计时器/注册/台账
不同，因此不是完整同源码对照，也不能关闭 K557-F2。

### K560 pane-grid 命令放大与不可删除的身份保护（2026-09-25）

确定性回包驱动真实TmuxSession/PaneGridLayout/TmuxTransport，观察一次存活+日志采集
为9条命令：身份查询5次、PID1次、current command1次、capture-pane2次（120/200行）。
12worker×115轮×9=12420，符合K559 `_capture_logs` 嵌套命令数。新负例证实在resolve
完成后pane被换属或消失，当前路径拒绝继续读PID/输出；不能直接删掉第二次所有权验证。
数量只记录到JUnit，不作为必须保留的实现合同。下一可评估方案是原频率下合并一次
所有权/PID读取，或批量观测，但需等价/负例和真正host证据，不能直接以猜测改生产。
离线定向联调131 passed，注册后完整MAINLINE378 passed；详细报告路径见steps K560。

### K562 同一身份快照与影响边界（2026-09-25）

zf-cr 定向复核 `TmuxSession.pane_pid` → `PaneGridLayout.resolve/target_owned_by`、
`TmuxTransport._agent_process_liveness` 与 watchdog/stuck caller：旧路径在最终owner
检查后独立读取PID；新合同把最终pane/owner/PID绑定在同一tmux格式化回包。窗口布局
保留旧逻辑；pane-grid resolve中的首次绑定验证不删。该快照不是后续signal授权，
`terminate_window` 的精确目标和所有权复核不变，不引入缓存、降频或canonical写入。

9个原实现红灯见 `/tmp/zf-k562-owned-pid-red-20260925.xml`；候选后的87 passed只
证明局部合同。当前补充transport/layout/watchdog/stuck/S0/S5联调119 passed，隔离
真实tmux sibling保护1 passed；下一证据是无计时仪器、原150秒断言的两条browser-live。
这些测试均不调用真实模型；不能将tmux命令少一次直接视为浏览器超时已修复。

只读架构覆盖：本次检查transport/layout/宿主和测试入口；控制面仍经zf.yaml，状态仍由
既有EventWriter/Store维护。历史从fanout/runtime、generic workflow到TestKit发展，
近期工作树含多模块未提交改动，保留tmux.kill_session等既有修改。doc142与backlogs
目录缺失；未声称全仓架构审查或真实产品项目交叉验收。dev-verify精确两源码加探针
仍选择187文件并升级全仓；按任务约束不执行该扩展，改为具名直接闭包＋MAINLINE＋
显式host/browser。正式premerge为47 passed/4既有ZF-TEST-040 failed，阈值未改。

K562原预算结果：默认Refactor与双lane各一次pass，来源一致、无计时仪器。双lane
invoke→Task done evidence147.829s，仍不足以关闭波动。回读该轮原始账本，两个verify
派发在66.835/67.762s、结果准入在69.265/70.364s，而child.completed到90.159/92.597s；
最终judge准入121.757s、child.completed134.453s、Run完成141.820s。这里的事件时间
只是处理边界观察，不单独证明CPU/IO归因，也不把较晚的child.complete解释为worker
一直工作。记录来源：`/tmp/zf-k562-two-lane-20260925-BqBWFg/case-work/`下同次
`test_browser_issue_start_is_dr0/project/custom-state/events.jsonl`，未修改账本。

下一补充审查应先约束 `TmuxTransport._agent_process_liveness` 的shell、context-exhausted、
node wrapper与错误返回语义，再评估把PID/current_command纯读绑定同一身份快照。现有
探针已从9条减到8条；不得删除resolve/owner检查或直接缓存跨轮状态。改变transport
接口前必须证明窗口布局/CompositeTransport/测试替身兼容，并以caller测试证明实际
watchdog/respawn使用新路径；未证实安全或收益时不继续扩大生产改动。

### K563 PID/命令快照的定向补充审查（2026-09-25）

实际调用链：watchdog/stuck → TmuxTransport.is_alive → _agent_process_liveness →
TmuxSession/PaneGridLayout。K562仍独立取得PID与current_command，node分支还在
transport和session各自重复读取。17项原实现红灯已回读（含新接口缺失及独立命令漂移
导致的shell/context-exhausted/node误判），见 `/tmp/zf-k563-process-status-red-20260925.xml`。
仅将最终owner/PID/command合成一次tmux格式化读取，并将结果传给node进程探针；
保留resolve检查、窗口布局和旧fake adapter兼容，不改alive阈值/重启/预算/频率。

审查覆盖：tmux_layout、tmux、transport及lifecycle/context-exhausted、watchdog、
pane-grid sibling保护测试；状态仍由既有Event/Store拥有，本次只改宿主只读观测。
Git历史按kernel/fanout→generic workflow→TestKit演进；近期高churn集中steps/coverage、
suites和runtime。现有脏树包含kill_session/CompositeTransport等前序更改，完整保留。
doc142与backlogs缺失，Web业务/其它恢复缺陷/整机崩溃未审，不声称全仓架构健康。
后续证据：真实node probe不得再次取PID/命令；直接调用方及原预算浏览器验证实际接线。
单次绿色不关闭K557-F2，快照也不赋予后续signal权限。

### K568 优化后单次诊断与事件队列阻塞定位（2026-09-25）

复用原计时器及正式browser-live/refactor-two-lane入口，没有新增仪器或修改生产。
命令：`ZF_TESTKIT_RUNTIME_TIMING=1 uv run python -m tests.testkit --profile browser-live
--browser-case refactor-two-lane --timeout 480 --output /tmp/zf-k568-two-lane-timing-20260925/run`。
原150秒业务等待保持；串行一次，无自动retry。来源绑定1 passed/198.480578s，
phase完整、source stable；Playwright expected1/unexpected0/skipped0/flaky0，
Run于invoke后95.940424秒完成，正式restart/cleanup通过，50登记PID均已不存在。
四份source/phase/JUnit摘要独立重算；before/after files_sha均
737b256e79211ec298d42944da0aacfb5136587fa9ee0abe58b2db3e1794bd99。

首个runtime的114次run_once合计104.252005秒；其中_capture_logs 75.162301秒，
is_alive 44.300449秒、capture_log 21.956555秒；这些是嵌套耗时，不相加。
两个verify的准入到child.completed分别13.623337/14.263023秒，按相同operation_id/
request_hash与source/result event关联，不只按相邻事件猜测。第一路区间跨17次run_once，
重叠13.335143秒；其中_capture_logs重叠11.983582秒，react重叠0.648969秒。
wall区间用end_unix_seconds与wall_seconds推导，只作诊断归因，不作业务裁决/精确时钟SLA。

第一路两端为evt-aebe5c220f94（准入）与evt-6783f460565d（child.completed）。区间内
先逐个处理dev.build.done、worker.state.changed、task.ref.updated、fanout.child.completed、
candidate.integration.started/completed、candidate.ready等既有批次事件，最后才处理
verify.child.completed。EventWatcher.poll_once先取得不可变快照并逐事件同步调用；
start._process_watched_event对每个wake调用run_once，_process_watched_batch再处理余项，
与本次观察一致。**已定位队列排空路径的实际延迟，不等于已证明一种安全修复。**
不能直接跳过身份检查、降频watchdog、合并语义事件或改变ack边界来求绿；下一步先在
确定性实际caller中验证批次期间新结果的处理、回调异常及durable cursor合同，再决定
是否有不改变状态/恢复语义的最小处理。没有此证据不继续小优化或重复长测。

证据均在上述run/case-work/test_browser_issue_start_is_dr0下：
live-browser/runtime-timing-1.jsonl SHA f47dac08331648254b4c4ce085117509bad6634589349b15b6412e13f507e3b8；
project/custom-state/events.jsonl SHA 5239183fc27d2d473f000b7c413eeb3a7109ebba67ce5c418d0d651634d71238；
live-browser/browser-gate.json SHA 1698c595dfadaa69b38ba1e7819194e9baeded46755bff5e2b410536b9c9e1a0。
K557-F2保持open，不声称统计稳定性、历史红灯根因唯一、完整同源验收或真实Provider通过。

### K569 批次异常与startup catchup补充（2026-09-25）

已有tests/test_runtime_watcher.py覆盖回调内追加事件后精确snapshot ack；CLI start测试
覆盖pending/pushed分流与纯观察不ack；terminal mock E2E覆盖simulation先结算后停止。
缺口是这些入口在callback/ack抛错后的持久恢复组合，而非再加一份正常批次测试。
新增S4两例使用真实EventLog/EventWriter/SessionStore/TaskStore/Orchestrator与CLI helper。
终态是Given输入，只证明机械Task结算；第二callback故障时第二Task仍进行中，batch ack
seam故障时前两个已完成，晚到第三Task两者均未处理。原durable offset不变，新Watcher
从EOF不能捞回历史，正式startup catchup从持久游标完成三Task且无重复done。
最后版本通过真正的_process_watched_batch调用ack seam触发异常；这不是磁盘断电或
物理进程重启证明。最终来源绑定三例（含S4登记审计）见steps K569。
两种恢复成立不意味着把多个wake合成一次run_once也等价；中间派发/副作用以及
watchdog死亡计数随调用频率变化，仍需单独论证，当前没有修改或批准合并方案。
## K593-D 首页拆分的定向责任边界调研（2026-09-28）

本轮 zf-cr 定向读取 AGENTS、设计索引、code/backlog/docs rules 和当前调用方。
doc142/backlogs 在当前工作树缺失；不声称全仓审查。历史从 kernel/fanout 经 generic
workflow 到 TestKit 演进，高 churn 包含 App/server/fanout 及测试台账；本轮不扩审这些域。
最新 need-to-do 要求修复040结构阻断；前一状态答复没有推进目标，并误读旧审计，不能
作为进度依据。当前 K591 审计为32项局部清单，不能换算整体完成百分比。

选择证据：App.tsx 与 HEAD 无 diff，3707行超过3580 cap；fanout/server 分别有9/7、
119/1行共享diff。本轮仅拆 App 中 ProjectHomePage、Focus/Spine 展示及其 helper，
不覆盖另两文件。App→ProjectHomePage 的 props 和回调、fetchOverviewPulse、
useProjectCost、board/shared/cockpitModel 是直接边界；没有 Event/Store 写入。
正文原样移动，保持 effect 依赖/取消及250ms刷新，不删除原有未使用代码来凑行数。

验证范围：独立新浏览器用例通过真实 App；固定 API 响应只证明 UI 行为。既有 Web
unit/typecheck/build 联调、premerge和S0证明结构/台账，不能代替原生或最终同源验收。
首版浏览器 fixture 的 **/api/** 误拦 /src/api/client.ts，trace证实404；第二版暴露
未配置health/history/proposals响应。按 client.ts 合同补齐夹具，不修改生产与断言。
实现说明见 [首页边界](../../../../docs/impl/project-home-boundary.md)。

## K593-E schema education 成对职责迁移核对（2026-09-28）

依据 zf-cr 定向复核 AGENTS、设计索引及 code/backlog/docs rules；doc142和backlogs
缺失，tasks未发现精确 proposed/backlog 候选，不作全仓架构或产品现场验收声明。
历史主线为fanout/kernel→generic workflow→TestKit，高churn仍集中steps/coverage、
server、App、orchestrator；本次只检查schema/briefing责任边界。

已检查：fanout_schema_education、orchestrator_fanout、fanout_briefing_runtime、
schema_profiles_evidence、reader_fanout_runtime及坏task-map/independence调用方。
`Orchestrator`经协调器继承新mixin，实际briefing的成功/失败payload仍调用原两方法。
共享占位表与两方法必须一起移动，不把顶层/report教育拆成各自的规则源。
`self.config`和EventSchemaRegistry仍为唯一规则来源，没有新增Event/Store副作用。

迁移正文与`git show HEAD:src/zf/runtime/orchestrator_fanout.py`对应段只差尾部空行；
保留dispatch reinjection、completed_writer_task_ids和workdir等前序dirty改动。
当前fanout 8560行低于既有8564 cap；不为新尺寸+10%而抬高更严格的旧cap。
server包含portable Issue action共享改动，本子阶段未动；剩余四个未登记文件也未豁免。

验证界限：四个直接合同文件192 passed；五文件mock/EventWriter/fanout闭包59 passed，
后者具有执行前后backend来源快照且稳定。planner识别backend/orchestration及211个
建议文件；本轮按原样迁移的直接caller/shared schema闭包执行，不声称跑完211文件。
premerge继续检出server及未知文件两红，S0 19 passed；既有未闭合主项不因此关闭。
后续只读补查server的独立非Issue职责及精确caller，找到无重叠安全边界才实施。
实现说明见 `docs/impl/fanout-schema-education-boundary.md`。

## K593-F Web授权/会话独立职责核对（2026-09-28）

本轮继续定向zf-cr：检查server原始dirty diff（portable Issue actions新增点）、
request_util限流/口令解析、CLI web host校验、HTTP解锁/上锁路由、operator两条
WebSocket和workflow-request/terminal注入的mutation_auth_error回调。
授权组与portable Issue修改不重叠；未触碰Action/Store/Event语义，也未新增Issue新功能测试。

选择16个现有授权/session projection函数及cookie/会话/失败计数/loopback常量，
一起迁至`web_auth.py`。server同名导入且复用同一字典对象，lock路由与授权校验
不能各持一份session表。临时表不是业务规范状态；EventWriter/Store权威保持不变。
请求参数、返回值、异常、失效清理、配置及优先级按原样保留；限流解析仍归request_util。
所有16函数以及server剩余函数/类（包括create_app、_web_action和_validate_action_payload）
与迁移前快照AST完全一致，证明共享dirty逻辑未覆盖。旧文件9554→9353行，新模块276行，
维持原9360 cap而非借新尺寸+10%抬限；四个未知大文件继续由哨兵检出。

方法纠错：尚未接线的factory草稿引入多余回调和重复限流解析，已撤回为原样迁移；
未运行/部署该草稿。一次自动patch因hunk顺序（projection在更早行）拒绝，另一次
同路径Delete/Add被拒绝；之后按源码顺序精准删除，保留迁移前快照进行完整AST比对。
这些是本轮编辑方法错误，不是新生产bug；不在生产保留factory/重复配置源。

新增4项真实API/WebSocket回归检测共享表的lock/expiry拒绝旧cookie、拒绝前无事件
副作用和1008关闭；登记S6。前序server/S7/CLI 366 passed，直接新测试4 passed；
随后对terminal/workflow-request caller及mock做限定联调，不触发全仓或真实Provider。
实现说明：`docs/impl/web-auth-boundary.md`。未覆盖的真实Provider、其他OS模型、
长期稳定性、历史证据缺失和最终同源验收继续按need-to-do保留。

## K593-G1 Issue triage 只读投影迁移核对（2026-09-28）

继续 zf-cr 定向补审：四个未登记大文件分别为 ingress 1551行、routes 1174行、
IssueTriagePage 1349行、CSS 1225行。ingress 有既有 pattern_id 共享dirty修改；
另外三文件相对HEAD干净。此次仅选择 routes，不覆盖 ingress 或 UI。
doc142仍缺失，沿用当前代码和AGENTS权威规则，不声称全仓架构审查完成。

读取 routes 的 EventLog读取/缓存/投影、server注册入口及 mirror测试，确认
`_workflow_projection` 与三个状态常量属于完整只读职责；无规范状态写入。
保留同名路由导入，缓存测试仍可在原路由处 monkeypatch，未改缓存键或失效规则。
完整函数包含既有候选/PR分支，原样迁移而不重写，matcher依赖仍为原函数。
迁移前快照与迁移后的全部函数/类AST等价；不以静态等价替代行为验证。

步骤 → verify：迁移完整投影及常量、保持调用位置 → 全函数AST/常量核对；
既有API入口与ingress/clarification调用方 → 精确行为测试和JUnit；
前序auth/Event合同 → 来源绑定限定联调；结构 → 原阈值premerge；
台账 → 归档原件、固定SHA、注释已完成子项。没有新增注册，不为回执再跑S0。

执行偏差必须保留：初轮按完整 mirror 文件运行，误执行两项既有候选交付/PR/merge
测试，与承诺排除不符；没有新增这些测试、没有真实远端发布，也不据此记新功能done。
后续限定清单不再包含这两项。完整镜像测试不应再作为此拆分的默认命令。
自动patch生成的索引/语法/上下文错误被工具拒绝，已按精确片段修正；不是生产bug。

实现说明：[Issue triage投影边界](../../../../docs/impl/issue-triage-projection-boundary.md)。
其余三文件、Provider/K547、K530/K584-R、性能/其他OS/缺失证据与最终同源验收仍开放。

## K593-G2 ingress只读triage准入边界调研（2026-09-28，实施前）

定向zf-cr补审当前ingress、mirror路由、clarification service、CLI web、managed
workflow、project-root、安全探针和triage mock入口。ingress1551行，已有8增3删的
pattern_id修改必须保留；Page1349行与CSS1225行无dirty。CSS存在筛选、详情、交付等
多职责，但本阶段不同时动UI。没有重做全仓审查；doc142缺失仍为已知上下文限制。

最初考虑身份/附件/提案三模块，检查后收敛为一个`ExternalIssueTriageAdmissionMixin`：
source revision、artifact/attachment refs、intake排队、immutable workflow身份、Goal绑定、
retry/task/run身份是“外部输入→只读triage request”的完整职责；不迁移结果收敛、
合同promotion或Fix提案。服务实例继续拥有config/state/writer/store/cache，mixin只复用
这些对象，不另建规范状态，不改变EventWriter/TaskStore/sidecar权威入口。

关键兼容：`_canonical_source_revision`留原模块，S7显式替换原模块subprocess的探针
不失效；urllib patch使用共享stdlib模块；source schema与pattern常量在新模块定义并在
原模块同名导入，避免循环import/两份规则。managed-workflow测试覆盖既有dirty pattern
选择逻辑；用迁移前快照逐方法AST等价核验，不以HEAD覆盖尚未提交修改。

实施步骤 → verify：
1. 原样迁移完整准入方法与所需imports/constants → 全部方法/剩余顶层函数AST、常量、
   dirty pattern_id保留，原文件≤1000且新模块≤1000，不抬cap/白名单。
2. 明确选择现有准入、重试、附件、managed合同与manual-start HTTP节点 → 保存JUnit；
   不选择候选交付/PR/merge与Feishu用例，不以整文件选择引入暂缓域。
3. 联调前序只读投影、共享Event/sidecar/run-contract和实际triage mock入口 → 来源绑定
   TestKit、完整session/exit/verdict；当前mock红灯原样记录，不能改oracle追绿。
4. 原阈值premerge一次、planner解释影响闭包 → 归档原件/SHA，记录040子项已修复，
   注释仅已完成步骤；UI/CSS、Provider/其他OS/稳定性及最终同源验收保持开放。

实施结果：ingress853行、mixin718行；16迁移＋11保留方法及其他顶层入口AST一致。
SOURCE_SCHEMA_VERSION/TRIAGE_PATTERN_ID单一定义，附件日志保留原logger名。
54项caller/mock首轮通过；最终来源绑定精确组合68 passed/24.761099s；最终premerge
50 passed/1 failed，仅Page/CSS两文件。首轮gate后清理重复import/constants，因此补
一次最终gate而非为元数据反复执行。planner7文件无errors/warnings，实际扩展共享合同。
没有新注册，不重复S0。证据与边界见steps K593-G2及
`docs/impl/external-issue-triage-admission-boundary.md`。

## K593-G3 UI筛选与样式边界调研（2026-09-28，实施前）

zf-cr定向补审延续前轮地图，不重复全仓历史审查；doc142缺失仍为上下文限制。
当前Page1349行、CSS1225行及既有controls浏览器测试相对HEAD干净。
已核对App懒加载/渲染入口、IssuesTab状态与GET filters、五个筛选组件、共享展示
helper、模型函数、CSS完整顺序及既有浏览器正文。后端Event/Store边界本轮不改。

原样迁移OrderFilter、IssueStateStages、StateFilter、ContributorFilter、LabelFilter
和两项共享展示helper到IssueTriageFilters；页面保留状态、请求、副作用、详情和动作。
CSS在首个.issue-triage-content前分为controls与content，原文件保留顺序import入口；
展开后必须逐字等于前快照，尤其重复详情规则及末尾media不得重排。未知语义不重写。

步骤 → verify：先保存源码快照；函数正文及CSS展开等价 → typecheck/build与两个相关
模型单测 → 真实App固定只读API的筛选交互与原有四个安全CSS浏览器节点 → 原阈值
premerge → 归档日志/源码/SHA并仅注释已完成todo。UI验证不默认跑后端全套或S0。
浏览器仅Docker mcp/playwright:latest，自有8002+端口，API拦截不接真实runtime。
既有controls的“issue list and detail are equal independently scrolling panes without
horizontal overflow”含候选PR展示/Refresh PR，因此明确排除，不为本轮设计其替代测试。
真实App测试只操作筛选/排序，不打开候选详情、不发业务POST、不测试Feishu或issue-merge。
未覆盖：原生Provider、其他OS、性能、间歇恢复、历史缺证和最终同源验收均继续开放。

实施/验证于2026-09-29完成：Page954行、Filters404，CSS534+691，TS解析器确认
19函数正文相同，CSS29944字节完全相等。typecheck/build、两个模型文件通过；Docker
5节点通过，首轮仅fixture漏pending-proposals GET已补齐。premerge51通过，040已修复。
真实App验证固定只读API而非后端E2E，暂缓候选节点未执行。精确归档、取证遗漏导致的
补跑和操作错误见steps K593-G3；不因此扩大测试或重跑S0。

## K594 K547原生legacy队列入口补审（2026-09-29，实施前）

沿前轮zf-cr地图定向补查，doc142仍缺失；本阶段不是全仓复审。读取timeout policy、
provider_turn_liveness、SpawnCoordinator、CodexAdapter、session tailer，以及K542/K547
和NativeCLI直接调用方。规范状态仍由Kernel/EventWriter/TaskStore管理；测试只准备
Given任务/任务图，真实模型负责文件、提交和Success CLI，不由driver补写worker结果。

已确认：Codex的prompt/stop成对轮次在结果提交后仍可能开放；Claude不具备这个等价
条件。自然queued_wait_timeout要求没有pending/assigned/dispatched child，不得替换
时钟或_active_provider_turn。测试使用单writer、两Task、私有runtime-state；TASK-1
提交真实结果后，在同turn运行有界release-file等待工具，driver只释放测试信号文件，
不发hook/stop/cancel。核对真实transcript session_meta/cwd、turn_context、function_call
及task_complete，并要求同session/turn的真实stop hook；终态tailer不能被伪造hook替代。

安全前置：当前codex login status仍为Not logged in，不启动Provider。生产spawn会尝试
更新全局version.json；原生入口必须在发现待dismiss版本时拒绝启动，而非替操作者改它。
不重设HOME/CODEX_HOME，不改认证或共享config；不接受ZF_CODEX_WORKER_SANDBOX的危险
覆盖。初稿曾用default模式（workspace-write/-a never），但进一步核对发现该分支不
传allowed_paths的--add-dir，无法保证worktree内的CLI能写外层state。已以缺少--add-dir
的红灯修正夹具为restricted＋私有project allowed_paths，不用bypass。2026-09-29
实测`codex -a untrusted -s workspace-write --help`退出2，仅支持on-request/never，
复现既有ZF-TEST-048；原生入口加入只读argv探针，拒绝时不启动runtime。生产适配器
未修改，下一阶段先处理此真实阻断的兼容性与权限语义。仅启自有tmux，不使用8001。

骨干步骤 → verify：
1. 实现私有Given、只读前置、turn/transcript证据判定 → 离线正反例拒绝错session/cwd/
   turn、过早终态和缺工具证据，明确synthetic不是native。
2. 接独立完整zf start→真实结果/占槽→自然超时→CLI dry/apply/replay 0/1/0→释放真实
   turn→gap-only TASK-2→双文件本地candidate→终态重启幂等；进程/来源/清理完整留证。
3. 聚焦前序K547/hook/liveness/CLI恢复与新增合同，不重跑无关MAINLINE/browser/VM。
4. host节点显式登记，非opt-in及认证不具备为skip且不计通过；当前缺原生报告仍missing。
   归档JUnit/源码摘要，新增done只关闭已验证的实施层；Feishu、issue-merge、降级项不做。

实施结果：新host入口395+行（最终以源码为准）、离线26合同注册S5/MAINLINE；配置
加载/检查、真实Kernel派发、正式hook、10秒自然时钟、独立CLI恢复0/1/0、只补TASK-2、
TaskRef/双文件candidate保留及两次重建均通过。163项前序/注册/runner联调通过后，
发现并纠正上述default权限夹具问题；最终restricted夹具26 passed/22.31s。两者不相加。
最终原生入口仅执行auth前置，1 skipped、外层fail，未构建私有业务夹具或启动模型/tmux。
K594新审计保留32项和旧报告，10 pass/1 fail/6 missing/15 harness_failure；旧MAINLINE
缺Codex hook trust和本次新合同，不代表当前注册面，不因此立即重跑整套。原040旧红
原件保留，最新局部门禁修复事实仍由K593-G3证明；最终同源验收和native均未完成。

## K595 ZF-TEST-048 权限保真迁移补审（2026-09-29，实施前）

沿 zf-cr 已有地图补审 CodexAdapter → SpawnCoordinator → 角色私有 config/hook
调用链；不重复全仓审计。官方 OpenAI Docs 已检索并读取：
- https://learn.chatgpt.com/docs/agent-approvals-security#migrate-from-the-retired-untrusted-approval-policy
- https://learn.chatgpt.com/docs/hooks#where-codex-looks-for-hooks
- https://learn.chatgpt.com/docs/app-server

确认不能将 `-a untrusted` 简单替换为 `on-request`：后者允许沙箱内命令免审批。
官方保真路径是省略显式 approval_policy、将 user 层 projects.<cwd>.trust_level
置为 untrusted；项目层 config/hooks/rules 同时失效，但 user 层 hooks 仍加载。
当前 ZaoFu 复制全局 config 后为项目设 trusted，且删除角色 home/hooks.json；因此
仅删除 argv 的 -a 会受继承 policy 影响，并可能放宽权限或丢失回报，不能采用。

骨干步骤 → verify：
1. 无模型、无认证的私有 CLI 能力探针 → 验证原 argv 裸红，检查不信任项目的
   effective approval 与 user hooks 可观察入口；有界进程、自有 CODEX_HOME，
   不修改共享认证/配置/安装，不使用原生业务通过率替代权限证明。
2. 仅在保真路径可验证后实施角色私有迁移 → 正反例覆盖继承显式 policy、已有
   project trust、quoted 路径、重复启动、默认/bypass 不变；未确认不得静默降权。
3. 实际 spawn caller、adapter、hook、K594 离线链联调 → 保留 sandbox/allowed_paths、
   角色隔离与 hook 输入；host 元数据验证独立于模型认证，native K547 仍单列。
4. 验证后归档并更新 Issue/coverage/新 done → 048 仅在修复证据成立后标已修复，
   保留旧失败，不重跑 browser/VM/整套 MAINLINE，不涉及暂缓或降级项。

当前研究尚未证明已安装 CLI 与官方文档完全一致，步骤 1 完成前不改生产权限映射。

### K595-A 资格结果（2026-09-29）

已安装 0.155.1 的私有 app-server metadata 调用可用，不沿用旧版本“hooks/list无
响应”的假设。只发 initialize/config/read/hooks/list：project untrusted、显式
approval_policy=null；角色home/hooks.json的Stop被发现，source=user，但其
trustStatus仍为untrusted。尚未证明ZaoFu五个hooks执行与当前hash兼容；下一步必须
用真实渲染器/来源绑定验证，不能盲改CODEX_HASH_VERIFIED_VERSION旧基线。

debug prompt-input的两个无显式policy样本均给出unless-trusted；显式never和
on-request覆盖派生策略。真实SpawnCoordinator调用方的私有样本证明当前argv拒绝，
只删-a后实际配置变成never。因此必须同时处理继承策略与user hooks，不能只改adapter。
真实全局config仅作结构核验：approval_policy=never、无选定profile、存在hooks表，
不存在/etc/codex/config.toml；没有复制或输出其正文。其他宿主的system/default/profile
层优先级仍需在K595-B明确，遇到无法保证的effective policy应在physical spawn前
fail-closed；不要把模型收到的提示文本当作实际权限执行器，也不改共享配置解决此问题。

资格脚本5组断言通过；048现有精确节点--runxfail为1 failed/0.49s，Issue仍open。
源码前后稳定，进程已退出。证据和脚本见steps K595-A及其attachments；未修改注册面，
不将配置资格、hook发现或Mock transport包装为原生业务验收。下一步只补实现所需合同。

### K595-B 最小实施决策（2026-09-29，实施前）

补读官方 config-basic#configuration-precedence 与 config-advanced#profiles：CLI高于
项目、profile文件、user、cloud、system层；untrusted项目跳过项目config/hooks/rules。
0.134起root profile选择器与内嵌profiles已不支持，本次不增加新profile功能；发现该
旧配置时明确拒绝并提示迁移，不能自动选其他模型或静默忽略。

实施边界：仅restricted/allowlist且无既有显式danger-full-access覆盖的Codex worker。
1. adapter去掉退休的-a untrusted，但不改sandbox/allowed_paths/default/bypass；完整
   启动仍必须走SpawnCoordinator，不能把孤立argv生成器当作已准备的安全启动器。
2. 角色config副本只去除root approval_policy；用tomllib对删除前后对象做精确相等
   校验，无法安全处理的布局拒绝，不重写整个operator TOML、不引入新依赖。project
   trust通过绑定实际cwd的CLI -c覆盖为untrusted，不改共享或项目配置来授信。
3. 复用现有ZaoFu hook渲染器向角色home生成user hooks，按该source path生成trust
   键，保留原hook trust机制与默认/bypass路径；不盲改版本hash基线。
4. 物理spawn前通过有界app-server initialize/config/read验证合并后无显式approval
   覆盖、目标trust=untrusted、没有活动project层；错误、超时、旧profile或不确定均
   明确拒绝。该通路不调用thread/turn/model，不将自然语言提示匹配放入安全代码。
5. 先红后绿的adapter/role-local/caller/失败前无physical spawn合同；再用私有host
   验证真实CLI接受、有效权限、五hook发现/trust与角色来源，最后前序K594/hook闭包。
   K547实际模型/自然超时业务仍须认证后单独验收，不用host metadata关闭native项。

K595-B首轮真实host检出CLI键解析差异：`-c projects."<quoted-path>".trust_level`
把引号/转义当成键的一部分，两个host节点均被新guard拒绝；未启动物理worker。
直接读取该私有config/read确认错误键，不归因于认证。修正为根projects的TOML
inline-table值，让路径作为TOML字符串值解析；保留quoted/dotted路径的真实host检测。

同轮将hooks/list纳入启动前机械检查，防止user hook迁移后静默无回报；首轮发现
该RPC的eventName是camelCase，而state key后缀是snake_case。读取原生私有响应后
按engine名及原state key分别校验，不删五hook要求、不改hash算法或版本基线。

### K595-B 收尾分类（2026-09-29）

48项新安全合同、真实CLI两sandbox配置＋原048 detector三项和结构51项均通过。
精确前序闭包206项中196通过、10失败；十红均来自K593-C已记录的静态hash向量，
不能以新host metadata通过为由抹去它们。独立加载本轮实施前`codex_hooks.py`快照和
当前模块：三个hash相关函数AST完全相同，十向量修复前/后输出逐项相同且都不匹配
旧期待；测试文件与HEAD逐字相同，故不是本次迁移引入的回归。这里只排除本次改动，
尚未证明旧期待来源失效或算法错误，不用函数自身计算值重写基线。

后续可在无模型调用的私有CLI metadata层核对原始命令字节与currentHash，取得独立
Provider回执后再决定是否修正静态fixture；不能把发现/trust升级为hook实际执行证明，
不能顺手更新0.144执行基线。Codex只读登录状态仍未认证，K547不重跑skip、仍未验收。
最终精确步骤/报告/归档摘要见steps K595-B及相应attachments。

## K596 静态hook hash独立来源核验（2026-09-29，实施前）

沿zf-cr补审`codex_hook_settings`→renderer/hash→角色trust→K595-B物理启动前验证，
不重做全仓历史审查。当前test文件干净，生产hash和0.144执行基线不在本轮修改范围。
官方hooks文档确认信任绑定精确定义的current hash；发现/信任不等于hook执行：
https://learn.chatgpt.com/docs/hooks#review-and-trust-hooks 。

历史证据：041a931曾在同一占位state_dir输入下把十个期待摘要改为另一组并固定
ZF_CLI_CMD；793199c又把摘要恢复为当前失败组并删除环境隔离；后续恢复隔离但保留
了失败摘要。Git差异只能证明期待值更替，不能证明哪组有独立Provider原件，不推断
旧值必为路径脱敏或算法错误，也不据此直接恢复某次提交。

骨干步骤：
1. 私有CODEX_HOME/项目，独立构造两state_dir×五个固定hook command字节；仅调用
   已有有界initialize/config/read/hooks/list，不使用生产hash作为期望来源。
2. 原样保存本私有source的Provider发现回执及精确输入、CLI版本、相关源码摘要。
   若currentHash不匹配生产结果，则保留检测并登记；不擅自改算法或期待值。
3. 只有独立回执证明fixture错配，才把这十项静态期望换成有原始输入/回执的fixture；
   保留旧红报告/旧源码，增加显式host精确命令复核及命令改变不能仍用旧hash的反例。
4. 联调静态向量、renderer/trust、K595-B caller和hook CLI/队列调用方，按测试域留证；
   更新Issue/coverage/steps done。不会以metadata刷新0.144实际执行基线或宣称native通过。

实施结果：真实0.155.1私有回复十个currentHash与生产算法全部相同、与原期待全部
不同；可以修复测试fixture，不能由此认定历史原始路径或旧值来源的唯一原因。
JSON fixture保留精确输入及独立摘要；静态合同同时约束command字节，host用literal
fixture重新发现，Stop命令变化必须只改变其hash。前序191 passed，host四项passed；
原失败报告与旧源码保留。此次只修测试来源，实际hook执行/原生业务继续由独立层验收。
