> 状态: active

# 控制面空转中的终态结算与暂停判断

持续监工授权。dev5e9b2d47真实176827事件/287 attempts/358已settled operations只读profile：0新结算却遍历全账本259次，9.627s（profiled）；暂停布尔判断生成整份展示投影0.917s。原Watcher采样也定位到这两个热点。证据LS-v1 docs/records/2026-11-09-idle-predicates-profile/。

- [x] 先复现终态历史和暂停判断额外工作 -> verify: 实际入口和最小定向回归RED，保留真实profile。
- [x] 仅跳过不可变终态候选的结算扫描、暂停只查最新意图 -> verify: shadow lease expiry仍可修复；活动/旧新dispatch、归属、结果准入不变；六类暂停/恢复按账本顺序，空输入/磁盘读取保持。
- [x] 冻结真实输入比较和影响闭包 -> verify: 逐项输出一致；不写真实TaskStore/EventLog；focused/sentinel/mock通过。
- [ ] 提交合并重载并记录 -> verify: 真实FMS24 Impl与OA operation/attempt/pane/PID保持，继续监工，不声称整体CPU已解决。

不引入缓存、不截断账本、不改timeout/租约/终态、权限、事件或暂停规则；不push。

最小测试5 RED/4 GREEN，实施后75项首轮、298项调用/事件/暂停/Attempt闭包、53sentinel、6恢复mock通过。包含较新终态dispatch不得误结算旧活动attempt及shadow租约恢复。完整176888事件/287attempts冻结视图的358个结算输出和pause结果逐项一致，全账本扫描259→0；无profiler结算2.657→0.004s、暂停0.518→0.038s。dev-verify因185反向映射文件数量建议full，按AGENTS明确跳过自动deterministic/serial并运行领域闭包，不声明full。
