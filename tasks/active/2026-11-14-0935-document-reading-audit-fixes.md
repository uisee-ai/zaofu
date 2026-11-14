# Plan／PRD 真实阅读审核修复

> 状态: active

来源：用户批准下一步优化；[207](../../docs/design/207-review-cost-and-incremental-evidence-optimization.md)，本地证据 reports/spec207-visual-audit/evaluation.md。

- 按 AST 识别行内／块代码、保留 h6 -> verify: 长行内代码不断句，无语言代码块仍可复制，六级标题语义正确，Mermaid/高亮回归通过。
- 跨章节／分页共享引用定义 -> verify: Plan 与 PRD 引用链接可点击，源码和下载不变，超长保护保留。
- 自适应表格与滚动提示 -> verify: 1440／1024 普通表格适配阅读宽度，390 宽表格局部滚动、列不挤成逐字竖排。
- 释放正文空间、PRD 主入口直达全屏 -> verify: 桌面和移动真实截图、返回审阅／分屏、只读约束与深链回归。
- Plan 生成方法先写业务方案和验收、后附审计 -> verify: canonical skill 和两份分发一致；历史工件原样保留，不声称新 Provider 实测。
- 将前轮已实现的共享正文排版、PRD 深链／入口与专注阅读一并验证收口 -> verify: 生产构建、相关 Playwright 与真实只读页面复查。

## 验证结果

- Docker 生产 `npm run build` -> pass；typecheck、Vite、103 chunks 包体预算通过，最大 478973 / 500000 bytes。
- `dev-verify.py plan --base dev` -> 检查发现包含旧批次后端改动的 905 文件闭包；本次按 HEAD 到工作区的 Web／Skill／文档增量执行 `dev-verify.py run --base HEAD` -> pass：41 passed、1 skipped，typecheck 与全部 81 个 Web 单元测试文件通过。
- Docker Playwright 固定最终构建，8 个 spec 文件 -> 107 passed（3.8m）。覆盖源码／下载、引用上下文、代码／图表、宽表格、入口／深链／只读与责任状态；fixture 的 simulation.done 及清理成功。
- 真实归档页面 8003／8004，1440／1024／390 三尺寸 -> 无页面错误、HTTP 错误或写入请求；另行标注的人工样本验证 h6、行内代码、引用、表格对齐和代码高亮。
- 桌面真实首表：Plan 1064→760px、PRD 846→760px；390px Plan 专注正文 668→741px。PRD 顶部入口直达全屏，390px 正文 654px（原分屏 292px）。
- canonical Skill 与两份分发字节一致；业务优先的新生成顺序尚未运行新 Provider 验证，旧工件保持原样。
- 详细证据：本地 `reports/spec207-reading-fixes/evaluation.md`；测试中旧状态／DOM 换行断言的修正及中间超时均有记录，最终冻结源文件与构建后完整通过。

本批次不 merge／push。实现提交将在归档时记录。
