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
