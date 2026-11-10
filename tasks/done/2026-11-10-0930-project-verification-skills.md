> 状态: done — `a44693a2 chore: add project verification skills and executable acceptance probes`

# 项目验证方法与 ZaoFu 三条回归链

用户批准将通用验证方法固化为 Skill，以 Prompt 传入本轮范围，测试代码执行断言。
本批扩展现有 Verify 方法，新增 ZaoFu 项目方法和外部产品参考，复用现有执行入口。
不引入第二套任务/结果 schema，不修改真实项目运行状态。

## 验收

- 通用方法关联原始条款、场景、断言与证据 -> verify: 现有 Verify 入口可路由到方法，资源在 Codex/Claude 的实际 materialization 后仍可读取。
- ZaoFu 项目方法提供调用 Prompt 和覆盖地图 -> verify: 启动、多 lane、重启恢复各有可执行命令，注明 mock、浏览器、真实进程的证明边界。
- 多 lane 验收回执来自执行 -> verify: Impl/Verify 在各自绑定的 Git target 执行合同命令，候选中两个产物均通过；错误内容、缺失产物、错误 target 均被探针拒绝。
- 项目模板可用于其他产品 -> verify: 方法明确启动、数据隔离、API/数据库/文件验收及清理，并沿用该项目原始需求；不声称已经验证 dl。
- 分发与收尾 -> verify: 三份 Skill 包一致，针对性 pytest、dev-verify 通过，实现提交后记录 commit 并归档。

## 入口

- `skills/zf-harness-verification-checklist/`
- `skills/zf-verify-zaofu/`
- `docs/runbooks/project-verification.md`

## 验证记录

- 定向探针和双 lane 场景：13 passed；错误内容/缺失文件的反例均以预期 exit 1 拒绝，错误 commit/dirty target 在执行前拒绝。
- dev-verify plan/run --base dev --no-parallel，显式补充 project_verification、generic_workflow_complex_mock_e2e、design199_recovery_mock_e2e、task_pipeline_fault_matrix、workflow_runtime_start、call_result_admission、verification_evidence：102 passed，1 skipped。
- 跳过项：test_dev_codex_example_materializes_each_role_skills_and_dependencies 缺少 ./skills/external 与 ./skills/critic；不计为覆盖通过。
- bash scripts/dev-premerge-gate.sh：manual docs currentness ok，53 passed。
- 新 Skill 元数据、依赖闭包、引用资源和三份分发一致性由上述 pytest 验证；Codex/Claude × copy/symlink 实际 materialization 均通过。agents/openai.yaml 三份校验通过。
- 通用 skill-creator quick_validate 在添加 ZaoFu 原生 dependencies 前通过；最终包含该仓库扩展字段的包采用仓库解析器与实际 materialization 验证，未弱化原生依赖声明。
- 原始日志、执行计划与 receipt：/tmp/zf-project-verification-20261110-095002-44reo4wq/。
- 未运行浏览器、OS 进程恢复或真实 Provider；没有安装 Midscene/e2e，没有声称 dl 已验收。
- 修改限于方法、文档和测试；未修改 runtime 生产实现、zf.yaml 或真实项目状态。
