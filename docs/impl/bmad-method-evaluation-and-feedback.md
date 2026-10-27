# BMAD 增量：方法贡献评估与返工反馈

> 状态: implemented — 工具与方法接线；语义效果和长程收益按实际评估记录报告。
> 来源: 用户批准的六项 BMAD 增量建议；原设计 187 为本地忽略文档。
> 任务: `tasks/done/2026-10-26-0504-bmad-followup-*.md`。
> 验证: [六项实施与真实模型试验记录](../records/2026-10-26-0504-bmad-followup-validation.md)。

## 实施范围

| 项 | 实际落点 | 验证 |
| --- | --- | --- |
| 方法贡献 | 扩展 `eval-plan-execution.py`；新增 `eval-method-report.py` | 四组输入、隔离副本、摘要、调用上限、匿名审阅与缺证据拒收 |
| 路由/上下文 | start-planner 评测 reference；readiness 条件引用 | 正反例、角色实际物化、源与分发一致 |
| 独立评审 | verify-review 的 independent-findings | 原始需求/代码先于作者结论，逐项核实后归并，保留反证 |
| 返工保留 | tdd-evidence 的 rework-preservation | 完整反馈保留 passed/failed AC；现有 briefing 可传递 summary/required_actions |
| Epic 复盘 | self-improve 的 epic-retrospective | 累积范围、双轨行动、上轮行动证据、未检查范围 |
| 有界 Spike | readiness 的 bounded-spike | 问题、时间/尝试上限、停止条件、等待关系与证据消费者 |

实现不改 Kernel、默认 lanes、Task schema、状态或 rollout。报告中的局部 ID 和保留
清单是语义正文，继续使用既有报告、required sidecar 和 refs。Verify 不编辑冻结需求，
Thin Judge 不承担探索、修复或产品测试。真实依赖解锁仍由现有路线判断。

## 贡献评估入口

输入目录包含 `prompt.md`、`project/`；`rubric.json` 留在 `project/` 外。
旧有双组参数兼容。新增可选 `--variant DIR` 和 `--include-bare`：

- baseline/candidate：从各自 Git ref 冻结 `skills/` 和 `yoke/`。
- variant：复制 candidate 后覆盖 DIR 中明确列出的既有 `.md`/`.json` 方法文本。
  不修改源码；拒绝软链接、隐藏路径、新文件和可执行脚本。调用者负责保持引用自洽，
  不能通过制造缺文件来假装方法无效。文件与完整包均记录 SHA-256。
- bare：不物化方法；输入、容器权限、事实保持一致。prompt 必须自包含，不能依赖
  方法组专有文件或要求不存在的 Skill。它只表示无本地方法，不表示模型没有先验知识。

贡献对照不允许 `candidate-facts`，避免把方法与额外事实混为一个变量。
每次最多 12 次调用，`--repetitions` 为 1..3；默认超时 600 秒，`--timeout 0` 表示不设时限，
也可指定其他非负秒数。真实试验应选择明确且合适的任务预算。顺序在每轮反转，不能据此声称消除了缓存/服务噪声。

```bash
python scripts/eval-plan-execution.py \
  --inputs tests/e2e/fixtures/bmad_followup_eval/review \
  --baseline dev --candidate HEAD \
  --include-bare --model MODEL --repetitions 1 --max-provider-calls 3 \
  --prepare-only --output /tmp/zf-method-prepare-UNIQUE
```

prepare-only 不要求 Docker/Provider/认证，不生成真实完成或隔离证明。
真实运行移除该参数并指定本地 `--docker-image IMAGE`；沿已有容器只读输入、资源上限、
隔离认证挂载和退出清理。所有组返回码为零也只说明执行成功，语义评分独立完成。

## 匿名审阅与指标

```bash
python scripts/eval-method-report.py export \
  --run /tmp/zf-method-RUN \
  --rubric tests/e2e/fixtures/bmad_followup_eval/review/rubric.json \
  --output /tmp/zf-method-review-UNIQUE
python scripts/eval-method-report.py summarize /tmp/zf-method-review-UNIQUE
```

只把 `review/` 交给评分者；原始组别映射和成本留在外层。包内提供最终答复、完整
工具轨迹、原始案例输入、冻结评分标准及逐项评分模板。文本仍可能暴露方法身份，
所以这是隐藏组别标签，不是保证盲评。完整源包仍保存在原 run 目录。

评分者逐项写 boolean 与证据，并明确审核 weak/uncovered 标准；空评分、重复项、
无证据、修改答案/轨迹/案例输入或修改冻结标准不能成为通过记录。失败/超时/输入变化、
Provider 错误、缺失结果或 prepare-only 都不参与有效运行质量计数。
输出始终 `human_review_required`：工具汇总人的判断，不验证证据语义或授权产品通过。

统计真实 usage、耗时均值/样本标准差和完全重复命令数。缺失用量保留 null，
不把字节当 token、不估算未知价格，重复命令也不自动认定为浪费。

## 方法加载与验收边界

readiness 中跨域义务、复杂/UI 片内反馈说明移入条件 reference，正文保留触发条件；
预算细节仅在有计量事实、预算失败或拟修订预算时读取。完整需求和必要验收仍必读。
materialize-role 测试确认实际角色可取得全文，物化包大小不是实际读取量。

固定案例见 `tests/e2e/fixtures/bmad_followup_eval/`。四组共 24 条外置 mandatory
标准，包含路由近似意图、作者错误主张、返工保留、复盘缺证据和未知事务能力。
合成案例是局部方法检查；它们不证明真实多轮恢复、生产调度或长程吞吐改善。

源码参考：BMAD 的 bmad-eval、code-review/build 分诊与 KEEP、retrospective、PO Spike。
采用其方法，保留 ZaoFu 当前权限、证据绑定、受控 replan 和独立验证边界。
