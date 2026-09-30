# 测试资料逐文件审核清单

审核日期：2026-09-30（Asia/Shanghai）。这是文档与证据可读性审核，不是新一轮测试验收。

## 1. 当前权威入口

| 测试对象 | 方案 | 进展与待办 | 实现依据 |
|---|---|---|---|
| ZaoFu 自身 | [ZaoFu_TestKit.md](../03_plans_and_steps/ZaoFu_TestKit.md) | [zaofu_done_part.md](../03_plans_and_steps/zaofu_done_part.md)、[steps.md](../03_plans_and_steps/steps.md) 顶部 K613 暂停快照 | `tests/testkit/suites.py`、`tests/testkit/need_to_do_inventory.py`、`tests/conformance/`、`tests/e2e/` |
| 通用生成产品 | [ZaoFu_Product_TestKit.md](../03_plans_and_steps/ZaoFu_Product_TestKit.md) | [productOfzaofu_done_part.md](../03_plans_and_steps/productOfzaofu_done_part.md)、[Product_steps.md](../03_plans_and_steps/Product_steps.md) | `tests/product_testkit/manifest.py`、`planner.py`、`executor.py`、`runner.py`、`test_reference_product_closure.py` |

审核沿用 zf-cr 的只读源码核对与证据分层方法；之后按用户要求修订文档。没有实施运行时修改。调研建议、框架实现、历史执行结果、产品质量与外部操作授权分别记账；稳定检出产品问题不等于框架未完成，harness failure 也不能转嫁给产品。

## 2. 方案、台账及导航逐文件审核

| 文件（相对资料根目录） | 处理与审核结论 |
|---|---|
| `README.md` | 补两套现行方案与本审核入口；说明 manifest 模板需替换占位符、逐域提供实际测试，不可用一个 unit 命令标签冒充七域覆盖。 |
| `03_plans_and_steps/ZaoFu_TestKit.md` | 对齐 S0～S11 注册与 T0～T6 风险分类；修正产品方案尚待调研、EventLog 去重、Store 重建、支撑层缺失等旧描述；保持 K613 暂停。 |
| `03_plans_and_steps/ZaoFu_Product_TestKit.md` | 对齐核心域 P-01～P-05、P-17、P-18，target commit/dirty/untracked 边界、统一 runner、OpenAPI phases、Docker Playwright 和远端写入限制。 |
| `03_plans_and_steps/Product_steps.md` | 增加当前口径和历史阶段边界；将具体产品名/路径泛化为占位符，明确不可直接执行；381 passed、1 skipped 等只属历史记录。 |
| `03_plans_and_steps/steps.md` | 明确 K613 暂停快照优先；旧 active/下一步、CLI/认证诊断和旧方案摘要属于历史，不把旧 /tmp 引用当当前现场。 |
| `03_plans_and_steps/zaofu_done_part.md` | 沿用已完成清单，补现行方案、暂停状态与证据边界；保留待办分类，不用产品通过率计算框架完成率。 |
| `03_plans_and_steps/productOfzaofu_done_part.md` | 区分通用产品 fixture、宿主 P-18 接线、真实业务验收；历史汇总编号改为 R-01～R-27，避免与 P-01～P-18 正式域冲突；修正 Provider 旧环境结论。 |
| `03_plans_and_steps/test_p0.md` | 标记为两套方案拆分前历史草案，不再当作当前实施清单。 |
| `03_plans_and_steps/zaofu_test.md` | 标记为早期综合方法；旧建议不自动扩为当前主干待办；保留 CLI 缺失等 mock 故障测试场景。 |
| `03_plans_and_steps/product-test-manifest.example.json` | JSON 语法检查；保留模板内容。commit/命令/验收是占位符，不宣称可直接通过 manifest 准入或业务验收；使用说明见根 README。 |
| `04_audit_and_issue_ledger/ZaoFu_issue.md` | 补历史台账说明；不改写红灯、修复证据和 Issue 状态；K600/K610 操作者跳过不等同当前 CLI 缺失或认证失败。 |
| `05_restart_handoff/README.md` | 保留重启前说明，追加重启后校验结果、明确附件计数口径和本审核入口。 |
| `05_restart_handoff/DOCUMENT_REVIEW.md` | 本次新增审核索引；记录处理范围和未验证边界，不是新的测试结果报告。 |

## 3. 调查报告逐文件审核（47 份）

所有报告均增加历史定位及当前对齐入口。表中入口路径已逐一确认存在；正文旧行号、建议和测试数仍按各报告基线理解，不宣称全部历史发现已复现、修复或通过。本表反映文档对齐，不等于完整源代码审计。

