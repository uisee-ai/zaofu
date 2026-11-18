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

1–5 已实现并通过定向及集成回归；第 6 项已通过 mock 8+8 与宿主启动/mock provider 验证。真实模型 PRD 验收已准备但尚未启动：用户追加操作边界提醒，已询问本轮是否包含会消耗模型额度的新隔离验收，等待范围确认。

待验证项目：`/tmp/zf-eight-recovery-20261118T002529Z/product`。只完成本地源码/配置冻结和 SessionStore 初始化；无 workflow-submit、无模型任务。原实验 `/tmp/zf-eight-by-eight-20261117T151837Z` 保持停止及证据保留。

审阅补充发现并修复：仅用 Task Pipeline 校验推荐会误丢弃 Planner/Reader 的有效求助；新增真实消费入口 RED→GREEN 回归，按对应 role/operation 身份链校验。

不把 mock 容量当作真实模型吞吐，不把浏览器可启动当作产品验收。全部真实完成前不标记整批 done。

自动影响闭包：`PATH="$PWD/.venv/bin:$PATH" python scripts/dev-verify.py run --base dev` → 8088 passed / 16 skipped；premerge sentinel 53 passed；flow smoke 10 passed。另行执行的 8+8 容量/安全专项 84 passed、Reader/RM 专项 238 passed、宿主 mock 启动 1 passed；这些集合存在重叠，不合并宣称唯一用例总数。未执行完整 pytest / coverage / 真实模型业务验收。
