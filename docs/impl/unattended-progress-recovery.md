# 持续推进、恢复效果与无人资格

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
新实验 `/tmp/zf-unattended-eight-20261118T102607Z` 已准备16个独立 TypeScript 合同和8+8池，待补充修复提交后启动。
Plan 必须审阅批准；批准后冻结 config/合同/源码/授权，观察者只读。
任何追加提示、补采证、状态修复或加载补丁都记为本轮介入，不能回填无人资格。
分别报告业务 Goal、真实 Impl/TaskVerify 峰值与重叠、故障自动恢复和介入次数；配置8不等于达到8。
最终仍须真实 Candidate/Judge/交付证据及清理后才能归档 P2。
