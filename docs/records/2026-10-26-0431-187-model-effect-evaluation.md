# 187 真实模型效果评估：方法更明确，尚无明显质量跃升

> 日期: 2026-10-26 UTC；用户在工程验收后要求追加实际效果评估。
> 设计: [187](../design/188-bmad-inspired-epic-task-map-and-tdd.md)。
> 前序: [工程验证记录](2026-10-26-0336-187-delivery-validation.md)。

## 结论与交付决定

三组固定案例、两版各重复两次，共 **12 次真实模型调用均完成**，输入文件均未改变。
旧版 **71/72**、新版 **72/72**；全部预设正例均未被错误阻断。
差异只来自一次通知延期事项的责任人与最迟决定时点是否明确写出。
Plan 拆分与 Verify 缺陷识别持平，两版都能准确执行关键方法。

因此，本轮支持把 187 作为**方法显式化、可审阅性及诊断能力补充**合入，
不能把它宣传为已证明的长程交付质量或吞吐提升。没有依据调整默认 lanes、
切换 Task Pipeline rollout 或宣称大幅减少返工。

本次新版累计输入 token 增加 **11.8%**，平均单次耗时增加 **8.0%**。
这是小样本观测，不能排除采样、缓存和服务时延的影响；也没有价格依据换算费用。
如采用更宽松的评分，把旧版引用的延期来源视为已完整表达 owner，则两版均为满分。
**无论采用哪种口径，都没有明显质量跃升的证据。**

## 试验设计与隔离

- 模型请求为当前配置的 `gpt-6-astra`，Codex CLI `0.163.0`；两版同配置，reasoning
  使用同一 provider 默认值。并未证明底层服务始终使用同一权重快照。
- 冻结基线是 Git 中 `feat: integrate OA and WRC task pipeline orchestration` 所对应
  方法；候选是 `feat: add BMAD-inspired delivery methods and plan dependency diagnostics`
  对应方法。完整 `skills/`、`yoke/` 分别冻结，目录 SHA-256 随测量保存。
- 使用既有 `eval-plan-execution.py` 与 `eval-review-skill.py`，每次最多 240 秒，
  总上限 12 次。第一轮 baseline→candidate，第二轮 candidate→baseline。
- Docker 固定本地镜像 ID，根文件系统及输入只读，1 CPU / 1 GiB；仅评审输入和
  模型调用所需认证文件挂载，没有挂载主仓库或 Docker socket。网页搜索关闭。
- 两版 prompt/案例相同，不额外给 candidate execution facts；本轮只对比方法文本
  和加载路径，新增图诊断能力另由工程测试证明。
- [评分规则和案例](../../tests/e2e/fixtures/bmad187_effect_eval/README.md)先于运行冻结。
  每案 6 项，每项 0/1/2 分；oracle 不在模型容器可见输入中。原始输入字节摘要
  [已保存](2026-10-26-0431-187-model-effect-evaluation/preregistered-inputs.json)。
- 评分由本回合操作者读取答案逐项完成，**非盲评分**。没有用关键词匹配或技能名称
  出现自动判定语义通过。样本落盘到仓库既有 pytest 排除的 fixture 目录，内容未变。

## 逐组结果

每行分数上限 24（6 项 × 2 分 × 2 次）；token 是两次调用的累计输入。

| 案例 | 旧版分数 | 新版分数 | 旧版平均秒 | 新版平均秒 | 旧版输入 token | 新版输入 token |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Intake 来源对账与问题分层 | 23/24 | 24/24 | 63.521 | 73.936 | 131,148 | 149,528 |
| Plan 正例、切片与并行反例 | 24/24 | 24/24 | 65.254 | 72.954 | 199,245 | 209,313 |
| Verify/TDD 与组合缺陷 | 24/24 | 24/24 | 64.513 | 61.876 | 183,517 | 215,480 |
| 合计/总体均值 | **71/72** | **72/72** | **64.429** | **69.589** | **513,910** | **574,321** |

新版非缓存输入为 131,697，旧版为 122,358，观测增加 7.6%；输出 token 为
9,522 对 9,421。不能把输入字节、累计 token 或缓存命中单独当美元成本。

### Intake：更完整的决定责任，但主要缺陷原本就能发现

两版都纠正原子性被工程建议反转、幂等键含义被替换、权限遗漏、弱网错误提示缺失、
通知被误删，以及“本轮仅批准规划”被写成永久 Non-goal。

唯一分差是旧版第一次的通知处理写了延期和重访条件，但没有显式落实负责人及
最迟接口决定时点；新版两次都写明产品负责人、重访触发和相关接口/计划确定前解决。
旧版第二次也完整表达了这些内容，因此只能认为新版表述更稳定，不能归因出显著收益。

