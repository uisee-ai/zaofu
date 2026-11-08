# Work record 使用当前派发 placement

> 状态: done — `dd9537d9` fix: validate work records against current placement

授权：用户要求持续监工、修复阻塞并记录。现场FMS15 operation已在placement 7执行，原不可变请求仍是placement 1；`artifact work-record publish`两次拒绝 dispatch identity mismatch。只读复现 `_operation_workdir`：run/task/operation/dispatch/lease/role/attempt与当前kernel派发一致，唯一被旧request覆盖的预期字段是placement_epoch=1，实际operation及dispatch=7。

依据：[199](../docs/design/199-worker-failure-recovery-and-role-handoff.md)、[工作记录](../src/zf/runtime/work_record.py)。保留不可变原请求、当前lease/manifest/Task/角色校验和完整envelope；不能相信worker声称的placement。

- 同operation重新placement后发布 -> verify: 公共CLI消费事件归约的当前placement，发布/展示/幂等回放通过，原request字节不变。
- 旧placement、未知/不匹配sent来源 -> verify: fail closed，不生成work record。
- 合并部署并由原worker自然重试 -> verify: 原operation/attempt不变，实际publication有绑定证据；没有真实发布不能称现场修复闭环。

验证：新增6回归全部先RED（合法p7被拒、5个旧/未证明placement错误通过），修复后work-record+operation定向45 PASS；dev-verify plan/run 6文件82 PASS；redrive race与design199 mock3 PASS；哨兵53 PASS。现场同一operation只读目录定位 baseline拒绝、fixed定位原工作区，原请求不改、未冒充worker发布。首次定向命令误填不存在的test_workflow_operation_redrive.py，pytest未收集，已使用实际redrive文件重跑通过。

实现说明：[work-record-current-placement](../docs/impl/work-record-current-placement.md)。
