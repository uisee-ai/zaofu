---
name: zf-harness-spec-freeze-contract
description: "Use when PRD/spec/design artifacts must be frozen into stable artifact refs and SHA-256 attestations before downstream implementation, review, verify, or judge workers consume them."
---

# Skill: zf-harness-spec-freeze-contract

> Sprint: ZF-SKILL-MP-006 (doc 39 §4.7)
> 目标角色: orchestrator / arch / critic
> 状态: net-new
> Absorbs zf-harness-plan-attestation. (ZF-PWF-SKILL-003, doc 41 §5)

## 目的

把 PRD / spec / 设计文档 freeze 为 **artifact ref + SHA-256 attestation**，
让 dev/review/test/judge 不依赖对话记忆。一份 spec 被 freeze
之后，任何对它的引用都通过 `spec_ref` 字段 + attestation 验证。

## 职责边界（谁写 attestation）

attestation 的**写入与校验是 kernel dispatch 路径的事**，不是 agent 的事
（`orchestrator_dispatch.py::_project_state_packet_on_dispatch` 在每次
dispatch 后自动 attest state packet，并对 `state-packet.json` 做
verify-before-overwrite）。agent 的职责是：

1. **保证 spec 落盘**（artifact，不是聊天记忆）
2. **把 `spec_ref` 写进 task contract**（经 `task.contract.update` 事件，
   不直改 `kanban.json`）
3. **发现 `task.contract.invalid`（`reason=state_packet_hash_mismatch`）
   审计事件时停手上报**（emit `memory.note category=attestation_mismatch`
   —— skill-owned 约定，无内核校验），而不是自己写/改
   `.zf/attestations/*.attest.json`

## 操作规约

### orchestrator backlog synth 阶段

完成 task contract 合成后，**强制**：

1. 把 spec 落盘到 `docs/specs/<task_id>-spec.md`（如尚未存在）
2. 把 `spec_ref` 写进 `task.contract.spec_ref`（经
   `zf emit task.contract.update --task <task_id> --payload-file ...`）
3. attestation 由 kernel dispatch 路径自动完成。如需在 kernel 侧代码 /
   白名单工具中直接调用，API 参考（file / object 两个入口按场景选，
   勿混用 import 与调用）：

   ```python
   from zf.core.security.attestation import (
       attest_file_artifact, attest_object_artifact,
       ATTESTATION_KIND_TASK_CONTRACT,
   )

   # 落盘文件 artifact（spec、manifest 文件本体）
   attest_file_artifact(
       state_dir,
       artifact_path=spec_path,  # Path 对象
       kind=ATTESTATION_KIND_TASK_CONTRACT,
       task_id=task_id,
       dispatch_id=dispatch_id,
       source_events=("evt-arch-...", "evt-critic-..."),
   )

   # 内存对象 artifact（task contract dict）
   attest_object_artifact(
       state_dir,
       obj=task_contract_dict,
       artifact_path=f"task-contract:{task_id}:{dispatch_id}",
       kind=ATTESTATION_KIND_TASK_CONTRACT,
       task_id=task_id,
       dispatch_id=dispatch_id,
       source_events=("evt-arch-...", "evt-critic-..."),
   )
   ```

### dev/review/test/judge dispatch 时

worker **不自行** import zf Python API 校验或写 attestation。kernel 已在
dispatch 路径做 verify-before-overwrite：hash mismatch 时发
`task.contract.invalid`（`payload.reason=state_packet_hash_mismatch`）
审计事件，**不阻断 dispatch**。worker 的语义是：读
`task.contract.spec_ref` 指向的落盘 artifact；若发现上述审计事件，
停手上报，不要静默继续。

## 6 类 attestation kind（自 zf-harness-plan-attestation 并入）

> **仅 `state_packet` 已接线**（dispatch 路径自动 attest + verify），
> 其余 5 类为设计目标（doc 41 ZF-PWF-ATTEST-001）：kind 常量已在
> `zf.core.security.attestation` 定义，但无 kernel 消费者。

| kind                | 设计触发时机                      |
|---------------------|-----------------------------------|
| `task_contract`     | orchestrator backlog synth 完成   |
| `state_packet`      | SP-001 projector 每次 write ✅已接线 |
| `context_manifest`  | CTXMAN-001 写出 context.jsonl     |
| `role_briefing`     | dispatch briefing 生成完成        |
| `skills_manifest`   | skills.lock 写入                  |
| `research_index`    | research artifact index 更新      |

## Mismatch 处置（现状，勿假设 fail-closed）

- kernel 仅在 dispatch 侧对 `state-packet.json` 做 verify；mismatch 只发
  `task.contract.invalid` 审计事件，不进入 degraded/blocked，不阻断
  dispatch。
- recovery 路径当前**无** attestation 校验。"mismatch → degraded/blocked
  拒绝继续" 是 doc 41 的 backlog 验收目标，不是现状。
- agent 语义：看到该审计事件 → 停手上报，由 operator / human 处置。

## 反模式（以及被谁抓）

- ❌ Spec 只存在 user.message 聊天里
- ❌ task.contract.spec_ref 是空字符串 —— kernel
  `supervisor_plan_integrity` 的 `task-missing-plan-ref` 检查会抓
  （"task contract lacks plan_ref/spec_ref/source_backlog_task_id"，warn）
- ❌ Spec / contract 被修改但没重算 attestation —— 仅 state_packet 会被
  dispatch 侧 verify-before-overwrite 抓到（审计事件）；其余 kind 目前
  无机器守卫
- ❌ 直接编辑 `.zf/attestations/*.attest.json`（agent 不直触 runtime truth）
- ❌ 发现 hash-mismatch 审计事件后静默继续（必须上报）

## 守护测试

- `tests/test_attestation.py` — 覆盖 attest → tamper →
  `verify_*.tampered == True`
- `tests/test_pwf_invariants.py::test_inv_i62_six_attestation_kinds_only`
  — 6 kind 封闭集

## 关联

- ZF-PWF-ATTEST-001 (sha256 helpers + 6 类 attestation, doc 41)
- ZF-LH-SP-001 (State Packet refs.task_ref)
- ZF-TR-CTXMAN-001 (Context manifest)
