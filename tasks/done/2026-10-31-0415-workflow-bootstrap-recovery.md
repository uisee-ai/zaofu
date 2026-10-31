> 状态: done；9be4a5cb fix: recover workflow preparation after missing Git baselines

# 工作流准备的 Git 基线与失败恢复

Owner 已批准执行四项修复，并授权修复后监督 `dl` 的任务准备、审批、执行与问题修复。真实复现为 `TASK-C9A6DC` revision 2：注册控制器装配因缺少 Git 基线失败三次；同 revision Prepare 返回 queued 但复用 failed operation；页面显示零阻塞。

## 范围与验收

1. 提前检查代码交付的 Git 前置条件 -> verify: 无仓库、无提交、detached HEAD 分别给出结构化原因，确定性缺项不空转重试；只读研究不被无关 Git 条件阻断。
2. 受控建立 Git 基线 -> verify: 预览绑定文件摘要、忽略路径及分支；只提交批准的文件，拒绝越界、secret、陈旧清单与已有暂存内容；重复执行不产生重复提交。
3. 准备失败恢复 -> verify: failed 不再冒充 queued；修复环境后以新尝试恢复同一 Request/Task，保留旧失败并复用仍有效的选路证据，不提前启动 Run。
4. Web/CLI 状态一致 -> verify: 准备失败显示原因和恢复动作，不显示 Ready/零阻塞或虚假的 Proposal prepared。
5. 非空目录集成回归 -> verify: 带 PRD 与数据的新目录经建单、阻塞、受控基线、恢复到真实 YAML；Docker Playwright 验证页面。

实现遵守现有 EventWriter、受控 action、Request/Operation 身份与 sidecar 合同；不直接改运行态，不新增产品 Task schema。

## 验证与交付

执行聚焦后端/API/UI 测试、相关 deterministic mock E2E、前端 build、dev-verify 和 premerge gate；归档时补充命令与提交。修复说明更新 `docs/impl/registered-controller-assembly.md` 及相关操作手册。

已完成 24 个直接调用/状态合同测试文件：262 passed；绑定逻辑提取后的相关回归：56 passed。
`npm run build`、Web 全部 unit/typecheck、skill quick_validate 通过；premerge gate 51 passed，
三流及 V4 runtime-noise mock E2E 10 passed。`dev-verify` 因 control_actions/server 共同入口
扩展到 366 文件并建议全量；本批使用上述聚焦闭包替代全量 deterministic/无关 serial tier，
其余自动 tier 已执行，收据 `/tmp/zf-workflow-bootstrap-verification-20261031.json`。

Docker `mcp/playwright:latest`、API 0.0.0.0:8002、独立 workspace/state 验证：Raw Git blocker、
详情阻塞、token-gated 基线 apply、旧失败保留、重试入口与无虚假 Ready，pageErrors=[]。
证据 `/tmp/zf-workflow-bootstrap-browser-20261031T0438Z/result.json` 与同目录截图；
simulation.done 已发出，临时 Web 已停止。

风险评估：中等（Git 副作用、Operation/Request 绑定及 Web 投影跨边界）；仅替换失败且同需求
绑定，显式文件清单与独立 Git index、摘要校验、真实 CLI/API/装配与浏览器回归覆盖。

## 监工交接（独立后续执行，不计入四项修复完成）

修复上线后恢复 `dl` 的既有 Task；使用 Owner 指定的 Astra/xhigh、Terra/xhigh、Sol/high，保留完整 v1.4 范围和独立 Verify/Judge。问题与处理证据持续记录，不能以减少验收或伪造通过解除阻塞。
