# 集成环境恢复与已完成 Worker 生命周期修复

> 状态: active

用户要求继续监工 Impl/Verify 并行；沿用本 session 已授权的故障介入、修复、断点恢复与记录。现场 Verify 已通过 evt-65454f990f2e，但集成 evt-5da8656905e1 因未配置 project.scripts.setup 而 environment_missing；已完成 Verify 又被 watchdog 误判卡住并将任务 requeue。保留原 38 项 Plan 与全部验收门禁。

## 实施与验收

- [x] 复现已结束的 Task Pipeline operation 被当作活跃 worker 义务 -> verify: 实际 watchdog 入口不再重启/重排终态角色；活跃新代际仍受监测。
- [x] 补齐精确集成 operation 的受控恢复入口 -> verify: 预览不写事件，apply 保留原失败事实并使用既有 supersede/下一 integration generation；已通过 Verify 不重跑，旧代际/活跃任务/终态 run 拒绝。
- [ ] 在项目 zf.yaml 声明 setup，核对 Run Contract 后加载 -> verify: 新候选工作区准备可重现，冒烟必须真实通过；不手改 runtime JSON 或跳过门禁。
- [ ] 聚焦闭包、sentinel、相关 mock E2E 与真实恢复 -> verify: Scaffold 集成并 done，后续依赖释放；观察独立任务就绪时 Impl/Verify 能重叠。未达到的真实并行不能标为已证明。

## 现场证据

- Verify passed: evt-65454f990f2e，target=21d8ffac72a45bbde1d1eb1b1079cae950a1afa5。
- 集成 operation: wop-integration-tp-FMS-SCAFFOLD-39102b23f7680a25-d16734836f11，rolling smoke exit 2：缺少固定 Node 工具链 setup。
- 误恢复：evt-8fe2347b0beb、evt-fa6360c0e320；evt-82722ad9a7ef 将已完成 Verify 的任务重排。
- 本地证据目录：/home/min/workspace/public/zaofu-evidence/integration-recovery-20261025/。

## 批量派发背压修复

补充复现：已有 1 个 Verify-ready 时，8 lane 批量派发错误地再安排 8 个 Impl，超过 max_unverified_tasks=8。改为在同一派发批次扣减剩余名额。保留 Verify 排空与返工通道；不改项目的容量配置。

- [x] 8 lane 批次遵守剩余名额 -> verify: 1 Verify + 7 fresh Impl；8 Verify 时 fresh Impl=0。修复前 1 failed / 1 passed，修复后通过。
- 聚焦回归：focused-impact.log 131 passed；后补拒绝分支及 mock E2E 31 passed；最终并行调度、CLI、watchdog、runtime entry、rework、mock E2E 闭包 72 passed。
- pre-merge sentinel 51 passed，手册生成校验通过；Ruff 与 diff check 通过。
- dev-verify 保守选择 112 个测试文件后扩为全量 deterministic；本批按 AGENTS.md 采用上述影响闭包，显式跳过全量/serial，不能把选择器 receipt 当作全量通过。
- 产品 zf.yaml 已显式添加 project.scripts.setup，Run Contract 实际检查 WARN / strict=false；尚待重载与现场集成恢复，不标记真实并行完成。
