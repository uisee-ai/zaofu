# 187 实施与评估记录

> 日期: 2026-10-26 UTC。
> 范围: [设计 187](../design/188-bmad-inspired-epic-task-map-and-tdd.md)的六项实施任务。
> 分支: `wip/codex-20261026-bmad187`。
> 工作树: `/home/min/workspace/public/zaofu-187-bmad`。
> 方法落点: [实施说明](../impl/bmad-187-methods-and-validation.md)。
> 后续: 用户追加要求后已完成[12 次真实模型效果对照](2026-10-26-0431-187-model-effect-evaluation.md)；下文“未运行”描述的是初次工程验收时点。

## 来源对账与问题分层样本

样本为 Epic reference 附带的 5 条车辆场景来源，属于人工构造的固定评估样本，
不是实际产品 PRD 或模型 A/B。下表是本回合对样本与候选方法的人工复核。

| 来源 | 保留的语义与落点 | 缺失时的具体判据 |
| --- | --- | --- |
| SRC-1 | AC-LIST → E-READ → vehicle-read，筛选/分页/详情贯通 | 只写“列表完成”无法覆盖分页与详情 ID 一致性 |
| SRC-2 | AC-DENY → E-WRITE，拒绝且无写入副作用 | 仅 HTTP 拒绝而仍写数据库不成立 |
| SRC-3 | AC-TXN → E-WRITE，车辆/审计整体回滚 | 分拆成各自提交的任务会破坏事务边界 |
| SRC-4 | AC-ERROR → E-READ，错误时可理解且有下一步 | 不用擅造时延/分数阈值替代定性要求 |
| SRC-5 | AC-NOTIFY → E-NOTIFY，仍列在未履行验收中 | 渠道待定不等于通知被排除或已完成 |

Q-TXN 是落实已要求原子性的存储边界决定，阻塞写入细化；Q-NOTIFY 是未来渠道
决定，两者有 owner、resolve_before、revisit_when 和允许继续的范围。
无关的只读研究可以继续，但示例没有阶段 Goal 批准，因此不授权半图执行。
当前 Channel 要求 owner frontier 清零的门禁也没有被这套方法豁免。

另审查一个冲突变体：后续输入 SRC-3b 提议“审计失败仍保留车辆变更”，与 SRC-3
的整体回滚冲突。合理产物须保留两份来源，并由产品/领域负责人在写入任务准入前
决定；不静默覆盖 SRC-3，也不将提议当作已批准例外。方法中的冲突处理覆盖此案，
本轮只作人工方法审查，未伪造 Owner 决定或模型评审输出。

实际读取测试证明 `shared` 不因 Task 分区而丢失；未来通知 Task 没有分区会被
reader 拒绝。Task Map 校验器拒绝该阅读 schema。人工复核不能代替独立模型评审。

## RED / GREEN 与组合故障

新增依赖诊断的 6 项测试先运行于实现前基线，均因缺少 `longest_dependency_path`
产生 `KeyError`，不是环境或收集错误；之后同组测试及原诊断测试共 15 项通过。
基线和测试摘要保存在归档任务中；源码和测试同一实现提交可回溯。

随后在独立 Python 进程中给 CLI 与诊断函数的交接注入回归：删除输出中的路径字段。
图算法的局部检查与旧 CLI 检查继续通过，新 CLI 行为检查失败：**14 passed / 1 failed**。
缺口是原 CLI 测试没有检查这个新增公开字段；补测经过真实 `main()` 入口解析 JSON，
同时确认没有创建运行状态目录。注入不修改源码，退出进程即恢复正常实现。

复现注入的方法（从仓库根运行，已安装测试依赖）：

```python
from unittest.mock import patch
import pytest
from zf.cli import plan_execution

original = plan_execution.review_execution
def drop_path(*args, **kwargs):
    report = original(*args, **kwargs)
    report["graph"].pop("longest_dependency_path", None)
    return report

with patch.object(plan_execution, "review_execution", drop_path):
    raise SystemExit(pytest.main([
        "tests/test_plan_execution_review.py",
        "tests/test_plan_dependency_diagnostics.py", "-q", "--no-cov",
    ]))
```

预期退出码 1；撤除注入后相同测试应全部通过。这是接口故障注入演练，不是生产
缺陷修复，也不是实际 Provider 执行 Candidate Verify 的录像或证明。
产品验收与 Goal closure 的既有回归另行检查失败断言不能伪报通过、旧目标证据
不能签收以及兼容 Judge 投影不能单独完成 Goal，不能把两组测试拼称一个真实产品 E2E。

## 并行与依赖评估

新图案例为 `contract → {audit, read} → integrate`。结果最长链为
`contract → audit → integrate`、层宽度为 `1/2/1`；反转输入顺序不改变诊断。
缺依赖与环返回不可完成的路径；1200 节点链正常返回，无递归失败。

复用现有 v4 的确定性用例核对：A 的 Impl 完成时可进入 Verify，同时 B 开始 Impl；
Verify 积压达到上限时停止新 Impl；8 lane 批次仍受未验证容量限制。
结合恢复、返工、Plan Package currentness 与 runtime mock E2E 检查现有入口。
这些用例验证调度规则，不是 wall-clock 吞吐对照，不能据此声称长程交付更快。

