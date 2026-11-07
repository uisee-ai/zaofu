# 工作记录沿当前派发绑定读取固定工作目录

> 状态: active

依据：持续监工修复授权、199；治理返工 `90a85c28` 已验证，却在 work-record publish/show 报 operation has no available pinned workdir。

## 根因与验收

1. 复现真实 request 正规化 -> verify: WorkflowOperation 将 workdir 作为物理易变字段删除；现有工作记录测试绕开正规化，错误地总提供该字段。
2. 从当前 kernel 派发及有摘要的 worker envelope 读取目录 -> verify: 同 run/task/operation/attempt/dispatch/lease/角色/代次/placement 全匹配；旧派发、外来事件、篡改/冲突目录拒绝，禁止猜路径或采用当前 shell cwd。
3. public work-record publish/show/recovery 走通 -> verify: 实际 CLI 回归及 Task Pipeline 派发 caller、文件范围/凭据/失效测试通过；不扩大完成权限，不修改 request hash 或旧 sidecar。
4. 合并并部署，原断点继续 -> verify: 保留当前治理代码与验证，观察工作记录或最终结果实际提交；记录 OA/RM 漏检及真实恢复边界。

OA/RM 此前未覆盖工作记录辅助交付失败；单测仅验证带 workdir 的手造 request，缺真实 producer→consumer 合同。

## 验证与真实进度

- 基线公开 CLI 回归 1 failed / 10 passed，精确复现无 pinned workdir。修复初次测试因 show 默认分页未包含 next_step 而断言失败，改核对该页摘要与发布摘要；实际派发 caller 的夹具空 binding 同样被新校验拒绝，补齐真实会有的绑定字段。最终聚焦 47 passed。
- 12 文件调用/恢复/事件与目录绑定影响闭包 116 passed，sentinel 53 passed，三 Flow runtime noise mock E2E 3 passed；ruff 通过。未运行全量或真实 provider 测试。
- 现场只读回放正确解析治理工作目录，记录 LS-v1 docs/records/2026-11-07-work-record-workdir-replay.json。该 operation 已自行经正常 dev.build.done evt-d0d36c4f7d55（07:54:48）提交，07:55:16 evt-db2ed31b8755 自动进入 Verify。辅助入口缺陷没有被误记成产品失败，未重开已结算操作或代 worker 发布证据。
- 尚待合并加载；之后用活动 operation 的只读授权与目录解析核验现场，不用旧 operation 的成功提交冒充补丁后的工作记录发布。