| 文件（位于 `02_research_reports/`） | 当前实现入口（仓库根相对路径） | 本轮对齐结论 |
|---|---|---|
| `action_commit_window_research.md` | `tests/conformance/test_zaofu_testkit_s4_action_crash.py` | 已有真实 action 提交窗口检测入口；本报告的实施前假设不再等于待新建测试，生产重复副作用结论仍查问题台账。 |
| `action_result_loss_model_research.md` | `tests/conformance/test_zaofu_testkit_s11_action_loss_model.py` | 已有结果丢失模型入口；动作族覆盖与任意远端 exactly-once 分开，不外推缺失组合。 |
| `attempt_generation_research.md` | `tests/conformance/test_zaofu_testkit_s11_generation_model.py` | 已有 generation 模型；不再把“仅 attempt 列表性质”作为当前全部实现。 |
| `cli_workflow_coverage_research.md` | `tests/conformance/test_zaofu_testkit_s6_cli_parser.py` | 已有 parser 及后续 route 检测；有限入口断言不等于所有 CLI/工作流全覆盖。 |
| `continuous_allocation_method_research.md` | `tests/testkit/allocation_segments.py` | K601/K603 分段准入方法已存在；K552-F2 的32 Task/1200秒连续长样本仍开放，不用短校准替代。 |
| `delivery_route_terminal_research.md` | `tests/testkit/issue_route.py` | 已存在正式 route 驱动及后续修复；K596 hook 来源已另有完成记录，旧宿主诊断不代表重启后的认证状态。 |
| `event_ingress_dedup_research.md` | `tests/conformance/test_zaofu_testkit_s2_event_ingress.py` | EVT-05 检测入口已存在；去重要求、raw occurrence 写入与已检出生产问题不能混为一谈。 |
| `eventlog_model_research.md` | `tests/conformance/test_zaofu_testkit_s11_eventlog_model.py` | 已有独立事件模型；正确检出失败不等于模型缺失，不把当前实现自动声明为去重通过。 |
| `fanout_model_research.md` | `tests/conformance/test_zaofu_testkit_s11_fanout_model.py` | 已有 fanout/fanin 生成式测试入口；仍保留有界动作空间和后端范围限制。 |
| `hypothesis_failure_capture_research.md` | `tests/testkit/model_evidence.py` | 失败证据支撑已存在，配套 test_model_evidence.py；保留 shrinking/历史反例边界，不作为本轮执行结果。 |
| `hypothesis_state_models_research.md` | `tests/conformance/test_zaofu_testkit_s11_control_models.py` | 控制模型及后续专用模型已存在；早期“缺少两个模型”结论已由后续实现推进。 |
| `isolated_vm_crash_method_research.md` | `tests/testkit/isolated_vm_run.py` | 隔离 VM 驱动已存在；owned QEMU SIGKILL、guest panic、guest 文件系统损坏和物理掉电必须分别判定。 |
| `k6_gate_research.md` | `tests/testkit/performance.py` | 性能摘要与阈值判定已实现；工具 oracle 不代表生产容量或长稳已通过。 |
| `mutation_audit_research.md` | `tests/testkit/mutation_audit.py` | 已有 mutation 审计工具；依赖版本、样本和真实运行仍属显式周期层，不扩大为本轮执行。 |
| `ordinary_async_evidence_research.md` | `tests/testkit/test_async_case_evidence.py` | 已有异步证据合同；fixture 生命周期与原始事件/状态证据按实际注册范围判断。 |
| `ordinary_case_evidence_research.md` | `tests/testkit/case_evidence.py` | 普通用例证据支撑及 runtime_case_evidence.py 已存在；不能再按早期建议认定尚未建设，也不假定所有 case 自动接入。 |
| `ordinary_parallel_retry_research.md` | `tests/testkit/test_parallel.py` | 已有并行及 test_rerun_evidence.py 合同；重试不覆盖首轮红灯，隔离并行不等于业务并发证明。 |
| `persistence_crash_research.md` | `tests/conformance/test_zaofu_testkit_s4_persistence.py` | 已有进程级持久化检测；真实掉电/文件系统损坏方法完成与实机验收仍分开。 |
| `product_manifest_admission_research.md` | `tests/product_testkit/manifest.py` | manifest 顶层白名单、引用、有限超时和输入准入已存在；不再将这里建议的全部校验认作未实现。 |
| `product_openapi_runner_research.md` | `tests/product_testkit/schemathesis.py` | 已有 OpenAPI 执行适配器；当前 phase 仅 examples/coverage/fuzzing，stateful 仍未由该适配器准入。 |
| `product_test.md` | `tests/product_testkit/runner.py` | 早期“尚无统一产品 TestKit”结论已经过时；当前通用 runner 与参考产品闭环已存在，宿主接线/真实产品验收另记。 |
| `product_testing_research.md` | `tests/product_testkit/planner.py` | 方法选型供产品按能力采用；核心域含 P-17，Pact/Toxiproxy 不作为所有产品的前置。 |
| `product_worktree_identity_research.md` | `tests/product_testkit/executor.py` | 当前已补 target 规范化、tracked diff、执行前未跟踪白名单及 cleanup 后身份复核；ignored/沙箱边界仍需单独证明。 |
| `real_provider_canary_research.md` | `tests/testkit/native_run.py` | Codex K547 与 Claude 原生 pipeline 按 K610 操作者要求跳过；保留方法，不重新检查安装/登录或自动运行。 |
| `research_route_failure_recovery_research.md` | `tests/conformance/test_zaofu_testkit_s8_research.py` | 已有 Research 路由检测及重建合同；静态候选不自动变成已证实 bug。 |
| `rework_retry_model_research.md` | `tests/conformance/test_zaofu_testkit_s11_rework_model.py` | 已有 rework 生成模型；生产问题修复状态仍以台账为准，未复现组合不新增结论。 |
| `runner_concurrency_research.md` | `tests/testkit/runner.py` | 已有独占 claim 与独立输出合同；合作 writer 的边界不等同抵御任意恶意进程。 |
| `runner_integrity_research.md` | `tests/testkit/runner.py` | 完整性校验及 pytest_plugin.py 已存在；本报告是方法来源，不是 runner 仍未实现的清单。 |
| `s6_projection_freshness_research.md` | `tests/conformance/test_zaofu_testkit_s6_projection_contract.py` | 已有 freshness 检测入口；投影缺陷、环境错误、报告缺证须分别归因。 |
| `s6_schemathesis_research.md` | `tests/conformance/test_zaofu_testkit_s6_schemathesis.py` | 已有 ZaoFu API schema 测试，不等于生成产品的 api_contracts 适配器或全部 API 状态序列。 |
| `s7_security_research.md` | `tests/conformance/test_zaofu_testkit_s7_untrusted.py` | 已有不可信输入与 TOCTOU 检测入口；真实外部副作用/远端写入不由安全用例授权。 |
| `s8_recovery_research.md` | `tests/e2e/test_zaofu_testkit_s8_e2e.py` | 已有本地 candidate/Product Acceptance 测试；不要把回调/fixture 当作真实远端 merge 或新 /issue-merge。 |
| `store_integrity_concurrency_research.md` | `tests/conformance/test_zaofu_testkit_s2_store_integrity.py` | Store integrity、concurrency 和 projection_rebuild 检测已存在；已检出缺陷不等于检测框架缺失。 |
| `store_requirement_mapping_research.md` | `tests/conformance/test_zaofu_testkit_s2_projection_rebuild.py` | 重建只覆盖明确 projection 合同，不能由 EventLog 推导任意 canonical Store/必需 sidecar 可丢弃。 |
| `testKit_research.md` | `tests/testkit/suites.py` | 总体选型已进入实际 S0～S11 登记；T 层是风险分类，不按编号替代 S 阶段或产品 CI 层。 |
| `test_framework_research.md` | `tests/testkit/runner.py` | pytest/证据/报告底座已有实现；后续工具选型须按能力与预算，不重建大型平台。 |
| `test_p0_research.md` | `tests/testkit/suites.py` | 这是拆分前综合方法来源；当前 ZaoFu 自测与通用产品测试独立计划，模型评估不是主干验收条件。 |
| `testkit_browser_chain_research.md` | `tests/e2e/test_testkit_browser_truth_chain.py` | 已有 browser truth-chain 入口；必须在 Docker 执行并绑定后端真值，本次不启动浏览器。 |
| `testkit_core_requirement_audit.md` | `tests/testkit/suites.py` | 原 2026-09-14 有限缺口审计不能覆盖后续 K598～K613；当前开放项查 need_to_do_inventory.py。 |
| `testkit_runner_research.md` | `tests/testkit/pytest_plugin.py` | 已有 TestReport 分相采集及 runner 分类；不能只按 JUnit skip 或退出码判断已知缺陷通过。 |
| `testkit_surface_requirement_audit.md` | `tests/testkit/zap_launcher.py` | ZAP/k6/host 后续能力已有增量；早期工具范围保留作历史审查，当前长样本和实机验收仍按 K613。 |
| `timeout_cleanup_research.md` | `tests/testkit/cleanup.py` | 当前 terminate_process_group 已去除组长退出即早退并按进程组存活升级 SIGKILL；历史风险不再直接描述当前函数。 |
| `triage_route_execution_research.md` | `tests/conformance/test_zaofu_testkit_s8_triage_route.py` | 已有 triage route 检测；只读 triage 与写入 Fix Run 权限边界仍独立。 |
| `vm_inflight_recovery_research.md` | `tests/conformance/test_zaofu_testkit_s4_isolated_vm_recovery_host.py` | 后续 K577/K591 已记录在途恢复/guest panic 检测证据；早期载荷待验证不再代表全部现状，实机掉电仍开放。 |
| `vm_supervisor_cleanup_research.md` | `tests/testkit/isolated_vm_supervisor.py` | supervisor 及 test_isolated_vm_supervisor.py 已存在；owned child 清理不外推任意后代/cgroup 或 OS 断电保证。 |
| `xfail_traceability_research.md` | `tests/testkit/pytest_plugin.py` | 当前插件已保存 skipped/xfail 的 longrepr；S0 已有追溯入口。旧“只保存 failed”描述只适用于历史基线。 |
| `zap_launcher_research.md` | `tests/testkit/zap_launcher.py` | 已有受控 launcher 与 fake 合同；真实扫描、认证/主动覆盖和风险判定仍须独立证据，FakeDocker 不是实扫。 |

