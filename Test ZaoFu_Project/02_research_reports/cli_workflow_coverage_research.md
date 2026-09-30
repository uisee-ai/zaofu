# K2：CLI 与官方 workflow 有限入口覆盖调研

> 文档定位（2026-09-30）：历史调研，正文“当前/建议/尚未实现”仅适用于各段所记时点，不是最新执行队列。现行方案见 [ZaoFu_TestKit.md](../03_plans_and_steps/ZaoFu_TestKit.md)，执行仍按既有暂停/跳过边界；本次不重跑或改写历史结果。
>
> 当前对齐入口：`tests/conformance/test_zaofu_testkit_s6_cli_parser.py`。已有 parser 及后续 route 检测；有限入口断言不等于所有 CLI/工作流全覆盖。

> 日期：2026-09-14。按 research 技能在后台仅阅读当前源码、测试和注册表；本报告是唯一新增文件。本代理未运行测试、CLI、服务、Provider、容器或网络，未修改生产代码、测试、注册表或 Issue。以下“覆盖”指已审查节点的断言能力，不代表本次通过；“未覆盖”均限于列出的注册表与已审查文件，不推断全仓库不存在测试。

## 1. 先固定范围：两个目录不是同一个概念

运行时可启动路由来自加载后的唯一控制面 `zf.yaml`：[workflow_route_catalog()](/home/uisee/zaofu_v7/src/zf/runtime/workflow_route_catalog.py:23)按实际 stages、roles、kind_routes 和入口契约派生；[WorkflowStartService.routes()](/home/uisee/zaofu_v7/src/zf/runtime/workflow_start.py:74)再按 Task 判断资格。目录 digest 不是第二配置。固定/自适应 Research 的注册契约来自 [RESEARCH_TEMPLATES](/home/uisee/zaofu_v7/src/zf/runtime/research_templates.py:23)。

项目创建向导的“可选生产 archetype”另由 [_catalog_entries()](/home/uisee/zaofu_v7/src/zf/core/profile/flows.py:63)扫描 `examples/prod/*.yaml` 与 `examples/prod/controller/*.yaml`，只接受 `metadata.zaofu.catalog` 完整登记项；不会扫描全部历史 examples。[test_prod_flow_catalog_scans_yaml_metadata](/home/uisee/zaofu_v7/tests/test_project_profile.py:229)实际构造已登记/未登记 YAML，断言只列出前者，但这是目录规则测试，不是各 archetype 的 E2E。

本轮有限矩阵选根项目当前启用的五类路由：Issue、PRD、外部 Issue triage、固定 Research、adaptive Research。来源为根 [zf.yaml](/home/uisee/zaofu_v7/zf.yaml:3)的 Workflow/IssueFlow/PrdFlow、实际引用的 [profiles.yaml](/home/uisee/zaofu_v7/examples/prod/controller/common/profiles.yaml:157)，以及 [test_root_catalog_projects_delivery_and_registered_research_routes](/home/uisee/zaofu_v7/tests/test_workflow_route_catalog.py:18)的具体 route/角色断言。该测试检查五类存在，但未断言集合精确相等；后续冻结矩阵应以实际目录集合为准。

RefactorFlow 仍是 [flow CLI](/home/uisee/zaofu_v7/src/zf/cli/flow.py:128)支持的官方可选类型；根 `zf.yaml` 当前没有 RefactorFlow。不能因根配置未启用将其称为不支持，也不能把所有 provider/topology/archetype 组合自动加入本轮五类闭环验收。generic operation/template 注册项是语法能力，亦不等于活跃路由（[generic_workflow_catalog.py](/home/uisee/zaofu_v7/src/zf/core/workflow/generic_workflow_catalog.py:1)）。

## 2. CFG-01～10：原要求 → 实际断言 → 有限缺口

原始要求见 [ZaoFu_TestKit.md §5.1](</home/uisee/zaofu_v7/awesome-zaofu/New_function/Test ZaoFu_Project/03_plans_and_steps/ZaoFu_TestKit.md:232>)。S2 当前入口为 [suites.py](/home/uisee/zaofu_v7/tests/testkit/suites.py:25)。

