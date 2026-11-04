# Mock Channel E2E 隔离宿主 Provider 认证

> 状态: done
> 实现: `f13fa274 fix: preserve artifact values across resumed reads`

## 复现与验收

在 dev 基线和上一轮分支，`tests/e2e/test_channel_group_product_mock_e2e.py::
 test_channel_prd_evaluation_task_workflow_run_and_receipt_continuation` 因本机
Claude 未登录失败，尽管测试使用 mock backend。

1. 重新在当前基线复现 -> verify: 错误发生在 mock 流程的宿主认证探测。
2. 隔离该测试的外部探测 -> verify: 无 Provider 凭据时模拟场景通过，真实
   Provider/宿主资格测试仍按原合同运行，不改变生产认证策略。
3. 运行受影响 Channel E2E 和 mock preflight 邻近回归 -> verify: 通过。

## 验证记录

在未登录 Claude 的宿主先复现原失败；隔离对应模拟用例的认证探测后，
Channel mock E2E 与 provider preflight 邻近回归共 32 项通过。