## 4. 审计与验收 JSON 逐文件核对（31 份）

以下每份均完成 JSON 解析和文档角色识别，未改内容。除 coverage matrix 为历史覆盖/状态账外，其他均是审计或验收**输入清单**，不是执行结果。文件名中的 current 仅代表该日期快照；语法通过不代表其中所有证据引用仍存在或语义验收通过。

| 文件（位于 `04_audit_and_issue_ledger/`） | schema_version | 校验及处置 |
|---|---|---|
| `coverage_matrix.json` | `zaofu-testkit-coverage-matrix.v1` | 可解析；保留历史内容、refs 和摘要，不重新裁决 |
| `native-acceptance-20260923.json` | `zf-native-acceptance-input.v1` | 可解析；保留历史内容、refs 和摘要，不重新裁决 |
| `native-acceptance-k512-20260923.json` | `zf-native-acceptance-input.v1` | 可解析；保留历史内容、refs 和摘要，不重新裁决 |
| `native-acceptance-k513-20260923.json` | `zf-native-acceptance-input.v1` | 可解析；保留历史内容、refs 和摘要，不重新裁决 |
| `native-acceptance-k514-20260923.json` | `zf-native-acceptance-input.v1` | 可解析；保留历史内容、refs 和摘要，不重新裁决 |
| `native-acceptance-k515-20260924.json` | `zf-native-acceptance-input.v1` | 可解析；保留历史内容、refs 和摘要，不重新裁决 |
| `requirement-audit-current-20260928.json` | `zf-requirement-audit-input.v1` | 可解析；保留历史内容、refs 和摘要，不重新裁决 |
| `requirement-audit-k554-20260925.json` | `zf-requirement-audit-input.v1` | 可解析；保留历史内容、refs 和摘要，不重新裁决 |
| `requirement-audit-k555-20260925.json` | `zf-requirement-audit-input.v1` | 可解析；保留历史内容、refs 和摘要，不重新裁决 |
| `requirement-audit-k556-20260925.json` | `zf-requirement-audit-input.v1` | 可解析；保留历史内容、refs 和摘要，不重新裁决 |
| `requirement-audit-k557-20260925.json` | `zf-requirement-audit-input.v1` | 可解析；保留历史内容、refs 和摘要，不重新裁决 |
| `requirement-audit-k561-20260925.json` | `zf-requirement-audit-input.v1` | 可解析；保留历史内容、refs 和摘要，不重新裁决 |
| `requirement-audit-k562-20260925.json` | `zf-requirement-audit-input.v1` | 可解析；保留历史内容、refs 和摘要，不重新裁决 |
| `requirement-audit-k563-20260925.json` | `zf-requirement-audit-input.v1` | 可解析；保留历史内容、refs 和摘要，不重新裁决 |
| `requirement-audit-k566-20260925.json` | `zf-requirement-audit-input.v1` | 可解析；保留历史内容、refs 和摘要，不重新裁决 |
| `requirement-audit-k571-20260926.json` | `zf-requirement-audit-input.v1` | 可解析；保留历史内容、refs 和摘要，不重新裁决 |
| `requirement-audit-k577-20260928.json` | `zf-requirement-audit-input.v1` | 可解析；保留历史内容、refs 和摘要，不重新裁决 |
| `requirement-audit-k578-20260928.json` | `zf-requirement-audit-input.v1` | 可解析；保留历史内容、refs 和摘要，不重新裁决 |
| `requirement-audit-k580-20260928.json` | `zf-requirement-audit-input.v1` | 可解析；保留历史内容、refs 和摘要，不重新裁决 |
| `requirement-audit-k581-20260928.json` | `zf-requirement-audit-input.v1` | 可解析；保留历史内容、refs 和摘要，不重新裁决 |
| `requirement-audit-k582-20260928.json` | `zf-requirement-audit-input.v1` | 可解析；保留历史内容、refs 和摘要，不重新裁决 |
| `requirement-audit-k583-20260928.json` | `zf-requirement-audit-input.v1` | 可解析；保留历史内容、refs 和摘要，不重新裁决 |
| `requirement-audit-k584-20260928.json` | `zf-requirement-audit-input.v1` | 可解析；保留历史内容、refs 和摘要，不重新裁决 |
| `requirement-audit-k585-20260928.json` | `zf-requirement-audit-input.v1` | 可解析；保留历史内容、refs 和摘要，不重新裁决 |
| `requirement-audit-k587-20260928.json` | `zf-requirement-audit-input.v1` | 可解析；保留历史内容、refs 和摘要，不重新裁决 |
| `requirement-audit-k588-20260928.json` | `zf-requirement-audit-input.v1` | 可解析；保留历史内容、refs 和摘要，不重新裁决 |
| `requirement-audit-k589-20260928.json` | `zf-requirement-audit-input.v1` | 可解析；保留历史内容、refs 和摘要，不重新裁决 |
| `requirement-audit-k591-20260928.json` | `zf-requirement-audit-input.v1` | 可解析；保留历史内容、refs 和摘要，不重新裁决 |
| `requirement-audit-k594-20260929.json` | `zf-requirement-audit-input.v1` | 可解析；保留历史内容、refs 和摘要，不重新裁决 |
| `requirement-audit-k597-20260929.json` | `zf-requirement-audit-input.v1` | 可解析；保留历史内容、refs 和摘要，不重新裁决 |
| `requirement-audit-k599-20260929.json` | `zf-requirement-audit-input.v1` | 可解析；保留历史内容、refs 和摘要，不重新裁决 |

