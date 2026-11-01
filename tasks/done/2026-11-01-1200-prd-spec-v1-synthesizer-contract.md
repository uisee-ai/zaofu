# PRD Spec v1 Synthesizer 合同

> 状态: done — `945dd587 feat: add PRD spec contract and audience views`

依据：`docs/design/197-prd-spec-contract-and-readable-views.md` §5、§7.1；当前 Channel synthesis prompt 与 `zf-channel-discussion-synthesizer`。

## 范围

让新的正式 Channel PRD 按 `prd-spec.v1` 输出标准正文：文档控制、Overview、Background、Goals/Non-goals/Scope、用户画像与 P1/P2/P3 场景、User Flow、Functional Requirements、Business Rules/State、Non-functional Requirements、Success Metrics、Acceptance、依赖/风险/未决问题和可选 Rollout 附录。保留现有来源、决策、异议、质量合同和 readiness 字段。

## 验收

1. Synthesizer skill -> verify: 明确 Requirement 单一可观察行为、Scenario、Acceptance、Success Metrics 与 NFR 的输出规则，并区分 PRD 正文、Design、Tasks、Evidence。
2. Provider prompt -> verify: 正式 synthesis 要求 `spec_format: prd-spec.v1`、`document_control`、标准章节和结构化字段；旧 `prd_file` / `prd_composition` 路径仍可用。
3. Deterministic fake reply -> verify: 生成的固定合同包含完整标准章节，并能通过新的文档合同校验。
4. Legacy compatibility -> verify: 没有 `spec_format` 的历史 PRD 继续通过原有合同路径。

## 边界

不在 Skill 或 Prompt 中判断商业价值、优先级正确性或文字说服力；不把 Provider、Workflow action、Docker 或内部事件写入产品正文。

## 实施与验证记录

实现提交：`945dd587 feat: add PRD spec contract and audience views`。

- `uv run pytest tests/test_channel_prd_document.py tests/test_channel_prd_composition.py tests/test_channel_prd_file.py tests/test_channel_reply_turn.py tests/test_channel_templates.py::test_discussion_start_dispatches_participants_and_synthesis_once tests/test_web_channel_delivery.py -q --no-cov` -> 95 passed。
- `uv run pytest tests/test_memory_rotate_concurrency.py tests/test_session_mutex.py tests/test_state_locks.py -q --no-cov -m serial` -> 10 passed。
- `npm run typecheck && npm run test:unit && npm run build` -> passed。
- `bash scripts/dev-premerge-gate.sh` -> 51 passed。