| ID | 已审查源码与具体测试 | 覆盖等级与剩余边界 |
|---|---|---|
| CFG-01 | [load_config](/home/uisee/zaofu_v7/src/zf/core/config/loader.py:2642)；[test_load_minimal_config](/home/uisee/zaofu_v7/tests/test_config_loader.py:24)断言 name、默认 tmux_session、空 roles | 真实 loader 行为，但仅这几个默认字段；当前 S2 未整文件登记 config_loader，不能称 schema 所有默认已闭合。 |
| CFG-02 | [validate._run_impl](/home/uisee/zaofu_v7/src/zf/cli/validate.py:84)；S2 [test_validate_rejects_missing_project_with_nonzero_exit](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s2_config.py:191)，既有 [test_validate_invalid_config](/home/uisee/zaofu_v7/tests/test_cli_validate.py:67) | 有真实 CLI 非零/错误信息；原文“准确字段路径、无运行态”不能由 return code 推出。实现还会写 validation diagnostics，需区分允许诊断与 canonical state，不能静态判生产 bug。 |
| CFG-03 | [_reject_unknown_keys](/home/uisee/zaofu_v7/src/zf/core/config/loader.py:410)；[test_security_event_signing_typo_rejected](/home/uisee/zaofu_v7/tests/test_config_loader.py:145)、[test_verification_contract_subkey_typo_rejected](/home/uisee/zaofu_v7/tests/test_config_loader.py:175) | 真实拒绝且匹配字段路径；本轮未核清全部错误类型矩阵，不将未审查项记为已覆盖或全仓缺失。 |
| CFG-04 | loader；[test_validate_rejects_missing_artifact_matrix_gate_config_ref](/home/uisee/zaofu_v7/tests/test_config_loader.py:51)先缺文件失败、补文件成功；[test_validate_fails_for_unknown_runtime_backend](/home/uisee/zaofu_v7/tests/test_cli_validate.py:77)、[test_catalog_hides_registered_research_stage_with_wrong_role_contract](/home/uisee/zaofu_v7/tests/test_workflow_route_catalog.py:65) | 已有引用/backend/Research 契约真实拒绝节点，非官方工作流运行闭环。 |
| CFG-05 | [resolve_project_context](/home/uisee/zaofu_v7/src/zf/core/config/project_context.py:104)；S2 [test_read_only_cli_surface_uses_configured_state_dir](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s2_config.py:31)、[test_cli_state_dir_override_beats_configured_state_dir](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s2_config.py:153) | 真 main(argv) 执行指定命令，检查 custom state 和不产生默认 `.zf`；不是每个命令的全覆盖。 |
| CFG-06 | [validate._run_cold_start](/home/uisee/zaofu_v7/src/zf/cli/validate.py:383)、[doctor._run](/home/uisee/zaofu_v7/src/zf/cli/doctor.py:86)；S2 [test_cold_start_rejects_uninitialized_workspace](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s2_config.py:206)，既有 [test_validate_cold_start_passing](/home/uisee/zaofu_v7/tests/test_cli_validate.py:333)、[test_doctor_provider_reports_missing_codex](/home/uisee/zaofu_v7/tests/test_cli_doctor.py:53) | 已有成功/失败真 CLI 和 mock 缺依赖；并非真实 Provider auth tier。本轮没有审完独立 preflight CLI 文件集合。 |
| CFG-07 | [workflow_route_catalog](/home/uisee/zaofu_v7/src/zf/runtime/workflow_route_catalog.py:23)；[test_root_catalog_projects_delivery_and_registered_research_routes](/home/uisee/zaofu_v7/tests/test_workflow_route_catalog.py:18)检查稳定 sha256 digest；S2 surface [run contract/sidecar case](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s2_surface.py:24)检查 state_dir | 有派生目录 digest、run contract helper；不能冒充所有结构化报告记录完整配置来源/digest。 |
| CFG-08 | context resolver；S2 [test_nested_diagnostic_cli_surfaces_share_project_state_root](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s2_config.py:69)、[test_hook_recv_from_nested_project_directory_uses_configured_state_dir](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s2_surface.py:60) | 这些是真入口；同文件 [AST 入口审计](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s2_surface.py:153)只查导入/委托符号，不能证明“每个注册命令”。主线程负责动态 parser 与有限 state-root 实验，不在本报告重复实现。 |
| CFG-09 | [load_env_file](/home/uisee/zaofu_v7/src/zf/core/config/project_context.py:35)；S2 [test_does_not_override_existing_env](/home/uisee/zaofu_v7/tests/test_load_project_env.py:30)、[test_env_runtime_context_overrides_detached_workdir](/home/uisee/zaofu_v7/tests/test_project_context.py:57) | 验证 shell 优先、受支持的 runtime context override；它们并非“禁止控制字段不能覆盖”负向矩阵。`test_zf_start_loads_project_env` 仅源码字符串检查。 |
| CFG-10 | validate / project admission；S2 [test_implicit_init_rejects_invalid_project_metadata_before_writing](/home/uisee/zaofu_v7/tests/test_project_admission.py:309)实际调用 Web init API，断言 422 与目标目录不存在 | 已登记的是 API 局部 fail-closed，不是 CLI 初始化失败证明；尚未在已审查文件发现覆盖全部失败入口的 state/进程/端口/临时文件联合快照。不能从 fixture 自动清理推导失败时无残留。 |

