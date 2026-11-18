> 状态: active

# 8×8 真实验收暴露问题修复

用户已批准按照复盘顺序实施。基线 dev dda38a25，工作分支 wip/codex-20261118-eight-by-eight。
原验收 workflow-ed9b4a6810cf641c 已停止；原证据和 worktree 保留。不得手工修改 canonical 状态、跳过审批、继承伪造阅读记录或扩大 sandbox 权限。

## 已批准范围与验收

1. P0 人工 gate：保留显式范围、任务默认限于任务，过期推荐不得新增 gate -> verify: 真实 escalation producer → RM admission 回归；T1 不阻塞 T2，显式 run gate 仍全局阻塞。
2. P0 OA currentness 与求助：同一 Plan 下换 operation 清理未派发旧请求，运行中请求保持所有权；求助公平派发 -> verify: G1→G2 重放、活跃 lease 不提前释放、忙队列求助不被新 checkpoint 插队、不重复投递。
3. P0 Plan 输入：将已有 task-scoped 输入能力接入显式 PRD profile，Planner/Critic 完整声明共享与任务分区 -> verify: profile → package → task → read manifest 集成，增加无关任务不放大全文读取；旧 Plan 不静默迁移。
4. P0/P1 恢复分类与额度：宿主 publication 与产品失败分离，统一重试计数且保留有限基础设施恢复预算 -> verify: publication 不触发语义重规划，reader/产品失败正确路由，额度耗尽拒绝越权重试。
5. P1 AR：子流程等待原因及时回传、区分 Plan 阻塞和环境故障、明确工作期限与清理宽限 -> verify: 子流程 owner gate / timeout / 环境失败分别产出绑定诊断，保留证据与停止清理。
6. P2 分层验证：容量、产品完整交付、自恢复分别验收；真实供给不足不等同于容量失败 -> verify: mock 8+8 容量与故障集成通过，再按新合同隔离真实 PRD 验证，记录真实结果和介入。

## 方法与证据

先回归复现，再实施最小修复；运行影响闭包、dev-verify 和 premerge sentinel，真实 provider 为独立层。
实现说明见 docs/impl/eight-by-eight-recovery-closure.md（本批次创建）。
历史报告：reports/eight-by-eight-20261118/qualification-review.md；报告及私有证据不入 git。
完成时记录实现 commit 和逐项验证，不把未完成真实验收标成通过。

## 实施进度

1–5 已实现并通过定向及集成回归，实现提交 `de770b5c fix: bound parallel task recovery and scoped PRD inputs`；第 6 项已通过 mock 8+8 与宿主启动/mock provider 验证。用户随后明确授权最小真实模型测试，已完成一次有界运行，但业务闭环未通过，整批仍为 active。

待验证项目：`/tmp/zf-eight-recovery-20261118T002529Z/product`。只完成本地源码/配置冻结和 SessionStore 初始化；无 workflow-submit、无模型任务。原实验 `/tmp/zf-eight-by-eight-20261117T151837Z` 保持停止及证据保留。

审阅补充发现并修复：仅用 Task Pipeline 校验推荐会误丢弃 Planner/Reader 的有效求助；新增真实消费入口 RED→GREEN 回归，按对应 role/operation 身份链校验。

不把 mock 容量当作真实模型吞吐，不把浏览器可启动当作产品验收。全部真实完成前不标记整批 done。

自动影响闭包：`PATH="$PWD/.venv/bin:$PATH" python scripts/dev-verify.py run --base dev` → 8088 passed / 16 skipped；premerge sentinel 53 passed；flow smoke 10 passed。另行执行的 8+8 容量/安全专项 84 passed、Reader/RM 专项 238 passed、宿主 mock 启动 1 passed；这些集合存在重叠，不合并宣称唯一用例总数。未执行完整 pytest / coverage。

## 最小真实模型测试（2026-11-18）

项目 `/tmp/zf-minimal-real-20261118T013230Z/product`，workflow `workflow-5bb0af811aaa5397`，源代码 `de770b5c`；一个文本整理业务 Task，1 Impl / 1 Verify，PRD v5.1。真实 Terra High 完成 Scan，Astra High 产出首轮 Plan 并执行 Critic。Critic 因测试 PRD 的 Enter 焦点歧义要求 Owner 决定；本 session 按既有授权经受控入口确认按钮焦点 Enter，系统自动派回 Planner。

返工必读输入由首轮 17,381 字节增至 182,040 字节，含两份完全相同的 `plan_ports` 语义正文；原生上下文压缩发生。达到预设 1800 秒时，Kernel 自动取消 Planner 并记录 `workflow_budget_exceeded:wall_clock`。业务完成 0/1，正式 Plan 审批、Impl、Task Verify、Candidate/Judge 及业务浏览器均未执行。$10 配置存在，但 110 个用量条目未定价，不能证明费用门禁有效；美元值 0 不表示免费。

一次正常 Owner 决策、零源码故障修复介入；独立预算停止有效。专属 systemd unit 已停止禁用，私有 tmux 及角色进程已清理；候选产物、原始事件和断点保留。证据及后续缺口见 `reports/minimal-real-20261118/README.md`。后续需明确测试输入、去除 Plan 返工重复正文，再验证实际任务输入消费与完整业务交付；不以取消 gate 或无限延长时限替代修复。

## Owner 纠正后的 8×8 启动

Owner 随后明确要求取消 Planner 时间限制，并恢复原定 8×8 小任务验证。复用此前未启动的 `/tmp/zf-eight-recovery-20261118T002529Z/product`，新 Run `workflow-8625b886a7912513` 已通过受控提案审阅和提交进入真实 Scan/Plan；不覆盖上一轮失败记录。

