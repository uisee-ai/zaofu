# PRD 产品阅读与覆盖审查验证记录

基线 `3746d313 fix: remove requested settings from agent timeline`；实现位于
独立分支 `wip/codex-20261031-prd-review`。范围与消费重载要求见
[实现说明](../impl/plan-execution-readiness.md)，ls 语义缺口见
[来源纠偏记录](2026-10-31-ls-plan-coverage-review.md)。

## 已完成验证

- `npm --prefix web run build`：TypeScript、Vite、bundle budget 通过。
- `npm --prefix web run test:unit`：模型单元检查通过；再单独执行
  `node scripts/run-unit-tests.mjs tests/planReviewModel.test.ts tests/planTraceModel.test.ts tests/planMarkdownPages.test.ts`
  核对最终读取模型、引用诊断、证据路径和章节解析。
- `PYTHONPATH=src .venv/bin/python -m pytest tests/test_plan_review_decisions.py tests/test_operator_plan_preview.py tests/test_plan_review_packet.py tests/test_plan_review_packet_lineage.py tests/test_workflow_workbench_cache.py tests/test_plan_counterexamples.py tests/test_plan_readiness_skills.py tests/test_web_plan_pending_refs.py tests/e2e/test_kanban_plan_approve_mock_e2e.py -q --no-cov`：94 passed。
- `bash scripts/dev-premerge-gate.sh`：51 passed，manual docs currentness 通过。
- 临时 8003 API + 本次构建 + Docker `mcp/playwright:latest`：
  `playwright test --config=playwright.plan-review.config.ts --output=/tmp/zf-prd-review-results`：
  25 passed。包含模块反复切换、刷新深链、正式/人工场景、页面与参考图状态、来源锚点、
  2,500 条来源分页与末条搜索、版本身份/坏工件、迟到响应隔离、审批动作、引用编辑、
  原文下载、长表格及 320/390/1440/1485px 布局。
  只读断言排除 Inbox 既有的已读回执，业务写请求仍不得由阅读视图发出。
- 真实 ls 已绑定 Plan 只读抽查：完整登记 16、待决定 14、显式阻塞 0、已授权待落实 2。
  没有改写其 Package 或派发状态。

浏览器首次启动遇到容器输出目录 UID 不同与自定义 UID 缺 Chromium 用户目录的问题，
改用镜像默认用户、只读挂载和容器内临时输出后正常运行；未安装宿主浏览器。
页面实测发现导航刷新丢失 fragment，修复 App 地址同步并以刷新回归覆盖。
浅色主题检查发现辅助文字误用背景 token，改用 muted-foreground。

## 扩展检查中的既有失败

执行 `python scripts/dev-verify.py plan --base dev`，再执行
`PYTHONPATH=src .venv/bin/python scripts/dev-verify.py run --base dev --keep-going --tests tests/test_plan_counterexamples.py --tests tests/test_plan_readiness_skills.py --tests tests/e2e/test_kanban_plan_approve_mock_e2e.py`。
选择直接调用方、共享投影及 API 边界后，Python 结果为 **858 passed / 1 skipped / 1 failed**；
Web typecheck 和完整 unit tier 通过。没有运行 full pytest 或真实 provider E2E。

失败为 `tests/test_feishu_callback_security.py::test_plan_approve_button_by_approver_emits_plan_approved`。
它未创建待审批计划，却期待 `plan-approve:plan-7` 成功；实际控制动作正确返回
`stale_decision | plan is not awaiting review`。
用 `git archive 3746d313` 的未修改源码和测试在独立 `/tmp/zf-prd-review-baseline-20261031/`
再次执行同一 node，得到相同失败。该测试不属于本批改动，未通过放宽审批 gate 消除它。
因此扩展自动检查整体仍为 failed，不能报告“全部回归绿”；本批直接影响验证已通过。

后续修复该基线测试应通过 EventWriter 创建真实待审请求并保留拒绝 stale decision 的
反例，不能允许无请求审批。部署本批代码仍需正常合并与 Web/API 重载，不因完成验证
自动发布；旧 ls Plan 的业务语义修订还须独立 Critic 和受控准入。
