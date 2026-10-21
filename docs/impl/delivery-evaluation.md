# 整体交付配对验证协议

用户批准按六步方法执行；任务见[执行记录](../../tasks/active/2026-10-21-0656-delivery-evaluation.md)。
此前结论与负结果见[原评估记录](real-plan-ab-full-build.md)，不由新试验覆盖。

## 不可变比较对象

A=`5191c9e6`，B=`4b0a96e0`；评估工具在独立worktree实现。两组同一产品种子提交、
PRD、模型gpt-6-astra/medium、单lane的PrdFlow fanout、预算、自动交付审批策略、
独立Critic/Verify和外部黑盒验收。原cargo人工Plan审批不变。
两组均使用显式self-check、maxAC=0，因此本批比较代码/skill差异，不估计maxAC配置变更收益。

首轮是小产品pilot，单位为一份完整PRD的一次交付；AC不是独立样本。固定首对cargo-mini，
两组都运行（第一组失败也不取消另一组）；两组目标完成且外部验收均通过才继续库存、发布。
AB/BA顺序使用固定种子生成并在首次调用前冻结。每个arm最多2400秒，run最多1000万token，
单操作600秒/150万token，语义rework按配置2次，不手工代写产品。预算上限不代表预算必耗尽。

全部mandatory独立验收、无新增严重缺陷和错误通过是硬门；完成率不得下降。
总金额成本比≤0.80、耗时比≤1.10、人工干预不增加为联合目标。金额必须有可追溯计价；
未知模型价格不算免费，计价缺失时金额门UNKNOWN，不拿输入token替代。报告失败、返工、
缓存、输出以及人工时间。估计型安全预算不是已证实账单。

## 证据路径与执行

`scripts/eval-delivery.py prepare --root /tmp/zf-delivery-eval-<UTC>/` 创建新输出，冻结
registration、各PRD、oracle、镜像和两个干净版本，两个arm克隆同一产品种子。
`run-arm --root ... --run-id cargo-mini-baseline` 通过真实 `zf init`、intake、clarify、
submit、start调用生产入口；不拼接假task事件或写canonical状态。status只读事件。

`tests/e2e/delivery_acceptance.py` 在目标提交的干净克隆执行npm ci/test/build、真实HTTP、
故障与重启、Docker Playwright；判定与对应原始日志归档。oracle置于产品外；host provider
不是安全沙箱，必须核对trace是否越界读取，不能把路径分开说成强制隔离。规划/假脚本退出0、
只有task.done或文件存在均不能替代产品通过。

机制层复用最近79项定向通过收据，并验证本轮评估器的冻结/失败分类边界。正式统计确认批次
在pilot后根据波动另行预注册；此轮最多三对，不能宣称显著性。发布是不同业务迁移探针，
不是由独立第三方保密的holdout。任何中途候选更改必须新批次；不得挑选有利重试。

## 完整cargo前置

完整cargo需先闭合Plan责任、保留人工审批并取得真实数据/GT/设备/仿真/业务决定。
小产品绿色只能允许扩大验证，不代表149要求/36指标已交付。该阶段需从相同产品基线
分别创建A/B运行；不能拿已有B规划结果与A从零启动直接比总时间。

运行结束/诊断完成后通过sanctioned CLI记录simulation.done并停止本轮tmux/provider，
复制必要证据到workspace evidence目录后清理自有临时目录。不得清理其他会话或8001。

## 2026-10-21 实际启动记录

- `...T0705Z`：两组在provider调用前同样触发目标app目录不存在的预检异常，保留
  preflight-failure.json；种子补空app/.gitkeep，无业务实现。
- `...T0710Z`：两组同样暴露Codex不能使用当前Claude SDK stream-json路径；无完成
  provider turn。候选额外有一次评估runner误用嵌套目录的 `start --simulation`，已记录
  operator-intervention.json且不重置计时。该批不能用于效果比较，两个arm均停止。
- 当前批 `/tmp/zf-delivery-eval-20261021T0718Z` 使用两组相同Codex/tmux；顺序为
  cargo-mini B/A、warehouse A/B、publishing B/A。registration摘要为
  `35d35c235fd5e417c25a85021d140e218e91e0798ea70e7b3b9343b92fd91118`。
  候选真实两路Scan已完成并进入Planner，尚无完整产品通过结论。
- 固定Docker镜像有完整Chromium但无headless-shell。外部浏览器运行器只增加
  `channel: chromium`，在首次产品验收前对两组一致修正；原oracle与兼容性修订摘要
  分别保留在oracle/和oracle-runner-v2/，oracle-runner-amendment.json说明不改变
  行为断言、API oracle、阈值或镜像。浏览器环境预检通过，不算产品证据。
- 新工具/相关机制闭包59 passed；增补计价、账本损坏和state_dir边界后工具/指令15 passed。
  两者有重叠，不合计成独立测试总数。dev-verify自动层29 passed/1 failed：缺少
  `.gitlab-ci.yml`，独立baseline复现；不伪造CI文件、不改测试预期。
- 真账本为subscription/unpriced，不能用cost_usd=0声明免费；已询问实际计价口径，
  不阻止继续质量/完整token/耗时验证。
