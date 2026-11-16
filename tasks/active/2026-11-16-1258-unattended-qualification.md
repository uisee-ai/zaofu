# 无人恢复 P0/P1 与新 PRD 资格验收

> 状态: active

用户已批准建议中的 P0/P1 实施及真实 PRD 两轮评估。基线 2137a567；独立 worktree unattended-qualification-20261116。规格：docs/spec/unattended-recovery-qualification.spec.md（本地忽略）。前序证据位于 continuity-handoff-20261114/reports/unattended-repair-20261116T1053Z/review.md。实现记录：docs/impl/unattended-qualification.md。

## Backlog / 验收

- [ ] B01 / U01：标准宿主监督部署入口 → verify: CLI 测试、真实用户 service 控制器退出恢复；pause/stop/terminal 拒绝重启；配置与重启前置可诊断。
- [ ] B02 / U02：预授权测试 → verify: 精确合同/源码/依赖绑定、限定预算与命令、旧授权/变更/未决副作用拒绝；调用入口和证据消费测试。
- [ ] B03 / U03：统一 workspace/scratch 权限 → verify: 实际派发说明及原生结果提交无需人工澄清；canonical state 仍拒绝直接改写。
- [ ] B04 / U04：修复链路配置和集成 → verify: AR 补丁、独立验证、受控 apply、加载新源码、恢复原 checkpoint；越界失败关闭。
- [ ] B05 / U05：恢复效果 → verify: 诊断建议/应用不能冒充原目标进展；重复恢复幂等且旧检查点退出。
- [ ] B06 / U06：诊断生命周期 → verify: 阶段/报告落盘，终止不冒充成功，跨进程重启可重建未完成工作。
- [ ] B07 / U07：冻结最终源码，最小真实 PRD 2+2 → verify: 原业务断言及 Docker 浏览器证据，账本到 run.goal.completed；规定故障矩阵与人工介入计数独立记录。

## 执行纪律

先 RED→GREEN，影响闭包、sentinel 和 mock E2E 后真实运行。轮内监工只观察，预定注入脚本记录故障；一旦临时回复/执行宿主命令/恢复/改码则该轮无人资格失败。轮间修复后新轮复验，不抹去历史失败。不存在通过的真实证据不能归档为 done。源仓仅本地提交，未授权本轮 merge/push。

## 进展

- 新 worktree 与依赖准备完成；已确认 source_repair_apply/restart/continuation 现有链路，不重复创建状态机。

### 实现与测试检查点（未宣称全流程完成）

B01–B06 的入口与安全边界已补齐第一批实现，B07 尚待真实 PRD。安装版监督服务第一轮发现 WorkingDirectory 引号错误，修正后 2 个真实宿主测试通过。容器第一轮发现 tmpfs 根目录 cp 保留时间戳无权，改用容器用户子目录后真实执行通过。

影响闭包 1,101 文件：13,059 passed / 19 skipped / 17 failed；在 2137a567 干净导出基线复现其中 16 项，另一 signed_board_delivery 当前单独复测通过。保留 baseline-failures.json/log，不修无关历史失败。最后定向 187 passed、交接 8 passed；sentinel 53、mock E2E 10、serial 10、Web typecheck/unit 通过。非全量通过，非新真实 PRD 通过。

复核还发现旧 self-repair sandbox bypass、修复分支 -B 重置及运行 briefing 污染 worktree；本批移除这些路径，提供默认关闭的受控候选发布，并验证其 CLI/tick 既有调用链。真实 Provider 故障资格仍不能用此处单测替代。

### 真实流程复验与后续修正

实现提交 `7bbd61ad feat: add bounded unattended recovery execution`。后续修正：

- `48274873`：修复验证保留完整影响闭包，不因文件数超过 80 扩展为全仓；27 passed / 1 skipped。
- `45e6f959`：repair → apply → continuation 保留原 RecoveryCase 身份；186 定向通过，影响闭包 4990 passed / 7 skipped，3 项 PATH 环境失败补齐虚拟环境后逐项通过。
- `af1ff7f9`：已完成的失败容器回执也交回独立 Reader；36 passed + sentinel 53。
- `9e1e4393`：Plan/Critic 显式物化原始 source_ref，不能只读包含路径的 Requirement 摘要；35 定向、930 影响测试及 sentinel 53 通过。
- `459bfbb6`：活跃期 repair apply 在修改源码之前核对 operator_approved 重启策略；187 定向、5013 影响测试 / 7 skipped、sentinel 53、mock E2E 10 通过。