## 5. 重启材料及备份逐文件核验

| 文件 | 审核结果 |
|---|---|
| `20260930-1116/evidence_manifest.json` | 可解析；按每条记录读取备份，112 个文件的大小和 SHA-256 全部匹配，共 10,966,887 字节。 |
| `20260930-1116/audit_tmp_reference_inventory.json` | 可解析；49 个去重 /tmp 引用的存在性是保存时快照（1 存在、48 缺失），本轮没有更新或补造缺证。 |

下面路径相对 `05_restart_handoff/20260930-1116/`。备份记录按字节校验，不逐条重写日志含义或把备份源码当当前实现；原路径和期望 SHA 见 evidence_manifest.json。

| 备份文件 | 字节数 | 大小 / SHA-256 |
|---|---:|---|
| `tmp_evidence/zf-k613-readiness-s0-20260930-YJTU7L/pytest-junit.xml` | 7870 | 匹配 / 匹配 |
| `tmp_evidence/zf-s1-k612-power-method-20260929-current/.testkit-run.claim` | 9 | 匹配 / 匹配 |
| `tmp_evidence/zf-s1-k612-power-method-20260929-current/pytest-junit.xml` | 81179 | 匹配 / 匹配 |
| `tmp_evidence/zf-s1-k612-power-method-20260929-current/pytest-testkit.json` | 403258 | 匹配 / 匹配 |
| `tmp_evidence/zf-s1-k612-power-method-20260929-current/pytest.stderr.log` | 0 | 匹配 / 匹配 |
| `tmp_evidence/zf-s1-k612-power-method-20260929-current/pytest.stdout.log` | 672 | 匹配 / 匹配 |
| `tmp_evidence/zf-s1-k612-power-method-20260929-current/testkit-run.json` | 283351 | 匹配 / 匹配 |
| `tmp_evidence/zf-s1-k608-foundation-20260929/.testkit-run.claim` | 9 | 匹配 / 匹配 |
| `tmp_evidence/zf-s1-k608-foundation-20260929/pytest-junit.xml` | 75734 | 匹配 / 匹配 |
| `tmp_evidence/zf-s1-k608-foundation-20260929/pytest-testkit.json` | 375941 | 匹配 / 匹配 |
| `tmp_evidence/zf-s1-k608-foundation-20260929/pytest.stderr.log` | 0 | 匹配 / 匹配 |
| `tmp_evidence/zf-s1-k608-foundation-20260929/pytest.stdout.log` | 672 | 匹配 / 匹配 |
| `tmp_evidence/zf-s1-k608-foundation-20260929/testkit-run.json` | 264109 | 匹配 / 匹配 |
| `tmp_evidence/zf-k579-host-probe-20260928.log` | 98 | 匹配 / 匹配 |
| `tmp_evidence/zf-k579-focused-20260928.log` | 194 | 匹配 / 匹配 |
| `tmp_evidence/zf-mainline-current-20260928-mK027d/.testkit-run.claim` | 29 | 匹配 / 匹配 |
| `tmp_evidence/zf-mainline-current-20260928-mK027d/pytest-junit.xml` | 91693 | 匹配 / 匹配 |
| `tmp_evidence/zf-mainline-current-20260928-mK027d/pytest-testkit.json` | 378815 | 匹配 / 匹配 |
| `tmp_evidence/zf-mainline-current-20260928-mK027d/pytest.stderr.log` | 0 | 匹配 / 匹配 |
| `tmp_evidence/zf-mainline-current-20260928-mK027d/pytest.stdout.log` | 5889 | 匹配 / 匹配 |
| `tmp_evidence/zf-mainline-current-20260928-mK027d/source-after.json` | 496868 | 匹配 / 匹配 |
| `tmp_evidence/zf-mainline-current-20260928-mK027d/source-before.json` | 496868 | 匹配 / 匹配 |
| `tmp_evidence/zf-mainline-current-20260928-mK027d/testkit-run.json` | 262482 | 匹配 / 匹配 |
| `tmp_evidence/zf-mainline-after-premerge-bind-20260928-023131/.testkit-run.claim` | 29 | 匹配 / 匹配 |
| `tmp_evidence/zf-mainline-after-premerge-bind-20260928-023131/pytest-junit.xml` | 91835 | 匹配 / 匹配 |
| `tmp_evidence/zf-mainline-after-premerge-bind-20260928-023131/pytest-testkit.json` | 379404 | 匹配 / 匹配 |
| `tmp_evidence/zf-mainline-after-premerge-bind-20260928-023131/pytest.stderr.log` | 0 | 匹配 / 匹配 |
| `tmp_evidence/zf-mainline-after-premerge-bind-20260928-023131/pytest.stdout.log` | 5889 | 匹配 / 匹配 |
| `tmp_evidence/zf-mainline-after-premerge-bind-20260928-023131/source-after.json` | 496868 | 匹配 / 匹配 |
| `tmp_evidence/zf-mainline-after-premerge-bind-20260928-023131/source-before.json` | 496868 | 匹配 / 匹配 |
| `tmp_evidence/zf-mainline-after-premerge-bind-20260928-023131/testkit-run.json` | 262941 | 匹配 / 匹配 |
| `tmp_evidence/zf-mainline-current-20260929/.testkit-run.claim` | 29 | 匹配 / 匹配 |
| `tmp_evidence/zf-mainline-current-20260929/pytest-junit.xml` | 120733 | 匹配 / 匹配 |
| `tmp_evidence/zf-mainline-current-20260929/pytest-testkit.json` | 514279 | 匹配 / 匹配 |
| `tmp_evidence/zf-mainline-current-20260929/pytest.stderr.log` | 0 | 匹配 / 匹配 |
| `tmp_evidence/zf-mainline-current-20260929/pytest.stdout.log` | 6418 | 匹配 / 匹配 |
| `tmp_evidence/zf-mainline-current-20260929/source-after.json` | 500568 | 匹配 / 匹配 |
| `tmp_evidence/zf-mainline-current-20260929/source-before.json` | 500568 | 匹配 / 匹配 |
| `tmp_evidence/zf-mainline-current-20260929/testkit-run.json` | 353454 | 匹配 / 匹配 |
| `tmp_evidence/zf-s0-k598-final/.testkit-run.claim` | 9 | 匹配 / 匹配 |
| `tmp_evidence/zf-s0-k598-final/pytest-junit.xml` | 3142 | 匹配 / 匹配 |
| `tmp_evidence/zf-s0-k598-final/pytest-testkit.json` | 14409 | 匹配 / 匹配 |
| `tmp_evidence/zf-s0-k598-final/pytest.stderr.log` | 0 | 匹配 / 匹配 |
| `tmp_evidence/zf-s0-k598-final/pytest.stdout.log` | 100 | 匹配 / 匹配 |
| `tmp_evidence/zf-s0-k598-final/testkit-run.json` | 11188 | 匹配 / 匹配 |
| `tmp_evidence/zf-k599-audit-current-20260929-132040/K598-current-deterministic-mainline/deterministic_mainline-after.json` | 500568 | 匹配 / 匹配 |
| `tmp_evidence/zf-k599-audit-current-20260929-132040/K598-current-deterministic-mainline/deterministic_mainline-before.json` | 500568 | 匹配 / 匹配 |
| `tmp_evidence/zf-k599-audit-current-20260929-132040/K598-current-deterministic-mainline/deterministic_mainline-junit.xml` | 120733 | 匹配 / 匹配 |
| `tmp_evidence/zf-k599-audit-current-20260929-132040/K598-current-deterministic-mainline/deterministic_mainline-phases.json` | 514279 | 匹配 / 匹配 |
| `tmp_evidence/zf-k599-audit-current-20260929-132040/K598-current-deterministic-mainline/testkit-run.json` | 353454 | 匹配 / 匹配 |
| `tmp_evidence/zf-k599-audit-current-20260929-132040/input-manifest.json` | 578 | 匹配 / 匹配 |
| `tmp_evidence/zf-k599-audit-current-20260929-132040/requirement-audit.json` | 4509 | 匹配 / 匹配 |
| `tmp_evidence/zf-s0-k599-final/.testkit-run.claim` | 9 | 匹配 / 匹配 |
| `tmp_evidence/zf-s0-k599-final/pytest-junit.xml` | 3146 | 匹配 / 匹配 |
| `tmp_evidence/zf-s0-k599-final/pytest-testkit.json` | 14403 | 匹配 / 匹配 |
| `tmp_evidence/zf-s0-k599-final/pytest.stderr.log` | 0 | 匹配 / 匹配 |
| `tmp_evidence/zf-s0-k599-final/pytest.stdout.log` | 110 | 匹配 / 匹配 |
| `tmp_evidence/zf-s0-k599-final/testkit-run.json` | 11181 | 匹配 / 匹配 |
| `tmp_evidence/zf-k593c-20260928-vxEi4l/baseline.xml` | 673 | 匹配 / 匹配 |
| `tmp_evidence/zf-k593c-20260928-vxEi4l/dev-verify-plan.json` | 12896 | 匹配 / 匹配 |
| `tmp_evidence/zf-k593c-20260928-vxEi4l/focused.xml` | 26657 | 匹配 / 匹配 |
| `tmp_evidence/zf-k593c-20260928-vxEi4l/hash-sensor-independent.log` | 13945 | 匹配 / 匹配 |
| `tmp_evidence/zf-k593c-20260928-vxEi4l/hash-sensor-independent.xml` | 17247 | 匹配 / 匹配 |
| `tmp_evidence/zf-k593c-20260928-vxEi4l/impact-final.xml` | 23479 | 匹配 / 匹配 |
| `tmp_evidence/zf-k593c-20260928-vxEi4l/impact.xml` | 10257 | 匹配 / 匹配 |
| `tmp_evidence/zf-k593c-20260928-vxEi4l/new-direct.xml` | 649 | 匹配 / 匹配 |
| `tmp_evidence/zf-k593c-20260928-vxEi4l/premerge-after.log` | 5706 | 匹配 / 匹配 |
| `tmp_evidence/zf-k593c-20260928-vxEi4l/premerge-after.xml` | 13022 | 匹配 / 匹配 |
| `tmp_evidence/zf-k593c-20260928-vxEi4l/premerge-before.xml` | 13178 | 匹配 / 匹配 |
| `tmp_evidence/zf-k593c-20260928-vxEi4l/premerge-final.log` | 5706 | 匹配 / 匹配 |
| `tmp_evidence/zf-k593c-20260928-vxEi4l/premerge-final.xml` | 13022 | 匹配 / 匹配 |
| `tmp_evidence/zf-k593c-20260928-vxEi4l/s0/.testkit-run.claim` | 9 | 匹配 / 匹配 |
| `tmp_evidence/zf-k593c-20260928-vxEi4l/s0/pytest-junit.xml` | 3145 | 匹配 / 匹配 |
| `tmp_evidence/zf-k593c-20260928-vxEi4l/s0/pytest-testkit.json` | 14411 | 匹配 / 匹配 |
| `tmp_evidence/zf-k593c-20260928-vxEi4l/s0/pytest.stderr.log` | 0 | 匹配 / 匹配 |
| `tmp_evidence/zf-k593c-20260928-vxEi4l/s0/pytest.stdout.log` | 100 | 匹配 / 匹配 |
| `tmp_evidence/zf-k593c-20260928-vxEi4l/s0/testkit-run.json` | 11267 | 匹配 / 匹配 |
| `tmp_evidence/zf-k593c-20260928-vxEi4l/s0-final/.testkit-run.claim` | 9 | 匹配 / 匹配 |
| `tmp_evidence/zf-k593c-20260928-vxEi4l/s0-final/pytest-junit.xml` | 3142 | 匹配 / 匹配 |
| `tmp_evidence/zf-k593c-20260928-vxEi4l/s0-final/pytest-testkit.json` | 14401 | 匹配 / 匹配 |
| `tmp_evidence/zf-k593c-20260928-vxEi4l/s0-final/pytest.stderr.log` | 0 | 匹配 / 匹配 |
| `tmp_evidence/zf-k593c-20260928-vxEi4l/s0-final/pytest.stdout.log` | 100 | 匹配 / 匹配 |
| `tmp_evidence/zf-k593c-20260928-vxEi4l/s0-final/testkit-run.json` | 11299 | 匹配 / 匹配 |
| `tmp_evidence/zf-k593c-20260928-vxEi4l/s5/.testkit-run.claim` | 9 | 匹配 / 匹配 |
| `tmp_evidence/zf-k593c-20260928-vxEi4l/s5/pytest-junit.xml` | 76729 | 匹配 / 匹配 |
| `tmp_evidence/zf-k593c-20260928-vxEi4l/s5/pytest-testkit.json` | 366880 | 匹配 / 匹配 |
| `tmp_evidence/zf-k593c-20260928-vxEi4l/s5/pytest.stderr.log` | 0 | 匹配 / 匹配 |
| `tmp_evidence/zf-k593c-20260928-vxEi4l/s5/pytest.stdout.log` | 2162 | 匹配 / 匹配 |
| `tmp_evidence/zf-k593c-20260928-vxEi4l/s5/testkit-run.json` | 254229 | 匹配 / 匹配 |
| `tmp_evidence/zf-k593c-20260928-vxEi4l/spawn_coordinator.before.py` | 43013 | 匹配 / 匹配 |
| `tmp_evidence/zf-k587-k565-s4-20260928/.testkit-run.claim` | 9 | 匹配 / 匹配 |
| `tmp_evidence/zf-k587-k565-s4-20260928/case-evidence/cf1a6be5a1629505bc20061bdaa6d266a1dcffb710cbf2c701e92430afdb1486/call-report.json` | 137846 | 匹配 / 匹配 |
| `tmp_evidence/zf-k587-k565-s4-20260928/case-evidence/cf1a6be5a1629505bc20061bdaa6d266a1dcffb710cbf2c701e92430afdb1486/checkpoint.json` | 86201 | 匹配 / 匹配 |
| `tmp_evidence/zf-k587-k565-s4-20260928/case-evidence/cf1a6be5a1629505bc20061bdaa6d266a1dcffb710cbf2c701e92430afdb1486/pre-teardown.json` | 137847 | 匹配 / 匹配 |
| `tmp_evidence/zf-k587-k565-s4-20260928/case-evidence/cf1a6be5a1629505bc20061bdaa6d266a1dcffb710cbf2c701e92430afdb1486/registered.json` | 1463 | 匹配 / 匹配 |
| `tmp_evidence/zf-k587-k565-s4-20260928/case-evidence/e0937b8bebcdd095e3e279f99c46ff923eb6749d22707f9e21e49042b0ccd402/call-report.json` | 138598 | 匹配 / 匹配 |
| `tmp_evidence/zf-k587-k565-s4-20260928/case-evidence/e0937b8bebcdd095e3e279f99c46ff923eb6749d22707f9e21e49042b0ccd402/checkpoint.json` | 85290 | 匹配 / 匹配 |
| `tmp_evidence/zf-k587-k565-s4-20260928/case-evidence/e0937b8bebcdd095e3e279f99c46ff923eb6749d22707f9e21e49042b0ccd402/pre-teardown.json` | 138599 | 匹配 / 匹配 |
| `tmp_evidence/zf-k587-k565-s4-20260928/case-evidence/e0937b8bebcdd095e3e279f99c46ff923eb6749d22707f9e21e49042b0ccd402/registered.json` | 1440 | 匹配 / 匹配 |
| `tmp_evidence/zf-k587-k565-s4-20260928/pytest-testkit.json` | 180860 | 匹配 / 匹配 |
| `tmp_evidence/zf-k587-k565-s4-20260928/pytest.stderr.log` | 0 | 匹配 / 匹配 |
| `tmp_evidence/zf-k587-k565-s4-20260928/pytest.stdout.log` | 160 | 匹配 / 匹配 |
| `tmp_evidence/zf-k579-s0-20260928/.testkit-run.claim` | 9 | 匹配 / 匹配 |
| `tmp_evidence/zf-k579-s0-20260928/pytest-junit.xml` | 3148 | 匹配 / 匹配 |
| `tmp_evidence/zf-k579-s0-20260928/pytest-testkit.json` | 14394 | 匹配 / 匹配 |
| `tmp_evidence/zf-k579-s0-20260928/pytest.stderr.log` | 0 | 匹配 / 匹配 |
| `tmp_evidence/zf-k579-s0-20260928/pytest.stdout.log` | 110 | 匹配 / 匹配 |
| `tmp_evidence/zf-k579-s0-20260928/testkit-run.json` | 11192 | 匹配 / 匹配 |
| `tmp_evidence/zf-k583-direct-20260928.xml` | 22203 | 匹配 / 匹配 |
| `tmp_evidence/zf-k583-impact-20260928.xml` | 32491 | 匹配 / 匹配 |
| `tmp_evidence/zf-k583-cross-boundary-20260928.xml` | 8826 | 匹配 / 匹配 |
| `tmp_evidence/zf-k583-host-complete-20260928.xml` | 850 | 匹配 / 匹配 |

