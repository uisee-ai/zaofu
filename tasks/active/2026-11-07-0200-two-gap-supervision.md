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

## 02:50Z 已批准并恢复三个并行 Impl

第三项harness修复 `f3d4b175` 已提交、精确同步运行源码并于02:40:44Z重载（watcher3656958）；349项自动闭包、sentinel50通过/1skip，主运行源码实际loader也选16。归档见 `tasks/done/2026-11-07-0237-broad-gap-selection.md`。

Critic提交 `evt-cee6ad122f5c` passed，无阻断fix_items；格式枚举提交错误已由Critic自行修正。保留非阻断旧说明措辞和真实外部资格待验限制，不等于产品通过。结果到ready约63秒、到approval request约72秒，时间线已闭合，不据此宣称结果丢失或已定位性能根因。

本轮包 `678b9a2f447d84c5a01b33c4f6aee11e8f90e02fa079f51c11a441c571df65ef`；真实ready `evt-f48391760da0` 经loader复核16项、3前置齐全，审批包/ref/count一致，证据 `/tmp/zf-supervision-20261107/approval-review.json`。已根据用户既有委托执行 `zf plan approve evt-f48391760da0`，批准事件 `evt-909dadf0ebc6`。旧10月28日待审批项不属于本轮，未批准。

真实派发：
- 02:49:38 `evt-4754d9910dda`，P09A-ALIGN-TRUTH-R1 → prd-dev-lane-0。
- 02:49:46 `evt-a389c37f63b3`，P16B-RETENTION-CONTRACT-R1 → prd-dev-lane-1。
- 02:49:54 `evt-2bc81ad0e3c2`，P16B-MEDIA-REDACTION-R1 → prd-dev-lane-2。

02:49:59规范状态：26done、3in_progress、14backlog（含父任务，即13待执行子任务）、15历史cancelled。有效总量42，当前无Verify worker；Impl/Verify容量8/8，图的首波真实宽度3。后续继续监工TDD、媒体编解码/真实WebM、独立Verify及候选集成，产品修复尚未完成。

## 03:12Z 留存白名单勘误与媒体下载卡顿

