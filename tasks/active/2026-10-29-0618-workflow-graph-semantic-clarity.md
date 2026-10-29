> 状态: active

# Workflow 主图语义与视觉收敛

基于 8004 真实项目页面及已批准方案，保留 Graph 的流程判断价值，收敛内部阶段名、终态大卡与长目标文本；低频阶段只折叠展示，运行事实和决策条件仍可追溯。

## 验收标准

- [x] 标准 Workflow 阶段采用准确的用户可读名称，保留自定义或未知名称和原始节点 ID -> verify: PRD、Issue、Refactor 已知阶段、定制阶段、节点详情和旧深链均有回归，不把 Judge 混同 Verify。
- [x] 终态由紧凑门禁节点呈现，保留自身投影状态、关系及可打开详情 -> verify: pending、失败、成功和未知门禁不误报 Run 成功，原节点深链和折叠路径可用。
- [x] 长 Goal 默认只截取原文并支持展开/收起 -> verify: 内容完整可恢复、短目标不产生无效按钮；切换 Workflow 不泄漏展开状态，移动端可用。
- [x] 标准验证后扫描在等待时收进明确显示名称及状态的“其他阶段”，支持展开；运行中、失败、阻塞、未知等均直接显示 -> verify: 最新投影改变状态后及时展示，不能借折叠补造跨阶段关系；用户可选中、查看详情和以旧深链定位。
- [x] 不改变 Kernel、状态、Task/门禁和计划验收语义 -> verify: 阶段映射只使用可识别的标准 ID，未知类型按原始内容显示；现有历史、Trace、Raw/Review 与 Timeline 路径保持可用。
- [x] 完成 UI 验证 -> verify: 构建/类型检查、阶段模型测试、Docker Playwright 桌面与窄屏交互、真实项目只读快照与 dev-verify 计划覆盖通过。

## 范围

展示层变更；不删除底层节点、事件或工件，不硬连缺失阶段关系，不推断完成、费用或 ETA。

## 验证记录

- `.venv/bin/python scripts/dev-verify.py plan --base dev --tests tests/test_stage_loop_projection.py --tests tests/test_web_measure_loop_routes.py` 与对应 `run --workers 2 --keep-going` -> Python 312 passed、前端 typecheck 与 unit 通过；该计划包含本分支上此前的 Workflow/Loop 基础提交。
- `npm run build` -> 通过；Docker Playwright Workflow 结构、刷新、Proposal 浏览器夹具 22 passed / 2 skipped，跳过的 Raw/审批场景通过仓库隔离 `workflow-prepare-server.py` 补跑 2 passed。新增门禁四种状态、异常阶段自动显示、被折叠阶段旧链接、长目标展开与切换请求另有针对性浏览器验证。
- `bash scripts/dev-premerge-gate.sh` -> 50 passed / 1 skipped；隔离服务写入 `simulation.done` 后关闭并清理。
- 在 8004 的 `ty` 项目进行只读浏览器核对：默认显示 5 个业务阶段和 1 个紧凑门禁，等待中的验证后扫描保留入口；另以 datal 真实只读快照核对已运行及失败状态。未执行真实 Agent。
