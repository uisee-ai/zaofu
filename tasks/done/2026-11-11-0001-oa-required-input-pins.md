> 状态: done

实现 commit：`137780d9 feat: pin required OA inputs before manifest publication`。本批候选 1+2 均已完成；未扩展 delta/公共正文语义裁剪。

# OA 必读输入 pin 与完整页交付方法对齐

授权：本轮 root 明确批准基于 `da249af1` 实施只读审计候选 1+2；root 为唯一 dev merge owner。实现位于独立 worktree，不操作 LS 项目或运行态。

证据：LS 记录 `docs/records/2026-11-10-post-core-engineering/required-read-rework-oa-audit.{md,json}`。OA 实际 publisher 没有通用 source / selected-block pin；71 页逐次走 legacy 全正文 preview。72/72 正文片段已交付，不能归为正文丢失。本次 OA briefing 未要求保留完整 envelope，造成与既有严格恢复 witness 的方法差异。

范围：只为 OA 现有明确 required 来源和 selectors 在首次发布前接入已有 pin；真实 briefing 明确原样返回完整页。保持 exhaustive / compact / execution 必读策略、optional 范围、逐 occurrence 授权、旧 manifest / cursor、replay 及 cross-attempt 账本规则。不改 stale-target CAS、LS 产品代码、配置或运行态；不做 delta 包/共享正文裁剪，不承诺消除 25 分钟。

验收：

1. 实际 OA operation/request caller 生成输入后执行真实 rendered required CLI -> verify: 多页期间不读取整原文，正文逐字 lossless；仅 required 来源出现 recovery / selected pin，optional 不增加全文副本。
2. 三种既有 input mode 保持冻结 required 集合 -> verify: exhaustive / compact / execution caller 测试、每 occurrence 授权继续发生、无权 continuation fail closed。
3. 已发布 legacy 输入及 replay -> verify: 沿用原 manifest 和 cursor，原流程仍可读取，旧输入不被重新 pin。
4. OA 真实 briefing -> verify: 明确 raw page unchanged / 不过滤字段；既有完整 envelope 正例与 stripped identity 负例保持。
5. 更新 [OA 输入实现说明](../../docs/impl/oa-context-route-preflight.md) 并按 impact closure 验证 -> verify: dev-verify plan/run 收据、sentinel、相关 deterministic mock E2E；不无理由 full pytest。

验证记录（全部在独立 worktree，临时 pytest state）：

- TDD：修正测试 fixture 的 credential 初始化顺序后，实际问题基线 4 failed / 2 passed；修复后 6 passed。最早 fixture-only 红另留日志，不计为产品失败。
- `PYTHONPATH=src /home/min/workspace/public/zaofu/.venv/bin/python -m pytest tests/test_orchestrator_agent_required_delivery.py -q --no-cov` -> 6 passed；覆盖实际 pre_impl execution、exhaustive、compact publisher 与 CLI。
- `python scripts/dev-verify.py plan --base dev --json` -> `broad_python=false`、errors/warnings 空；直接 caller、文档约束和相关 OA mock E2E 闭包，不全量。
- `PYTHONPATH=src /home/min/workspace/public/zaofu/.venv/bin/python scripts/dev-verify.py run --base dev --no-parallel --receipt /tmp/zf-oa-input-pin-verify-20261111.json` -> deterministic 236 passed / 7 skipped；sentinel 53 passed；flow smoke 10 passed。7 skipped 来自 wake-pattern 内部循环事件既有排除，不记通过。
- `PYTHONPATH=src /home/min/workspace/public/zaofu/.venv/bin/python -m pytest tests/test_artifact_read_snapshot.py tests/test_artifact_read_ledger.py tests/test_artifact_read_capability.py tests/test_artifact_read_cli.py tests/test_artifact_read_batch_cli.py tests/test_artifact_required_read.py tests/test_artifact_required_resume.py tests/test_artifact_output_identity.py tests/test_artifact_navigation_shards.py tests/test_orchestrator_agent_execution_policy.py tests/test_orchestrator_agent_execution_review.py tests/test_orchestrator_agent_plan_binding.py tests/test_orchestrator_context_preflight.py tests/test_orchestrator_directive_preflight.py -q --no-cov -m 'not perf and not host and not real_provider'` -> 166 passed；包含 occurrence alias 授权、固定版本回退、resume/compaction 与身份边界。
- `PYTHONPATH=src /home/min/workspace/public/zaofu/.venv/bin/python -m pytest tests/test_artifact_lossless_reads.py -q --no-cov` -> 8 passed；高精度数值、signed zero、Unicode、legacy 丢精度拒绝。
- `git diff --check` -> passed。

证据：`/tmp/zf-oa-input-pin-red-confirmed-20261111.log`、`/tmp/zf-oa-input-pin-green-20261111.log`、`/tmp/zf-oa-input-pin-plan-final-20261111.json`、`/tmp/zf-oa-input-pin-verify-20261111.{json,log}`、`/tmp/zf-oa-input-pin-shared-closure-20261111.log`、`/tmp/zf-oa-input-pin-lossless-20261111.log`。纳入 task 后最终复核同样通过：`/tmp/zf-oa-input-pin-plan-staged-20261111.json`、`/tmp/zf-oa-input-pin-verify-staged-20261111.{json,log}`；自动层结果仍为 236 passed / 7 skipped + 53 sentinel + 10 smoke。

限制：OA 当前 producer 固定 `$` selector，本批未新增非 root 语义切片；不更改完整 envelope matcher；不自动回填历史 receipt。源码 2 文件小接线，新增的首次 pin 成本只覆盖冻结必读来源，不证明整体 OA 25 分钟消除。未运行 real-provider / host / 全量 pytest，未访问或写 LS runtime；部署与真实再观察归 root 管理。
