> 状态: active

# 205 真实 Plan Preview 问题与文档阅读修复

用户已批准修复上一轮 Web 评估发现的问题，并以 Codex Desktop 风格的连续 Markdown 阅读体验为目标；不声称复刻其内部组件。依据：`docs/impl/205-prd-methods-and-design-handoff.md`、本地 `reports/prd205-web-preview/20261110T094339Z/README.md`。

- 兼容真实测试矩阵并保留任务/来源/AC/命令 -> verify: 同版 21 个场景可见，其他任务不串入，NOT_RUN 不变。
- 决定登记缺失不报零、正文内容不丢 -> verify: 待决入口展示已绑定原文 D-1～D-3；未知登记与已确认空登记分开，审批动作范围不改变。
- 摘要和设计支持原文章节回退 -> verify: 原文已有设计/状态直接可读，页面/表单标签不为空；只引用不改写原文。
- 文档默认连续阅读，优化标题、行宽、目录、搜索、高亮、代码与流程图 -> verify: 真实浏览器目录跳转、搜索定位、语法高亮、Mermaid 图/源码与错误回退，下载保持字节一致。
- 修正长表格、长 URL、窄屏与 Channel 长文裁切 -> verify: 桌面/375px 浏览器可读；容器内滚动且无正文裁切。
- 失败 Channel 回复显式标识 -> verify: 超时草稿起始处提示生成中断，不能被呈现为正式 PRD。
- 生成方法与阅读合同对齐 -> verify: canonical skill 与两份分发一致；回归真实工件，不修改历史状态补造成功。

交付包含定向测试、Docker Playwright、真实归档回放、修复前后报告、本地提交。无合并或 push 授权；保留现有 8002 报告服务。

## 验收记录

上述 7 项已完成。真实归档回放中，同版 Plan 的 21 个场景保持 NOT_RUN，D-1～D-3 原文可读、未确认机器登记不显示零；下载 SHA256 不变。Channel 中断草稿标识及正文容器实测通过。未修改历史状态、未重新运行 Provider。

- `npm run build` -> TypeScript / Vite / bundle gate 通过，最大 chunk 478943 < 500000。
- `npm run test:unit` -> 79 个文件通过；`npm run lint:tokens` -> 通过。
- Docker Playwright：`tests/plan-reading-regression.spec.ts tests/plan-human-review.spec.ts tests/plan-preview-content.spec.ts tests/plan-preview-navigation.spec.ts tests/channel-chat-layout.spec.ts --workers=2` -> 25 passed（47.0s）。
- 后端定向最初 64 passed；最终审核包补充修复重跑 `tests/test_operator_plan_preview.py tests/test_plan_review_packet.py tests/test_plan_review_decisions.py` -> 51 passed。
- Skill/指令检查 -> 27 passed、1 skipped（可选外部技能目录），三份技能内容相同。
- `bash scripts/dev-premerge-gate.sh` -> 53 passed；变更 Python ruff 与 `git diff --check` 通过。
- `dev-verify.py run --base dev --tests tests/test_operator_plan_preview.py --tests tests/test_plan_review_packet.py --tests tests/test_plan_review_decisions.py` -> 1187 passed、1 skipped、1 failed。失败为 `test_loop_view_cache_invalidates_on_config_and_signer_identity` 的 SimpleNamespace 缺少 observability；修改前干净基线 a223088c 同样复现。与本次阅读修改无关，未修改该测试，也未声称整个扩展闭包通过。前端层另行执行。

证据：gitignored `reports/prd205-web-preview/20261110T102303Z/README.md`、`browser-final.log`、`after-checks.json`、`event-integrity.json`、`baseline-failure.log`。8002 `/fixed/index.html` 提供前后对比。

剩余边界：新 Provider 产物遵循率及独立读者实验尚未验证；历史 Channel 正式 PRD 仍未生成；architecture-beta 等大体积 Langium 图型明确回退源码，flowchart / sequence / C4 等支持图形预览。本批次没有把这些未执行项标为成功。
