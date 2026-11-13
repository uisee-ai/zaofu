> 状态: done — `1faf3ed7` fix: bind late context sessions to admitted output evidence

# 首次上下文交付的迟到会话关联

沿用用户对 LS-v1 监工期间发现问题、修复、记录及合入 dev 的授权；在隔离 worktree 实施，root 为唯一 dev 合并负责人。基线 `8b61d9e1`。

## 已复现问题与边界

LS-v1 同任务返工复用了 native session，首轮 context delivery receipt 却在 provider session ID 尚未可知时固定为空，后续同执行绑定被判为 incompatible。实际同 attempt 已准入 sealed read ledger 含完整 provider-output-observation，能够提供迟到的历史会话关联。证据见项目 records `2026-11-13-trend-frontier-resume/insurance-rework-resume-observation.md`，SHA `3bce23ffd66475f6f519e41d8f1f821adfe8e47bcb1c432fe439cabfb01d0c12`；历史文件保留。

本项修复历史交付关联质量，保持设计 198 的 shadow-only 与全部 required-read/attempt 隔离。不声称实现跨 attempt 正文续用，不改变当前健康 Worker，不将 registry 的 role 最新 UUID 当作原 attempt 的发送证据。无真实观察、冲突或身份不明均保持保守回退。

报告已追加第 10 节真实入口审查，当前 SHA `3b7d01182450ec1afbd9e6ce0deb1e631a02c6cfd5b29ae0f24f1656e721ec24`；上段 SHA 是追加前版。真实 p1 输出为 3 个 required-artifact-page.v2 与 37 个 manual 页；后者没有 attempt/delivery identity，不能作为会话锚。至少一个严格 v2 输出及全部页的完整投递与同会话一致性是本项补证前提，manual-only 保守回退。

## 实施与验收

1. 在实际 CallResultAdmissionService 完整准入路径，检查精确 operation/attempt/dispatch/role/run 对应的原 envelope/receipt 与已验证 sealed ledger -> verify: caller 回归覆盖首轮空 ID、后续真实同会话观测；新补证通过不可变 sidecar + 现有受控事件引用持久化，旧文件不改。
2. 仅接受可重新核验的完整输出见证与唯一 session；核 sealed ledger、output body/digest/delivery identity、provider observation 与原派发对应关系 -> verify: 假标志、损坏 digest、缺见证、错 operation/attempt/dispatch/role/run、混合会话、空会话、已明确不同会话均不补证。
3. 下一次 context delivery 从精确历史关联读取相容性 -> verify: 同 session/同执行绑定可追溯，轮换或 execution binding 改变继续不相容；新 operation 优先，不能跳过较新未知/失败关联去借更老成功记录。
4. 补证失败不让已成功业务结果重复派发、也不使无效结果准入 -> verify: caller 的失败和重放回归；body delivery 始终 full，旧 read ledger 不能满足新 attempt 的现有测试保留。
5. 更新设计 198/199 的边界说明，明确 native session reuse、历史输入交付、当前保留状态、正文重发分别计量 -> verify: 文档检查及相应源码 caller 测试；跨 attempt 差量仍标为未启用。

修改采用最小已存在入口；不新增第二状态机或无调用方库。执行 focused 影响闭包、dev-verify planner/run、sentinel 和对应确定性 mock E2E；记录具体覆盖及未覆盖的真实 provider 层。实现 commit 后用其 hash/title 归档，交 root 独立审查、合并及清理，不 push。

## 实施与验证记录

