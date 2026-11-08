> 状态: active

# 修复漂移巡检复制完整日事件的开销

依据 LS-v1 2026-11-08 drift-refresh-profile：实际刷新输入方法三次读取49320事件、仅使用尾50，profile8.029秒，read_days7.958秒。周期入口已接线，不能将历史drift事件错误替代全事件尾部。

- [x] EventLog限定返回尾数且先选后复制 -> verify: cold/warm/append/partial/rotation、多日全序、payload隔离、签名损坏与零/负参数回归。
- [x] drift与refresh实际调用入口使用尾50 -> verify: 旧drift落出窗口、新目标角色仍生效、只复制尾部且检测输入完整。
- [x] 冻结生产事件完整正文/顺序、实际刷新结果一致 -> verify: 只读回放记录；局部性能与整个Watcher开销区分。
- [ ] 文档、闭包测试/哨兵/mock E2E、提交/合并/受控加载与记录 -> verify: 保持在途operation/attempt/workdir，工作树收尾，保留主目录原修改。

作用域：EventLog读投影、LifecycleObservation的两个尾50调用及其测试/说明。无窗口放宽、无canonical状态直写、无产品验收改动。开发授权来自持续监工修复请求；全套pytest/真实provider不默认执行。

验证：129项定向闭包、53项哨兵、10项mock flow smoke通过。开发脚本plan因EventLog反向闭包799文件建议全套，按AGENTS采用本次两个模块及直接调用/事件共享合同的明确闭包；自动run显式skip deterministic/serial，运行contract层，mock E2E另行运行。没有full pytest或真实provider声明。

冻结49368事件对照：尾50完整正文/顺序一致，旧/新实际刷新方法输出一致（无刷新目标）；三次无profiler调用3.268014秒→0.001368秒，仅证明该入口。初次回归4RED；一次新增测试actor None/空字符串预期修正，另一次误填测试文件名未收集已纠正，均为开发测试问题。

证据在LS-v1 docs/records/2026-11-08-drift-refresh-profile/。待受控加载验证后归档；不声明整个Watcher CPU已解决。