证据：[旧版第一次](2026-10-26-0431-187-model-effect-evaluation/answers/intake-1-baseline.txt)、
[新版第一次](2026-10-26-0431-187-model-effect-evaluation/answers/intake-1-candidate.txt)。

### Plan：两版全部正确，没有新增发现收益

四次均 `approve P-good / revise P-risk`。合理的 Phase-1 已获 Owner 授权，未来通知
保留在薄 Epic，因此没有被错误要求先细化整个未来范围。五个反例也全部定位：

1. wave/任务名代替真实接口依赖；
2. 顺序波次下仍重复独占共享文件；
3. 原子事务拆成三个可独立提交任务；
4. 路径独立但固定端口和数据库 fixture 互相干扰；
5. 默认 stage_barrier 下用 task_verify 标签承诺不存在的逐片独立 Verify。

这些多数已由 182 的 readiness 方法与当前 wrapper 说明，因此不是 187 新赋予的能力。
证据：[旧版第一次](2026-10-26-0431-187-model-effect-evaluation/answers/plan-1-baseline.txt)、
[新版第一次](2026-10-26-0431-187-model-effect-evaluation/answers/plan-1-candidate.txt)。

### Verify：实际探针捕获组合缺陷，两版结果一致

试验夹具的 3 个局部 unittest 均通过；真实内存探针则确认两个产品失败：流水异常后
库存仍从 10/0 变成 7/3，以及生产者返回 `transfer_id`、展示端读取 `id` 导致组合
入口 `KeyError`。四次模型评审都执行新探针并正确 reject，给出具名回归和漏检原因。

四次也都拒绝把 `ModuleNotFoundError` 认证为功能 RED，保留既有查询首跑即绿，
复用给定的当前 U3 而拒绝旧 J2，并保留 Candidate 执行验证、Thin Judge 消费证据的
边界。U3/J2 与 revision 字符串是受控案例给定事实，不是实际平台收据的 E2E。
部分探针捕获异常后退出码为 0，模型仍按断言观察报告失败，未把进程成功冒充产品通过。

候选两次工具轨迹均实际读取新增的 `regression-and-integration.md` 与
`cycle-evidence.md`，所以结果持平不是“新方法没加载”。
证据：[旧版第一次](2026-10-26-0431-187-model-effect-evaluation/answers/verify-1-baseline.txt)、
[新版第一次](2026-10-26-0431-187-model-effect-evaluation/answers/verify-1-candidate.txt)。

## 限制与下一步判断

样本是三个清晰的合成案例，prompt 明确提醒了审查维度与部分安全边界，存在明显的
天花板效应；每版只有两次重复，评分者知道版本。两版共享的既有方法本来已经较强。
结果不能推断弱模型、长 PRD、多轮重规划、实际写代码或完整产品交付的效果。
某些基线读取过未随独立样例提供的正式发布文档；模型正确限定为本案不适用，没有
将其当授权或阻塞。完整运行 trace 留在原始目录，失败命令未从证据中抹除。

下一轮应采用真实 PRD 的盲评与多轮交付，固定总预算，测遗漏率、无效返工、首个
独立反馈、Verify 积压及最终 mandatory AC 通过率，再决定是否调整并行策略。
这是一项后续验证条件，本轮没有扩大 rollout。若要进一步优化，应优先减少重复
读取和重复规则，在保持关键边界的前提下再做有同等评分的成本对照。

## 可复核产物

- [完整测量与工具读取记录](2026-10-26-0431-187-model-effect-evaluation/measurements.json)：12 次结果、token、时长、冻结 bundle/trace/答案摘要。
- [逐次评分卡](2026-10-26-0431-187-model-effect-evaluation/scorecards.json)：每项分数及理由。
- `answers/` 保存 12 份最终答复，仅移除每行末尾空格/制表符以满足仓库空白检查；测量中同时保留整理前后摘要。内部 `/workspace` 引用对应各案例的 `project/`，不是主项目文件。
- 原始 manifest、完整 JSONL、stderr 和 Docker 调用记录保存在
  `/tmp/zf-187-effects-20261026T043123Z/`，未提交认证内容或完整主机配置。
- 合并前源分支自动闭包 **107 passed / 1 skipped**，哨兵 **51 passed**；
  skip 来自可选外部 Skill source 缺失。评估脚本与指令检查 **14 passed**；
  16 个冻结输入、12 份答案与评分摘要、5 组文档链接核对通过。