- 入口已接到 CallResultAdmissionService 完整准入分支；补证经既有 immutable sidecar writer 持久化并由 admitted 事件引用，原 receipt/ledger 不改。消费端在最新 started operation 内匹配同 run/op/request 的准入补证，再重核原 receipt/manifest/ledger/完整 provider witness。角色 registry 不参与历史回填。
- 3 个源码文件：新增 `context_session_observation.py`，在 `call_result_admission.py` 接准入，在 `context_delivery.py` 接读取。实际投递仍 full；新 attempt 的 required reads 门禁未变。198 §8、199 §12.3 已说明边界。
- 首次真实 caller 回归先红（无补证 ref），实现后绿。原先严格要求所有行是 v2 会拒绝真实混合账本：独立审查指出后，混合格式 caller 再先红后绿；manual-only/unknown/混合 session 仍不补证。
- 只读固定 LS p1 复核：ledger SHA `9e20e3563a6f077d4e14eb77aafdbefcbb7ffcec2896f40cd748ea921382cbcb`，3 v2 + 37 manual；调用纯读取 `_validate` 得到 S1 `01a1d511-6713-74d1-b32d-54da8ed0df05`，原 canonical request hash 为 `9deb3b1a98836ac3ea82a9f8275f1e98caf85ee7aee6ea6aac9e0f6f37db94a4`。没有调用 writer、发事件或写 LS sidecar。该检查证明当前已有证据可关联，不声称真实控制面已部署或下一轮已消费新补证。
- 独立审查 P2：可选补证调用的 `event_log.read_all()` 实参在 helper 异常边界外，额外读取 OSError 会阻断合法 admission；未证明会重复派发。已用真实 caller 故障先红复现，再将读取纳入 helper 内非致命边界；合法结果继续准入、重放不重复 admission，错误作为补证诊断保留。

验证命令与分层：

1. `PYTHONPATH=src /home/min/workspace/public/zaofu/.venv/bin/python scripts/dev-verify.py plan --base dev --tests tests/test_context_session_observation.py`：选中 124 个影响测试文件。工具因 >80 自动建议整仓 deterministic suite；依 AGENTS 的 full-suite 范围限制，没有执行整仓库测试，也没有缩小选中闭包。
2. 按上述 planner 的全部 124 个 `selected_tests` 执行 `python -m pytest <124 paths> -q --no-cov -m 'not serial and not perf and not host and not real_provider' -n 4 --dist loadfile`：**1950 passed，91.81 秒**。包含 8 个 mock/E2E 文件。此为 P2 修订前完整闭包；后续仅移动可选 scan 的异常边界。
3. `scripts/dev-verify.py run --base dev --tests tests/test_context_session_observation.py --skip-tier deterministic --receipt /tmp/zf-session-delivery-20261113/dev-verify-receipt.json`：明确未执行扩大后的 deterministic tier，改由第 2 项全 selected closure 验证；自动 serial **10 passed**、sentinel **53 passed**。
4. P2 最终改动后，`pytest tests/test_context_session_observation.py tests/test_context_delivery.py tests/test_context_delivery_integrity.py tests/test_call_result_admission.py tests/test_artifact_read_ledger.py tests/test_artifact_input_recovery.py tests/test_artifact_required_resume.py tests/test_artifact_output_identity.py tests/e2e/test_stale_contract_handoff_mock_e2e.py tests/e2e/test_agent_owned_verification_mock_e2e.py -q --no-cov`：**156 passed，16.78 秒**；新增本文件为 41 项。另 `pytest tests/test_call_result_runtime.py -q --no-cov`：**21 passed**。
5. 最终 `bash scripts/dev-premerge-gate.sh`：manual docs currentness OK，**53 passed**；`pytest tests/test_instruction_stack_contracts.py -q --no-cov`：**8 passed**；`git diff --check` 通过。
6. 一次命令误含不存在的 `tests/test_call_result_submit.py` 导致 exit 4/no tests，随后纠正路径完成第 4 项；不把该次失败算作通过。

运行证据在 `/tmp/zf-session-delivery-20261113/{plan.json,bounded-closure-command.json,bounded-closure.log,dev-verify-receipt.json}`。未启用 real-provider/host tier；没有启动、重启或干预 LS Worker。最终代码仍未启用 198 C8，不承诺减少 154,948 bytes shared 重读。

独立复核：`session-observation-review/p2-fix-recheck.md`，SHA `1cc668b14e06388bf6d5175d35c77708ff7b9535e5aacb9246a5721b785140bf`。独立临时目录真实 caller 再现并确认 P2 已闭合，限定本片无剩余 confirmed finding；不替代上述测试证据。root 保留唯一 dev 合并权。
