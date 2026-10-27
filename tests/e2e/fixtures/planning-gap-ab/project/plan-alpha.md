# 候选计划（待审，未实现）
有效路由：task pipeline，Impl 后独立 task_verify，通过集成后解锁 blocked_by 后继；Impl/Verify 各8槽。wave 仅显示层级，不能提供解锁约束。
S：脚手架、接口注册、真实服务入口及最小读取 smoke，wave1，blocked_by=[]，拥有 app/main.py 与 tests/test_boot.py。
A：权限predicate片，wave2，blocked_by=[S]，拥有 auth.py/tests/test_auth.py。本片验证“仅操作员可创建预约，审核员可确认，拒绝后预约和资源不变”；recipe先调用 B 创建预约，再调用 C 确认。
B：预约创建/读取及局部测试，wave3，blocked_by=[A]，拥有 booking.py/tests/test_booking.py。包括本片真实API smoke，R3读取路径已通过S入口可运行。
C：确认预约原子命令及权限/占用/幂等测试，wave4，blocked_by=[A]，拥有 confirm.py/tests/test_confirm.py。测试setup先调用 B 创建预约。B的wave早于C，故认为可以用它。
C的撤权竞态用例：Given仅有操作员权限、空闲资源、已有待确认预约，When在确认屏障暂停、另一个事务撤销审核权限，然后继续，Then确认拒绝且所有状态等于竞争事务之前。计划没有无竞争成功对照。
Z：最终装配，wave5，blocked_by=[B,C]，拥有 app/extensions/booking.py、tests/test_wiring.py。变更：将确认处理器注册到真实入口。唯一AC为最终浏览器全旅程通过；唯一命令owner=candidate_verify，Task Verify无本地断言。
R4在C有有效幂等对照（成功两次仅一次占用）；R5在Q1登记为pending_decision，由产品负责人确认，关闭证据为通知开关决定，enter_before=通知接入，C/B不依赖该决定。
artifact_refs=['source_index.json','coverage.json','source_inventory.json']。
plan_ports只绑定task_map、source_index、source_inventory、execution_spec、acceptance_matrix、test_matrix。
source_index.json内容：{"tasks":[{"task_id":"A","source_ref":"requirement.md#R1"},{"task_id":"C","source_ref":"requirement.md#R2"}]}。
coverage.json内容：{"rows":[{"source_id":"R1","disposition":"covered"},{"source_id":"R2","disposition":"covered"},{"source_id":"R3","disposition":"covered"},{"source_id":"R4","disposition":"covered"},{"source_id":"R5","disposition":"pending_decision"}]}。
source_inventory.json内容：{"items":[{"path":"requirement.md"}]}，作者称Preview有一张页面，来源100%覆盖，产品未执行。
