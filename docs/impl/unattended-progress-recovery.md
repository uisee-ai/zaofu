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
