# Z06 真实云端模型与工作台联合验收

> 状态: active

## cwd 验证检查点

真实独立 tmux 探针证明 gVisor 的 pane_current_path 为空、进程 cwd 可读；新增带
所有权和进程身份检查的回退。派发前检查前移，避免尚未发送输入的错误产生预算
义务。142 passed、3 deselected，sentinel 53 passed；未代替完整 Workflow 验收。
旧失败的 131,072 未知预留保留，需通过受控且可审计的纠正流程处理。

## Q01c 默认 Workflow 修复检查点

真实 PRD v5 已受理并进入 scan fanout，但 gVisor tmux 前台误判导致反复启动失败。
已通过产品 stop 暂停 Charlie 实例，保留原 Workflow/PVC；修复前台身份判定、取消时
前台进程组终止、动态角色纳管合同、角色独立原生会话目录与逐轮结算。未伪造 Workflow
完成事件，部署后从同一请求恢复验证。

回归先证明原 caller 动态 resident 角色遭拒，再修复；初始影响闭包 277 passed、
1 项 10ms 时钟敏感测试失败（独立重跑通过）。该测试改为可控时钟，保留实际错误类别
断言，最终结果后续补录。新增原生日志/实际 observer 测试与三流 mock smoke 17 passed；
sentinel 53 passed。这些结果不替代完整真实 Provider Workflow。

最终改动定向闭包（tmux lifecycle、角色准入、native observer、Terminal usage、三流
mock smoke）38 passed；更新后的 sentinel 53 passed。完整真实默认 Workflow 仍在复验。

`ea3de378 fix: bind managed TUI turns to native execution evidence` 已部署；
`native-turn-adopt-1` 正常 MFA 接纳成功。新 Pod CA 包 SHA256 与退役 A 后配置一致。
恢复原请求被旧 Project lease starting 义务阻止，未执行 Provider；继续补双端权威
租约终态查询和本地条件恢复，不通过手工改状态消除阻塞。

租约恢复最终影响闭包 68 passed，含 CLI、Controller command caller、Workspace 与
三流 mock smoke；sentinel 53 passed。Controller 对应真实 PostgreSQL/API 48 passed。

来源：Owner 要求完成剩余 1–8 真实验证；承接 Z05，联动 Controller Q01c。基线 7b32668f，旧回归结果不冒充真实 Provider。

1. 已审核 Workspace 中调用真实 Provider -> verify: 模型确实执行小型任务、产物可复核，并通过现有受管执行入口上报 Session/Operation/usage。
2. 额度与恢复 -> verify: 真实 token 对账、额度耗尽拒绝、取消及未知责任保留；重建不改变 origin 或重复记账。
3. 用户界面 -> verify: 真实默认 Workflow/预览/终端/下载经 Gateway 授权，无本地免鉴权退路。
4. 问题纠正 -> verify: 最小修复、真实 caller 回归、更新 spec、重新部署真实复验；遗留项和外部依赖明确记录。

使用独立 worktree，保留 dev 及其他会话修改；Provider 凭据只在授权隔离环境临时使用，不能进镜像、源码或证据。用户未指定容量目标时执行测量并如实报告，不虚构 SLO。

## 2026-11-17 修复检查点

- 真实坏策略复现：Controller 接受 batch_size=100，Runtime 拒绝后无新心跳，正常策略
  更正被 freshness gate 拒绝。新增拒绝 ACK 后最小存活通道，原策略/执行拒绝保持。
- 定向 86 passed；dev-verify 相对本工作分支基线自动影响闭包 333 passed；pre-merge
  sentinel 53 passed。三个集合有重叠，不相加作为唯一测试数。
- claw02 新镜像已构建，真实坏策略恢复、Provider 推理与完整 Workflow 尚待后续证据。
- 原任务保持 active；本检查点不代表 1–8 或完整云端资格完成。
- `recovery-2` 进一步复现首次同步拒绝导致服务重启循环；补 `serve` 对已验证策略
  未应用的受限恢复，身份错误仍拒绝监听。启动聚焦 21 passed、直接调用闭包 68 passed，
  不把这些回归计为真实模型或完整 Workflow 证明。
- 真实 `recovery-4`、`policy-3` 通过；`provider-smoke-6` 完成 gVisor Pod Codex
  订阅模型调用，`provider-observability-1` 核对 23,413 tokens、额度、Session/Operation、
  排行与 Dashboard。失败模型用量继续 unknown，不能代表实际费用或完整 Workflow。
