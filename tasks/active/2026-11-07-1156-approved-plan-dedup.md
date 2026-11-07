# 已批准计划的重复触发不重铸审批

> 状态: active — 用户授权持续监工发现问题后修复。

现场FMS13原请求evt-77a685e597eb在evt-f0ce88ea38e4已批准；同包/相同v2指纹的重复ready先被suppressed，随后又生成evt-b8622460aa29审批请求。实际任务已派发，但idle投影误以为还等审批。仅修有确切审批身份的同内容重复，不推断历史不同指纹请求过期。

1. 真实审批入口重复触发 -> verify: 批准后重复请求先红；不重新执行、不产生自动批准。
2. 共享归约同Run/Stage/PDD及完整v2指纹的owner已批准等价请求 -> verify: 旧已重铸请求从pending投影退出；不同身份/语义审批/拒绝/旧短指纹仍须审核。
3. Runtime idle与CLI pending采用同一归约 -> verify: 实际调用入口与边界测试、sentinel、mock E2E。
4. 合并dev并保留worker重载 -> verify: 现场duplicate pending消失，原业务attempt继续，原事件不删除。

关联：[199实现](../../docs/impl/199-worker-recovery-implementation.md)。

验证：3项真实复现先红，12项拒绝用例原通过；新增worker身份拒绝后16项本轮回归通过，初步39项关联、13文件闭包287、writer实际入口6（156 deselected）、Web关联共19通过；sentinel53、flow mock10。dev-verify静态推导357文件触发全量，本轮按审批/CLI/Web/受控动作/RM/idle直接调用闭包运行；未跑全量pytest/真实Provider。实际重复请求只读回放与部署待继续。
