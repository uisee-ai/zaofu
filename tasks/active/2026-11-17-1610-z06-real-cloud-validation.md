# Z06 真实云端模型与工作台联合验收

> 状态: active

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
