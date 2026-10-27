# 另一候选计划（独立版本，待审，未实施）
来源同 requirement.md。有效路由事实已冻结：task-pipeline，可复用 Impl/Task Verify 各3槽；任务数不限于3。每个任务经独立 Task Verify 并准入集成后才解锁 blocked_by 后继。下列摘录供语义评审，省略的机器字段由当前已通过结构校验的正式 Task Map 保留；不要把摘录当成可准入完整JSON。7个任务按有向依赖执行。
B0：脚手架和通用扩展加载器，blocked_by=[]，独占 app/main.py, app/extensions.py, tests/test_boot.py。最小健康入口可运行；后续扩展各自独占文件，加载器已证明会自动加载它们。
B1：授权谓词，blocked_by=[B0]，独占 auth.py, tests/test_auth.py。只验证操作员/审核员/无权限主体对应predicate，不声称业务状态改变。
B2：预约创建和读取（R1创建、R3读取），blocked_by=[B1]，独占 booking.py, app/extensions/booking.py, ui/booking.ts, tests/test_booking.py, tests/booking_smoke.spec.ts。本片真实API和浏览器只创建/读取，C确认仍未出现；独立数据库、端口、账号夹具，实际入口已由B0接线，局部 smoke owner=task_verify。
B3：确认（R1审核、R2、R4），blocked_by=[B2]，独占 confirm.py, app/extensions/confirm.py, tests/test_confirm.py。原子确认同时改变预约和资源，包含最小审核员无竞争成功；竞争事务撤权或抢占提交后，本请求拒绝，不新增占用，不改变竞争事务已提交状态；同键重复确认不重复占用。合法竞争提交后的状态为失败不变基准。
B4：确认UI（R3变更），blocked_by=[B3]，独占 ui/confirm.ts, tests/confirm_smoke.spec.ts。本片真实浏览器确认和失败提示已具备全部依赖，task_verify验证本片，最终全旅程另列。B2和B4不写同一UI文件。
B5：通知决定登记（R5），blocked_by=[B0]，execution_mode=external_gate。Q1为待产品负责人确认的条件扩展，required_manual_evidence明确通知开关决定，选择不启用则记录原因；没有伪造通过。B1-B4可继续，不依赖通知决定；通知接入必须等Q1关闭，未启用不能声称通知运行已验收。
B6：阶段集成与最终验证设计，blocked_by=[B4,B5]，独立 logical owner=assembly，root_owner_class=assembly，独占 tests/test_assembly.py, tests/complete_journey.spec.ts, tests/evidence/check_candidate.py。没有重写B0入口。局部task_verify检查各入口注册和失败错误传递，候选candidate_verify覆验R1-R4完整旅程，R5保持所选条件及证据边界。若未来选择启用通知而无实现，通过受控修订补任务，不能越过未实现行为。
所有任务各自拥有生产与测试文件，验证命令逐项绑定本片producer和相同owner/tier；最终candidate命令不是当前片完成条件，完整源码路径/命令字段存在于正式Task Map。
本版独立从原始输入枚举 source_index.traceability=[R1,R2,R3,R4,R5]，原任务来源仍在source_index.tasks。coverage_ledger.entries记录R1-R4 covered并映射上述生产者、AC、测试，R5 pending_decision/NOT_RUN。两个逻辑端口及execution_spec、source_inventory、acceptance_matrix、test_matrix均以ref+sha256显式绑定同一Plan Package及required_plan_ports。source_inventory.pages=[预约页]，forms=[创建表单,确认表单]，items只列输入文件。未宣称运行验收通过。完整来源R1-R5保留，不提供支付。