## 3. 五类 workflow：真实入口与断言边界

原文 [§8.3](</home/uisee/zaofu_v7/awesome-zaofu/New_function/Test ZaoFu_Project/03_plans_and_steps/ZaoFu_TestKit.md:380>)明确要求每条官方工作流“成功、失败、缺失依赖、重复、乱序”；重启是本次另要求核对的恢复维度。下表不把共享机制测试复制成每条 route 的勾选。

| 当前路由 / 源码归属 | 已有成功与失败/缺依赖节点 | 重复、乱序、重启与缺口 |
|---|---|---|
| `delivery:issue:default`；[zf.yaml IssueFlow](/home/uisee/zaofu_v7/zf.yaml:16)、[flow kind 分支](/home/uisee/zaofu_v7/src/zf/cli/flow.py:504)、[Orchestrator.run_once](/home/uisee/zaofu_v7/src/zf/runtime/orchestrator.py:864) | 已登记 S9 [test_runtime_issue_fix_run_dispatches_implementation_to_provider](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s9_fixrun.py:411)：受控 request→submit→真实 Kernel→recording transport，断言唯一 invoke、绑定 run_id 的 child dispatch、无 defer、transport 收到任务；只到实现派发。相邻 [without_git_baseline_is_environment_failure](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s9_fixrun.py:506)覆盖缺 baseline 的 preparation fail-closed。 | 已审查节点不证明该 route 的 writer→test→verify/judge→terminal 成功/失败，更不证明该全链的重启/乱序。重复 run_once 直到派发不是重复终局验收。S9 local fix/merge helper 场景不替代这一整链。 |
| `delivery:prd:standard`；[zf.yaml PrdFlow](/home/uisee/zaofu_v7/zf.yaml:21)、[flow kind 分支](/home/uisee/zaofu_v7/src/zf/cli/flow.py:523) | 已登记 S8 [test_prd_flow_ingest_and_first_dispatch_smoke](/home/uisee/zaofu_v7/tests/test_flow_smoke_e2e.py:59)直接调用 ingest，断言两 task/依赖/verification 字段，**没有实际 first dispatch**。未登记 [test_delivery_route_rejects_incomplete_task_contract_before_invoke](/home/uisee/zaofu_v7/tests/test_workflow_start_service.py:289)拒绝不完整 PRD contract、ledger 为空。 | 现有 inspection/ingest/preview 不证明完整成功、执行失败、重复、乱序或 route 重启；本轮最明确的 route 入口闭环缺口。 |
| `general:external-issue-triage`；[zf.yaml 两阶段独立 verify](/home/uisee/zaofu_v7/zf.yaml:10)、[ExternalIssueIngressService.poll_once](/home/uisee/zaofu_v7/src/zf/runtime/external_issue_ingress.py:146) | 已登记 S7/S9 [test_new_issue_revision_queues_one_read_only_triage_run](/home/uisee/zaofu_v7/tests/test_external_issue_ingress.py:120)真 service→ledger/task，两次 poll 只有一次接收/唯一 invoke；[test_failed_provider_sync_does_not_publish_ingress](/home/uisee/zaofu_v7/tests/test_external_issue_ingress.py:455)注入同步失败，ledger 不存在。 | [test_same_revision_reuses_task_and_changed_revision_gets_retry_suffix](/home/uisee/zaofu_v7/tests/test_external_issue_ingress.py:149)覆盖 revision 重复/变化；[test_semantic_triage_outcome_closes_run_admission](/home/uisee/zaofu_v7/tests/test_external_issue_ingress.py:767)是注入 ready_to_fix 再 reconcile，不是 agent.read→agent.verify 真执行。该精确两阶段 route 的完整成功/缺依赖/乱序/重启仍未由这些节点证明。 |
| `research:fixed`；[registered template](/home/uisee/zaofu_v7/src/zf/runtime/research_templates.py:35)、[settle_research_delivery](/home/uisee/zaofu_v7/src/zf/runtime/research_delivery.py:23) | 未登记 [test_research_fanout_template_runs_to_channel_update](/home/uisee/zaofu_v7/tests/test_research_fanout_template.py:145)：真实 Orchestrator+mock transport，invoke→四 child dispatch→注入四份报告→synth dispatch→注入 synth 结果→aggregate/channel projection，检查 artifact 内容/ref/digest。是成功链，不是真 Provider。 | 同文件 [test_workflow_invoke_fanout_stage_matches_requested_pattern_only](/home/uisee/zaofu_v7/tests/test_research_fanout_template.py:102)验证不误启动另一 stage；未覆盖该 route 的执行失败、缺报告依赖、重复/乱序完成和重启。fixture 形状检查不补这些空格。 |
| `research:adaptive-pilot`；[opt_in_pilot template](/home/uisee/zaofu_v7/src/zf/runtime/research_templates.py:23)、[project_direct_research_artifact](/home/uisee/zaofu_v7/src/zf/runtime/research_delivery.py:68) | 未登记 [test_adaptive_research_uses_one_root_and_one_canonical_result](/home/uisee/zaofu_v7/tests/test_research_fanout_template.py:278)：真实 Orchestrator+mock，单 root dispatch/briefing，注入 root report+operation summary，验证无 synth、唯一 result.available、Task done、唯一 done evidence。 | [test_adaptive_research_failure_has_recovery_contract](/home/uisee/zaofu_v7/tests/test_research_fanout_template.py:94)只查 event registry failure_class/owner，**不是失败执行**。失败/缺依赖/重复/乱序/重启未被该全文件覆盖。pilot 仍是显式 opt-in，不升级为真实 provider 必选门禁。 |