第 1 轮隔离真实 PRD 在 Plan 补证阶段停止，NOT_QUALIFIED，未进入资格计时。入口驱动错误传入 `from` 而非 `source_ref` 属于本 session 的问题，不能算作框架故障；正确嵌套 source_ref 的运行时交接缺口另行复现并修复。Planner 自动返工已发生，但不替代完整产品验收。

第 2 轮 `/tmp/zf-unattended-r2-20261116T141114Z` 冻结 `459bfbb6`，使用正式 source_ref 和显式 operator_approved。14:29 UTC Planner 已准入，独立 Critic 开始；尚未批准 Plan 或进入 Impl，B07 继续未完成。详细失败历史、日志与故障脚本摘要保存在 reports/unattended-qualification-20261116/ 及隔离根 receipts/，不能把能力测试计作无人真实 PRD 通过。

第二轮后续：14:36 UTC 同版 Plan/Critic/Preview 审阅批准，开始资格计时。控制器启动窗口异常退出后约 9 秒恢复并并行派发两项 Impl；带未提交测试的 Worker 退出后自动 respawn，原文件摘要保留；结果通知期间控制器中断后，原 blocked 结果正确准入。

本轮未通过：两个 Impl 报告提交要求与 Provider 的“未明确请求不 commit”规则冲突；一个 Worker 因未说明既有 literal apply_patch 适配而停止填写 scratch。OA 在只读完部分输入后结束回合，无结果却持续占据 running operation，Run Manager 已提交的恢复请求无法继续。未到独立 Verify、Candidate 或 F4–F6；没有人工代提交、代测试或宣布完成。

下一修复批次显式传达合同内的本地提交请求、说明已有精确 scratch 适配，且为 OA 正向结束回合但缺结果增加同检查点一次重投；暂停、终态、已准入结果及活动新回合均拒绝重投。RED 已复现，准备以修复后源码从原断点验证，不改写本轮失败历史。

该批定向 136 + 33 passed；297 文件影响闭包 4212 passed / 9 skipped / 1 failed，唯一失败是已在 2137a567 复现的 wake 数量快照（194 vs 193），本批未修改 wake 集合。sentinel 53 与 mock E2E 10 通过。新增说明曾导致 12 KiB briefing 回归，已压缩说明后复测通过，没有放宽阈值。真实断点续跑仍待验收。

`a686f199` 后的 epoch 3 确实自动重投、结算旧 OA operation，并继续 semantic_failure。
OA 正式升级旧提交授权阻塞；它没有凭空推断新源码赋予权限。监工通过现有
`zf recover task-impl` 对两项 settled blocked 操作先 preview 再 apply；两 Impl 已并行续跑。
记录为人工介入，不记无人通过。真实 OS 诊断中断测试证实活 owner 保留、死 owner
只关闭一次、dirty 修改保留、不重放命令；仍不能替代真实 Provider 整条修复链。

旧 pre_impl 结果另因裸阶段 work_unit 被拒绝。补全结果模板的 task_ids/stage_ids，
不降低 execution_task_outside_plan 门禁。RED→GREEN 定向 78 passed；相对已验证
HEAD 的增量影响闭包 172 passed / 7 skipped、sentinel 53、mock E2E 10 passed。
这次方法修正留给下一冻结轮次，未热改运行中的 epoch 3；B07 继续 active。

OA 模板修正已提交 `52ac03a1`。epoch 3 后续证明不是单纯授权文案问题：两个 Worker
实际 `git add` 均因共享 Git index 只读失败。15:26 UTC 停止本轮专属服务、tmux 和观察器，
保留全部 dirty 产品与证据；evaluation-epoch3.json 仍为失败，无 Verify/Candidate 完成证据。

新增默认关闭的 `runtime.task_publication`，经明确预授权精确路径和有限次数，复用已有
host-assist commit 与 Task Impl retry，不改变 Provider sandbox。仅发布当前 blocked 候选，
提交不是任务通过；源码/合同/身份/活动回合/未决副作用/预算门禁保持失败关闭。
32 项定向验证通过（包括实际 Git 与原 retry 事件）；影响闭包和新真实轮次尚待完成。

受控发布最终定向 76 passed / 1 host deselected；859 文件影响闭包 10817 passed / 18 skipped / 15 failed，15 项逐名属于已在 2137a567 复现的基线集合，无新增失败名称。sentinel 53 与 mock E2E 10 通过。保留失败对账 JSON；不宣称全量或真实 PRD 通过。准备新冻结轮次验证自动发布→原 Worker 自检→独立 Verify。

### 第三轮验收未通过（2026-11-16 16:17 UTC）

