# 通用 Plan → Impl → Verify 执行一致性改进

> 状态: active

用户批准依据：2026-10-21 对任务拆分、SDD/TDD、实施及验证机制的讨论；要求通用演进并适配其他业务。独立 worktree 基线 `5191c9e6`。不直接改正在运行的项目或自动批准其 Plan。

## 实施清单与验收

- A. 任务切片方法与新项目默认值：取消默认按 AC 数量强制切片，保留显式限制和路径隔离；强化实现自检。step -> verify: 新 draft 可承载原子事务的多条 AC；旧项目显式限制仍生效。
- B. 执行事实交接：在现有 Plan preflight/critic 路径暴露实际验证触发方式、初始化配置、自检策略、依赖图和输入体积；语义判断由 Planner/Critic 作出。step -> verify: 阶段屏障与逐任务 pipeline 得到不同事实；不会因计数直接批准、拒绝或改变流程。
- C. 受控输入按任务读取：提供显式、版本绑定的局部规格交接，保留共享规则及 Candidate 全量验收，兼容旧端口。step -> verify: 两种业务样例读取自己的规格和共享规则，漏配/篡改失败，最终验收仍读全部正文。
- D. 跨业务回归与效果评估：记录旧新对照、运行时 caller 测试及限制。step -> verify: focused tests、dev-verify、sentinel 和相关 mock E2E；不把静态检查当成真实产品/模型质量提升。

## 边界

实现说明：[Plan 执行一致性与按任务交接](../../docs/impl/plan-execution-readiness.md)。

不增加业务语义状态机；不默认启用 canary v4；不代替 owner 选择技术栈或批准产品决定。环境初始化由项目声明，执行事实必须指出未接线而非猜测安装命令。复杂片的设计/TDD 细化及短反馈路线选择写入通用 skill。

## 验证记录

实现清单 A/B/C/D 已完成，未合入 dev、未 push，未修改 cargo 的运行配置或审批。

### 基线对照（5191c9e6）

| 指标 | 修改前 | 修改后 |
| --- | --- | --- |
| 新项目默认 AC 上限 | 8，blocking | 0，不用数量强制拆片；显式设 8 仍阻断 9 AC |
| 新项目 impl_self_check_required | false | true |
| Critic 执行事实输入 | 没有此专用输入 | preflight 生成并作为必读、摘要绑定的 sidecar 接入真实 caller |
| 库存转移规格样例，50 任务 | 全文 103091 bytes | 本片及共享规则 2093 bytes，减少 97.97% |
| 文档发布规格样例，50 任务 | 全文 103141 bytes | 本片及共享规则 2094 bytes，减少 97.97% |

两个规格样例为确定性合成夹具，不是 LLM 质量评估；shared、本片正文逐值相同，
Candidate 仍全文读取，旧端口仍全文读取。不能外推为整个运行 token 或耗时下降 98%。
只读运行新 CLI 检查 cargo，准确得到 60 节点依赖链，以及
`task_verify_after_candidate`、`setup_not_wired`、`self_check_not_required`。
这没有改变其现有计划或证明它可完成真实产品 build。

### 检查层级与结果

- `python scripts/dev-verify.py plan --base dev --tests tests/test_plan_execution_review.py --tests tests/test_task_scoped_plan_inputs.py --workers 2 --json`
  → 无未映射错误，选择 141 个影响测试文件。工具按数量自动扩大到全套；
  按 AGENTS.md 不因数量触发全套的要求，实际执行 selected_tests，未运行全量 pytest。
- 141 文件，`pytest <selected_tests> -q --no-cov -m 'not serial and not perf and not host and not real_provider' -n 2 --dist loadfile`
  → 1808 passed / 1 skipped / 1 failed（新接线令两个既有文件超过 1000 行）。
  已提取新增职责到独立模块，未新增债务豁免。
- 提取后，`pytest tests/test_plan_execution_review.py tests/test_task_scoped_plan_inputs.py tests/test_plan_candidate_preflight.py tests/test_artifact_query_service.py tests/test_artifact_read_ledger.py tests/test_plan_synth_handoff.py tests/test_writer_contract_handoff.py tests/e2e/test_stale_contract_handoff_mock_e2e.py tests/e2e/test_task_pipeline_v4_runtime_noise_mock_e2e.py -q --no-cov`
  → **94 passed**；包含 4 个 mock E2E，覆盖旧契约拒绝及已有 v4 路线。
- `python scripts/dev-verify.py run --base dev --tests tests/test_plan_execution_review.py --tests tests/test_task_scoped_plan_inputs.py --workers 2 --skip-tier deterministic`
  → serial **10 passed**，sentinel **51 passed**，手册生成一致性通过。
- CLI/流程 profile/writer/查询/计划交接前一轮兼容检查 **121 passed**。
- 两个 skill 三份完整分发字节一致，项目 `read_skill_metadata` 无 warning。
  通用 skill-creator quick_validate 不识别 ZaoFu 原有 stages/dependencies 等扩展字段；
  没有为通过通用工具删除这些有效字段，采用项目 provenance 检查。
- 新模块 Ruff、`git diff --check` 通过；无真实 provider/browser 产品验收。

### 未改变的执行边界

SDD/TDD 语义审查、真实依赖和输入充分性仍由 agent/skill 判断；运行时只检查显式分区、
身份、摘要和必读路径。新 CLI 使用当前指定配置并明确标记，不伪称在途冻结配置。
v4 仍显式 opt-in，旧项目需受控配置调整/重规划才能采用新默认及分区规格。
真实模型 A/B 与 cargo 首个纵向产品闭环仍未执行，不以静态测试替代这些证据。