共同但不能逐路线充数的证据：

- [test_cli_routes_propose_apply_and_replay_without_web](/home/uisee/zaofu_v7/tests/test_workflow_start_service.py:379)真实调用 CLI routes/preview/propose/apply；无 token、错误 token 被拒，合法 apply 后重复 apply 为 already_resolved；只产生一次 invoke/resolve 且 token 不入 ledger。生产入口是 [workflow._apply](/home/uisee/zaofu_v7/src/zf/cli/workflow.py:308)与 controlled action。没有继续驱动 Kernel，因此仅证明启动提交与授权/幂等，不是五条完成链。
- 已登记 S8 [test_generic_workflow_replans_once_and_closes_after_restart](/home/uisee/zaofu_v7/tests/e2e/test_generic_workflow_complex_mock_e2e.py:966)使用五阶段 generic 场景；[重建 Orchestrator 后 synth 仅派发一次](/home/uisee/zaofu_v7/tests/e2e/test_generic_workflow_complex_mock_e2e.py:542)、[verify 同样去重](/home/uisee/zaofu_v7/tests/e2e/test_generic_workflow_complex_mock_e2e.py:647)是真重建 runtime 和 duplicate dispatch 断言；其旧 generation 报告/barrier 测试有参考价值，但不是根 triage 的精确两阶段验收。
- 已登记 S4 [test_interrupted_task_pipeline_operation_redrives_after_restart](/home/uisee/zaofu_v7/tests/test_workflow_operation_resume.py:296)调用 operation service 和 resume，断言 requested/redrive_count/source_attempt；没有重启 CLI 或完整官方 workflow。S5 [test_scripted_provider_repetition_is_observable_and_bounded](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s5.py:50)仅 mock provider helper 计数/耗尽，不算 route 重复事件幂等。
- 已登记 S8 [test_refactor_lane_full_cycle_smoke](/home/uisee/zaofu_v7/tests/test_flow_smoke_e2e.py:34)用合成 lane config 真跑 writer/verify，并断言派发/测试通过/spine 计数；[test_issue_flow_contract_smoke](/home/uisee/zaofu_v7/tests/test_flow_smoke_e2e.py:86)只 inspection。另 [controller smoke matrix](/home/uisee/zaofu_v7/tests/test_controller_flow_smoke_matrix.py:113)检查编译配置/报告，不能称三流完整成功。

## 4. CLI-01～06：已登记与可复用节点

原要求见 [§14.1](</home/uisee/zaofu_v7/awesome-zaofu/New_function/Test ZaoFu_Project/03_plans_and_steps/ZaoFu_TestKit.md:585>)。CLI 当前使用 [argparse build_parser/main](/home/uisee/zaofu_v7/src/zf/cli/main.py:11)，不是 Click。当前登记依据 [S6](/home/uisee/zaofu_v7/tests/testkit/suites.py:166)与上面的 S2，主线程并行新增/注册内容不计入本报告静态基线。