真实Plan/Critic通过且原文已读，人工批准后2Impl完成代码与自测但Git元数据只读，均正式blocked。预授权自动发布在两次Run Manager tick后仍未生效；只读独立复算满足绑定，需补充运行进程拒绝诊断。F2活动Impl控制器9.088秒自主重启；F1内容摘要为空，F3故障未命中，均不记为完整通过。R3/evaluation.json保留失败，停止4个预定注入观察器，保留产品与控制器现场。后续诊断重载算监工轮间介入，不能改写本轮结论。候选绑定拒绝原因日志回归先RED后GREEN，12项定向通过，66文件调用闭包进行中。

已定位根因并修复：空闲Codex仍持环境共享租约，候选发布/容器快照错误请求排他租约。Git发布和隔离只读快照改持共享依赖锁，依赖修改仍排他阻止；直接宿主验证不变。host-assist补充活动Provider回合拒绝，未暴露外部只读开关。两个真实锁回归先RED（共享读不应阻塞）后GREEN，排他修改仍拒绝；51定向通过/1host未选，增量68文件影响闭包1123通过，sentinel53通过。报告包含R3四个实际阻塞锁及持有者证据。下一步从保留断点加载修复，不能据此改写R3无人失败结论。

断点epoch4加载fc9fe4ea后，宿主发布已越过锁门禁，但隔离产品仓库缺Git本地作者身份（初始commit仅继承监工环境），两任务暂存后commit失败。系统保存失败回执并拒绝盲重放，索引保留。追加发布前身份检查（RED→GREEN），后续新fixture初始化显式设本地测试身份；不改全局、不人工补写产品commit。

身份前置检查验证：7文件影响闭包71通过，sentinel53通过，design199 recovery mock E2E 1通过。第三轮及epoch4已通过simulation.done、zf stop --include-run-manager和所属systemd停用结束，staged候选与全部证据保留。

扩展实际缺陷影响闭包：AR修复候选复用同一Git提交实现，也补齐identity前置检查；真实Git回归先RED，确认旧实现已暂存再失败，修正后必须零effect/零staged。R4规划已经启动，资格尚未开始，两隔离仓库都已显式设置本地测试身份，未更改全局配置。

AR身份预检定向3通过，4文件调用闭包29通过（含runner与refactor mock），sentinel53通过。仅在尚未批准Plan的R4预备阶段载入此防御，不增加产品写入权限。

第四轮预备阶段 Critic 正确要求两项数字规则确认；监工经正式 owner decision 答复 Number 全串有限输入与原生有序聚合，Planner 已按此返工，最终 Plan 尚未批准。首次请求误填了原审批为空的 candidate_task_map_ref，被409拒绝；改用原绑定后成功。此为监工请求错误，单独记录，不归因于运行时。

另复现语义 owner checkpoint 的 Preview 缺正文：旧代码只为 Reader execution failure 收集 Planner refs。抽出严格同版只读解析，新 checkpoint 与旧事件读投影均可显示实际受审 Plan；原审批不变、不放宽 Reader retry。RED→GREEN 定向58通过，14文件影响闭包383通过、sentinel53、Plan approval/replan mock E2E5通过。真实R4旧事件只读重放得到9122字正文且无内容/工件错误；这仅证明Preview修复，不代表最终Plan或产品通过。

R4于17:04:11 UTC批准Plan evt-dae1196b30c0，冻结cc3d33f6。F2活动Impl控制器自动恢复6.105秒；F1 watchdog新pane保持同工作区与非空测试摘要；F3实际结果通知中断命中、结果正确结算；F5两次原manifest重复请求返回already_recorded且副作用started仍1。两候选已由预授权宿主发布，无人工代提交。

新缺陷：隔离容器 /work tmpfs默认noexec，npm test中的tsc返回126。独立于R4的0755脚本真实回归先RED，再仅为容器工作区显式exec后GREEN；2项host通过、66文件影响闭包1091通过、sentinel53通过（闭包含恢复mock）。本修复尚未加载R4；该轮RM已自行启动AR诊断，继续区分自动链与监工工作。

R4自动诊断现场另确认loop→inner runner漏传budget_usd：7.5的回归实际收到500，已RED→GREEN补传；39定向、3文件调用闭包29与sentinel53通过。本修正没有盲目继承产品的容器命令/文件发布权限，也没有声称解决父目标跨诊断累计计费。现场父workflow run cap40、诊断inner cap500，两个CostTracker美元值0仅表示记录值，不能推断调用免费或未消费。

R4两业务任务于17:38前均独立Verify通过并done，Candidate冻结6d3022d8；但Candidate Verify两lane因已归档的旧Provider回合仍active而不派发。17:45正式记录FAILED，停止剩余预定故障观察器，保留所有产品提交和F6注入。资格期间人工恢复0，但不能宣称无人完成。F4观察器等待flow-verify而实际为verify-lane，未命中；下一轮须先修正并重新冻结。

