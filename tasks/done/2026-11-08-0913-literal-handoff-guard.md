> 状态: done — c0427dac fix: distinguish literal handoff bodies from write targets

# 修复字面量交接正文被误判为跨工作区写入

LS-v1 的 FMS-14-GAP-CONTINUE-02 在 2026-11-08 09:04:50 UTC 用单引号 heredoc 写自有交接文档，正文引用项目根下只读证据路径，workdir guard 把该路径当成写目标。worker 随后用原生编辑工具完成写入，当前业务执行已恢复，但误判仍存在。此项依据用户“遇到问题修复问题并记录”的授权实施。

范围：仅识别完整、单条、无展开的 `cat > literal-path <<'MARKER'` / 追加及双引号 delimiter 形式，提取真实目标走现有 runtime/workdir/allowed_paths 检查；复杂或不完整 shell 保留原保守规则。不是 shell 通用解析器，也不放宽任何路径边界。

- 重放合法文档写入 -> verify: Codex/Claude 真实 hook 入口允许，正文绝对证据路径及惰性命令示例不造成误拒。
- 实际越界写入 -> verify: 根目录、其他工作区、相对穿越、符号链接、错误 cwd、合同外路径仍拒绝。
- 保留 shell 执行边界 -> verify: 未引用 heredoc、提前结束、尾随命令、解释器及目标替换不进入字面量识别。
- 运行调用链回归 -> verify: hook 直接调用者、scratch/结果受控写入、事件/状态约束聚焦测试及 premerge sentinel 通过。

相关实现说明：docs/impl/literal-handoff-write-guard.md。

## 实测结果

- 原 dev `10d1e45b` 上新增测试先复现：8 failed / 7 passed，包括正文误拒及旧 shell 相对目标没有进入路径检查；修复后所有新增场景通过。
- `dev-verify.py run --base dev`：9 文件调用闭包 139 passed。
- runtime write guard、Codex hook routing、stale contract handoff mock E2E：15 passed。
- `scripts/dev-premerge-gate.sh`：53 passed。未运行全量 pytest 或真实 Provider E2E。
- LS 原事件：`evt-516606a1001b`（2026-11-08T09:04:50Z）。worker 在修复部署前已用原生工具自行恢复；不将该恢复归因于本次源码修复。

实现已由 17e2eb19 合入 dev；合并后 hook/保护/路由 34 passed，sentinel 53 passed。每次 hook CLI 进程加载 dev 源码，无需中断现有 watcher；真实 LS 下次触发该 shell 形状的观察仍继续。
