> 状态: done

实现提交：`a8f7163b fix: bound replan inputs and admit reader execution failures`。

# 有界返修输入与诚实失败交接

用户已授权持续监工、修复并合并 dev。DL Planner 的必读返修上下文为 1,019,911 字符，单页 8192 至少需 125 次读取，超过本轮 60 次工具往返合同。诚实失败提交又被 required_read_missing 拒绝。保留所有任务、AC、状态、完整快照和原预算，不伪造读取或成功结论。

- 对显式受影响任务编译可消费上下文 -> verify: 93 任务回归保留全量索引、摘要绑定完整合同、受影响任务正文，必读体积不随未受影响合同正文膨胀。
- 接收 reader 执行失败 -> verify: 受控提交产生 failed/abstained、不完整读取账本；成功及语义拒绝仍要求完整必读，非法身份/账本仍拒绝。
- 实际接线 -> verify: Plan handoff 与 SemanticResultSubmitService 调用测试、影响闭包、sentinel 和确定性 mock E2E。

实现已完成：93 任务完整合同独立封存，范围索引与受影响任务正文必读；reader 执行失败封存真实/空账本并标记 incomplete。真实 DL 输入 1,019,911 → 101,177 字符（最少 125 → 13 个字符分页），全量快照保留 858,358 bytes。原 Provider 拒绝操作不重试，旧结果不改写。

验证：基线 2 failed / 2 passed；扩展专项与共享合同 183 passed，调用方/恢复/真实内核 mock 347 passed，最后修订 9 passed。自动计划选择 222 文件触发全量回退，按仓库普通修复规则跳过该 deterministic tier，以上 29 文件为显式影响闭包；自动 serial 10 passed、sentinel 51 passed、flow mock 10 passed。未运行全量 pytest 或真实 Provider E2E。两次验证命令发现不存在的测试路径即停止，修正为实际测试文件后通过，未跳过已发现失败。