## 验证命令与结果

验证使用共享虚拟环境中的解释器，`PYTHONPATH=src` 保证导入当前工作树。
原始日志与 machine receipt 位于 `/tmp/zf-187-validation-20261026-0336/`。

| 层次 | 结果 | 解释 |
| --- | --- | --- |
| 新图功能 RED | 6 failed，均缺少目标字段 | 预期失败，保留原始输出与测试文件 |
| 新图 + 既有诊断 GREEN | 15 passed | 含 CLI 与 1200 节点深链 |
| 组合故障注入 | 14 passed / 1 failed | 预期失败，只影响新增 CLI 集成断言 |
| `dev-verify` 主回归，23 个文件 | 247 passed / 2 failed / 1 skipped | 含 2 个 mock E2E 文件；发现 Skill 漏登索引、旧测试将浏览器依赖写成唯一依赖 |
| 修复后的 `dev-verify` 定向闭包，4 个文件 | 53 passed / 1 skipped | 补索引；用实际 metadata 成员断言保留浏览器依赖要求，允许同时依赖新方法 |
| 最终 `dev-premerge-gate.sh` | 51 passed；manual docs currentness OK | 完成索引修复后运行 |
| 新文档本地链接与 `git diff --check` | passed | 3 份关联文档无失效本地链接 |

主回归的 2 个失败在定向闭包中均已通过，因此主回归范围共 249 项最终通过、
1 项跳过；这是跨两次运行的闭合结果，不是伪造一次全绿收据。跳过项是
`examples/tmp/dev-codex-backends.yaml` 引用的外部 Skill source
`./skills/external` 与 `./skills/critic` 不存在，已单独用 `pytest -rs` 核实。
没有运行全量 pytest。

本轮曾错误地用 writer 路径检查预期拒绝整个 Epic 草稿；发现职责不符后，测试改为
正式 Plan preflight 所调用的 `validate_task_map_payload`，确认 schema 拒收。
没有为通过测试扩大该路径 helper 的职责。

主回归命令（`python` 指上述解释器）：

```bash
PYTHONPATH=src python scripts/dev-verify.py plan --base dev
PYTHONPATH=src python scripts/dev-verify.py run --base dev --workers 2 \
  --tests tests/test_task_scoped_plan_inputs.py \
  --tests tests/test_writer_fanout_admission.py \
  --tests tests/test_plan_synth_handoff.py \
  --tests tests/test_fanout_briefing_scope.py \
  --tests tests/test_artifact_query_service.py \
  --tests tests/test_task_pipeline_reconciler.py \
  --tests tests/test_task_pipeline_parallel_backpressure.py \
  --tests tests/test_task_pipeline_fault_matrix.py \
  --tests tests/test_task_pipeline_rework.py \
  --tests tests/test_product_acceptance.py \
  --tests tests/test_goal_closure_runtime.py \
  --tests tests/e2e/test_doc152_artifact_package_mock_e2e.py \
  --tests tests/e2e/test_task_pipeline_v4_runtime_noise_mock_e2e.py \
  --tests tests/test_project_semantics_boundary.py
PYTHONPATH=src python scripts/dev-verify.py run \
  --paths skills/INDEX.md tests/test_instruction_stack_contracts.py \
  --tests tests/test_plan_readiness_skills.py --workers 2
ZF_PYTHON=/home/min/workspace/public/zaofu/.venv/bin/python bash scripts/dev-premerge-gate.sh
```

新增明确测试选择覆盖当前调用者、read ledger、Task Map、产品验收与依赖调度，
未修改测试选择器或放宽门禁。原始失败收据 `dev-verify.json` 与修复通过收据
`repair-verify.json` 分开保留；日志包含完整 pytest 参数与退出结果。

## 完成度口径与待验证项

工程完成度以六项任务的实现与明确测试验收为分母；真实模型效果与上线采用另计。
不以测试数量、方法文件存在或本文长度证明语义质量与性能收益。

| 完成度分母 | 本轮结果 |
| --- | --- |
| 六项工程任务 | 6/6 实施与验收闭合，100% |
| 五方法的角色接线 | 5/5 已接入；薄 Epic、图诊断与 TDD 补充均落地 |
| 本轮选定主回归检查 | 249/249 最终通过；另 1 项可选示例跳过 |
| 真实模型 A/B | 0 次；收益未测量，不能给提升百分比 |
| 默认并行 rollout | 未变更，不属于本轮完成声明 |

原设计 §8 中，真实 PRD 五方法对照、独立 Critic 对共享入口/隐含依赖/原子事务的
实际判断、真实缺陷的独立 TDD 验收、同模型同预算的 v4 吞吐对照，仍待实测。
本轮具备可加载方法、固定样例、确定性诊断和回归证据，不能将设计总体标为全量通过。
下一次具备选定真实 PRD、固定模型及成本预算后，沿现有评估/受控 canary 路径开展。
默认配置和 rollout 未改变；本轮没有真实 Provider、浏览器或外部服务验证。
