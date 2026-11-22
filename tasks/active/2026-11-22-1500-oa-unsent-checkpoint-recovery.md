# OA 历史登录提示与未投递 checkpoint 恢复

> 状态: active — 按 Owner 持续监工、遇错修复后恢复的已有授权；承接 ec46ef3e 后真实恢复发现的问题，不改变模型、权限、预算或验收判决。

## 现场证据

Run workflow-ddd320aa0dc7c2ef 在 14:45 UTC 恢复；evt-4cbc168abb07 的
provider_interactive_blocked 阻止 OA 输入。原生 session
01a1ff1a-c3e2-7d12-83ed-e4dd9dd3a41f 在 14:52:27 UTC 对独立准备探针
回复 READY（turn 01a203be-6a00-7101-a606-3f26296efa76），旧登录错误仍在屏幕上。
这证明本次历史屏幕误判，不证明业务审核完成。普通 restart 不会重排 failed OA。
同时 transport_error_diagnostics 未保留 input_not_sent/interactive_prompt_marker。

## 范围及验收

- [x] R6 仅过滤严格 Codex 完成 footer 之前的确切历史 inline token-refresh 错误。
  step -> verify: 实际屏幕样本可发新请求；当前 auth/quota/trust/permission 提示均保留。
- [x] R7 原 checkpoint 未发送输入时的有界恢复，保留结构化传输诊断。
  step -> verify: 当前 failed operation/dispatch/Run/请求绑定一致，外部门解除后最多一次
  原身份重试；暂停、终止、已发送/未知发送、失效绑定、重复 tick/丢 wake 均不得重复执行。

记录至 docs/impl/eight-by-eight-recovery-closure.md；隔离 provider mock 与直接调用验证，
sentinel、相关 OA mock E2E 后提交，再部署真实断点验证。原无人窗口保持 closed_failed。

## 实现验证

新增34项回归通过；直接caller和OA mock E2E158通过（重叠不相加）。最终精确路径的
`dev-verify.py plan/run --base dev --paths ... --workers 4` 影响闭包4916通过/8跳过；
sentinel53通过；flow mock10通过。均清除继承ZF_*环境并固定本工作树PYTHONPATH。
命令、计划、回执、日志：`/tmp/zf-oa-unsent-final-{command,plan,receipt}.json` 和
`/tmp/zf-oa-unsent-final-closure.log`，同时保存到实验reports目录。非全量pytest，
provider/host为隔离mock；真实OA准备探针证据与正式checkpoint观察分开保存。

cdd-1:1只读审查提出完整config、Candidate适用性及并发领取问题，均已修复；
最终审查无新的确定缺口。现场只读资格验证已确认原失败evt-4cbc168abb07仍绑定当前
输入，修复后的屏幕检查返回无阻塞；实现提交后再部署并记录真实后继。
