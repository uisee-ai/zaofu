# PRD Plan 人工审阅与规格追溯改进

> 状态: done — `adae93f7 feat: add traceable human review layers to plan previews`

用户已于本轮明确批准生成 backlog 并实施。依据：loshu-fms 62 页/6 表单/54 场景已映射，但 Preview 工程历史占约 42%，121/129 执行步骤采用相同 TDD 模板。目标是在同版已绑定工件上提供可读审批视图，并改善后续 Planner 的规格表达。保留业务范围、唯一 Task 合同、审批与状态所有权。

## 批次

- [x] B1 Planner/Critic 方法：审批摘要、模块规则、原子验收、具体 Given/When/Then、测试先行与返修附录 -> verify: 技能三份一致、引用可读、真实注册技能 caller 的相关测试通过。
- [x] B2 Preview 分层：审批概览 / 模块规格 / 验收场景 / 完整原文；工程附件保留 -> verify: 浏览器可切换、原文完整可下载、移动端可读，旧 Plan 不伪造新规格。
- [x] B3 同版追溯：从现有摘要校验的附件 API 读取 Task Map/Execution Spec/Source Inventory，展示来源、AC、命令、依赖和待决事项；缺失/失败显式展示 -> verify: 单元测试覆盖旧/新/缺失/非法形状/同版引用；迟到响应不可污染新 Plan，无业务写请求。
- [x] B4 验证与收口：隔离真实 API + Docker Playwright，以及 loshu-fms 真实已接纳工件只读验证 -> verify: 62 页、6 表单和 35 任务保持可见，审批未触发，记录真实数据与模拟数据边界；提交实现和归档。

## 范围边界

不直接编辑已接纳工件，不批准 loshu-fms Plan，不启动产品 Impl，不自动合并或 push。新技能方法作用于后续生成；旧 Plan 的缺少细化必须可见，不能靠 UI 宣称语义覆盖或测试已通过。无需增加 Kernel 的业务语义 gate。

## 验证记录

- 自动影响域验证：`dev-verify.py plan/run --base dev --tests tests/test_operator_plan_preview.py --tests tests/test_plan_readiness_skills.py --tests tests/test_plan_artifact_ports.py`：67 passed / 1 skipped；Web typecheck 与全部 unit scripts 通过。显式测试选择覆盖两个浏览器 API fixture 的真实 caller，未弱化未知文件策略。
- 前端生产 build、bundle budget、token lint 通过。
- 真实 loshu-fms 新 UI/既有 API 只读验证：35 tasks / 129 AC / 62 pages / 6 forms；原 Plan sha256 `4283d6cd753151eec53ba0836abaa0fa103dd4ee7ce541f617e958926acfbca3` 保持，pending，零写请求、零 pageerror。
- Docker Playwright 最终回归：专用配置下 **13 passed (4.0m)**，覆盖真实 API/React、具体场景、附件错误、迟到响应、原文重建/下载及 320/390/1485px 导航。初次环境在 build 前启动，静态资源未挂载导致 404；重启后消除。共享主机旧用例默认 5 秒及 15 秒等待不足，含 12,000 行压力样本的专用 `web/playwright.plan-review.config.ts` 使用 assertion 60 秒 / case 120 秒，不改变业务断言或声明性能达标。
- 发现旧 `mobile contract health table` 测试仅用静态 HTML 检查已退役组件；当前生产代码/CSS 无消费者，`plan_health_routes.py` 已明确退役。使用 dev 原始 CSS 单独复现同样 579px > 391px，确认非本改动回归。移除该过时用例，移动端由真实 API/React 的新阅读器断言接替。
- 证据：`/home/min/workspace/public/zaofu-evidence/plan-human-review-20261025/`，包含真实数据收据、桌面/移动端截图及 baseline 复现 trace。

## 完成边界

本批只改善 ZaoFu 的生成方法与同版工件阅读；未重新调用真实 Provider 生成 loshu-fms Plan，未改写已接纳工件，未批准或启动 Impl。新细化格式通过合成业务的真实 API/UI 验证；旧项目通过真实工件只读验证。代码提交在独立 worktree，未合并 dev、未 push。

实现提交：`adae93f7 feat: add traceable human review layers to plan previews`。全部四项验收完成；临时 API/UI 已停止，真实 8001 服务与 loshu-fms runtime 保留。
