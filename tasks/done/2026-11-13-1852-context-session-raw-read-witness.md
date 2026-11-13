# 修复会话补证对合法未分页读取的误拒绝

> 状态: done — `f2b159ba` fix: validate raw context delivery witnesses。授权依据：用户持续监工要求“遇到问题记录、修复、恢复并继续”；本项为当前 dev 388c6fb 在 LS-v1 真实执行中已复现的有界回归，不改变授权、读取门或业务状态。

## 已证问题与范围

C2 P1 已准入结果的 context_delivery_session_observation_status=unavailable / reason=manual_page_schema。sealed ledger 共39行，其中5 v2、30 manual page、4 CLI合法未分页 raw body；后者是 workflow.input_manifest.v1 / critical-context.v1，已观察在同 native 会话。CLI 明确允许未分页原正文，而 context_session_observation 对所有非v2一律要求5键分页包装，误拒绝整份可核验证据。

只补合法未分页读取的严格内容/摘要/身份一致性校验，不减少 required inputs、不引入跨attempt自动省略、不改变 context delivery full/shadow 策略，不把本修复宣称为250KB重读的充分解决。保留 v2锚、同session witness、缺失/篡改/混合session fail closed。不得写LS canonical状态或伪造旧补证成功。

## 实施与验收

- 当前HEAD复现合法raw与分页混合ledger误拒绝 -> verify: deterministic回归先复现manual_page_schema，不用真实provider或读取私有推理。
- 适配现有合法raw输出 -> verify: 原v2+manual+raw同session在真实补证调用入口通过，observed正文/hash/binding一致；错误body/hash/session、缺v2锚或未观察项仍拒绝。
- 补真实caller回归与既有design/impl说明 -> verify: 不只单独helper通过，结果准入/补证实际entrypoint保持optional/fail-closed行为；相关说明明确范围。
- 按影响闭包验证 -> verify: dev-verify plan/run --base dev、直接调用方与共享context合同测试、sentinel及相关deterministic mock E2E；不启动全pytest/真实provider。
- scoped commit与任务归档 -> verify: explicit pathspec、实现hash写done、干净worktree交root sole merge owner。不得push/merge或直接重启LS。

## 分工与保留

实施agent仅写本隔离worktree，root独立审查、合并/部署另控。main的AGENTS.md和design/00-index.md已有他人dirty，不得触碰；文档更新优先已有直接相关设计/impl，不新增索引冲突。

## 实施与证据

- 新分支由读取账本是否含分页字段决定格式。完整未分页输出使用 manifest 已绑定来源、固定版本恢复、原 JSON path selector 与数值保真 codec 重建原文，逐字比对输出和字节数；拒绝限长/截断或伪造 delivery nonce。手动页/v2 分支、全部输出摘要和 provider 完整见证不变，raw 不增加身份锚计数。
- 真实 `CallResultAdmissionService.report_legacy_result` 回归：v2 + manual + raw 三种合法组合先红（JSON 原文为 `manual_page_schema`；JSON 字符串及 Markdown 为 `JSONDecodeError`），修复后均 observed。9 个负例覆盖 raw-only（即使正文自称 v2）、unknown、异 session、同见证但错误正文、错误输出/来源摘要、截断见证、残缺分页字段、限长读取。业务结果仍可准入，失败只影响可选补证。
- 直接 consumer 回归继续核对原 receipt 不变、下一次仅 shadow unchanged 而实际 full、`current_context_availability=unknown`、重放无重复 admission。198 §8 与199 §12.3 已说明：这是历史输出一致性，不能证明压缩后保留或豁免新 attempt required。
- LS 真实 C2 P1 账本 `artifacts/attempts/ta-a301a7682f0d36abcd91/read-ledger-1a0bf696044f39ce.jsonl`，SHA `1a0bf696044f39ce2b9790c8c9a422831209403fe5159bf086b5cfe191aa6e9e`，39 行为5 v2/30 manual/4 raw。新源码纯读取 `_validate` 可核得原 native session；4 个固定 receipt/ledger/result-envelope/policy 文件 SHA 前后不变。没有执行 sidecar writer 或事件入口，原 admitted 仍保留 unavailable；不冒称历史状态已修或部署生效。

验证使用主仓现有 `.venv/bin/python`，所有 Python 命令明确设置 `PYTHONDONTWRITEBYTECODE=1`、`PYTHONPATH=/home/min/workspace/zaofu-worktrees/context-raw-witness-20261113/src`，cwd 为本 worktree，未改主仓环境。

1. 新增3个正向 caller 用例先红，随后本文件 **53 passed**；加入 context delivery/input recovery/required resume 的4文件 focused 组合 **84 passed**。
2. `scripts/dev-verify.py plan --base dev --workers 4` 与相同参数的 `run`：基础直接 caller 闭包14文件；显式增加共享 artifact 读账本、固定版本/codec、输出/恢复边界及两项 mock E2E，最终22文件 **626 passed，69.69秒**。完整命令附加参数为：

   ```text
   --tests tests/test_context_session_observation.py
   --tests tests/test_artifact_read_ledger.py
   --tests tests/test_artifact_read_snapshot.py
   --tests tests/test_artifact_output_identity.py
   --tests tests/test_artifact_input_recovery.py
   --tests tests/test_artifact_required_resume.py
   --tests tests/test_context_delivery_integrity.py
   --tests tests/e2e/test_stale_contract_handoff_mock_e2e.py
   --tests tests/e2e/test_agent_owned_verification_mock_e2e.py
   ```

   自动选择还包括 `test_generic_workflow_complex_mock_e2e.py`；仅 deterministic 层，不运行 host/real_provider/perf 或全仓 pytest。
3. `ZF_PYTHON=/home/min/workspace/public/zaofu/.venv/bin/python bash scripts/dev-premerge-gate.sh -p no:cacheprovider`：manual docs currentness OK，**53 passed**。`git diff --check` 通过。
4. 收据：`/tmp/zf-context-raw-witness-20261113/{plan.json,dev-verify-receipt.json,dev-verify.log,sentinel.log,ls-readonly-recheck.json}`。正式原包/独立Worker的验收不由这组 ZaoFu 修复测试替代。

独立审查：root 报告 `LS/docs/records/2026-11-13-context-raw-witness-fix/independent-review.md`，SHA `b2b0cf1c1f9edf7e8d23eed16bdde5596ea241b404dad550df63f006e4d5fa93`，限定本片无剩余 confirmed finding。root 另以实际 admission 探针确认已固定来源在原文件变化/删除时可用正确副本，副本损坏保持 unknown 且不阻合法业务准入；临时状态已清理。该独立探针不冒充本 agent 执行，亦不证明 provider 压缩后的记忆保留。
