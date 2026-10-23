> 状态: active

# 184 · 真实链路故障与改进

来源：用户已批准执行真实验证第一至第三步；基线 `5dad3cb7`。

## 范围

覆盖版本变化、重复批准、取消与重启；根据实际失败最小修复，不降低验收。

## 验收

旧提案/重复批准 -> verify: 拒绝过期且无重复启动；取消/重启 -> verify: 不复活取消操作，未知效果不伪称成功；修改 -> verify: 失败证据、回归、原场景复测及本地提交。

实施与证据见 `docs/impl/184-live-validation.md`。未闭合真实验收不得标 done。

## 本轮进展

重复启动批准幂等通过；真实取消缺陷已修复并用 Codex 复测为实际停止进程，应用重建后未复活。
原始失败日志重放证明未知交付应在新 synthesis 入队前阻断；轻量 blocking 配置改为启动前拒绝。
这三项修复的独立提交验收见 `tasks/done/2026-10-23-1245-184-live-boundary-fixes.md`。

仍未解决原生 tmux 首次输入停留在草稿的问题；人工 Enter 后才能开始，且消耗了角色运行预算。
不得用延长预算或自动重复发送任务掩盖接收状态未知；需要接收确认与无重复执行的宿主复测。保持 active。

## 2026-10-23 后续复测

185 实施轮中，Issue triage 与 PRD scan/planner 首次输入均自动接收，没有人工 Enter；
旧终端问题本次未复现，未擅改 transport。Channel PRD 非目标污染已修正，Owner 确认、
完整建单、独立启动批准和重复批准幂等真实通过。Issue 仍在 operation token 预算耗尽时失败，
未达到产品交付验收。该任务保持 active，后续以新 [逐分支验证记录](../../docs/impl/185-routing-admission-validation.md)
和关联 185 backlog 继续追踪，旧失败记录保留。