| ID | 真实节点与断言 | 状态/不能外推的部分 |
|---|---|---|
| CLI-01 | [test_version_flag/test_no_args_prints_help](/home/uisee/zaofu_v7/tests/test_cli_main.py:17)实际 main；[test_all_cli_help_is_ascii](/home/uisee/zaofu_v7/tests/test_cli_output_language.py:59)递归 parser.format_help | 原注册表未登记这两个文件；后者没有逐路径实际 main(--help)/错误参数，也无 state 副作用快照。主线程正在补有限真实 parser 边界，不把 callback trap 当 state-root 证明。 |
| CLI-02 | S2 validate/doctor/cold-start 节点；[test_doctor_sidecar_json_reports_clean_refs](/home/uisee/zaofu_v7/tests/test_cli_doctor.py:95)查 JSON ok/count，缺 ref 节点查非零/ref_missing；入口 [doctor._run_sidecar](/home/uisee/zaofu_v7/src/zf/cli/doctor.py:186) | 原有真实 CLI 能复用；全 report 字段/每个 preflight family 尚未在本轮审完，不能用 argparse help 替代。 |
| CLI-03 | S6 [test_emit_cli_uses_configured_state_dir_and_not_default_zf](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s6_cli.py:25)、[test_events_cli_queries_same_configured_state_dir](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s6_cli.py:41)真实 main→ledger→JSON 回读；S2 [events trace causation](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s2_config.py:475) | 已登记；源码归属 [events.run_emit](/home/uisee/zaofu_v7/src/zf/cli/events.py:84)/[run_events](/home/uisee/zaofu_v7/src/zf/cli/events.py:402)/[run_trace](/home/uisee/zaofu_v7/src/zf/cli/events.py:438)。无需为已实现入口再发明平行 helper。 |
| CLI-04 | [TestRestart.test_restart_uses_project_state_dir](/home/uisee/zaofu_v7/tests/test_cli_restart.py:71)真 main init/start/restart，custom state/instructions/event/default .zf；[codex recycle](/home/uisee/zaofu_v7/tests/test_cli_restart.py:96)、[claude recycle](/home/uisee/zaofu_v7/tests/test_cli_restart.py:171)检查 session/launch argv | 全部 start/restart 为 dry-run；生产 [restart.run](/home/uisee/zaofu_v7/src/zf/cli/restart.py:39)将 dry_run 传给 [make_transport](/home/uisee/zaofu_v7/src/zf/runtime/transport.py:754)。可计 CLI+本地恢复合同，不能计真实 process lifecycle/host kill/restart。 |
| CLI-05 | S2 [test_status_after_init_reads_custom_state_dir](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s2_config.py:305)、[test_state_clean_confirm_archive_removes_only_rebuildable_projections](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s2_config.py:417) | status 与受控清理已经有真入口。仅 helper/store archive/recover 测试不足以宣称全部 CLI recover 分支；本轮未审完这些 family。 |
| CLI-06 | workflow start service 的 [真实 CLI 授权/重放节点](/home/uisee/zaofu_v7/tests/test_workflow_start_service.py:379)；源码 token 比较及 controlled action 在 [workflow.py](/home/uisee/zaofu_v7/src/zf/cli/workflow.py:308) | 可覆盖该一个 action 的 auth/提交幂等；不能外推所有 action 均不触外部 sink。环境中若已有 ZF_WORKFLOW_ACTION_TOKEN，会破坏“未配置 token”用例前提，需要先隔离。 |

Flow 的既有红灯不是缺测试：S6 [cli_flow wrappers](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s6_cli_flow.py:16)直接调用既有 `test_cli_flow` 行为节点，strict xfail 标识 ZF-TEST-024（specialized kind）、025（init apply STOP）、026（multi-kind submit STOP）。本代理未复跑，不新增 Issue，也不因注册了 wrapper 宣称生产已绿。

## 5. 已逐文件核查的整文件候选（7 个，不在本报告注册）

共同前提：使用现有 pytest [repo import guard / workspace registry 隔离](/home/uisee/zaofu_v7/tests/conftest.py:43)，且外层隔离 cwd、ZF_PROJECT_ROOT/ZF_STATE_DIR、授权/env 污染。全文件没有真实 host/Provider 执行需要，不等于零本地文件副作用；这些测试需要 tmp state。登记前仍由主线程精确执行并处理真实结果。

