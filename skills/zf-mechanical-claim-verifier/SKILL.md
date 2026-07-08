---
name: zf-mechanical-claim-verifier
description: "Use when ZaoFu needs to convert agent completion statements, design assertions, review approvals, channel synthesis, or gate reports into a structured claim set with evidence refs, deterministic verdicts, passRate, failedClaims, unknownClaims, and regenPlan."
---

# ZaoFu Mechanical Claim Verifier

## Purpose

Turn narrative statements such as "done", "tested", "safe", "covered", or
"ready" into explicit claims that can be checked against evidence. This skill
extends `zf-harness-evidence-collection` and
`zf-harness-verification-checklist`; it does not decide runtime truth by itself.

## Hard Rules

- Prefer command/file/event evidence over narrative evidence.
- Do not count an unsupported claim as pass.
- Do not use an LLM score as `passRate`; derive it from claim verdicts.
- Do not emit terminal completion events or edit runtime truth.
- If a referenced artifact path is unavailable, mark the related claim
  `unknown` or `fail`.
- claim 证据必须锚定 child payload 的 `target_commit`(pin-commit,
  FIX-9/15),禁止对派发后漂移的树取证。

## Claim Set Artifact(内部工作底稿)

`claim-set.json` 是本 skill 的**内部工作底稿,不是 emit 的产品**——最终交给
kernel 的是 schema 校验的 verify report(见「与 verify report 合约配对」)。
下面的 `schema_version: zf.claim_set.v1` 是 **skill-owned 报告约定(无内核
校验)**:`grep -rn "zf.claim_set.v1" src/zf/` 无验证器。写在
`docs/impl/<work>/claim-set.json`:

```json
{
  "schema_version": "zf.claim_set.v1",
  "subject": {
    "kind": "task|candidate|plan|review|test|channel|release",
    "id": "TASK-001",
    "source_refs": ["docs/plans/example.md"]
  },
  "claims": [
    {
      "claim_id": "CLM-001",
      "claim_text": "The implementation is covered by a focused unit test.",
      "claim_type": "implementation|test|design|risk|scope|artifact",
      "required_evidence": ["test command", "test output", "changed files"],
      "evidence_refs": ["reports/test-output.txt"],
      "verdict": "pass|fail|unknown|not_applicable",
      "reason": "The referenced test output shows the focused test passed."
    }
  ]
}
```

## Verifier Output(内部评分,非 emit 终点)

内部汇总打分,**不是 verify 角色的产出终点**。`schema_version:
zf.claim_verifier_result.v1` 同为 **skill-owned 报告约定(无内核校验)**
(`grep -rn "zf.claim_verifier_result.v1" src/zf/` 无验证器);`passRate` /
`failedClaims` / `unknownClaims` / `regenPlan` / `owner_hint` / `needs_regen`
均为 skill-local 字段,内核不消费。rework 路由与 judge 终审只读 verify
report 的结构化字段(见下节);本节仅把 claim 裁决整理成可映射的形状。

```json
{
  "schema_version": "zf.claim_verifier_result.v1",
  "claim_set_ref": "docs/impl/example/claim-set.json",
  "passRate": 0.67,
  "failedClaims": ["CLM-002"],
  "unknownClaims": ["CLM-003"],
  "regenPlan": [
    {
      "owner_hint": "dev|arch|research|review|test|operator",
      "action": "Produce focused test evidence for CLM-003",
      "required_evidence": ["pytest output", "artifact path"]
    }
  ],
  "evidence_refs": ["reports/test-output.txt"],
  "verdict": "pass|fail|needs_regen"
}
```

Compute `passRate` as:

```text
passing claims / claims with verdict pass|fail|unknown
```

Ignore `not_applicable` claims in the denominator.

## 与 verify report 合约配对(emit 的产品)

claim 裁决只有落进 kernel 现行 verify report 合约才进机器。verify/review
reader 完成时经 `verify.child.completed` / `verify.child.failed`
(orchestrator_fanout.py:1229-1230)的 event schema 校验;报告字段见
orchestrator_fanout.py:6511-6520 的 schema 教育占位。映射规则:

- **每条 claim verdict → `requirement_coverage_matrix` 一行**:
  `requirement_id` 取 task contract/PRD 验收条款(**不是** CLM-xxx 编号),
  `status` covered/partial,`evidence_refs` 给可复跑路径。矩阵有
  `non_empty` 档位(FIX-14,event_schema.py:87-89;r4 全轮 9/9 份报告矩阵
  0 行的实锚),**空矩阵直接 schema 拒收**——只装载本 skill 的 reader 若
  只交 passRate/regenPlan 而不落矩阵,报告会被拒或交出 0 信号。
- **failed / unknown claim → `gap_findings` 条目 + `replan_recommendation`**:
  gap_findings 给文件级定位,replan_recommendation 给路由动作——这两个才是
  rework 路由消费的结构化字段。
- **裁决词表对齐 gap_findings,不另造**:发现分级沿用 `yoke/verify-review`
  (Critical/必改进 gap_findings,Nit 不进)。本 skill 的 `owner_hint` /
  `needs_regen` 只是 skill-local 底稿标注,映射到 kernel 的 rework 路由
  (`rework_owner_hint`),不要当第二套裁决词表下发。

## 证据锚定 pinned commit

reader child 派发时 `target_commit` 被写进 child payload(FIX-9,
orchestrator_fanout.py:6560 `_pin_reader_target_or_reject`),判审收敛门
按该 commit 判重(FIX-15,orchestrator_fanout.py:6390-6505
`fanout.retrigger.suppressed`)。所以:

- claim 的 evidence **必须锚定 pinned `target_commit`**:取证前
  `git rev-parse HEAD` 对齐 briefing 的 `target_commit`,不符先报 workdir
  mismatch,**禁止对派发后漂移的 workspace/树取证**——漂移取的 evidence 会
  被判重门按错误 commit 处理,污染收敛。
- 判「是否已集成/已审」用 `git rev-list --cherry-pick` 而非 hash 相等
  (等价补丁 hash 不同),见 `yoke/git-evidence`。

## Failure Policy

- Missing required evidence -> `unknown` unless the source explicitly
  contradicts the claim, then `fail`.
- Missing artifact path for an artifact claim -> `fail`.
- Test, scope, or risk claims with no command/file evidence -> `unknown`.
- Any failed or unknown required claim -> output a concrete `regenPlan`.

## Output Summary

Return:

- claim count, pass count, fail count, unknown count
- `passRate`
- top failed or unknown claims
- regen plan owner hints
- evidence refs used