## 6. 原始附件与兼容链接

`attachments/` 不移动、不改写、不删除。按 lstat、不跟随符号链接、包含隐藏项、排除根目录本身的口径，当前为：

| 类型 | 数量 |
|---|---:|
| 普通文件 | 18,649 |
| 目录 | 8,681 |
| 符号链接 | 189 |
| 合计 | 27,519 |

普通文件逻辑大小合计 1,974,460,927 字节；已读取所有普通文件计算摘要，但未逐行审阅每份原始日志、未重新判定历史测试 verdict。符号链接只读取链接文字，不把链接目标再次纳入计数。

本次可复现内容树摘要为 `f43cf8d09f1a0a0a9980230a0bad4a29980298ec13d216f19615f1f0bbf70883`。算法：以附件根相对路径排序；目录为 `[path,"d"]`，链接为 `[path,"l",readlink]`，普通文件为 `[path,"f",bytes,sha256(content)]`；每行 JSON.stringify(row) 后加 LF，将全部行 UTF-8 字节输入 SHA-256。此摘要是当前快照，不是原生文件系统镜像摘要。

此前记录 27,519 与本次同口径计数相同；另一扫描记录 18,952、1,985,487,031 字节和摘要 `6be300f6bf270f60e02dd8a024389dce212b939fe9b4ff8dd97acd19ced608a4`，早期另有摘要 `4a23d5338e4143accbe48248450e04943bb1813ecbc9baa15484225b08f8677d`。缺少这些摘要的完整算法和逐项基线，不能直接跨口径比较，也不能仅凭总数声称重启前后所有附件字节未变。未观察到计数减少；本次没有执行任何附件写操作。

