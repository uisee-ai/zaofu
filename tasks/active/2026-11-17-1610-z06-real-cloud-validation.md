# Z06 真实云端模型与工作台联合验收

> 状态: active

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
