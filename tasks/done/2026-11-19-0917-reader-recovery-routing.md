> 状态: done

实现：`cdc1089f fix: route reader failures without consuming progress retries`。

# Reader 恢复路由与故障预算

承接 Owner 已授权的监工发现问题后修复、mock 先行及真实 8×8 再验收。基线 dev cf5073e0。
K5 `workflow-295b3ec60fdd9fdd` 完成16项任务、严格8×8重叠89.420秒、原始浏览器断言；第二Candidate 7 passed/1输入读取失败，自动第三Candidate又因已覆盖的本地Docker权限问题弃权，最终Judge/Goal未完成。已受控结束并保留证据；此前人工恢复0，收尾取消1。

## 范围与验收

1. Candidate输入读取失败不得进入预授权宿主命令恢复 -> verify: 当前HEAD隔离RED（输入失败误执行1次，runner失败对照正常）转绿；保留真实失败、既有诊断及所有授权/绑定/来源门禁。
2. 正常分页续跑不得消耗idle故障重试次数 -> verify: 真实runtime调用中分页后首次idle仍可重试，下一次真实故障预算耗尽；未知/伪造/跨操作续跑事实不能免计。
3. Reader先核对绑定执行事实，再判断缺失执行条件 -> verify: 方法明确匹配目标/命令/来源/输出，全部已有事实时继续必读与独立验收；不因本地Docker拒绝重复已覆盖命令或代填passed。三份skill保持一致。
4. caller闭包、sentinel、相关mock -> verify: 运行dev-verify计划与自动层、聚焦反例、8×8 mock；真实Provider属于下一独立资格轮，不把本批mock当成真实通过。

证据：主工作区 `reports/eight-respawn-real-20261119/reader-host-route-red.txt`、`reader-idle-budget-red.txt`、`stopped.json`；实现说明沿用 [无人持续推进](../../docs/impl/unattended-progress-recovery.md)。

不改变冻结任务合同、原始浏览器断言、人工暂停、权限或审批；不实现完整额度自动探测功能，不运行Go测试，不push。

## 实施验证

- 原始两个缺陷在cf5073e0独立RED复现，worktree合并用例2失败/2通过；修复后4通过。
- 扩展阶段首次出现5项原Task宿主回归，已将路由改动收窄到Candidate；最终聚焦63通过、2个host层用例未选。
- `dev-verify.py plan/run --base dev --tests tests/test_reader_host_route.py --tests tests/test_reader_idle_budget.py`：确定性影响闭包3954通过/8跳过，sentinel53通过，flow-smoke10通过。
- 补充Candidate绑定/Reader续跑/失败分类/8槽位mock56通过；三份skill元数据有效且逐字一致。
- idle安全反例覆盖伪造来源、错误动作、跨operation/attempt/request、角色/因果不符、未来claim、缺失claim和空身份；真实故障仍在下一次idle耗尽原预算。

真实无人8×8仍由P2单独验收，不随本源码补丁标为完成。
