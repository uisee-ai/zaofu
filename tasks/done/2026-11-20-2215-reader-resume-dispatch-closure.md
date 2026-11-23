> 状态: done

# Reader 断点准入、实际派发与已准入执行失败的恢复闭环

真实8×8恢复：7项checkpoint已准入，但实际派发改用新的dispatch_lost事件，输入生命周期按因果链拒绝；lane6还在已有一次infra retry后被旧次数拦截。lane5正式提交reader_execution_failure/abstained并settled，child-rebuild又拒绝其状态。用户授权修复后继续断点，限定隔离分支，不合入dev或push。

- 在现有周期Reader恢复入口消费精确kernel redrive + workflow.resume.planned -> verify: 真正run_once周期恢复、相同operation/request/attempt/lease，实际started绑定原决定，input dispatch匹配，重复tick不再发送。
- 已授权停止恢复不消耗旧传输故障次数 -> verify: 已有一次infra retry仍能受控恢复，且不追加dispatch_lost；没有新授权则原有bounded recovery保持。
- 准入只读失败结果可在精确child-rebuild入口重建 -> verify: SemanticResultSubmitService真实mock失败→Kernel child failed→Run Manager recommendation→controlled action→新operation，旧settled记录不变。
- 错Run、请求、attempt、Owner、决定及结果ref/内容均拒绝 -> verify: 否定回归，不能重试passed/rejected/产品失败作为执行失败。
- 真实恢复仅原6个暂停operation + 2个失败子项 -> verify: 先只读核对当前frontier，再受控执行；保持同Run、Plan、Candidate及16个Task完成证据。

基线RED：已部署c2d1c0ea的完整周期恢复2 failed（实际启动因果链不同、旧重试次数拒绝），正式失败结果重建1 failed。初次测试漏掉周期tick、旧verify fixture无完整目标身份，修正后才作为有效RED；不将测试准备问题当产品故障。补修不降低读取/结果currentness门禁，也不修改终态Run。

验证：定向222 passed；`scripts/dev-verify.py run --base c2d1c0ea --workers 4 --keep-going` 1785 passed（包含恢复/复杂Workflow mock E2E）；premerge sentinel 53 passed；`tests/test_flow_smoke_e2e.py` 3 passed。真实Provider恢复仍待部署后签收；以上不是全量pytest或无人值守通过。

实现提交：`78bf66e8 fix: carry admitted reader recovery through dispatch`。

真实8×8：22:31:11 UTC当前8/8 Reader已running且input_lifecycle=current；6项operation/request/attempt/lease保持、started绑定同一已准入决定；2项生成新operation且旧失败记录未变；没有新增dispatch_lost或业务Task派发。证据reports/eight-qualification-20261120T122929Z/reader-checkpoint-dispatch-repair-proof.json。原无人窗口未通过，最终Candidate/Judge/Goal/交付继续观察，不能据此判产品完成。
