# 198 第二批：关系导航、发布生命周期与恢复输入

> 状态: active

来源：[设计 198](../../docs/design/198-context-index-write-read-resume.md)。用户批准“继续生成backlog实现”。
基线 `24990423`（第一批已提交，尚未合入 dev），独立分支
`wip/codex-20261102-context-198-followup`。本批接续 C3/C5/C6，不把它们的局部验收
等同于设计 198 的全部设想完成；C1/C2/C7/C8 剩余条件见设计对照。

## 实施项与验收

- [x] B1 关系和章节导航（C3）：从已绑定、获准 selector 的结构化字段提取声明的
  Task/REQ/AC/依赖引用；为 Markdown 生成绑定版本的章节位置。精确 ID 查询与
  既有 locator 查询共用授权/预算/游标，保留正文受控读取；不根据自然语言推断权威关系。
  100 Task、多角色、同名章节、数组重排 -> verify: 只显示获准切片的 ID/标题，
  不泄漏其他 Task，不混版本；章节定位可用原 selector 和分页偏移回读，索引不算完整消费。
- [x] B2 发布生命周期（C5）：沿既有不可变发布与 Artifact Query 投影呈现导航，
  复用 Plan Package reducer 的准入/拒绝/替代及取消事实，绑定水位/版本；未发布草稿
  不成为当前输入，旧 attempt 不静默切到新版本，目录丢失允许重建。
  发布/替代/取消/重放/投影损坏 -> verify: 旧来源明确历史或失效，当前来源由既有
  canonical resolver 决定；摘要可见性不扩大授权，无半发布与第二状态机。
- [x] B3 恢复输入编排（C6）：在现有恢复消费者自动构建当前 attempt 的输入恢复包，
  包含身份/固定版本、授权目录、缺读和当前记忆未知的区别、当前检查点及补读命令；
  提供相同能力的受控 CLI，保留独立角色自己的消费证据。
  当前身份/换 attempt/压缩/检查点损坏 -> verify: 重新核验绑定，不以历史回执省略必读，
  不恢复已失效授权；覆盖真实 runtime caller，包本身不能授予 lease 或恢复权限。
- [x] B4 消费入口与交付验证：Task Inputs 显示关系命中、章节/来源状态和恢复入口；
  更新方法、手册与设计中的“已实施/剩余”对照。
  CLI/Query/Web、Docker 浏览器、mock E2E -> verify: 同范围同版本，预算有界，空/拒绝/失效准确。

## 保持的边界

- 继续沿用 task contract、批准的 plan_input_selection、required-read 和结果准入。
- 不引入 FTS、任意可写全局记忆、第二发布/恢复状态机或真实 delta 交付。
- 图片区域的语义标注和 REQ/AC 对账由生产者/Critic 负责，不能由运行时猜测。
- 真实 Provider 压缩保留/8+8 成本资格需要单独隔离验证；本批完成不声称已验证。

## 验证与完成记录

四项限定范围已验收，实现提交后再记录 hash 并归档。新增实现均有实际调用方：
`source_manifest_from_payload -> pin_read_selections -> build_navigation`、
`ArtifactQueryService -> catalog catch-up transaction / navigation`、
`Orchestrator._inject_recovery_briefing -> build_recovery_briefing -> input recovery`、
`build_task_recovery_context -> input recovery`、`zf artifact index/recover` 和项目 Web API。
测试直接确认真实 recovery builder 生成的文件交给 transport；只 mock 外部发送和已满足的
contract sufficiency 前提，不把孤立库测试当作 runtime 接线证明。

验证记录：

- `PYTHONPATH=src .venv/bin/python scripts/dev-verify.py plan --base dev`
  加四个显式 `--tests tests/test_artifact_{navigation,input_lifecycle,publication_navigation,input_recovery}.py`
  -> 受影响闭包 **378 个测试文件**，无 unmapped 错误。base dev 包含上一批未合并提交，
  因此本批闭包同时覆盖第一批集成。
- planner 因超过 80 文件建议整个 deterministic suite；遵守 AGENTS 按影响闭包验证，
  使用 JSON `selected_tests` 的全部 378 文件，`-m 'not serial and not perf and not host and not real_provider'
  -n 8 --dist loadfile --no-cov` -> **5529 passed, 8 skipped，127.35 秒**。
  仅 deselect 下述已确认基线环境失败的完整 node；此结果不是全量 pytest 或真实 Provider E2E。
- `scripts/dev-verify.py run --base dev` 加同四个 `--tests`、`--skip-tier deterministic --keep-going`
  -> 其余自动层全部通过：serial、Web typecheck/unit、哨兵门 **53 passed**、flow mock E2E **10 passed**。
  deterministic 层由上一条完整影响闭包替代，不把 receipt 中的 not_run 记为自动通过。
- 最后收紧导航遍历/分页边界，并补 CLI/API 与真实 Orchestrator transport 断言后，
  重跑导航、生命周期、发布、恢复、输入索引、快照、QueryService、上下文恢复、manual、instruction
  的 10 个聚焦文件 -> **85 passed**。此前第一轮 13 文件为 105 passed，不重复相加。
- `cd web && npm run build` -> typecheck、Vite 构建及 bundle budget 通过。
- Docker `mcp/playwright:latest`，host network，Vite `0.0.0.0:8014`，仓库/依赖只读挂载，
  `playwright test tests/task-inputs.spec.ts --output=/tmp/test-results --reporter=line`
  -> **4 passed**。覆盖分页、项目隔离、授权失败、政策未知、关系展开、失效版本提示和恢复入口。
  这是路由 mock 的浏览器组件测试，不宣称真实 Provider/真实项目实施 E2E；临时 Vite 已停止，
  未创建 canonical runtime simulation state 或宿主浏览器。
- `git diff --check`、设计本地链接、四项 skill 三份副本一致性通过；manual generator/check 通过。

沿用的基线环境例外：
`tests/e2e/test_channel_group_product_mock_e2e.py::test_channel_prd_evaluation_task_workflow_run_and_receipt_continuation`
在第一批已于 pristine `869f7aa0` 复现宿主 Claude `authMethod=none`，返回 409。
证据见 [第一批归档](../done/2026-11-02-1253-context-198-task-input-loop.md)。本批仅排除此 node，
未变更登录或门禁；待 mock 宿主隔离修复或具备独立认证测试环境时重跑。

原设计仍未完成的部分和生效要求见 [198 §16](../../docs/design/198-context-index-write-read-resume.md#16-第二批实施关系发布与恢复2026-11-02)。
没有启用 Provider delta、FTS 或自动执行 rebind；输入恢复包无执行授权。
合并后需重启 runtime/Web 并重建前端，新的派发才获得带关系导航的 manifest。
本批不启动或恢复 FMS/ls 项目，不 merge、不 push；保留两批依赖链供后续合并。
