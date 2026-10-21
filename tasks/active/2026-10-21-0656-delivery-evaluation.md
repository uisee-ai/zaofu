# 整体交付改进的分层配对验证

> 状态: active

用户已批准按六步方法执行。A固定5191c9e6，B固定4b0a96e0；验证工具独立分支，不把新工具提交混入候选。此前负结果全部保留。

- [ ] 冻结协议、输入、独立oracle、预算、顺序与真实加载版本。step -> verify: 首次provider调用前有摘要和固定判据。
- [x] 机制回归。step -> verify: 直接caller、状态/收据/输入完整性闭包通过，精确记录层级。59 passed；工具/指令增补后15 passed（重叠不相加），自动层的既有CI文件缺失在baseline复现。
- [ ] cargo-mini首轮完整配对交付。step -> verify: 两组实际Scan/Plan/Critic/Impl/Verify、冷构建、外部API与Docker浏览器；失败也记成本，不能用生成文件替代。
- [ ] 库存与发布迁移探针。step -> verify: 首对通过后按固定顺序运行；正式确认批次依据pilot波动预注册，不反复抽样挑赢家。
- [ ] 完整cargo对照。step -> verify: 小产品门通过、原Plan责任闭合及人工批准、真实输入可用后，从相同产品基线分别全量交付。
- [ ] 汇总与归档。step -> verify: 质量/完成率/总成本/耗时/人工介入共同判定；UNKNOWN不写pass，任务未闭合不标done。

协议和证据见 docs/impl/delivery-evaluation.md。不修改原dev、8001、原cargo与当前待批Plan。

真实pilot已启动：`/tmp/zf-delivery-eval-20261021T0718Z`；两路Scan已完成，候选Planner进行中。
前两批预检/transport配置失败均保留，不作产品失败或优化收益；源代码候选仍为4b0a96e0。
金额计价UNKNOWN，完整交付未完成，因此本任务继续active。
