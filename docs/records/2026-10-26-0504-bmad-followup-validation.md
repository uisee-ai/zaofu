# BMAD 六项增量：实施验证与效果试验

> 日期: 2026-10-26 UTC
> 范围: [187 增量实施](../impl/bmad-method-evaluation-and-feedback.md)。
> 结论: 六项工程交付完成；本次试验未证明方法带来质量提升，也未证明长程收益。

## 工程交付

| 项目 | 已交付与验证 | 效果边界 |
| --- | --- | --- |
| 方法贡献评估 | 旧版/新版/删减版/无方法对照、调用上限、隐藏组别的评分包、证据摘要与统计 | 评分由本会话执行 agent 复核，未进行独立人评 |
| 路由与上下文 | 近似意图案例；规划说明按条件读取；真实角色物化与分发一致 | 未调用真实 workflow proposal/apply，未测生产 lanes |
| 独立评审 | 原始需求优先、逐项证实/反证后归并的方法及角色接线 | 新版此次先读作者 handoff，不能宣称盲审 |
| 返工保留 | KEEP 方法；既有 feedback/briefing 保留 passed 与 failed AC 的行为测试 | 本次只生成返工建议，没有执行修复后再验证 |
| Epic 复盘 | 累积范围、当次修复/上游预防、上一轮行动跟进方法 | 自动选择未读新增 reference；双轨行动输出不完整 |
| 有界 Spike | 实验问题、预算、停止条件、证据消费者与依赖关系方法 | 只测规划，未执行实验；自动选择未读新增 reference |

实现留在评估工具、Skill 和测试中，没有更改 Kernel、任务 schema、运行路线或默认预算。
“六项完成”指批准范围内的工程交付与接线，不表示六项运行效果均已证实。

## 确定性验证

在独立 worktree 执行 `scripts/dev-verify.py plan --base dev` 后，使用共享环境运行：

```bash
PYTHONPATH=src /home/min/workspace/public/zaofu/.venv/bin/python -B \
  scripts/dev-verify.py run --base dev --workers 2 \
  --tests tests/test_plan_execution_eval.py \
  --tests tests/test_eval_method_report.py \
  --tests tests/test_plan_readiness_skills.py \
  --tests tests/test_impl_verify_contract.py \
  --receipt /tmp/zf-bmad-followups-20261026T0504/verify-receipt.json
```

闭包包含 8 个测试文件，结果 **122 passed, 2 skipped**。跳过项分别是此 checkout
没有可选 `.gitlab-ci.yml`，以及示例配置引用的外部 Skill 源不存在；不是通过项。
评审包增加输入摘要绑定后，评估工具回归 **26 passed**，与前述结果有重叠，不相加。
另验证了 5 份 Skill 元数据、两份适用通用 validator 的 yoke Skill，以及源/分发一致性。
本批次没有运行全量 pytest，也没有运行真实 ZaoFu 产品流程 E2E。

故障案例内置的两项 unittest 本身通过，而事务探针失败。这是有意设置的漏检案例，
不是 ZaoFu 产品测试失败；它用于检验评审者能否发现“测试全绿”之外的需求缺口。

## 预先冻结的试验

四个合成案例、24 条 mandatory 标准在运行前固定；标准不放入 Provider 的项目目录。
旧版与新版各跑四例；评审案例另跑删减版、无方法组，共 **10 次**，每组每例一次。
最多同时运行两个案例，每次 180 秒上限。模型为 `gpt-6-astra`，Codex 为 `0.163.0`，
同一 Docker 镜像、只读项目与容器根目录、1 CPU/1 GiB，关闭 web search。
认证挂载只用于 Provider；没有挂载宿主仓库或 Docker socket。模型调用需要网络。

新版冻结于实现提交，之后的评审包输入摘要补强没有改变方法内容。
删减版仅替换 independent-findings 与 rework-preservation 两份既有 reference，
不制造缺失文件；无方法组保留相同任务输入与权限。仅评审案例包含这两类对照。

全部 10 次均正常完成、无超时/Provider 错误、项目输入摘要不变。个别探针先使用
不存在的 `python`，随后以 `python3` 成功执行；这是已恢复的命令失败。

评分者是本会话执行 agent，已知组别；**不是独立人评，也不是盲评**。
匿名导出功能通过行为测试，但本次没有以盲评流程证明效果。汇总工具始终输出
`human_review_required`，评分不改变运行态或授权产品验收通过。

## 输出质量

| 案例 | 旧版 | 新版 | 删减版 | 无方法 |
| --- | ---: | ---: | ---: | ---: |
| 路由近似意图与授权 | 6/6 | 6/6 | 未跑 | 未跑 |
| 真实缺陷、错误 finding 与返工保留 | 6/6 | 6/6 | 6/6 | 6/6 |
| Epic 复盘与行动跟进 | 5/6 | 5/6 | 未跑 | 未跑 |
| 未决事务假设与有界 Spike 规划 | 6/6 | 6/6 | 未跑 | 未跑 |

