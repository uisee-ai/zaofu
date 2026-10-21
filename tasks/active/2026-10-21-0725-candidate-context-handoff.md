> 状态: active

# Candidate 验证上下文与修复提交交接

来源：[设计 183](../../docs/design/183-runtime-result-authority-and-recovery-closure.md)。用户授权：继续执行剩余修复和断点验证。

1. 产品 reporter 显式接受当前 candidate reader 输入 -> verify: 原三条 Vitest 真正运行断言，收据保留 flow-discovery/candidate_verify 身份；旧 attempt、错 manifest、脏代码、冒充 task Verify 全部拒绝。
2. 修复写入隔离产品分支，再经受控新代绑定新提交 -> verify: 新 target 必须是原提交后代、ref 精确匹配，保留合同与 plan 分母；旧 snapshot 不变，错误输入不重开 Run。
3. 新 operation 独立执行原命令 -> verify: 当前 HEAD、operation/attempt/manifest 与收据一致，失败如实记录，不以动作收据代替产品通过。

Q01–Q03 外部接入仍归原 active 任务。本任务不得虚构任务 Verify 完成、业务全量完成或真实服务接入。