| 链接位置 | 目标 | 校验 |
|---|---|---|
| 根目录 `ZaoFu_issue.md` | `04_audit_and_issue_ledger/ZaoFu_issue.md` | 可解析 |
| 根目录 `coverage_matrix.json` | `04_audit_and_issue_ledger/coverage_matrix.json` | 可解析 |
| 根目录 `product-test-manifest.example.json` | `03_plans_and_steps/product-test-manifest.example.json` | 可解析 |
| `04_audit_and_issue_ledger/attachments` | `../attachments` | 可解析 |

## 7. 残留旧描述与明确保留项

- Product_steps 的历史 P-16“尚未形成统一 evidence”、旧“下一步继续”属于阶段记录；当前结论读顶部说明和最新增量，不自动新增执行任务。
- steps、delivery_route_terminal_research、ZaoFu_issue 的旧 Codex 未登录/Claude 命令不存在属于带阶段/日期的证据。zaofu_test 中“CLI 不存在”属于 mock 故障场景，两者都不应全局删除或改成当前成功。
- 调研中的旧“尚未实现”由顶部逐文件对齐说明限定；确有实现的能力引用当前入口，未证明的组合仍显式保留。
- 历史报告测试数、通过/失败、Issue 与证据摘要不重算、不回写。文档修订后的字节自然与历史 source digest 不同；历史绑定应回读当时快照，不把新文档伪装成当时原件。