旧/新均为 **23/24**。复盘未通过的 `dual` 项要求分别给出本次修复及上游预防行动，
包含负责人和验证。两版给出了修复、共享用例和本次组合验收，但未形成独立的上游
预防行动及复访证据；本次仍按原标准判失败，没有放宽分母。

评审四组均运行探针：ledger 失败后库存由 10/0 变成 7/3，失败后重试变成 4/6；
均驳回“actor=None 缺 guard”的错误断言，并要求保留幂等、冲突拒绝、权限零副作用。
此例没有显示完整方法的额外质量收益。无方法组仍有模型先验，不能据此决定删除方法。

## 方法是否实际读取

代码物化测试证明角色能取得 reference，运行轨迹另回答模型是否读取：

- 路由两版读取 start-planner 主文，未读评测 reference。普通路由任务无需加载评测方法。
- 评审新版读取 independent-findings，但先于它读取 handoff，且未显式声明作者结论暴露；
  未读 KEEP reference。六项输出标准未覆盖读取顺序，这是本次评分覆盖缺口。
- 复盘新版选择 backlog-closeout 与 zf-cr，未读取 self-improve 的 epic-retrospective。
- Spike 两版选择 requirement-reconciliation 与 grill，未读取 readiness/bounded-spike。

因此复盘和 Spike 的结果不能归因于新增 reference 的内容。自动发现、阶段明确加载、
内容是否有效，应分开评估。此次局部自由选择不等于真实 Planner/Critic briefing 的加载。

## 用量与耗时

以下为 Provider 报告值；input 包含重复上下文与缓存，cached 为其中缓存部分。
每格仅一次观测，样本标准差无定义，不做显著性或费用结论。

| 案例/组 | 秒 | input tokens | cached input | output tokens |
| --- | ---: | ---: | ---: | ---: |
| 路由/旧 | 57.400 | 56,350 | 43,520 | 949 |
| 路由/新 | 60.965 | 72,049 | 66,560 | 916 |
| 评审/旧 | 82.035 | 111,052 | 95,616 | 2,159 |
| 评审/新 | 79.880 | 129,498 | 112,384 | 2,251 |
| 评审/删减 | 84.664 | 127,176 | 113,664 | 2,264 |
| 评审/无方法 | 80.023 | 79,547 | 74,624 | 2,149 |
| 复盘/旧 | 77.785 | 135,436 | 113,280 | 1,482 |
| 复盘/新 | 78.633 | 117,594 | 91,648 | 1,617 |
| Spike/旧 | 66.341 | 55,100 | 40,320 | 1,116 |
| Spike/新 | 61.488 | 54,912 | 41,216 | 1,001 |

用量没有呈现一致下降；缓存和服务噪声、两案例并发、固定单轮顺序均影响比较。
不能把主文变短、物化字节减少直接等同于 token 或成本收益。

## 后续评估触发条件

本批次不提高默认 rollout，也不据此移除现有方法。下一轮有真实 Epic 或规划任务时：

1. 使用相同输入分别检查自动发现与阶段明确加载，先确认目标 reference 被读取，
   再比较内容。复盘和 Spike 是本次暴露的加载覆盖缺口。
2. 预先增加作者结论暴露声明、KEEP 实际返工后保持、上游预防行动复访等标准；
   保留本次原评分，使用新案例和独立评分者，避免针对已知样例调整方法。
3. 在真实多轮任务中统计返工率、旧 AC 回归、恢复次数、完成时间及实际用量，
   配合有界重复；本次合成单轮输出不回答这些问题。

## 可复核证据

- [实验参数与删减文本](2026-10-26-0504-bmad-followup-validation/experiment.json)
- [运行前 fixture 摘要](2026-10-26-0504-bmad-followup-validation/fixture-hashes.json)
- [逐次测量与最终答复文件索引](2026-10-26-0504-bmad-followup-validation/measurements.json)
- [冻结标准与逐项评分依据](2026-10-26-0504-bmad-followup-validation/scorecards.json)
- [实际读取命令及故障探针输出](2026-10-26-0504-bmad-followup-validation/observations.json)

十份最终答复保存于同名证据目录，仅去行尾空白并补结尾换行；原始及归档摘要均记录。
完整 manifest、原始 trace、审阅包、测试日志与验证 receipt 保留在
`/tmp/zf-bmad-followups-20261026T0504/`。仓库证据是可复核摘录，不含认证文件，
不替代完整原始轨迹；该临时目录可能被宿主清理。