- 发现公开会话与内部线程身份分叉，导致重复 Session/停止 fence 失配；回归先红后绿，
  直接影响闭包 200 passed。新实现仍待部署后真实会话停止复验，任务继续 active。
- `session-upgrade-2`：正常 stop/MFA adoption/start 部署会话修复；旧 PVC 保留。
- `budget-real-1`：真实预算耗尽在 Provider 启动前拒绝，但暴露 Web 失败事件制造
  pending 用量的缺陷；新增明确调用前拒绝证据，不能与启动后用量未知混淆。
  定向 20 项、直接影响闭包 217 项及 sentinel 53 项通过；真实重跑继续执行。
- 资格脚本增强：从 containerd 的 manifest target 取摘要并核对 OCI 内容及 Pod
  imageID 的解析关系；Docker 29 的 image ID 在本环境实际是 manifest digest，
  不能按旧版行为误判为 config digest。保留归档摘要与运行源码哈希作交叉证据。

- `budget-bob-1` 在干净租户真实复验调用前额度拒绝：用量条目和预留均无增量。
  额度恢复保留已用量，真实调用成功；公开 Session 唯一。Controller session.stop
  在约 10.5 秒终止 Codex 启动的 sleep 90，后续调用被 fence 拒绝，显式 resume 后成功。
  取消/恢复原生计量边界不足，最终 46,358 tokens 已知、65,536 预留未结；不可将
  未知部分视为零或借重置额度清除。完整 Workflow 另在无旧责任的批准租户验证。

## 部分用量与历史修订检查点

Run Manager 419,492 已知 tokens 的停止缺少原生完成边界，却被旧逻辑标完整；
原 Workflow 已通过 `zf runs pause` 暂停并保留。修复 tmux、Web Terminal、headless
完整性判断，并增加限定为降低完整性的 `controller usage-review` CLI，追加 revision
而不修改旧 ACK/已知消费。回归发现并修正旧 settle ACK 时序与受管 CLI 配置问题。
原生/Terminal 闭包 133 passed；其余新闭包 90 passed，CLI/Workspace 修复复验
38 passed；sentinel 53 passed。仍须部署后实际纠正两条中断收据和复核冻结，整批 active。

## 实际历史纠正及复盘

`fd5f93c6 fix: retain incomplete native usage and append audited corrections` 已在 Charlie
正常 stop/MFA adoption/start 部署，1,465 个实际 import 源码文件摘要一致。
两条 revision 2 在旧 handoff 上限 20 被正确阻止；审核精确纠正与旧 writer fence，
通过 Controller 运维 CLI 将相同 uploader/origin/stream 上限调整到 22 后均 ACK。
`delta-delete-3` 再核对已知 1,976,314 tokens、两条 partial、null 完整总量/账单和冻结。
原始收据、已知消费与未结责任未删除，任务仍 active。

完整 Workflow 夹具为 800m/2 GiB/2 agents、单调用 600 秒；Runtime 默认期限是
3,600 秒，不能将夹具配置误报为产品默认。Run Manager 与 product scan 占满槽位，
tech scan 容量准入拒绝；首轮工具只输出 stdout，缺少执行状态用于诊断。第二轮
工具 `wait` 和受控 artifact read 成功记录确实存在，不能归因于未等待或文件始终
不可读；十几至几十秒的多次读取和预算期限使执行无法完成。保留原 Run，不新建
替代请求制造通过。后续须按默认角色集合配置资源/期限、处理未知责任并完整恢复验收。

`bf5f3837 fix: bind terminal completion to final native counter snapshot` 的 35 项定向
回归通过，`terminal-final-adopt-1` 正常 MFA 更新已完成，实际 1,465 源码摘要一致。
运行镜像 `sha256:52261bbcc8eb42414a04e06340676259f5ed847955b3290841b88459051d41d7`；
真实完整 Workflow 仍未通过，不以源码核对或镜像就绪替代产品验收。

收尾通过产品接口停止三个实验实例，清除 7 份临时 Codex auth，保留宿主原文件、
原 Workflow/PVC 和未知消费。原 standalone 8002=200。脱敏证据及 677 个文件摘要
校验位于 `/home/min/workspace/.verification-archives/zaofu-controller-q01c-claw02-20261117/`。
最终 `dev-verify plan --base dev` 保守扩展到 632 个测试文件及 Web；没有据文件数量
再触发全套。各修复的实际 caller/共享合同闭包和 sentinel 证据按检查点记录，不将
聚焦测试或 Pod ready 解释为完整 Provider Workflow 通过。本任务继续 active。
