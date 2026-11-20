# 持续推进、恢复效果与无人资格

2026-11-20 GitHub 集成的兼容边界与验证方法见
[本地恢复能力与 GitHub dev 的集成核查](github-dev-integration-20261120.md)。

## 2026-11-20 多跳接力与重新入队效果补齐

接续 b4f63e6e，任务 `2026-11-20-0808-unattended-lineage-and-effect.md`。
本批通过实际 runtime/受控动作入口先复现，再修改；没有启动真实 Provider、
8×8 或宿主服务。覆盖范围是以下三个可独立核验的缺口。

- **多次中断保留 Reader 历史入口。** A 发布记录，B 继承后未发布即退出，
  C 从 B 的不可变 request 所绑定 manifest 继续继承 A 的记录。每次只读取一份
  固定 handoff 与 work-record，不递归展开祖先链；新目标得到重新绑定的正文。
  新发布记录优先，损坏最新记录不会回退；manifest 摘要、记录摘要、Task/Run、
  角色、child 和冻结目标仍须匹配。历史事实不获得提交权或替代独立验收。
- **重新入队不再提前关闭案例。** `task-requeue` 的受控回执进入 verifying，
  仅接受回执之后同 Task/Run 的 kernel 准入通过结果或任务完成证据。
  新动作固定具体 `task.requeued` 回执 ID；有效结果早于 effect.pending 仍能重放
  收敛。独立 Task 只接受不属于其他 Run 的进展。错误 Task/Run、失败 verdict、
  缺 operation 的结果、worker 自报均不能完成效果。人工批准路径保留升级记录中的
  Run、case、代际和合同绑定，避免重新构造动作时变成无 Run 的独立 Task。
  批准消息夹带的其他 Task/checkpoint 标识也不能改写原升级记录的恢复对象。
  其他动作族仍需专用合同。
- **已知 Provider 不可用时暂停 RM 诊断派发。** mock 暴露账本已有额度耗尽，
  RM 仍向不可用的 resident 发送诊断。现在投影保留原待办和 external_wait，
  发送前再次读取账本；不会将该等待转为同一 Provider 的反复诊断调用。
  仅核对 kernel 事实的 backend/instance，以及记录中的 account/model 范围；
  不推断多个角色共用账号。额度等待没有猜测到期时间；匹配的健康事实仅解除
  Provider 限制，原有暂停、取消和动作授权仍有效，不代表任务完成。
  直接建议、人工批准及自动 tick 对发送前发现的外部等待均保留原请求；
  等待不记为执行失败，也不追加已消费/批准已执行的终态回执。

直接回归：`test_reader_checkpoint_handoff.py`、`test_task_requeue_effect.py`、
`test_provider_wait.py`、`test_shared_provider_wait.py`，以及已有 worker 恢复、
Reader 外部等待和 resident 诊断消费者测试。共同故障 mock 对四个实例注入
同一后端不可用事实，核验 RM tick 继续、RM 语义诊断等待、无 AR fallback、
不受信任的健康声明不能放行、暂停期间不派发、解除后只发送一次。
它不等于四个真实 Provider 进程或 OA/AR 全部消费路径的共因故障资格。
审批链追加回归先复现 3 项失败：批准丢失 Run/case、建议在等待时被消费、
人工批准在等待时被消费。修复后专项及 resident 调用方共 40 项通过。
追加批准身份防错后，最终相对 dev 的 1029 文件影响闭包得到
**12594 passed / 18 skipped**，sentinel **53 passed**，mock flow **10 passed**；
收据 `/tmp/zf-unattended-lineage-closeout-verification.json` 为 passed。
真实 Provider 层 not_run，结果不与先前轮次相加。

仍需另行推进：其他恢复动作族的完整效果合同、在途诊断的长期外部等待/重发
矩阵、OA/AR 全部入口的 Provider 降级、真实额度探测与授权恢复、真实宿主接管。
原综合任务继续 active；本节不扩大为全 Spec 或无人真实 8×8 通过声明。

## 2026-11-20 跨 operation 接力与恢复效果续批

本节接续 46ad74ee，任务为
`tasks/active/2026-11-20-0717-unattended-recovery-continuation.md`。
下列源码与确定性 mock 可验证项已实施；实际额度接口、宿主部署和新版真实
8×8 没有启用，不能以本节替代 UE-16 无人资格。

1. **Reader 前驱历史。** 在受控 fresh retry 冻结请求/manifest 前，验证相同
   Run、Task、fanout/child、角色和冻结目标，沿用 `task-handoff.v1` 正文及
   work-record 摘要引用，作为 `reader-handoff` 输入。它不占用 Task 接收回执。
   当前凭据通过 recover 返回的 predecessor 命令分页读取；旧 operation 凭据、
   错误范围或损坏记录不能使用。重放复用当前请求已经冻结的输入。
   继承完成条款、未决项和报告草稿，但不继承执行权、必读完成证明或通过结论。
   此批支持直接前驱；后续多跳补齐见上节。跨角色转交和任意祖先检索未开放。
2. **运行中额度等待。** 修正 insufficient_quota、usage_limit_reached 等错误
   被归为普通未提交终态重试的问题；它们现归 quota_exhausted 外部等待。
   共享 Reader 即使引用已完成的锚点 Task，也保存带 Run/operation/attempt/
   request 摘要的等待事实。Provider health 展示 exhausted、需要操作人处理，
   不会被后续 cooldown 投影覆盖。记录明确 probe_status=not_configured；
   冷却到期、普通成功文本及进程在线均不证明额度恢复。
