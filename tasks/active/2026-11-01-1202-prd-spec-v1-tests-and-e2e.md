# PRD Spec v1 回归与真实闭环验证

> 状态: active

依据：`docs/design/197-prd-spec-contract-and-readable-views.md` §9、§10；现有 Channel PRD deterministic tests、Web tests 和产品 E2E。

## 范围

覆盖 `prd-spec.v1` 的正向、负向、legacy compatibility、composition/file delivery、API preview 和 Web 阅读视图。验证闭环保持 `conversation -> PRD -> Owner Confirm -> Task planning -> Workflow` 的 ref/digest/revision 绑定。

## 验收

1. Runtime tests -> verify: validator、renderer、sidecar publication、composition/file delivery 的 focused tests 通过。
2. Prompt/contract tests -> verify: fake provider、repair request、legacy body 和标准 body 均按预期接纳或拒绝。
3. Web tests -> verify: API response、三个阅读视图、正文/附录隔离、下载和旧 fixture 通过。
4. Product E2E -> verify: 使用现有 deterministic/mock Channel Group E2E 重跑 PRD -> Task -> Workflow 入口，确认新字段不改变执行授权和绑定。
5. Verification closure -> verify: `python scripts/dev-verify.py plan --base dev` 对所有变更给出闭包，随后执行匹配的 `run`；必要时运行 `scripts/dev-premerge-gate.sh`。

## 边界

不把 mock provider 结果报告为真实 Provider 资格；真实 Playwright 只在 deterministic/API/UI 变化需要时按仓库 Docker 规则执行。

## 当前验证状态

实现提交：`945dd587 feat: add PRD spec contract and audience views`。本任务的 PRD/Web focused、串行锁、Web unit/build 和 sentinel 验证已通过。仓库自动 `dev-verify.py run --base dev` 仍需恢复验证环境：其 `/usr/bin/python3` 没有 pytest；使用 `uv run pytest` 运行等价 broad selector 时，基线 generic workflow 测试在 `safe_sidecar_ref` 拒绝绝对 sidecar ref 处失败，该失败在 `dev` 基线独立复现。待基线修复或验证环境补齐后关闭本任务。
