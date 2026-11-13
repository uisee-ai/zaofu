# 消费者输入完整性的局部方法澄清

> 状态: done — `3c33f632 docs: clarify consumer-scoped input completeness`

## 授权与范围

root 依据用户持续授权“遇问题修复、记录、推进”批准归因报告第 4 节第二项：仅澄清 canonical 生产技能的完整性含义与冻结前消费者审查。root 是唯一 dev merge owner；本分支不 merge/push，不修改 LS R4、运行态、worker 或已有 approved 输入。

依据：[设计 198 §5.2/§6.2](../../docs/design/198-context-index-write-read-resume.md)；本轮 LS 记录 `docs/records/2026-11-12-delivery-entry-resume/input-producer-attribution-20261113.md`。报告已确认 55,688 字节依赖块来自准备 agent 对前置完整 Task row 和 pipeline context 的复制；既有读取器只是执行批准选择。此事实不证明某个字节大小本身错误。

当前 `a926abbc` 已有来源、异常、图片、AC 和消费者完整性要求，但“完整来源、异常、用例和依赖正文”未就整前置合同复制给出局部边界。无需重复既有规则或新增 skill/runtime gate。

## 唯一任务

- [x] 在 `skills/zf-plan-task-map-contract/references/task-input-production.md` 澄清“完整”是本消费者必要正文，说明整份前置 Task contract + pipeline context 的反例；冻结前列实际 shared/owned 选择、必要性/排除理由和完整来源的精确读取位置。字节/页数仅作为审查信号，同步两 provider 分发。

不实施报告第一项的 LS 输入重写；旧 shared 布局迁移仍是另行审批候选。不修改设计 198/199、Task schema、读取保护或独立 Verify。

## 验收

1. 审核局部文字及依赖反例 -> verify: 不把生产者旧 AC/实施命令/执行身份自动变为消费者义务；必要接口正文、版本/成果绑定、错误/UNKNOWN/生命周期与本消费者验证责任仍完整。
2. 审核冻结前方法 -> verify: 实际选择与保留/排除理由可审查，完整来源固定版本且可精确回取；计量没有阈值准入或自动裁剪语义。
3. 对照原输入保护 -> verify: 原异常、图片/状态/位置、source/AC、全局矩阵和独立 Critic/Verify 要求保持，不用 ID/ref 代正文。
4. 同步与入口检查 -> verify: 三份 reference 字节一致；现有 metadata/materialization 与输入离线反例测试通过，真实 provider 语义质量不以机械检查冒充。
5. 收口 -> verify: dev-verify 记录实际 docs/skills 闭包，sentinel 通过；implementation commit 后归档本任务，工作树干净交 root。

## 实施与验证

- 仅修改生产 reference 的局部完整性段落、前置全文复制反例和冻结前审查项；两 provider 副本从 canonical 用 `shutil.copy2` 同步（仓库未提供独立的分发同步命令），未新增同步工具。
- 语义对照：库存接口 v1、409、UNKNOWN/生命周期、必要验证与来源正文仍属消费者输入；前置 Task 的旧命令/身份不会因复制变成新义务。原 source/image/AC、完整矩阵及独立 Critic/Verify 保护保持。本文所述旧 shared 迁移没有执行。
- `python scripts/dev-verify.py plan --base dev --tests tests/test_context_input_eval.py --tests tests/test_plan_readiness_skills.py --no-parallel --json`：仅 docs/skills，5 个既有测试文件，`broad_python=false`，无 warning/error。
- 同参数 `dev-verify.py run ... --receipt /tmp/zf-input-method-receipt-20261113.json`：**67 passed / 1 skipped**，4.16s；未跑全 pytest。跳过项为可选宿主示例引用的外部 skill 目录 `./skills/external`、`./skills/critic` 缺失；单独用 `pytest ...::test_dev_codex_example_materializes_each_role_skills_and_dependencies -q --no-cov -rs` 确认原因，结果 1 skipped。
- 通过现有 `read_skill_metadata` / `materialize_role_skills` 实际沿 Planner dependency 为 codex、claude-code 分别 materialize；更新 reference 在两 backend 都与 source 字节一致，metadata 无警告。只用临时目录，结束已清理。
- `PYTHONDONTWRITEBYTECODE=1 ZF_PYTHON=/home/min/workspace/public/zaofu/.venv/bin/python bash scripts/dev-premerge-gate.sh`：manual-docs 及 **53 passed**。
- `git diff --check`：通过；没有新增字符串断言、runtime/schema 代码或新文档。既有设计 198 链接与 source skill 中的 reference 链接可达。
- 证据：`/tmp/zf-input-method-{plan,receipt,check,materialize,sentinel}-20261113.{json,log}`（实际后缀：plan/receipt/materialize 为 json，check/sentinel 为 log）。离线反例测试验证显式证据绑定与遗漏计分，未调用真实 Provider，不能据此声称模型语义质量获证。
- 更新 reference SHA256：`e3a80fe226e9e618fcba66adcfabd58100a5caf327120710e2a92607cb0817fb`。

实现 commit：`3c33f632 docs: clarify consumer-scoped input completeness`。实现提交后工作树无残留；本次仅归档状态与路径，交 root 作为唯一 merge owner 合并。