3. **外部主循环 watchdog。** 新增唯一配置
   `runtime.host_watchdog_seconds`，默认 0 关闭；启用需显式设置至少 15 秒
   （主循环 tick 5 秒，静默轮询最长约 10 秒，部署还需按实际周期留余量）。
   `recover host-unit` 生成 Type=notify、NotifyAccess=main、WatchdogSec 和
   WatchdogSignal=SIGKILL；保留 KillMode=process、重启次数限制及 host-check。
   只有当前 PID/guard epoch 的主循环完成 kernel 与 tick services 后才通知；
   无独立 Python 心跳线程掩盖主循环或内部 watchdog 卡死。宿主单元负责终止旧
   主进程后重启，原暂停/停止/终态 eligibility 仍约束重启。
   协议依据 [systemd.service](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html)
   与 [sd_notify](https://www.freedesktop.org/software/systemd/man/latest/sd_notify.html)。
   本轮只测通知协议、生成内容和实际 start 调用，未安装/启动 unit，真实停滞接管
   和宿主共同故障仍未获证；也没有给 provider 长任务添加执行超时。
4. **恢复效果和累计停止。** 对绑定 Task+Run 的新 worker 恢复请求，respawn
   完成后继续等待该工作、同代际的 kernel 准入通过/完成证据；其他 Run、Task、
   代际和 abstained 报告不能关闭案例。失败回执立即结束效果等待。
   无进展匹配不再命中 Task ID 后提前返回，并补 child 范围；新 attempt/部署
   不清零已持久化的重试次数，别的 Run 或 sibling 的进展不解除停止条件。
   没有完整工作绑定的历史/空闲 worker 恢复仍沿旧的进程恢复合同，不冒称它们
   已证明原业务恢复。其他动作族仍需分别核验，不推广本批局部证明。

直接测试：`test_reader_checkpoint_handoff.py`、`test_provider_quota_wait.py`、
`test_host_watchdog.py`、`test_recovery_progress_scope.py`、
`test_worker_recovery_progress_effect.py`。初始 RED 已分别复现缺失接力、
损坏记录忽略、额度误分类、跨 Run/sibling 误匹配及仅重启便结案。
最终影响闭包及收尾结果见任务记录。

首轮影响闭包得到 12562 passed、18 skipped、3 failed：两项由新增接力检查
误拦没有工作检查点的纯重试准备引起，已恢复可选历史输入的边界；另一项飞书确认
测试在 dev 基线也复现 readiness_unassessed。仅补测试夹具的已评估状态，并增加
未评估时拒绝的对照，未修改确认准入规则、未调用真实飞书服务。

**仍未完成：** 账户/模型级有界主动额度探测与自动恢复，需先确认接口、费用预算、
凭据作用域和可自动恢复的错误类别；未配置时保持显式外部等待。外部 watchdog
还需选定部署节点/单元后的真实冻结验收。跨角色接力、超大剩余责任的语义重划、
自主补丁部署全链、统一就绪界面及新版真实无人验收仍按 Spec 留在待办。
这批不将上述范围归档成 done。

## 2026-11-20 Reader 语义接力与暂停边界续批

本批沿上一分支继续实现 UE-07/09。初始回归分别复现了共享 Reader 工作记录被
Task assignee 检查拒绝，以及外部额度等待解除后 taskless Reader 绕过暂停补发。
本批不运行新版真实 Provider 或 8×8，未部署服务或恢复历史 Run。
任务记录：`2026-11-20-0603-reader-semantic-continuity.md`。

- 沿用 `worker-work-record.v1` 和原 publish/show/recover 入口，不引入 Task
  状态。增加互斥 `--stdin`，Reader 不必创建新文件或改写预填结果草稿。
  原四字段保留，可选 report_draft 保存未完成报告；整个 JSON 最多 16 KiB、
  草稿最多 8192 字符。敏感内容、超限及同 request-id 改内容拒绝。
- 共享候选 Reader 使用当前 running operation、不可变 request/manifest 摘要、
  当前 fanout/child 和独立传输 TaskAttempt/有效 lease 鉴权，不占用或修改
  已完成的锚点 Task。Reader evidence_files 必须为空；正文只引用已有获准证据，
  不因 state dir 内存在文件而获得任意读取权。普通 Task 记录的工作区约束保持。
  本入口要求已绑定的传输 TaskAttempt；没有该绑定的 taskless call 不开放发布。
- 初始简报只给短入口；`artifact recover` 按需返回发布命令与机械限制，
  续跑提示和验证 skill 说明条款、证据、未决项、草稿及下一步如何核对。
  已发布记录的恢复 projection 只给摘要与固定 ref，正文摘要分页
  回读；凭据轮换/控制器重建可恢复同一 operation 的记录。损坏最新记录不
  静默回退旧记录；过期租约、旧 dispatch、其他角色、终态和换代拒绝。
  记录仍是未验证历史，不代替必读、正式提交或模型当前理解，不增加续跑预算。
  对尚未绑定 Plan package 的旧候选也复用 candidate currentness 校验，不能因
  缺 package 跳过冻结候选、合同或 target 的新旧检查。
- 发送原语的 Run 检查不再以 Task ID 是否存在为前提，并核对项目级暂停。
  发送前额度等待解除后，控制器从账本恢复原 operation；暂停必须等显式 resume，
  取消不恢复。没有把 readiness、重新派发或工作记录写入记作产品完成。
  暂停等待沿既有 deferral 去重窗口，避免每个周期追加相同 child 等待事件。

入口回归为 `tests/test_reader_work_record.py` 与
`tests/test_external_wait_continuity.py`；还覆盖已有 Task 工作记录、输入恢复、
Reader 续跑、结果纠错、Run 准入及调度调用闭包。冻结候选 mock 证明记录后正式
失败报告仍可准入，故障报告与产品通过保持区分；不能据此宣称产品真实验收成功。
首次影响闭包发现两项 Judge 简报超限（其余 7196 passed、10 skipped）；
已将长用法改为按需读取，保留原 12 KiB 硬上限，两项完整 mock 已重新通过。

### 尚需推进的精确范围

| 范围 | 本批后剩余内容与推进条件 |
| --- | --- |
| UE-07 | 同 operation 会话接力已补；新 operation/更换责任人的语义记录继承还需受控 lineage 和独立适用性核验，不能自动放宽跨角色读取。长任务语义不收敛的判断仍属 agent，记录数量不当作进展。 |
| UE-09 | 发送前 readiness 等待的自动接续与暂停边界已验证；运行中额度耗尽的账户/模型级主动探测、探测预算和恢复效果闭环仍未实现完成。Spec §9 尚未确定探测接口与效果范围，不自行触发付费探测、换账户或解除登录/信任等待。 |
| UE-06/08 | 超大剩余责任的语义重划、独立评审及全局条款对账仍需沿既有 planner/critic 方法推进；按需分页不等于职责拆分已完成。 |
| UE-03/10/12 | 现有宿主重启、诊断和源码修复模块仍需分别核验活锁、共同故障、兼容加载和无效补丁再现；本批未安装或重启宿主单元。 |
| UE-11/16 | 自主补丁的可修改/推广范围与独立验收需具体授权；新版真实 8×8 按本次用户要求不执行，零监工资格保持未通过/未验证。 |

上述部分不以本批归档冒称整个 Spec 完成。上一批对账表中的“本批”仍指
3d71bd97 的三项修复；本节记录后续增量，具体测试层级见本批任务。

## 2026-11-20 ZF-SPEC-004 已确认缺口补齐

本批从 dev d442a9bd 建独立 worktree，针对 r2 复盘中三个可复现缺陷实施；
不是重新合并 AR 实验，也不将本地 Spec 强制纳入 Git。实施任务记录：
`2026-11-20-0531-unattended-spec-gaps.md`。

### 变更与验证边界

1. Reader 纠错回执：既有 `continuation_delivery` 周期入口复用纠错身份验证，
   分别核对业务 operation/语义 attempt 与传输 TaskAttempt/lease。
   共享候选锚点不存在普通 Task 不再导致合法接收证据被丢弃。
   精确 native prompt 摘要或 stream 身份回执只关闭 unknown delivery；
   不重复发付费 turn、不更换 attempt、不生成业务 passed。
   纠错文件名绑定 operation/纠错摘要，防止不同纠错请求重用同路径导致原生
   prompt 摘要相同。接收观察持久化后，原请求补记 delivery receipt；暂停时仅
   观察，恢复后补账。过期租约、被替换身份、其他 actor/提示词及 worker 自报
   不能解除等待。
2. RM 精确恢复：resident 的 `action_payload` 经白名单展开及冲突校验，接入
   现有 ControlledActionService。精确 `child-rebuild` 必须绑定 Run、fanout、
   child、operation、语义 attempt 和当前失败事件；入口及异步执行前均复核。
   与普通 Task 恢复分开校验共享 Reader 身份，仍沿既有
   `repair.action.requested → rerun_fanout_child` 执行，不重做健康兄弟。
   同一失败的恢复意图有稳定幂等键，核心候选冻结、暂停、取消、发送准入保持。
   请求回执与恢复效果分开：新 attempt 的实际 Reader 终态才能关闭效果等待，
   旧 attempt、其他 child 或单纯重新派发不能冒领成功。
   更大范围的 task-requeue、stage-retrigger、stage-replan-new-generation、
   rescan-grant 接入已有 owner 审批路线，resident 简报说明此限制。
3. 监督告警：完成缺 gate 按 Task/Run 和已有代际身份匹配；证据必须先于完成，
   同任务重新派发/返工/合同更新后旧 gate 不再兜底。合法 Task Pipeline 完成
   支持 kernel 集成事件、精确引用及摘要绑定的集成回执。
   这是只读监督信号，未新增语义验收规则或改变核心结果准入。

直接回归入口为 `tests/test_reader_repair_receipts.py`、
`tests/test_run_manager_reader_rebuild.py`、
`tests/test_completion_gate_scope.py`。它们覆盖真实 runtime 调用和 mock
transport；不是真实 Provider、Docker 浏览器或整轮无人资格证据。
验证范围由 `scripts/dev-verify.py plan/run --base dev` 给出，另含已有
premerge sentinel 与 flow smoke。最终计数见归档任务。

### 与完整 Spec 的剩余边界对账

以下按功能列出下一阶段责任，不用三项局部修复推算整个 Spec 的完成百分比。
历史已经落地的实现保留；“仍待核验”不等于源码不存在。

| Spec 功能 | 本批结果及剩余责任 |
| --- | --- |
| UE-01 无人范围/就绪 | 未改变权限；实际消费者、动作、宿主及加载版本的统一就绪展示仍待联验 |
| UE-02 持久责任 | Reader 修复意图和效果可重放；消费者退出/漏唤醒各窗口的完整责任矩阵仍待故障验收 |
| UE-03 控制面外部恢复 | 既有宿主退出恢复未改；主循环停滞、watchdog 同时失效及目标部署资格未由本批证明 |
| UE-04 身份与未知效果 | 补纠错发送返回丢失的精确回执对账；跨部署累计停止条件及其他副作用类型仍需联验 |
| UE-05 恢复闭环 | 补 RM 精确 Reader 动作及效果核对；其他动作族须分别验证原工作接续和案例关闭 |
| UE-06 局部恢复 | 失败 Reader 定位补齐；超大剩余任务的语义重划、独立评审和条款保留仍待增强 |
| UE-07 语义进度 | 本批未增加已评估条款/证据/报告草稿/未决项的共享 Reader 工作检查点 |
| UE-08 输入证据 | 既有按需输入不变；最终全局审阅的职责分片和输入收敛还需真实成本证据 |
| UE-09 外部等待 | 未发送恢复已具备基础；账户/Provider 额度探测到自动续派及有效进展的全链仍待实现/验证 |
| UE-10 有界诊断 | 已识别 Reader 故障可达精确动作；诊断 Provider 共因故障和降级策略仍需故障资格 |
| UE-11 受控自修复 | 本批由获准开发会话修复；未知源码缺陷的自主诊断、补丁和独立验收未因此获证 |
| UE-12 部署接续 | 无实际部署；兼容升级、加载回执和无效补丁再次出现的闭环仍需专门验证 |
| UE-13 并发 | 未改变调度容量；历史 8×8 证据保留，不记为本版本新的无人通过 |
| UE-14 隔离/复用 | 新 worktree 与临时 mock 状态隔离；其他资源故障域和跨实验影响还需独立核验 |
| UE-15 真实观察 | 补跨任务 gate 告警误抑制；全旅程指标、时线和未知/过期展示尚需综合验收 |
| UE-16 独立资格 | 本批只做源码/mock；产品、容量、自恢复、自修复、零介入分别报告，Q01–Q23 未整体通过 |

后续优先补 Reader 语义检查点及外部等待的恢复闭环，再针对已确认部署执行控制器
退出/停滞、诊断退出和复合故障验收。源码自修复部署需按具体运行授权限定范围。
沿有效成果接续验收，不能把旧 K7 的人工维护历史改成新版本零介入证明。

## 2026-11-19 冻结候选的验收接续

K5 已完成16项 Task，但最终 Reader/Goal 未完成，且诊断收尾已取消 Run。
`run-resume` 只恢复 paused；取消记录不能通过改状态复活。`candidate-continue`
增加显式操作员令牌控制的后继入口：先以 source_run_id、candidate_event_id 和
dry_run=true 获取预览，再提交同一输入、reviewed_digest 和 approval_ref。
入口为 `POST /api/projects/{id}/actions/candidate-continue`；同摘要重复提交不创建第二个 Run。

预览及实际 admission 均核对终态、最新冻结候选、Git ref/提交、当前代际完整 Task
集合、done 状态、独立 Verify 准入、当前 Task 合同权威、不可变 Plan/端口、原批准、
Goal claims 与执行策略摘要。预览可生成不可变校验快照，但不创建任务或派发 Provider。
任何输入漂移都需要新预览。新 Run 沿原 admission/队列/暂停/终态门禁进入配置的
Candidate Readers；不发布 task_map.ready，不重新执行 scan、Plan 或 Impl。

新 Plan package 显式继承原端口并记录来源包，Goal claims 与产品验收规格（PAS）只
重绑定运行身份，原始条款、用户旅程和断言保持不变；新候选合同
绑定新 Run，子 Task 权威仍指向旧 Run。旧 Run、Task、验证事件不改写，不建立 Run
别名，也不导入旧 Candidate passed。宿主采证仅复用同一目标的逐命令最新观察
（包括失败），原始执行事件仍保留；Reader 独立判断适用性和缺失项。最终 Judge 的
事实及必读证据包含接续授权、历史批准和 Task 结果来源，当前验收仍走原准入门禁。

受控动作回执先于 invoke 持久化；接纳、Goal、包及候选各步幂等。既有 Orchestrator
空闲重放入口补齐已接纳但入口生命周期不完整的崩溃窗口，paused/terminal 不补发。
Goal/Plan 事件必须通过实际 v5 schema，写入拒绝立即停止后续 Candidate 发布；
历史上已发 Candidate 但缺失 Goal/Plan 准入的情况同样幂等补齐。Reader 的 canonical
handoff 仅经接续回执鉴权后读取原 TaskRef 来源，新冻结候选和包仍绑定新 Run。
Reader 派发入口同样先补齐接续生命周期；不能让启动时积压的 Candidate 先派发，
再等待空闲巡检修复，否则会浪费一次 Reader 重试。补齐失败时禁止启动 fanout。
带重试来源的 Candidate Reader 在输入 index/recover 中先验证其不可变候选合同、
operation/request、冻结目标和当前包；活动锚点没有 Task 合同不是重绑定理由。
普通 Task/Task recovery 的校验保留，篡改或已终止 Reader 仍不可读取为 current。
预授权宿主采证按同Run、fanout、冻结目标和合同摘要协调一个已开始采证的Reader。
它等待聚合并交回原恢复入口时，兄弟Reader不重复执行同组命令；不同目标、合同或
fanout不互相阻塞。这里只协调外部执行效果，不共享Reader结论或授予跨attempt读取。
接续首次发布候选前，对继承的容器回执复用既有媒体投影器，校验原证据摘要、目标、
镜像和文件路径后原子生成只读图片/视频及索引，并交给Reader。无需先失败一轮再
由恢复入口生成媒体，也不让只读Reader自行解码落盘；原证据与独立验收职责不变。
从未投递的Reader请求经过暂停可处于suspended；这不等同于已有消费者。空闲释放
仍核对精确Run/stage/attempt及完整派发历史。Provider退出遗留空shell时，额外确认
受管槽位身份、PID、Reader工作目录、进程树可见且无子进程，再沿既有role lifecycle
释放该槽位。活跃或曾投递操作、未知持有者、路径/身份冲突保持等待，不删除锁文件。
当前范围限定同项目、已取消/失败的原始 Task Pipeline Run；不支持接续的再次接续，
也不把历史8×8并发记作新源码从头无人资格。需要改产品候选时仍走正常修复规划。

任务：[冻结候选接续](../../tasks/active/2026-11-19-1008-candidate-continuation.md)。
验证记录位于 `reports/candidate-continuation-20261119/`；真实接续完成与否单独记录，
源码/mock 通过不能把未完成的 P2 资格归档。

## 2026-11-19 Reader 失败路由与 idle 预算

K5真实轮第二Candidate的输入读取失败被预授权宿主协调器错误选中，额外执行19条
命令。该协调器现在为Candidate读取已经准入、摘要绑定的结果；`reader_execution_failure`
留给既有诊断/Reader恢复流程，不以产品命令执行冒充输入修复。真正的runner失败
仍保留原宿主授权、冻结目标、合同与执行预算门禁。未改写历史失败或生成产品通过。

真实分页续跑已证明保持同一TaskAttempt，但隔离回归发现idle分支仍按全部dispatch
计算故障次数。现仅豁免具有kernel进展claim、前序dispatch和完全匹配的
role/Run/operation/request/语义attempt/transport dispatch身份的正常续读。
真正故障重试继续计数，未知、伪造、跨操作及未来claim不享受豁免；原idle边界、
暂停、租约、权限与最终结果门禁不变。该idle故障是mock复现，未冒称K5现场触发。

方法层补充恢复时逐命令对账：执行事实已覆盖原命令时，Reader继续必读和独立判断；
本地Docker权限拒绝不推翻既有授权执行事实，也不能把尚未阅读误写成缺少执行。
这一语义判断仍由agent/skill负责，runtime不根据exit0代填passed。

任务：[Reader恢复修复](../../tasks/done/2026-11-19-0917-reader-recovery-routing.md)。
K5的16项Task、8×8重叠89.420秒和原始浏览器通过保留，但最终Judge/Goal未完成，
诊断收尾取消1次，整体资格未通过。完整额度主动探测/自动恢复不在本补丁范围。

## 2026-11-19 OA 旧任务提示失效

同轮的 `TOOL-MAX` 已在 `evt-d90ef9d24cc1` 经独立验证和集成完成，但
`evt-5a8f4d563187` 仍被 Run Manager 列为 Run 级待人工决策。旧 OA semantic_failure
提示没有 Task 字段，原有 Task 故障失效逻辑无法识别其目标。

现在由既有 `task_failure_retired → Run Manager` 投影沿 kernel 决策准入、
checkpoint 和 Task 范围故障请求的因果链确认原 run/task/map。只有提示之后出现
匹配的 kernel `task.done`，authority 为 `task_integration_receipt` 且带集成回执绑定，
才使该旧提示失效。只改变派生投影；不改原账本，不伪造用户批准，不派发恢复命令。
没有准入、来源冲突、不同 Task/Run/map、worker 自报完成、普通审批或新故障都保留。
Agent submitted 事件本身不提供权限，必须由 exact kernel admitted 决策绑定；
诊断 correlation_id 可是诊断编号，其显式 workflow_run_id 才是 Run 身份。

任务：[OA故障提示失效](../../tasks/done/2026-11-19-0044-oa-task-escalation-retirement.md)。
当前冻结真实轮不热加载此修复；真实账本只读回放不等于新版无人全流程通过。

## 2026-11-19 工作区发布等待范围

额度恢复后的隔离32任务轮证明，worker名称复用不等于原任务工作区仍被占用。
`TOOL-MAX` 的 Impl turn 在00:02:35结束；`dev-lane-0`随后执行其他独立Task，
旧候选直到00:20:58才发布。隔离mock复现：8个待发布候选中，旧worker在另一个
工作区执行会使其中1项持续等待；该turn结束后才恢复。此前发布批量8没有消除这个条件。

发布协调器和受控宿主动作现在共享同一机械核验：只有真实kernel hook的
session/turn、causation指向的kernel派发、唯一当前running operation、run/task/role/
dispatch及cwd均对应另一个Task，两个工作区不重叠且均在state_dir内，原工作区
进程持有者检查确认空闲，才允许继续原授权动作。同任务、未知/伪造/不完整绑定、
同目录/嵌套目录或遗留进程均保持等待。没有扩大可写路径、Run预算或发布verdict，
同一任务的新operation仍由原binding gate拒绝。

本项见[发布工作区修复](../../tasks/done/2026-11-19-0033-publication-workspace-fence.md)。
冻结真实轮不热加载此补丁；mock/源码回归不能替代修复版真实8×8资格。

## 2026-11-18 真实资格复核纠正

32任务轮 `workflow-8cacd612404a641a` 已有32项 TaskVerify/集成完成及冻结候选的真实
Docker浏览器证据，但最终Judge/Goal未完成，实际8 Impl + 6 TaskVerify，8+8重叠为0。
批处理轮 `workflow-fd9e9212ab345b77` 停在第二次Plan Critic，尚未批准Plan。
两轮复核发现测试脚本指定的 `TMUX_TMPDIR` 目录均不存在，tmux回退默认server，
相同的product会话内混有两轮pane。此前“运行互相隔离”的判断撤回，重叠期间证据
不得用于无人资格通过；不删除原始账本。Provider同时报告usage_limit_reached，
外部额度不能由自动恢复绕过。两轮已正常停止并保留代码/证据，停止属于监工介入。

补充修复在 [隔离及外部门禁任务](../../tasks/done/2026-11-18-1545-isolation-and-external-gates.md)：
显式TMUX_TMPDIR必须是现存、可写且可搜索的目录，否则在执行任何tmux命令前失败，
不自动回退默认server。测试启动还必须实际确认socket路径、独占会话名与pane所属工作区；
环境变量本身不证明隔离成立。

Reader初始派发和重试将transport证明“输入未发送”的交互门禁记录为既有
fanout.child.dispatch_deferred。TaskAttempt仅在当前delivery claim仍为delivering时
原子释放到prepared，维持同一attempt/dispatch/operation及预算，并续接等待租约；
门禁解除后通过原入口投递。Task Pipeline的Impl/Verify在激活和投递边界同样记录等待，
不增加派发失败计数或触发冷却。已发送、被替代或结果不明的claim不会回滚。
只做机械等待，不自动确认额度、登录、信任或权限弹窗，不增加故障重试额度。
Mock、host隔离探针和真实provider资格分层记录；前两者通过不表示真实8×8已通过。

本批按用户批准的 P0→P1→P2 顺序收敛 [8×8 恢复](eight-by-eight-recovery-closure.md) 的真实缺口。
基线 dev `70295cc8`。来源为本地 ZF-SPEC-001/002、207–209；源码闭合与独立真实资格分别对账。
批准任务为 `tasks/active/2026-11-18-0933-*`；未取得完整验收证据的任务保持 active。

## 实现与边界

| 项目 | 行为与实际入口 | 验证/限制 |
|---|---|---|
| P0-01 | Reader 恢复扫查按同一 operation/request/冻结 attempt 的新增必读覆盖续跑；沿既有 continuation 事件记录 claim/投递 | 重复页不延长预算；failed turn 仍属故障恢复。暂停、审批、待完成工具/协助、未知投递不重放；单个等待不阻断其他 Reader 的恢复扫描，结果仍走准入 |
| P0-02 | 既有 CallResult correction + submit 保持原读取与结果身份 | 真实 Orchestrator 调用链测试证明无效 verdict 修正后合法结算，无须重读/重跑业务；本项 verified-existing |
| P0-03 | 已批准端口可声明 task_input_policy.candidate_paths；Candidate 必读 shared + 完整验收区，旧包仍全文 | 路径、digest、版本机械校验；语义完整由 Planner/Critic 从来源反向对账。contract/target/完整矩阵保留 |
| P0-03 | 新 Plan rework manifest 对相同大对象/数组只读一次；第二来源所有独有字段仍必读 | read_equivalents 在 access_scope 中绑定第一份 source/digest/path，原文不变。部分授权不裁剪，生产者不能自行声明内核分区 |
| P0-04 | 预授权采证返回 Reader 前对账整个冻结命令合同，缺证明确等待而不重复重开 | 不扩大白名单，不伪造产品 verdict；失败命令仍可提前交给独立评审。新 fixture 预授权全部19条命令，保留100次执行上限 |
| P1-01/02 | 正式失败 fanout occurrence 也结算实际 retry transport attempt，原故障归 workflow 处置 | 修复租约影子失败重复触发 AR。旧账本副本回放：4项遗留→0，重复回放0改动，不改真实旧账本 |
| P1-02 | 保留 RecoveryCase 单 owner、原故障 effect、诊断→提案→独立验证→授权应用/加载/恢复链 | 已有机制专项验证，不将 proposal_only 提升为自动应用；没有证据的恢复保持等待 |
| P1-03 | worker.state.changed 在实际 housekeeping 入口同步驻留状态与 RoleSessionRegistry | 只消费 kernel 来源，沿既有 task-generation fence，保护新 writer；跨进程 reset 清理驻留 circuit/counter，无须重建投影 |

代码没有新增任务状态机或 Task schema，不根据 token 数、运行时长、消息活跃或模型自述宣布完成。
Planner/Run 的时间与 token 限制仍为0。诊断、宿主探针各自已有边界保留；未通过放宽权限来修复失败。

## 验证分层

证据目录：`reports/unattended-progress-20261118/`（运行证据不随源码提交）。

- `p0-01-red.txt`、`p0-boundary-red.txt`、`p1-settlement-red.txt`：先复现进度误耗故障预算、等待保护、部分采证提前重开、失败重试结算遗漏。
- `p0-01-callers.txt`：185 passed；`p0-01-closure.txt`：261 passed；各集合重叠，不合计为唯一测试数。
- `p0-input-overlap.txt`：44 passed（后续补充防伪声明反例进入最终闭包）。
- `p1-settlement-green.txt`：127 passed；`p1-lifecycle-diagnosis.txt`：114 passed，包含8槽位与动态接续 mock E2E。
- `p1-replay-baseline.json` / `p1-replay-fixed.json`：真实旧账本的隔离副本复现与修复对照。
- `closure.txt`：4388 passed / 8 skipped / 12 failed；12项旧断言期待“无效补充证据仍重开Reader”，已改为验证等待及缺失命令，不删除安全反例。
- `closure-final.txt`：4402 passed / 8 skipped，sentinel 53 passed、flow-smoke 10 passed；此后跨进程生命周期修复另跑闭包。
- `closure-lifecycle-final.txt`：4920 passed / 8 skipped / 4 failed；4个历史正例未标 kernel 来源，已与真实调用方对齐并保留 worker/external/无来源负例。CLI unblock 同步标明 kernel 来源，重放按 instance_id 而不是 operator actor 归属。
- `closure-release.txt`：最终影响闭包 6074 passed / 8 skipped；合并哨兵 53 passed、流程冒烟 10 passed。不是全库或真实工作流验收。
- `reader-fairness-release.txt`：补充等待隔离修复的调用方/并发闭包 441 passed；`fairness-sentinel.txt` 53 passed。增加8槽位与恢复 mock 覆盖，所有等待防重放断言保留。
- `p2-terra-preflight.txt`：1 passed，157.97s；真实 GPT-6.1 Terra High 两个新会话、受控输入、未提交工作保留与继续实现。它不等于8×8工作流通过。
- `browser-preflight.txt`：现有 mcp/playwright 镜像内 Chromium 149.0.7827.22 正常启动；这只是环境预检，不是产品验收。

## 真实资格

旧实验 `/tmp/zf-eight-recovery-20261118T002529Z` 已正常停止，保存账本摘要和退出回执；原始状态/源码/介入证据保留。
准备轮 `/tmp/zf-unattended-eight-20261118T100101Z` 在 Plan 批准前停止：补充并发测试复现了“等待一个 Reader 提前结束整个恢复扫描”，故不能作为最终资格轮。
修复将无变更等待与已执行恢复区分；`reader-fairness-baseline-confirmed.txt` 用已提交代码和当前代码对照，证明前者阻挡独立 Reader，后者恢复且保留未知效果等待。最初测试遗漏 backend 和固定扫描顺序，修正后重新复现，未把测试装配失败计作产品缺陷证据。
新实验 `/tmp/zf-unattended-eight-20261118T102607Z` 使用冻结源码 `33a343ed`（已合入 dev `ebaa20f5`），已启动16个独立 TypeScript 合同和8+8池。
Plan/Critic 使用 Astra High，Impl/Verify 使用 GPT-6.1 Terra High；Plan `evt-adacb064b307` 经独立 Critic 通过、同版本预览/条款/32个互斥写入路径审阅后，由受控动作 `evt-c59b97660bc9` 于 2026-11-18 11:07:24 UTC 批准。
实际 Task 输入已验证只必读共享约束和自己的任务区；本轮 Plan 未声明可选 `candidate_paths`，因此不能把本轮算作该可选路径的真实验证。该路径目前只有确定性合同验证。
Plan 必须审阅批准；批准后冻结 config/合同/源码/授权，观察者只读。
任何追加提示、补采证、状态修复或加载补丁都记为本轮介入，不能回填无人资格。
分别报告业务 Goal、真实 Impl/TaskVerify 峰值与重叠、故障自动恢复和介入次数；配置8不等于达到8。
最终仍须真实 Candidate/Judge/交付证据及清理后才能归档 P2。

### 当前轮的并发证据限制

本轮只有16个独立任务。第1项 `TASK-unique_words` 于 `evt-e1b219f7fe27` 完成时，Verify峰值仍为2，未出现8+8重叠；其余15项不能产生16个独立任务阶段同时运行。
因此这轮即使业务 Goal 和交付成功，也只能证明所观察到的无人推进链，不能满足完整8+8验收。保持冻结合同，不追加伪任务、延迟完成或用两个Run的槽位合并充数。并发资格须另用供给充足的有用工作负载验证，P2在取得该证据前保持 active。

### P2 暴露的宿主证据接力遗漏

`TASK-positive_count` 首次仅提交测试后阻塞；预授权 Docker 已在 `evt-e3d3f7d67945` 产出明确 TODO/RED，下一 Impl 却再次报告缺少明细。既有接力索引在 operation 结算处停止，只覆盖 Provider 工具输出；宿主探针必须等 operation 结算后才执行，所以其输出总在截断之后。

新增 `task_host_evidence.capture_host_evidence` 由实际 `attach_task_handoff → capture_execution_evidence` 调用，把冻结交接前的同 run/task/operation/attempt/lease/dispatch/request/generation 宿主检查加入既有索引与受控分页读取。核对 kernel 起止事件、唯一完成、receipt/manifest/output 摘要与来源；保留命令、目标提交、退出码和失败原文，不产生业务通过或新执行权限。原生工具仍以结算为界，旧 handoff 重放保持冻结；新事实只进入新交接。

验证：`reports/host-evidence-handoff-20261118/`。
- 先验回归 2 failed / 10 passed，准确复现结算后输出遗漏；修复首轮18 passed。
- 调用方、宿主动作、发布与交接验证95 passed / 2 deselected；一次误填不存在测试路径的命令未执行测试，已修正，不算通过。
- 实际账本隔离回放暴露测试夹具把 operation_id 放入 manifest.identity、正式格式在 manifest 顶层的差异，已对齐；最终回放可读同一目标提交上的 TODO/RED 和 exit 1，原运行状态未改动。
- 最终 dev-verify 影响闭包176 passed（包括鉴权分页、实际dispatch、mock E2E），sentinel 53 passed。

此补丁尚未加载进冻结真实轮；确定性通过和隔离回放不等于新源码的完整无人真实资格。

### P2 暴露的并行诊断作用域误判

`TASK-reverse` 的驻留诊断 `evt-842c261510a9` 已匹配当前失败 operation/attempt，却因其他 Task 推进改变全 Run `progress_digest` 被挡下，消费记录停在 `consumed / controlled_action_status=blocked`。原账本只读回放确认，拒绝时唯一变化是该全局摘要。

诊断转 OA 仍走既有 Run Manager 受控入口，但前置条件由内核绑定到原 Task 的完整 operation/attempt/generation/准入结果以及同一 Goal objective。其他 Task 的正常推进不撤销诊断；原 Task 换代、结果变化、目标变化、Run 终结仍拒绝。暂停、人工决策、权限和预算门禁保留，其他 Run 级动作继续使用原前置条件。新范围不能授权业务结论或直接恢复执行。

验证记录在 `reports/task-scoped-diagnosis-20261118/`：RED 为4 failed / 9 passed，专项及直接调用方61 passed。扩大影响闭包5306 passed / 7 skipped / 3 failed；3项均为测试子进程 PATH 缺少 python，补齐已有虚拟环境 PATH 后定向重跑3 passed，无需修改生产代码或基线。sentinel 53 passed、流程 mock 10 passed。实际旧事件前缀只读回放由拒绝变为允许当前故障请求，原运行未改动。

真实轮另出现行为 RED 明细缺失导致独立 Verify 拒绝、OA 请求人工介入。该拒绝不作为需要绕过的门禁；下一冻结轮必须先证明测试入口能在实际沙箱输出失败断言和退出码。本轮实际 Provider 观测 Impl峰值8、TaskVerify峰值6、同时8+8时长0；不能以修复已提交替代真实无人资格。

本轮于2026-11-18 11:57 UTC以 `failed_qualification` 正常停止，停止前完成8/16项；父控制器、独立Run Manager及两个AR子实验均停止，私有tmux无残留。账本/任务候选全部保留，未覆盖判定或人工补采证。结束实验是操作员操作，与批准后的恢复介入0分开记录。下一轮前先对实际Provider测试入口做RED/GREEN预检。

### 发布队列的有限批量

32任务资格轮进一步观察到：已发布/待发布候选排队时，Impl槽位仍可能空闲；
`Run Manager → reconcile_task_publication` 每tick原来只执行一次commit或retry。
8个真实Git工作树的独立回归复现了一轮只能推进1项，不能用已配置8个worker推断发布吞吐。

`zf.yaml` 可显式设置 `runtime.task_publication.max_actions_per_tick: 8`，合法范围1–100，
默认1保留原节拍。每轮只遍历一次候选，每个operation至多执行一次原有发布或续跑动作；
Git效果仍逐个执行，继续使用既有受控入口、精确路径与Run总发布预算。
每项重新读取事件核对暂停、总预算、前置状态及回执，未知效果/活跃worker不被重放。
此项不调整宿主容器命令执行节拍，也不改变CAS集成或产生业务通过。

默认1的授权摘要省略新增字段，保持旧配置回执在代码升级后有效；显式批量纳入授权摘要。
批量应在新Run冻结前配置；带未结算旧授权效果的Run不能热改该字段并沿用旧回执。
当前冻结的32任务实验不加载这份补丁，批处理单测通过也不等于真实8×8资格通过。

验证与任务：[候选发布批处理](../../tasks/done/2026-11-18-1402-publication-batch-throughput.md)，
记录目录 `reports/publication-throughput-20261118/`。

批处理修复验证：8候选/真实Run Manager的RED复现4 failed、6 passed；专项60 passed。扩大闭包发现两项基线旧断言，已在未改动dev树复现并按现行Busy调用合同与既有token配置对齐，模块及caller70 passed。最终影响闭包8035 passed、10 skipped，哨兵53 passed、流程mock10 passed；尚不能替代新版本真实8×8。

隔离/门禁补充批次验证：专项60 passed；影响闭包4845 passed、7 skipped，串行4 passed，哨兵53 passed，流程mock10 passed；独立8×8 mock8 passed、真实tmux隔离1 passed。真实模型因额度门禁未重启，本批源代码修复通过不改变P2未验收状态。

最终合并候选验证（含Task Pipeline激活/投递补充）：专项64 passed；影响闭包5020 passed、8 skipped；串行4 passed；哨兵53 passed；流程mock10 passed；8×8 mock8 passed；host双socket隔离1 passed。证据：reports/external-gates-20261118/validation-summary.json；真实provider未重开，P2保持active。

## 恢复 Reader 的输入消费（2026-11-19）

宿主验证产生当前候选绑定的回执后，Reader 仍须独立判断；不能因为其沙箱无 Docker socket 就忽略宿主执行证据。新 canonical required-read 声明将 `critical-reader-repair-context` 排在产品大输入之前；既有已冻结声明及分页游标保持不变。

若当前 attempt 有该必读恢复输入，`verifier_execution_failure` 的只读预检和正式准入都会检查该输入的完整读取凭证。未读或部分读返回可纠正的 `recovery_input_unread`，不消费预检凭据，也不把命令成功判为产品通过。读取后仍可依据实际证据报告 runner 失败；真实 `reader_execution_failure` 仍可在产品输入未读完时报告。错误 attempt、摘要和损坏 ledger 不予接受。此检查仅证明输入生产的完整性，不证明模型理解。

运行时只维护输入/凭证边界，回执的适用性与产品判断方法在 `skills/zf-harness-verification-checklist/SKILL.md`。回归入口：`tests/test_verifier_environment_failure_reads.py`、`tests/test_reader_replan_feedback.py`。本修复不包含额度主动探测或 Docker 权限扩展。

### Critic 派发前的 Plan port 路径闭包

生产者本地文件可在正常 fanout 归档后成为 sidecar；原始输入的绝对路径不能直接当作
`plan_ports` 的已归档引用。`plan_synth_runtime` 在完成文件归档后，要求机械预检按
Package 的同一 sidecar 路径边界校验显式端口。绝对路径、非 sidecar 根、父目录越界或
逃逸符号链接返回 `plan_port_not_materialized`，包含具体端口与字段，并走既有协议修复
入口，不启动完整 Critic。inline body、已归档相对引用与正常生产者文件归档保留。

此检查不复制任意输入，不改摘要、不扩展安全根，也不代替内容/大小/摘要和独立语义审查。
生产者提交前使用的预检仍允许其本地文件，路径闭包在 Kernel 完成归档的实际调用点检查；
不能把生产者预检通过等同于最终 Package 已准入。回归见
`tests/test_prepared_plan_port_paths.py`（实际 Critic 派发入口及越界拒绝）。

### TaskVerify 的预授权宿主证据自动接续

混合 8×8 真实轮 `workflow-5d021bd50a5ec52b` 的只读审计任务因沙箱无法写入
构建目录而报告 `verifier_execution_failure`。其冻结合同将审计验收责任标为
`candidate_verify`，但宿主命令选择硬编码为 `task_verify`，导致已授权命令被误拒。
Task Pipeline 的 Verify 命令选择现在沿用冻结 snapshot 的验证责任；普通任务的
未来 Candidate 命令仍不可提前执行，调用者不能通过请求字段扩大授权。

真实 Run Manager tick 仍以原预授权 Docker 路径执行隔离快照。随后核对全部当前阶段
必需命令的完整回执、kernel 起止事件、唯一完成、策略/合同/源码/依赖摘要、输出摘要
和 operation/attempt/dispatch/lease 身份，才沿既有 Verify retry 入口请求下一代
独立验证。只重开执行失败且没有产品结论的 Verify，保留已通过的 Impl 和 TaskRef。
非零退出码也作为事实交给独立 Verify；容器成功本身不产生 task.done 或产品通过。

暂停、活动调用、目标/合同变化、其他 operation 已接管、缺少或损坏的回执均保留等待。
同一 operation 的回执不重复消费，宿主执行沿用原 Run 总预算；跨代反复失败并不能
仅凭此次自动接续宣称已自愈。接续后继续使用已有 handoff evidence 分页，不新增任务
状态或证据协议。既有 retry 原因前缀保留兼容；判断是否人工介入须看 kernel 事件的
actor/来源及完整原因，不能仅以历史 `operator_verify_execution_retry` 前缀计数。

回归入口 `tests/test_task_host_verification_handoff.py` 包含真实 Run Manager 调用、
隔离容器 mock、Verify 下一代调度、输出接力和拒绝反例。复现和验证记录位于
`reports/task-audit-host-recovery-20261119/`。源码通过不等于真实工作流通过；
在批准后的 Run 加载补丁必须记为人工维护，不能回填零介入资格。

### 无人资格的 TaskAttempt 策略

混合真实轮的 TOOL-LINES / TOOL-INITIALS 在没有有效结果的情况下租约到期，
按默认 `workflow.task_attempt.mode: shadow` 记为 `lease_expired_shadow_only`，
没有保留供同 operation 自动重派的 expired 状态。这是验收配置遗漏，不是额度故障；
启用 Run Manager、OA 和 Autoresearch 并不隐含启用所有执行策略。

8×8 生成器现在为新实验显式配置既有 `enforce` 模式，并用实际编译配置验证
租约过期进入 Run Manager 所有的绑定恢复路径。默认兼容策略、重试上限和安全边界
不变。启动前应核对实际加载配置及冻结摘要；旧实验保持原配置和失败证据，
不能把新实验策略回填到旧实验并声称完成了原资格。

### Task 审计不提前承担全产品报告

同轮 TOOL-NORMALIZE 的下一代 Verify 已读取成功的宿主回执，但其通过结果在预检
被误要求完整 `product_acceptance_report`。原判定以结果的 `verification_owner`
推断全产品职责，混淆了仅审计一个冻结 Task 的 `candidate_verify` 条款责任与最终
Candidate operation。不是缺少业务测试或模型额度，不能靠伪造全局报告放行。

共享 Product Acceptance 绑定器现在识别内核 operation 的三个同时成立的条件：
`task-stage`、`task_pipeline_stage=verify`、`output_profile_id=task-verify`。
这个 Task 范围仍经过原单Task条款、命令、目标快照、身份与准入校验；全产品门禁
继续由真正的 Candidate/Discovery 承担，未知或旧式调用仍保守保留原判断。
语义结果自行声明这些字段不能改变内核绑定的阶段或输出 profile。

回归 `tests/test_task_audit_acceptance_scope.py` 覆盖预检、正式准入和拒绝反例。
真实冻结 Task 的同一 scratch 在旧代码只读回放返回缺全局报告，修复后预检通过；
已停止的旧Run没有重新准入结果或补写通过。证据在
`reports/task-audit-acceptance-20261119/`，完整真实资格仍须新轮核验。

### 受控失败提交的字面量 stdin

真实轮 `workflow-863563136fc24d34` 的Mask和Average使用briefing生成的
`zf result validate --stdin --submit`报告执行失败。说明文字中的Markdown反引号
位于带引号的here-doc正文，原守卫却将其按活动shell替换扫描，误拒结果提交。
完成提醒不会改变同一解析行为；没有入账的失败结果也无法启动已有宿主证据接续。

共享shell分类器只识别单个受控`result validate --stdin`命令和完整带引号的
here-doc。正文作为CLI输入交给既有身份、权限、schema及一次性准入检查；这不授予
runtime目录写权限，也不把hook放行当作提交成功或产品通过。未引用delimiter、
头部替换/重定向/管道/分号、尾随命令、提前结束、其他程序等仍走原保守检查。

回归`tests/test_hook_literal_result_stdin.py`直接使用真实提示生成器和Codex/Claude
hook入口，覆盖合法正文及shell副作用反例。运行中的旧实验不因源码修复而变为
新源码验证；任何批准后的源码维护仍须明确计入人工介入。

### Task宿主动作绑定当前派发

同一operation在租约恢复或受控resume后可以有多个`task.pipeline.stage.dispatched`
历史记录。宿主动作和Verify证据接续按当前operation的dispatch、attempt、lease及
Task/Run/map/stage/generation识别唯一内核派发，不按整个历史记录数量，也不选择
账本中的第一条或最后一条。原有合同、目标、工作区、静默、审批和副作用幂等检查保留。
重复当前派发、缺失当前派发、身份冲突或非内核来源仍拒绝执行。

回归`tests/test_task_host_redrive_binding.py`覆盖实际RunManager tick→隔离命令→独立
Verify接续，并保留错误绑定不产生宿主效果的反例。真实轮维护前后证据分开记录。

### 区分Reader修复源与Task宿主事实

恢复不必然带有`critical-reader-repair-context`。失败提示只要求消费当前manifest
实际绑定的恢复源；Task宿主重试通过既有`task-handoff`携带证据。缺少未绑定的
可选恢复源不构成读取失败，实际绑定的源不可读仍须报告真实失败。

handoff中的`host_execution_evidence_reads`从已核验、已冻结的宿主证据索引生成，
精确选择前序operation及完成事件，避免把回执`source_event_id`的动作创建事件
误作可读取的完成事件。原索引、摘要、完整输出分页和CLI身份鉴权继续有效；没有
宿主事实则导航为空，不新增执行权或产品结论。历史handoff保持不可变。

回归`tests/test_task_execution_evidence.py`覆盖实际派发产生导航、受控读取非零
退出事实、拒绝创建事件及跨代读取；`tests/test_reader_repair_navigation.py`覆盖
提示与实际来源一致。真实Average重复输入失败的证据在
`reports/recovery-input-navigation-20261119/`，真实验收状态单独记录。

### Provider重启后的同attempt接续

Task Pipeline当前attempt已经sent而Provider进程退出时，重启并等待就绪后的
恢复briefing必须走既有continuation校验，不再次申请首次派发claim。读取当前
canonical attempt绑定的operation/dispatch/lease，保留原typed输入和输出合同；
不扩展租约、不生成新attempt，也不把进程启动或提示送达当作业务完成。

仅sent使用此路径；prepared/delivering保持首次投递语义。暂停、过期、Task/role
不匹配、operation已结算、预算限制及不在线的目标仍由原门禁拒绝。回归
`tests/test_pipeline_respawn_continuation.py`直接覆盖实际恢复入口，且保持TaskAttempt
未变。初始Provider退出原因及最终真实验收分别记录，不因此修复而假定全部通过。

### 环境失败报告的证据定位

只读Verifier可以在失败结果中引用当前实际工具调用/输出标识，附命令、退出码和
原始诊断；已有持久ref时优先引用。无需另写探针文件或从账本查event ID，不能
编造标识、复用其他attempt冒充本次输出。方法在Evaluator skill与派发提示一致。
这是明确既有failure admission能力，不放宽产品通过、身份、必读输入或权限门禁。
真实Reader反复因寻找额外持久引用而不提交的记录在
`reports/failure-evidence-guidance-20261119/`；真实CLI回归同时覆盖文件和原生工具输出
引用，均只准入failed/abstained，未执行的产品报告保持不存在。

### Reader分页续跑与TaskAttempt enforce

`reader_progress_continuation`在必读材料唯一覆盖增加后，使用原生session和原简报继续原sent TaskAttempt。它不调用故障重试分配器，不改变lease、dispatch、durable call request/attempt，也不因续读增加故障重试次数。真实K3暴露旧实现保留了逻辑WorkflowOperation attempt，但重新分配底层TaskAttempt，导致正常读取耗尽3/3预算；原shadow测试未覆盖该边界。

新的发送仍经过统一预算及Run门禁，再校验kernel claim、当前Reader/operation/fanout/dispatch、独立的transport TaskAttempt身份、未到期lease和活worker。发送成功才记dispatched；结果未知保留原claim，不盲目重送。重复页不产生新进展，真实失败仍走原有有界故障恢复。该路径不赋予Reader写权限，也不替代最终结果准入。回归入口见`tests/test_reader_attempt_continuity.py`和`tests/test_reader_progress_continuation.py`。

### Reader返回遗漏的证据对账与分页方法

控制器在工具执行期间重启，遥测tailer从EOF重新挂接可能漏掉返回事件。Reader的
续跑器不能因账本中存在历史tool.use就永久等待，也不能将未知工具当作完成。
仅对当前RoleSessionRegistry绑定的Codex会话核对精确call ID、工具名、输入及
完成时间；单一调用/返回对存在且返回不晚于已关闭回合时，保存完整不可变观察
sidecar并绑定到continuation claim。缺失、重复、错会话、错输入、未来返回均
保持等待；显式pending tool、未知身份、审批和协助等待也不被覆盖。不重放工具，
不补造产品结果，不把历史返回当作当前模型理解。实际恢复入口的正反例见
`tests/test_reader_tool_observation.py`。

分页的`complete:false`和next_cursor表示继续工作。单批页数限制不是整个验收
attempt的结束条件；修正选项后已恢复读取的错误不再是弃权依据。Evaluator skill
及实际Task/Candidate输出合同统一说明这一点；真正未解决的执行失败仍按原
failed/abstained路径上报。此方法修复不增加确定性语义门禁，也不降低必读义务。

### wait_for_all中的单Reader空闲失败

单个Reader空闲超时只结算该子项及其durable operation。还有健康子项时不发布
整个fanout的timed_out或最终失败；它们继续原身份的阅读、结果编写与提交，最后
仍按原wait_for_all策略聚合。any_failed_fail及真实整阶段期限保持既有终止语义。
超时聚合沿普通fanout路径携带父Run/Plan身份；禁用阶段时限时不显示误导的“0秒”。

历史版本可能已因单项idle错误地终止聚合、留下其他dispatched Reader。恢复扫尾
只对当前running Run、当前wait_for_all Reader批次、timeout_seconds=0且存在精确
匹配的running调用进行对账，追加带原失败引用的aggregate.started恢复等待。原
失败事件和已失败子项不变，所有operation/attempt/lease不变。暂停、终态Run、
已替代批次、整阶段超时、错调用/attempt均不恢复；不是重启终态Run或复用旧通过。
回归`tests/test_reader_partial_timeout.py`直接覆盖超时和实际恢复入口及最终聚合。

### 同批Reader定向重试的恢复材料

现有`repair.action.requested(kind=rerun_fanout_child)`可为单个失败Reader创建新调用，
保留同批已通过和仍在运行的兄弟项。新调用必须重新完成自己的必读义务。
恢复材料正文未变化时保留父Reader已固定的不可变引用和来源，避免新dispatch
因source_event_id变化生成不同摘要，却把旧required_reads带入新请求而不可读。
复用前核对父调用Run/Task/role、必读登记、摘要、正文及恢复字段；改变或未绑定的
输入拒绝沿此路径继承。原结果不改，旧read coverage不转移。实际Candidate准备入口
及连续两次重试、篡改/错身份反例见`tests/test_candidate_reader_retry_authority.py`。

### 从未送达的Reader等待失败

环境等待超时后，requested或从未送达的suspended调用都应沿精确失败事件收敛；
曾started、retry_started或dispatched的调用不得当作未送达。现有单子项受控重试
入口可处理这类失败：要求当前running Run、未暂停、当前fanout、唯一匹配的
Reader请求及完整不可变身份，先结算原失败，再经过普通准备、目标锁定、权限
和transport门禁创建新调用。首次真实dispatch以原等待失败事件为因果，不伪造
历史派发，不重新Plan/Impl。新一轮继续失败时attempt序号仍递增，避免重复身份。
暂停、取消、已送达、错请求、晚于原失败的新状态均拒绝这一窄恢复路径。
回归`tests/test_reader_undelivered_retry.py`包含实际repair.action入口、幂等和反例。

受管空闲Reader占用被确认释放后，同一次受控操作立即再探测一次原目标锁定。
只有真实pin成功才继续；仍忙/超时继续defer，目标错误仍fail-closed。重试不增加
角色释放权限、不杀活跃持有者、不跳过环境锁。该检查消除“释放成功但请求仍被
拒绝、必须人工再次请求”的常见断点；第二次探测也失败时不循环或冒充成功。

旧Reader等待失败的待诊断投影在精确后继真实派发后停止提出同一诊断义务。
要求kernel事件、晚于失败、相同Run/fanout/child/stage/role/target及精确retry-of；
伪造、错身份、早于失败或同attempt事件不能清除。旧失败与RecoveryCase历史保留，
不代表产品通过；后续新的等待失败仍有独立诊断责任。

### 必读完成后的单次结果收尾

正常成功关闭的Reader回合若没有新增读取量，不能立即等同于Provider故障：输入
消费与结果交付是两个义务。对同一运行中的operation/request/attempt，在此前
已交付续跑、当前全部必读通过原ledger规则、没有未结束工具/审批/协助且没有
交付未知的条件下，允许一次`continuation_phase=result_completion`。它继续原
TaskAttempt/lease及原生会话，走原发送时预算、暂停、身份和权限门禁；不重读
已知材料来人为增加进度，不使用Task工作记录的越权替代，不生成产品通过结果。

此收尾机会不因控制器重启或重复关闭事件重置；后续仍无结果时回到原有有界
故障恢复。输入未读全、证据无效、失败的Provider回合或未知工具效果不获此机会。
这是机械交付宽限，不是对业务“已完成”的语义判断，不能代替validate/submit与
独立准入。回归同时覆盖TaskAttempt enforce和发送瞬间的预算/权限/暂停反例。

### 过期诊断建议的一次结算

Resident返回的human、reflect、repair、autoresearch建议沿同一运行条件和当前
operation/attempt校验。目标已被后继替代或绑定的运行条件已失效时，追加一次
superseded消费回执；后续tick不再反复deferred，也不对旧身份启动新效果。原建议
及原失败历史保留，产品Task/Run状态不受此诊断清理影响。共享修复被其他案卷
持有等临时门禁仍deferred，门禁解除且身份仍有效时可重新消费；暂停期间不消费。
真实Run Manager入口及临时等待恢复反例见`tests/test_run_manager_action_admission.py`。

### 已解决断点与后继Reader的诊断选择

工作流恢复断点参与优先级选择前，先按正式problem envelope规范化案卷身份。
已有resolved回执的断点不再占据恢复分支，避免后续去重删除旧动作后留下空队列、
同时压住当前失败的诊断。verifying、waiting、blocked案卷保持原有门禁与所有权。
旧案卷事件和投影保留，回归见`tests/test_run_manager_recovery_frontier.py`。

同一Run/Task/阶段/role的内核Reader请求已存在不同的后继operation时，旧调用的
失败不再抢占语义诊断入口。必须有完整旧/新请求身份、非空摘要和匹配的失败归属；
缺失、歧义、worker来源、不同角色/Run/Task/阶段均不能据此屏蔽诊断。晚到的旧
失败同样不能冻结后继，当前调用失败仍须诊断；不删除历史、不生成产品通过。
回归`tests/test_reader_failure_currentness.py`沿Run Manager实际选择入口验证。
并行Reader共享Task时，调用失败的诊断去重按operation身份区分，避免一个Reader
的诊断吞掉另一项当前失败；缺少operation身份的历史事件保持原有回退规则。
诊断动作保留原失败明确给出的Run及task-map代际，Autoresearch请求同时携带
顶层Task ID和事件Task ID，让后续intake可沿原身份处理；不从当前最新Run猜测
已有失败归属。上下文附件仍保留完整操作证据，诊断请求不授予产品状态写权限。

### 诊断中的控制器版本证据

诊断context侧车的`runtime_sources`复用已有`loop.started.runtime_load_ref`，
同时索引各来源失败事件发生前的启动回执及最新启动回执。验证侧车摘要、schema
和state_dir归属后才暴露source_root/revision；缺失、损坏、跨状态目录保持
unavailable，不能用最新版本填补历史未知。运行版本与产品target、诊断实验基线
是不同身份；启动回执也不证明进程仍在线或修复有效。此信息供诊断核对，不自动
切换实验工作区、执行代码或授予合并权限。实际诊断发起入口与反例见
`tests/test_diagnosis_runtime_context.py`。

### 当前调用失败的常驻诊断路由

`task.attempt.failed`通过统一事件注册表声明`run_manager_recovery`，进入已有
常驻Run Manager诊断消费者；可用时投递带request/context绑定的诊断，收到建议
前仍保持原故障未解决。显式Autoresearch路由和常驻不可用的回退保持不变。
当前配置明确关闭resident时，历史spawn事件只能作为历史证据，不能重新启用
发消息能力；无配置的只读投影仍可根据历史描述观察状态。实际tick入口及关闭、
投递失败、等待响应反例见`tests/test_run_manager_diagnosis_consumer.py`。

### Candidate 续跑的跨阶段证据接力

受控 continuation 的历史证明由 `continuation_evidence` 统一提供给实际 Reader
handoff、Goal read contract 和常驻诊断 context。入口先校验原授权、冻结目标及
sidecar 摘要，索引原 Task Verify control/envelope、精确 Task 提交上的已准入
Impl/self-check、它引用的工具事实，以及原 Run 派发/native hook 发生记录。
摘要损坏拒绝；找不到的工具引用明确 unavailable，不从其他 Run 补造。
所有事实保留原身份；派发/hook 观察不是硬件推理并发或最终资格 verdict。

`critical-continuation-context` 保存执行范围、原批准及证据导航。经现有受控
stage retry 成功发布的 owner feedback 同步进入后续消费，不修改历史 claims、
原失败/取消终态或已完成任务。产品条款与执行资格的适用性由评估 agent 对账，
不能把后继产品交付成功当作重新证明零人工干预。规范方法见
`skills/zf-verify-rescan-replan/SKILL.md`；完成审阅但缺证据使用既有 blocked/
dependency_blocked 结果，执行失败仅用于真实执行故障，不通过文本猜测改判。

常驻 Run Manager 的每次诊断 context 带当前 reflect/source-repair 能力及历史
证据 refs。已有资料的有界只读核对由 resident 自身先完成，避免依赖关闭的路由；
它不能释放人工门禁、批准重试或提交产品通过。当前恢复仍经原受控动作、精确失败
身份、Run/暂停/幂等校验执行。未将“readonly”当作绕过所有门禁的通行证。

回归：`tests/test_continuation_evidence_handoff.py` 覆盖真实派发消费者、Goal
消费、损坏/缺授权及历史工具身份；`tests/test_recovery_evidence_context.py`
覆盖实际常驻诊断入口和受控 scope review。真实状态与介入次数另记 reports。

### Reader 自动续读的停止条件（2026-11-20）

真实 Discovery 在 102 项必读尚未完成时连续结束两轮，再由 Run Manager 接回。
已有续读提示提供同 session 游标恢复和证据边界，但未明确排除任意页数后的提前
交接。补充实际 Reader continuation 派发提示：同轮持续读取，直到输入覆盖完成
或有具体阻塞；之后完成独立评估和受控提交。权限、预算、证据及未知工具效果
门禁保留，不靠修改 required reads 或将部分进度当作通过解决。该提示的效果需
真实 provider 继续观察，mock 仅证明实际派发携带提示且既有状态约束保持。

### 原生工具事实与前序 attempt（2026-11-20）

`hook-recv` 的 PostToolUse 不设置 `origin=kernel`，不能用该条件筛除原生事实。
continuation 复用 `task_execution_evidence.capture_execution_evidence`：核对内核
派发因果链、Run/Task/operation/attempt/角色/工作目录及结束边界，校验原始输出
侧车摘要并脱敏；不将截断预览冒充完整输出。

最终 Impl 可显式引用同一 Task、同代际中前序已准入 attempt 的工具记录。前序
结果可以是 blocked：结果被准入不等于产品通过，也不应因此丢弃其中真实发生的
测试事实。索引保留原 admission、verdict、提交、operation、attempt 和 origin，
标记为历史观察而非当前候选通过证明。未准入、错身份、晚于边界及损坏输出仍
保留 unavailable；当前通过状态仍只取已通过且与 Task Verify 提交一致的 Impl。
前序结算后发生的受控宿主机验证沿同一捕获器的完整 lease/request/代际/批准及
回执校验读取；rework 控制事件显式说明不是执行输出，不能补成测试证明。

### 结果纠错必须沿用当前派发（2026-11-20）

Discovery 首次提交缺少产品验收报告时，结果修复已请求，但发送入口把纠错当作
新 TaskAttempt delivery，被防重复派发保护拒绝。纠错现绑定原 operation/request、
纠错侧车、当前未过期的 sent TaskAttempt，使用 continuation 通过既有发送入口；
预算、暂停、Run、身份和存活检查仍执行。发送前记录 claim，未知发送结果不盲目
重试，也不新增 attempt 或清空读取进度。发送完成仅是交付回执，不是产品通过。

Discovery 的产品评估输出补齐与 Candidate Verify 同源的报告模板及只读 preflight。
缺报告、错目标或缺旅程在实际提交前返回诊断；准入仍复查当前身份与证据。已有
报告只有经独立审阅并以 ref/digest 正式绑定才算报告，普通 evidence_refs 引用不
替代绑定。旧 attempt 只接收有界纠错，不改写其既有 source manifest。

回归通过真实 `_send_transport_task` 检查幂等、控制器重启、发送未知、租约、预算
和身份边界：`tests/test_result_repair_delivery.py`；预检与实际提交、输出卡片见
`tests/test_verification_submit_preflight.py`。测试不再替换掉 TaskAttempt 发送保护。

发送前的预算或 Run 授权拒绝可以证明没有发送，单独记为 `not_sent`，恢复授权后
仍可纠错。仅这两类明确的前置拒绝可释放 claim；运输异常保持 unknown，禁止
将失败一律当作未发送。最终 Judge 的活跃 fanout child 也拥有自己的派发身份，
父 Task 的历史 discriminator/完成事件不能将它写为空闲或关闭其卡顿监测；
普通 Task 当前派发的真实完成仍按原规则识别。