16 个独立小任务、32 条不相交交付路径，Impl/Verify 各 8，活跃流水线和两个背压上限均为 16。原 8×8 配置的 Run、execution profile 和 Planner stage timeout 均为 0，本次不添加 30 分钟覆盖；失联检测、有限返工和独立验收继续保留。补明冻结输入的按钮 Enter 焦点语义。时间限制取消是 Owner 明确要求，不代表 Plan 重复输入问题已修复。

运行时 admission 已固定 timeout=0；当前仅规划阶段运行，尚不能宣称实际 8+8 同时满载或 16/16 完成。启动证据见 `reports/eight-by-eight-real-resumed-20261118/README.md`；本任务仍 active。

## 真实续测补修：writer 槽位与 reader 工作区解耦

真实 Plan 已于03:47 UTC批准，8个Impl并发已观察到，随后独立 Verify/任务集成开始。发现旧任务 Verify 的读锁错误阻塞同一物理 Impl 槽位承接新任务，归入第6项真实并发验证缺口；在独立临时 worktree 修复，不热改正在运行的源码。

验收：真实锁占用的旧工作区已合法移交 Verify -> verify: 新任务通过实际激活入口启动，原 Verify 不被终止；未知占用、未准入 writer、过期绑定、不同 generation、已退出 reader 或新工作区占用 -> verify: 仍等待退出。第一轮1 failed/10 passed正确复现；初版影响闭包219 passed、sentinel53 passed，补充边界后生命周期相关61 passed。最终复核与真实容量/产品闭环仍需分别记录，整批保持 active。

补修最终影响闭包（相对已验证f90dbf80）230 passed；premerge sentinel 53 passed；此前生命周期61项有重叠，不累计为唯一用例数。`dev-verify plan --base dev`已检查全分支范围，旧批次8088项证据保留，本次按新增变更闭包执行。真实Run仍使用f90dbf80，尚无修复后真实8+8吞吐证据。

## 最终验收环境失败补修

真实业务Task已16/16完成、Candidate冻结1589e636；最终Candidate验收尚未通过。多个reader探测Docker权限失败后未提交结果，重试耗尽。追加复现显示，正确的verifier_execution_failure因必读材料未完成被拒；最小补修允许只报告带证据的执行失败，保留产品结果与身份门禁，同时同步Verify方法。新回归初始2 failed/8 passed，最终10 passed；影响闭包及真实断点恢复结果后续追加。当前Impl峰值8、TaskVerify峰值6、两者同时峰值8，8+8同时运行0秒，不能将最终8个Candidate reader等同于目标8+8。整批仍active。

环境失败补修影响闭包539 passed / 1 skipped；premerge sentinel53 passed。三份Verify skill内容一致。该层为确定性验证，尚不代表真实Candidate恢复或产品通过。

第二层真实补修：请求已准入但未交付provider的reader锁自阻塞，真实caller RED 1 failed/10 passed，修复后相关30 passed；新/重试briefing的“preflight修正”与“任何错误停止”冲突，生成briefing RED 1 failed/1 passed。补修后实际验收仍在进行，产品未判定通过。

第二层补修最终影响闭包936 passed / 7 skipped，sentinel53 passed，flow-smoke10 passed；未执行全量pytest/coverage，未将真实reader结束回合当作产品完成。

恢复边界补修：资源刚释放却同轮超时（RED 1 failed/11 passed，相关31 passed）；reader rerun准备异常无修复动作终态（RED 1 failed/1 passed，相关34 passed）。仅让已证明释放的资源正常复检，并将准备拒绝落入修复动作终态，不自动覆盖历史pending意图。真实产品验收尚未完成。

恢复边界补修最终影响闭包491 passed / 7 skipped，sentinel53 passed，flow-smoke10 passed。历史pending修复请求和真实Candidate闭环仍保留为待核查，不标done。

Candidate重试补修：受控rerun明确拒绝“task contract lacks task_map generation source”，实际fresh-reader caller复现1 failed/8 passed。Candidate快照复用既有currentness校验，避免把血缘锚点当作普通Task契约；保持冻结证据和新调用身份，拒绝身份/摘要/目标/Package偏移及旧Candidate。定向42 passed；影响闭包和真实恢复结果待记录，整批仍active。

Candidate重试补修最终影响闭包263 passed，定向42 passed（集合重叠），sentinel53 passed，flow-smoke10 passed。真实失败child的冻结权威只读复核无issues；这只证明允许合法准备重试，不是产品验收通过。

34e68178已在真实Run完成新reader派发，但失败结果编写暴露重复键/null PAS问题；补充精简失败JSON及结构化删除方法，移除保留所有预填字段的歧义。生成示例经真实preflight和submit两条入口验证，相关29 passed；产品仍未验收完成。

失败编写方法补修影响闭包806 passed / 1 skipped，sentinel53 passed；三份skill一致。未新增结果schema或放宽产品报告验证。

真实受控重试另有投影遗漏：旧failed aggregate掩盖新running operation，使恢复扫描跳过已关闭回合。实际控制入口RED复现；绑定原operation/run的新reader派发恢复pending，并补齐中断后重新聚合。完成/取消聚合、writer及无绑定派发不重开。定向34 passed；扩展边界及最终闭包待记录。

投影补修：定向34 passed，扩展边界23 passed；影响闭包5242 passed / 7 skipped / 1 failed。唯一失败test_consumer_lock_contention_defers_without_reporting_failure在旧H=34e68178独立复现，相关源码/测试未改，属于既存consumer Busy行为与测试预期不一致，未冒称全绿。sentinel53 passed，flow-smoke10 passed。真实恢复仍待载入补丁验证。
