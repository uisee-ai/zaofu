> 状态: active

# Spec 一致性、任务规格切片与真实规划验证

用户已批准从最新 dev 新建 worktree，执行上一轮建议 1–3。基线 489d92b3；旧 worktree、8001/8002 与历史已接纳工件保持不变。本任务不包含 Channel 模型传递或超时修复，不包含示例产品实现、merge 或 push。

- 补充 Spec 一致性与 Task 模板 -> verify: Planner/Critic 实际物化的方法包含来源、输入/状态/失败、共享合同、任务责任、决定、版本和独立验收；不用计数代替语义结论。
- 接入现有 Preview -> verify: 同版任务规格可定位、共享合同可读，未知/重复任务不串读，保留原文及 NOT_RUN；新旧工件均有浏览器回归。
- 新版小 PRD 生成及独立评审 -> verify: 隔离 v5 Workflow 正确绑定 source_ref，真实 Planner 和配置 Critic 产生可追溯工件；检查任务切片、具体场景和决定一致性；停人工审批，不伪造实现或产品测试成功。
- 交付 -> verify: 运行变化影响闭包、前端 build/unit、Docker Playwright；报告真实结果和限制，本地实现提交与归档提交，干净 worktree 交接。

## 验收证据

- `scripts/dev-verify.py run --base dev`：45 passed、1 skipped（外部技能目录缺失）；Web typecheck/unit passed。`npm run build` passed，ruff 与 diff whitespace 检查通过。
- Docker Playwright：18 个既有用例通过；最终受影响组 8 passed，覆盖任务身份、重复/未知标题、历史 fallback、共享合同、命令与 375px 阅读。
- 真实 v5：`workflow-38a3e3db1318725f`，3 Task Specs、21 场景、7 决定；Critic `evt-2af5583160df` 为 passed/approve；Plan `evt-1dcfc632f607` 进入人工审批，未批准产品实现。
- 真实 Web：1280/375 × 3 任务通过，具名场景/NOT_RUN 保留，7 项登记可读；下载原文 SHA 与接纳版本相同，浏览器无异常、无写请求、账本未变。
- 运行证据 753 个文件归档并验证；simulation.done、隔离 tmux/Web 停止及临时目录清理完成。8001/8002 和其他 worktree 未修改。
- 完整评测及后续建议：`reports/spec-task-slices/qualification.md`。共享场景局部绑定提示、正文重复和非标准决定状态统计仍列为非阻断改进，不以一次 approve 证明稳定质量。