| full-file path | 全文件实际能力与安全理由 |
|---|---|
| [tests/test_cli_main.py](/home/uisee/zaofu_v7/tests/test_cli_main.py:1) | main help/version+元数据读校验；不调用业务运行时。只补 CLI 基础。 |
| [tests/test_cli_doctor.py](/home/uisee/zaofu_v7/tests/test_cli_doctor.py:1) | 6 个 CLI 行为节点，项目/sidecar 都在 tmp；provider missing 分支 mock which，namespace 分支 mock which/subprocess.run，不实际运行 codex/unshare。 |
| [tests/test_cli_validate.py](/home/uisee/zaofu_v7/tests/test_cli_validate.py:1) | 合法/非法/backend/skills/dependency/strict contract/cold-start。cold-start :130 在 remote policy 先返回，:333/:348 roles 为空，:358 五角色全 python；[provider auth filter](/home/uisee/zaofu_v7/src/zf/runtime/preflight.py:174)排除空/mock/python/deterministic，runtime 相关 backend 默认空，故当前夹具不触 ProviderCLI。remote-policy 两例会实际执行本地只读 `git remote get-url origin`，不是网络/Git mutation。 |
| [tests/test_cli_restart.py](/home/uisee/zaofu_v7/tests/test_cli_restart.py:1) | 全部业务执行为 dry-run；tmp config/state/launch/session fixtures，包括 codex/claude 名称也不等于真实 provider 调用。会在进程内设置 ZF_CLI_CMD，应由外层环境隔离约束。 |
| [tests/test_workflow_route_catalog.py](/home/uisee/zaofu_v7/tests/test_workflow_route_catalog.py:1) | 读 root config + 构造 mock config，核对角色、route、alias、digest、入口闭合；没有 dispatch/state/service 启动。是 CFG/目录防漂移候选。 |
| [tests/test_workflow_start_service.py](/home/uisee/zaofu_v7/tests/test_workflow_start_service.py:1) | tmp 项目复制 config、只读 symlink examples/skills；服务 preview/propose 与真 CLI auth/apply；未创建运行 transport。应显式清空/恢复初始 ZF_WORKFLOW_ACTION_TOKEN，不能仅依赖工作站恰好未设置。 |
| [tests/test_research_fanout_template.py](/home/uisee/zaofu_v7/tests/test_research_fanout_template.py:1) | 全文只有配置/registry检查、真实 Orchestrator+recording transport，fixture 所有 backend mock、状态在 tmp；接入后增加两个 Research 成功链，不是补齐其余五维。 |

不推荐本轮直接整文件登记：`test_cli_flow.py` 与 `test_cli_start.py` 未完成本报告全文安全审计；已有精确 strict wrappers/节点可继续使用。`test_cli_output_language.py` 的 [AST 全字符串 ASCII 审计](/home/uisee/zaofu_v7/tests/test_cli_output_language.py:81)会扫描非输出语义数据，当前 [self_issue_policy instructions](/home/uisee/zaofu_v7/src/zf/core/config/self_issue_policy.py:43)含中文用户日志短语；主线程反馈该节点失败。分类为静态审计边界与 prompt 数据混合，**不据此登记运行时 bug，也不改语义内容来凑绿色**。本报告没有复跑该结果。

## 6. 本轮退出判据与有限后续

1. K2 本轮可闭合的窄增量：接入已核实安全的现有文件；补 CLI-01 当前真实 parser 树的 help/非法参数无业务调用边界；每一执行结果如实标红/strict xfail。不要因此宣称 CFG-08 全命令 state-root 或 §8.3 已完成。
2. workflow 后续只保留五类当前路由矩阵：先复用两条 Research 成功链与已有启动授权/幂等节点；优先补 PRD 从 invoke 到实际 dispatch/terminal、Issue 从已证明的 dispatch 到终局、triage 精确 read→independent verify 的终局。每条再围绕其真实边界补失败、缺依赖、重复、乱序；重启优先复用共享持久化机制并增加 route 身份断言，不扩展任意 provider/模型/产品组合。
3. 准入标准必须写节点与 oracle：唯一 task/attempt/generation、实际 dispatch、被验真的证据 refs/digest、合法终局或有界 blocked/rework；失败/重放不能生成新成功或重复外部动作。仅 inspection/helper 返回成功不算 route 完成。
4. 退出条件是矩阵明确区分已登记可执行、现有但未登记、已覆盖生产红灯、未核实/尚缺入口；不是消灭所有未来可能组合。新增生产 Issue 仍须正式复现，本次静态报告不产生新 bug 条目。
