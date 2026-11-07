# 两个产品缺口、增量准入恢复与并行监工

> 状态: active

用户于2026-11-07授权修复两个卡点（包括视频）、恢复任务、Impl/Verify各8 lane并行及持续监工，修复与卡顿均需留痕。本项延续 `2026-11-05-1430-product-recovery.md`，不重开已完成工作。

## 起点与证据

- Run `workflow-0ea133e63454375e`，26/39有效子任务done、13未完成、无活跃operation。
- accepted candidate `65f285a843e79417fbd3c02ec3a95a9bcfa13887`；当前包 `ad1ccf42bea61157299c9c109e0b38e5a6ad122b342d1084b5fc6d65807bb638`。
- Privacy `evt-1dd20b5ba55d`：PNG/复核/授权代码已提交但未独立验收；缺视频与自动留存，scene引用可绕过生命周期锁。
- Dataset `evt-94680801c244`：缺受控对齐真值生产者，dataset本片尚未实现。
- 独立Discovery均复现并提出两节点图：`evt-edf3894fa2f8`、`evt-052d946440ca`。
- 准入失败：`evt-5d3a7ecf2bfa`（assembly owner冲突）、`evt-96bc459ca094`（rolling-smoke命令缺失）。Run Manager动作回执不等于恢复生效；后续无新派发而进入静默。
- 配置已是Impl8/Verify8、maxActive16，Postgres Compose健康；实际并行数由就绪依赖与写域限制。

## 执行与验收

1. 复用独立诊断，合并修订两个缺口及必要前置 -> verify: 保留26done/全部mandatory条款、当前package来源与candidate基线；load_writer_task_map、端口分片与独立Critic通过后委托approve。
2. 对齐真值前置→dataset；媒体适配与共享留存尽量并行→privacy终端集成 -> verify: 每片实际API/PG/worker/Docker浏览器原断言与WebM，独立Verify及candidate rolling smoke，禁止伪造批准/模型/外部资格。
3. 修复gap准入失败被当作已处理的恢复死端 -> verify: 因果失败有界返工、原失败原因送达、成功/待准入/跨run/重复/被后续处理的旧事件不复活，实际恢复入口回归。
4. 断点恢复与持续监工 -> verify: 真实task.dispatched和独立结果推进；每轮记录完成数、活跃operation、阻塞及后续措施，不能用健康心跳替代开发进展。
5. 有效并行 -> verify: 就绪任务不因不必要依赖/错误lane归属等待；保留真实依赖与独立验证，报告每波实际宽度，不为凑满8丢依赖。

## 2026-11-07T02:00Z 受控恢复

经 `ControlledActionService candidate-rework-apply` 发出一次有界修订，checkpoint `operator-two-gap-recovery-20261107`：replan `evt-1633545b2c36`、Planner trigger `evt-d89a3d5a5cc9`；目标激活 `evt-0b98fb6e3e17`。

Planner已真实派发 `evt-b0f3d36451ca`，operation `wop-prd-plan-prd-planner-trig-evt-d89a3d5a-re-e7aeda5b4dc7`。完整诊断上下文使用原子不可变sidecar，路径和结果收据见 `/tmp/zf-supervision-20261107/recovery-action.json`。本记录不将重新派发等同产品修复完成。

## 02:10Z 新卡顿

Planner operation仍running却触发idle上下文回收：`evt-7932592c37db`记录context=0.7206、idle=true；`evt-713edf329c02`/`evt-d51d0f05204e`替换会话，`evt-a60478908ff2`以idle_after_recycle跳过恢复。新pane重新读取相同briefing，原规划尚未落盘。已并行定位生命周期对reader活跃operation识别问题；不通过提高阈值或取消保护规避。

媒体环境预检：宿主PATH无ffmpeg/ffprobe；已缓存Playwright镜像的ffmpeg为专用VP8录屏构建，不能替代通用MP4编解码。验收新Plan时必须确认依赖安装/复现责任及真实视频输入输出测试。

## 02:36Z 修订完成与准入复核

已完成两项harness修复并加载：`e20431d6`（gap准入失败的有界恢复）、`ec0ac08c`（重复active不挪动reader恢复代次）。控制面于02:17Z保留worker重载，新watcher PID3526542；02:21Z活跃Planner被正确识别为idle=false。

Planner提交 `evt-8605c3d3196e`，42个有效任务、287条AC，旧274条全部保留；26done未重开。独立Critic operation `wop-prd-plan-synth-trig-evt-d89a3d5a-a230ca9cb24c` 正在评审，尚未批准。

固定提交图的loader复核发现：broad恢复诊断只选择13个旧失败替代/下游，遗漏3个明确声明的新增前置。显式选择批准gap与原下游并集共16项可通过原schema与pipeline准入。已授权机械选择器最小回归修复，必须保留显式子集与无关选择。首波真实可执行宽度为3（对齐真值、共享留存、媒体脱敏），容量仍为Impl8/Verify8；不以删除真实依赖凑满8。

Dashboard HTTP200、Compose PG healthy；它们不是产品验收证据。视频编解码及真实浏览器WebM仍由新媒体任务和独立Verify执行。固定复核见 `/tmp/zf-supervision-20261107/submitted-review.log` 与 `writer-selection-analysis.json`。