留存Impl暴露原Plan漏项：execution_spec要求 `/api/v1/archive/retention`，但该任务额外的 `scope_file_allowlist` 漏掉静态路由入口 `app/features/archive/api.py`。canonical scope虽已有archive/**，不能以此忽略更窄的明确语义约束。Planner/Critic及机械准入没有识别“路由要求与精确清单”矛盾，这是本轮新发现的规划缺陷；其余Align/Media写域独立复查未发现同类遗漏。

真实复现提交 `6434a9c444b156e687e2591c650f8348d33ad080`，PG/plugin健康，runtime1通过2失败（两入口404），健康smoke1通过，不计产品GREEN。已准入blocked `evt-b7a0618c324b`。

用户已委托监工修复与批准；等待operation/attempt settled后，通过 `zf kanban handoff` 同owner，仅追加规范contract.behavior的单文件勘误，明确补充archive/api.py用于既定API接线；原scope、AC、依赖、证据绑定和Plan不可变包均保留。这是明确语义清单修订，不伪称原Plan无缺陷。新authority `authority-r1959062ba07aa3b7734f4578`，handoff `evt-84f5e5b9e3ab`；理由/完整before-after/结果保存在 `/tmp/zf-supervision-20261107/retention-contract-*.json`、`retention-handoff-result.json`。

随后 `zf recover task-impl` 请求 `evt-9f0f6e785db6`，未扩大重试cap。03:12:26真实派发 `evt-96e425b58653`，新operation `wop-impl-tp-P16B-RETENTION-CONTRACT-R1-b5-b59069436b02`。已核实 `impl-g2-p2.md` 的Task instruction和rework_feedback都含同一勘误；后续Verify按当前Task构建快照，能获得相同授权。其余两路未中断；留存功能仍待实现和验收。

媒体另遇环境卡顿：锁定av16.0.1 wheel38.4MiB/Pillow12.0.0下载超过bootstrap300s；监工对官方同一wheel抽样，1MiB范围20s仅501180bytes（约25KB/s）。不能把bootstrap失败当功能RED。并行获取原版本、原hash的完整wheel，未降低哈希校验、未安装宿主浏览器、未改依赖版本。待环境修复后仍需重跑功能测试和真实Docker浏览器验收。

## 03:20Z 媒体环境恢复与对齐上下文回收

媒体下载已通过官方元数据+原锁摘要+ZIP CRC验证，清华镜像16路512KiB分段约70秒取得47.25MB。监工停止经PID/命令/cwd精确确认的重复网络installer3842392（未停止worker/provider），按bootstrap锁串行安装原锁的av/Pillow子集，再运行原bootstrap成功。完整锁直接--no-index缺少其余27包、普通find-links可能仍选原网络URL，故采用两步；原锁定版本和哈希不变。初次使用系统python3命中非3.11前置校验后改用项目指定3.11解释器；未绕过版本检查。

安装与复现资产已持久化到 `/home/min/uisee/zaofu-echo/datal/diagnostics/media-codec-wheelhouse-20261107/`（含wheel、原PyPI JSON、hash、下载分段、安装及干预记录和恢复命令），不是发布依赖来源。av16.0.1/Pillow12.0.0及内置libavcodec62.11.100导入通过。worker已重跑原bootstrap，并取得实际codec行为RED：5失败/1通过，失败为已有占位decode拒绝MP4/JPEG/PNG/APNG及逐帧区域处理；此前bootstrap错误不作为功能RED。随后继续实现，尚未功能GREEN或浏览器验收。

03:14:07对齐触发正常context hard-cap：ratio0.94持续180s、cap0.9，evt-0355e7198725；03:14:18 evt-46315bbb5803已恢复同任务、同operation和工作树，新pane646。已有实现提交和测试修改保留，无idle误判。完整输入读取较大导致回收的效率问题已记录；不通过提高阈值或跳过必读/门禁规避。


## 03:42Z 对齐转独立 Verify；必读输出去重已加载

对齐最终提交 `86a0b88064372d800a93de7d0e623e1b26050bd6`，Impl runtime39及smoke2通过；Kernel接纳 `evt-878c0028420c`。独立Verify真实派发 `evt-6537ab53fa4e`，operation `wop-verify-tp-P09A-ALIGN-TRUTH-R1-6a29118ce-042fc06b2826`；不以Impl自测代替Verify/Judge或task.done。

媒体codec6及扩展runtime17通过，发现并修复Pillow合并相同APNG帧造成帧号/时长丢失；首轮Docker浏览器失败后worker自行修正，重跑中，尚未认定浏览器通过。留存补齐实现后首轮runtime7通过，仍需跨owner证据及最终handoff。

上下文诊断确认Align241639/258400、Retention244012/258400为真实增长，无旧峰值误判；不能跳过fresh session必读。task_map/planning_result别名指向同文件同选区，机械重复可消除。已完成 `73af1209 fix: deduplicate required artifact output without skipping reads`，归档 `68d45784`。新显式flag仅同次输出复用正文，所有来源仍分别校验授权/磁盘摘要/选区并落完整收据；输出含行hash定位。88项focused闭包、sentinel50通过1skip，未跑全套；真实总输出Align减少15.23%、Retention15.21%，不能据此宣称context回收已消失。

三份运行源码逐文件核实base SHA匹配后原子同步，记录 `/tmp/zf-supervision-20261107/artifact-dedup-source-sync.json`。通过现有actuator保留worker/state重载，事件 `evt-5986fed68147`，watcher4000675于03:42:00Z启动。原3个operation仍running，Dashboard200、Compose PG healthy。现有Verify简报早于补丁仍为旧命令，未改不可变派发输入；新命令用于后续正常生成简报。CLI使用实际`.venv/bin/zf`验证新flag成功；此前`python -m zf`因无__main__失败，不计成功验证。

当前26done、2Impl+1独立Verify运行、13待执行子任务；capacity8/8不变，当前依赖图不能安全凑8个独立任务。根任务仍active，继续监督真实浏览器、跨owner留存、独立Verify及后续候选集成。


## 03:46Z 浏览器首轮证据与中断回收恢复

媒体浏览器真实报告 `app/evidence/privacy_media/browser/20261107T034042-aedf4e6a/`：Playwright1.62.0，1旅程passed/0unexpected，命令exit0；trace3971951B、WebM916345B、多张截图完整。覆盖真实媒体生成/播放推进/逐帧人工复核/版本修正与拒绝，fixture仍为synthetic且executor为implementation self-check，不能代替独立Verify或真实外部资格；worker继续补模型额外字段拒绝、修正后发布等探针。

留存03:41:49 `evt-e069692e24f2` hardcap强制回收（最后token244612/258400=94.66%），03:41:50 `evt-72e00a7a5648`进入recycling；旧provider当时处于nativecompact，但没有compacted/new token证据，因此不能称压缩后仍误判峰值。该替换与03:42控制面重载重叠，新pane649只剩bash、无provider子进程、启动命令未提交/未完成ready；新watcher03:42:37 `evt-ed1646b39a66`仅记录dead。_start_recycle无respawn锁，actuator直接终止watcher；存在中断会话替换窗口。

已通过 `zf restart prd-dev-lane-1` 现有角色级SessionLock入口恢复，未直接补键或写canonical状态。03:45:15 `evt-c3f08d742627`respawned、`evt-d77ab4e02b2c`restarted；新pane650启动provider并读取recovery简报。原operation仍为b59069436b02，代码/工作树/任务世代保留；其余Media/Verify不停。恢复简报复用旧dispatch内容仍为原必读命令，不能把新dedup flag已出现在所有旧简报当成事实。

已授权下一片有界watchdog修复：真正重建runtime、从事件hydrate recycling且provider dead且活动obligation存在时，走现有带锁fresh-session恢复而不等普通300s活性超时；保留同进程在途互斥、safe_halt、terminal、门禁与重试保护。不以仅等待respawn锁宣称actuator已覆盖未持锁的context recycle。


## 03:57Z 独立验证有效拦截与中断回收修复加载

Align独立Verify复现重复frameidx的真实缺陷：两图像/CAN重复帧号仍获201批准且worker发布，两个group ID相同并指向同一图像。正常对照通过，原39runtime/2smoke及历史归档摘要一致，但原用例漏掉唯一性。独立验证明确失败 `evt-7b940238ba02`，Kernel自动返工派发 `evt-55fe410f0266`，新Impl operation `wop-impl-tp-P09A-ALIGN-TRUTH-R1-6a29118ce-57e9b062f0e7`。无需监工直接改业务状态或代签通过；任务仍未done。

新恢复缺陷修复 `60c80cdd fix: recover interrupted context recycling after watcher restart`，归档 `04c00a7f`。重建runtime从事件恢复recycling、无本地marker且provider dead、仍有活动Task/fanout时，走已有带锁fresh-session恢复，不空等300s；保留原operation/attempt/lease/dispatch/workdir及现有门禁。审查中删除本地recycling的无条件continue，避免回收失败后永远不恢复，专门增加失败→stale恢复回归。104focused、sentinel50通过1skip、flow smoke10通过；不宣称全套或真实provider E2E。

main lifecycle已有4行idle_context_recycle调用与隔离base不同：仅应用本片hunk，原改动保留。同步记录 `interrupted-recycle-source-sync.json`；main新增回归+已有idle/capture/orphan组合39通过。重载前核对没有recycling/respawning，使用既有保留worker actuator，`evt-d5707a6f312d`；watcher4080706于03:56:49Z启动，三路原operation保留，Dashboard200。该检查不是已实现全局replacement barrier；不夸大优雅排空能力。

对齐新 `impl-g2-p2.md` 第55行已实际包含 `--required --deduplicate-content`，证明新CLI能力接入正常派发；旧不可变简报未改。媒体第二轮浏览器通过且继续补齐track/临时目录等边界，最新实现提交后准备最终合同命令；留存7项初测通过但仍在跨consumer接线。当前26done/3Impl运行/13待执行，容量8/8，依赖限制仍有效。
