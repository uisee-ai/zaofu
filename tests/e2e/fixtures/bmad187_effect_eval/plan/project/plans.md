# P-good：Phase-1 草稿

Epic：E-read 查询、E-transfer 原子转移。未来 E-notify 只有来源、预期结果、O2 决定 owner/时点，保留在全局规划中，不列为已完成或本期 Task。
contract：定义共享 TransferRequest 与返回字段 transfer_id；独占 contracts/transfer.py、tests/test_contract.py。
read：依赖 contract；独占 src/query.py、tests/test_query.py；真实读取入口与错误行为测试。
transfer：依赖 contract；独占 src/transfer.py、tests/test_transfer.py；同一 owner 实现扣源、增目标、流水及失败回滚测试。
integration：依赖 read、transfer；独占 tests/test_product_journey.py；真实入口验证查询→转移→查询、拒绝及失败后态。
测试服务采用每任务独立数据库命名空间和动态端口，候选验证只用最终集成树。
每片 Impl 自检；独立 Verify 沿当前 stage_barrier 执行。未来通知输入继续保存在 shared 规划范围。

# P-risk：另一份候选草稿

共享 contract 仍定义 transfer_id，但 transfer 不写 blocked_by；作者说“同在 wave 2，看名字知道接口已准备”。
read 和 transfer 都独占 src/app.py；作者安排 wave 2/3，认为先后执行就不会违反全图唯一所有权。
transfer 改为 debit、credit、ledger 三个任务，各任务各自提交数据库，ledger 在扣减任务之后执行。
read 与单独 reporting 同层、路径不同，因此允许同时跑测试；仍用 scope.md 的固定端口和同一数据库 fixture。
作者把全部验证命令标为 task_verify，承诺每片 Impl 完成后立即得到独立 Verify；不修改配置。
通知仍按 O1/O2 留在未来 Epic，不删总目标；这一点与 P-good 相同。