归档回合重放回归先RED后GREEN；同时覆盖真实slot release调用、终止失败仍存活不得释放、保留会话及新回合不误关闭。29定向、205文件增量影响闭包2938通过（含相关mock E2E）、sentinel53通过。下一次源码加载属于监工恢复epoch，不改写R4失败结论。

AR自动诊断在22分钟后被模板重新引入的10000000 token预算阻断，0/1修复完成；计量10010424中9525632为缓存读取，不能称为同时上下文长度。父流程明确token_budget=0。自动诊断保留dirty工作树，未输出可应用补丁；修复与原业务续跑的全链仍未验收。

归档占用修复提交68b46c81，真实R4事件离线重放确认两lane均无残留回合。诊断token继承回归先RED（三处缺口）后GREEN，0/正数/未指定及无关状态目录均覆盖；49定向（新增参数后）、155文件增量调用闭包1890通过，sentinel53通过。仅继承归属校验后的run token预算，不放宽其他权限。B07保持active，尚无最终Candidate/Judge通过证据。

epoch5于17:50:59通过本轮隔离harness合并已验证源码并重启控制器/AR sidecar，仍保留F6坏参数。两Candidate Verify随即自动派发，证明归档卡点断点恢复；产品dev仍为seed而Candidate分支保持6d3022d8，未提前Ship。F4目标改为实际verify-lane-0，独立冻结epoch5脚本和摘要，不改原资格脚本。

另修复诊断子流程模型/美元上限偏移：同名同backend仅继承model/effort，单次费用上限取父配置与loop最小正上限；不传递产品权限。两RED后51定向、156文件影响闭包1892通过、sentinel53通过。真实R4配置只读CLI重放确认Astra/High规划、Terra/High实施验收、token0、费用40；run_loop被捕获而未实际执行，不算诊断成功。

实际恢复延迟还复现为tick结构缺陷：Run Manager事件判定嵌在300秒Supervisor门内，使有限发布/采证每步等待完整巡检周期。新增有限机械恢复事件快路径，仍复用原Run Manager事务与消费游标；回归先RED（105秒无调用）后GREEN，证明无需触发Supervisor且记账事件不反馈自激。31定向、24文件影响闭包412通过/7跳过、sentinel53、mock flow-smoke10通过；当前仅离线验证，加载后还需实际回执时序验证。

epoch5两Reader自检通过后因Docker沙箱拒绝正式abstained，随后再次停止推进。只读核实真正停点为quiescent_now：17:43的failure-closeout审批明确blocking_scope=side，却在Reader结束后使全局tick静止。不是F4锁把控制器卡死；watcher心跳仍更新。修复旁路范围不阻断产品恢复，既有Run级升级不被覆盖；RED→GREEN 47定向、10文件闭包82通过、sentinel53、recovery mock2通过。真实事件离线重放从escalate_unresolved变为no_escalate；未代批归档动作、未修改历史升级事件。

epoch6载入f1f0b5c8后实际退出旁路误静止，18:06:36/58/18:07:03连续产生三份受控容器回执（F6坏参数仍失败），证明快路径已生效。F4记录reader_environment_busy后30秒释放租约，两Reader自动启动，没有监工释放/抢锁。

新竞争缺陷：通用batch先重发Candidate，导致原Reader回执交接报“reader failure has been superseded”。为精确内核准入的candidate-verify abstained保留Reader恢复所有权，不再通用重发；旧超时/缺准入不受影响。24定向、107文件闭包1684通过/7跳过、sentinel53、mock10；真实旧事件只读重放不再生成该批重发checkpoint。尚未加载现场，保留运行中修复的基线。

实际F6修复Worker正常退出但no_commit。其原生记录证明skill仅接受env/copied-config授权，未识别已准入kernel grant；合同scope/验证cwd还错误指向原harness。新增精确授权方法及隔离checkout合同绑定，回归先RED后GREEN；25定向、88文件影响闭包1339通过/2跳过，新增symlink/原仓验证拒绝回归另行验收。没有开启全局bypass，没有让Worker改写运行状态。

epoch6后续仍失败：未经加载的旧通用重发与Reader采证竞争，最终12次probe预算用尽；自动诊断Planner针对更早Docker权限错误生成有界调查草案而未输出task_map。不能把token/model继承修复记为诊断成功；需要冻结新源码再做新资格轮。

隔离合同最终定向27通过（含symlink/原仓验证拒绝）、sentinel53通过。影响闭包含相关恢复mock；未声称真实修复链已通过。
