# Skill: zf-harness-plan-attestation

> Sprint: ZF-PWF-SKILL-003 (doc 41 §5)
> 目标角色: arch / critic / orchestrator
> 状态: net-new

## 目的

让 freeze 后的 task contract / spec / context manifest **有 SHA-256
hash 锁**。recovery / dispatch 时如果 hash 不匹配 → 进入 degraded /
blocked，**拒绝静默继续**。封堵 "prompt 被悄悄篡改" + "context
manifest 漂移" 两类风险。

## 操作规约

当 arch / critic / orchestrator 完成一份将被下游 worker 消费的
artifact 时：

1. **freeze artifact + 调用 attestation API**

   Python 侧（kernel 已就位）：

   ```python
   from zf.core.security.attestation import (
       attest_object_artifact, ATTESTATION_KIND_TASK_CONTRACT,
   )

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

2. **6 类 artifact 都要 attestation**

   | kind                | 触发时机                          |
   |---------------------|-----------------------------------|
   | `task_contract`     | orchestrator backlog synth 完成   |
   | `state_packet`      | SP-001 projector 每次 write       |
   | `context_manifest`  | CTXMAN-001 写出 context.jsonl     |
   | `role_briefing`     | dispatch briefing 生成完成        |
   | `skills_manifest`   | skills.lock 写入                  |
   | `research_index`    | research artifact index 更新      |

3. **不允许 worker 修改 attestation**

   worker 可 **报告** hash mismatch (emit `memory.note category=
   attestation_mismatch`)，但不能写 `.zf/attestations/*.attest.json`。

## 反模式

- ❌ 改了 task contract 没重算 attestation
- ❌ 直接编辑 `.zf/attestations/*.attest.json`
- ❌ recovery 时遇到 mismatch 直接继续（必须升级到 human review）

## 守护测试

`tests/test_attestation.py` — 28 测试覆盖
attest → tamper → verify_*.tampered == True。
`tests/test_pwf_invariants.py::test_inv_i62_*` — 6 类锁定。

## 关联

- ZF-PWF-ATTEST-001 §3.1-§3.5 (sha256 helpers + 6 类 attestation)
- ZF-LH-SP-001 (State Packet)
- ZF-TR-CTXMAN-001 (Context manifest)
