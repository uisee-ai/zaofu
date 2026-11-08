# 操作请求扫描只复制相关事件

> 状态: active

授权：持续监工LS-v1、修复阻塞并记录。实际只读剖析：同一worker-action处理器三次调用8.83秒，其中7.07秒复制事件正文；约48060条当日事件、无未处理请求。不是整个Watcher的剖析，不能据此宣称已定位全部CPU开销。

- 在当前dev复现按类型读取缺失 -> verify: 新增读取合同测试先RED，真实数据profile保存。
- EventLog按需复制请求/结果，保持原时间窗口、顺序、完整payload、缓存隔离和签名校验 -> verify: 冷/热读取、追加、半行、轮转、不同过滤集合、签名篡改及无过滤调用兼容。
- 实际worker-action入口消费该读取接口 -> verify: 队列幂等、重放、失败重试及回复绑定测试，相关Event/Store/调用方闭包、哨兵、mock E2E。
- 只读生产数据对比原/新请求结果序列 -> verify: 逐事件内容一致、报告测得耗时，不将局部收益等同整个循环修复。
- 合并dev、受控重载并保留在途身份 -> verify: operation/attempt/dispatch/pane不变，健康检查通过，记录上线和剩余问题；无push。

实现：为read_days增加可选事件类型选择；AgentView队列重建和每轮补偿扫描实际接入。全部事件继续解码/校验并保留在缓存，只有相关payload复制给调用方，完整性诊断不会被过滤。

验证：新增合同3 RED；最终135项EventLog/签名/半行轮转/Store/实际队列及Orchestrator调用闭包通过，自动哨兵53通过，三流mock E2E10通过。dev-verify plan因EventLog反向导入扩大至816文件全量；依据AGENTS本次两域有界行为闭包执行，run显式跳过broad deterministic与serial自动层，定向closure包含State锁测试，不声称全量或真实Provider E2E。

生产快照只读对照：48145条事件选中182条，请求/结果逐事件内容和未处理请求序列均一致；预热后三调用旧2.5286秒、新0.01948秒。原cProfile测量包含额外采样开销，不能混为同一对照。整体Watcher延迟仍待上线观察。
