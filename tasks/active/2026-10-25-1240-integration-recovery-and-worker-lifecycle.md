# 集成环境恢复与已完成 Worker 生命周期修复

> 状态: active

用户要求继续监工 Impl/Verify 并行；沿用本 session 已授权的故障介入、修复、断点恢复与记录。现场 Verify 已通过 evt-65454f990f2e，但集成 evt-5da8656905e1 因未配置 project.scripts.setup 而 environment_missing；已完成 Verify 又被 watchdog 误判卡住并将任务 requeue。保留原 38 项 Plan 与全部验收门禁。

## 实施与验收

- [x] 复现已结束的 Task Pipeline operation 被当作活跃 worker 义务 -> verify: 实际 watchdog 入口不再重启/重排终态角色；活跃新代际仍受监测。
- [x] 补齐精确集成 operation 的受控恢复入口 -> verify: 预览不写事件，apply 保留原失败事实并使用既有 supersede/下一 integration generation；已通过 Verify 不重跑，旧代际/活跃任务/终态 run 拒绝。
- [x] 在项目 zf.yaml 声明 setup，核对 Run Contract 后加载 -> verify: 新候选工作区准备可重现，冒烟必须真实通过；不手改 runtime JSON 或跳过门禁。
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
- dev-verify 最终保守选择 122 个测试文件后扩为全量 deterministic；本批按 AGENTS.md 采用上述影响闭包，显式跳过全量/serial，不能把选择器 receipt 当作全量通过。
- 产品 zf.yaml 已显式添加 project.scripts.setup，Run Contract 实际检查 WARN / strict=false；已重载并真实集成成功（见下）；不标记真实并行完成。

## 真实恢复（2026-10-25 UTC）

实现：`ca500003 fix: recover candidate integration and enforce pipeline lifecycle limits`；已合 dev：`fa721b3d fix: restore verified candidate integration and bound parallel dispatch`。合并后 sentinel 再次 51 passed。未 push。

- 12:54:32 重载控制面，watcher 3154798 -> 3242027，保留现有 worker 进程；不重启 Dashboard。
- 12:55:38 受控恢复 evt-dbc4bfa1653f，将已失败 integration g2 supersede；已通过 Verify 保留。
- 12:56:28 integration g3 开始 evt-493780cb37e6。
- 12:57:56 evt-c98d69c56bad：setup exit=0、环境 ready、rolling smoke 通过，Candidate=fca394f6f8a62a36f47a6e711f3d917cd23bf63c。
- 12:57:58 evt-f7cdcc56c18b：Scaffold done；对应 Impl/Verify 会话归档。
- 13:00:30 FMS-ENVIRONMENT 实际进入 Impl/dev-lane-0，tmux %413 在对应 Task Workspace，已接收 briefing。当前只有它满足依赖；FMS-ASSEMBLY 仍有其他依赖。
- 现场快照 Impl=1、Verify=0。真实多任务 Impl/Verify 重叠尚未发生，不能将调度回归或一个任务的顺序闭环称为现场 8 lane 并行验收完成。

剩余验收：等待当前依赖链推进到多个独立任务 ready，再核对运行中 operation/attempt、不同 worker 与任务工作区的时间交叠；因此本记录保持 active。事件处理延迟独立候选尚未解决，不把本次恢复等同于性能修复。

证据：同目录 live-retry-preview.json、live-retry-apply.json、live-integration-receipt.json、live-recovery-proof.json、latest-live-monitor.json。具体测试日志保留首次失败及测试路径误写的 no-tests 输出，最终通过以 corrected/final 日志为准。

## 后续监工：Environment 宿主前置设施（2026-10-25 UTC）

- 13:14:15 `evt-a4e26e538646`：Environment Impl g1 提交 blocked。准确原因是已批准 execution_spec 要求宿主预置 `/var/lock/loshu-fms-acceptance.lock`，文件不存在；不是 Docker 不可用。worker 正确保持 allowed_paths 边界，没有修改宿主或伪造验收。
- 监工核对 execution_spec.environment_isolation.lock_storage 后，13:16:19 在宿主创建固定共享锁，uid/gid=1001、mode=0660、device/inode=29/6。未覆盖已有文件、未改 TaskContract/Plan、未操作共享 Compose 栈。
- 两个独立进程分别从 Candidate 和 Task Workspace 打开相同 inode；持锁期间均无法取得排它锁，释放后均可取得。这仅证明宿主前置设施，不代替产品的真实 Compose 生命周期、进程死亡接管等验收。
- `zf recover task-impl` 先 preview，再 apply；`evt-241086418e56` 受控请求恢复 g1。13:18:24 `evt-5bc42ad3e5cd` 实际派发 Impl g2，operation=`wop-impl-tp-FMS-ENVIRONMENT-653cb0f263cd5-dcdbe8286e3e`。
- worker 已收到 `impl-g2-p2.md`，其中包含宿主修复原因及证据。原 g1 阻塞记录保留；无需重启 watcher/Dashboard。
- 当前 Impl=1、Verify=0；真实并行仍待后续独立任务就绪。已批准规范规定同 host 共享验收栈持排它租约串行；这与编码/静态检查的多 lane 并行不同，等待共享栈锁不应判为调度故障。
- 锁位于 `/run/lock`（`/var/lock` 链接），为宿主临时设施；主机重启后需重新核对预置状态。本次没有添加系统级开机配置。

证据目录：`/home/min/workspace/public/zaofu-evidence/host-lock-recovery-20261025/`，含 `host-lock-proof.json`、`retry-preview.json`、`retry-apply.json`、`latest-live-monitor.json`。本轮没有改 harness 或产品源码；记录保持 active，Environment 尚未通过验收。只读冷启动 monitor 的 profile 不能用于宣称常驻 watcher 延迟根因已定位。