## 8. 验证范围与未完成的验真

- 最终静态检查：59 份 Markdown、926 处本地链接目标存在；47 份调研均有当前对齐入口；产品汇总恰有 27 个 R 编号；具体产品名称与旧中文分类目录名扫描无命中；manifest 模板 JSON 可解析；范围内 `git diff --check` 无报错（该命令不覆盖未跟踪文件，不能单独证明所有新文档无误）。
- 已核对两套当前方案、实现/待办、47 份历史报告定位、问题台账、31 个审计 JSON、模板、交接材料、112 个备份文件及四个兼容链接。
- 当前文档本地 Markdown 链接做文件存在性检查（忽略 :行号 和 #锚点）；这不验证行号语义、锚点或互联网链接，也不遍历历史报告内所有隐式代码路径。
- 原始附件仅做清单与内容摘要扫描；审计 JSON 只做解析/角色核对，没有重新执行 requirement audit 或逐项验证所有历史 refs。因此不能宣称整个证据库已重新验收通过。
- 本轮不运行测试、生成器测试、Provider、VM、浏览器或长测；未改测试代码、生产代码和运行状态，未提交、合并、推送或恢复构建。
- 原生两项继续跳过；Feishu、真实 /issue-merge 和明确降级项继续排除。恢复建设需要新的明确指令，不因文档中的历史下一步自动启动。
