# Skill: zf-harness-spec-freeze-contract

> Sprint: ZF-SKILL-MP-006 (doc 39 §4.7)
> 目标角色: orchestrator / arch / critic
> 状态: net-new

## 目的

把 PRD / spec / 设计文档 freeze 为 **artifact ref + SHA-256 attestation**，
让 dev/review/test/judge 不依赖对话记忆。一份 spec 被 freeze
之后，任何对它的引用都通过 `spec_ref` 字段 + attestation 验证。

## 操作规约

### orchestrator backlog synth 阶段

完成 task contract 合成后，**强制**：

1. 把 spec 落盘到 `docs/specs/<task_id>-spec.md`（如尚未存在）
2. 调用 attestation：

   ```python
   from zf.core.security.attestation import (
       attest_file_artifact, ATTESTATION_KIND_TASK_CONTRACT,
   )
   attest_object_artifact(
       state_dir, obj=task_contract_dict,
       artifact_path=f"task-contract:{task_id}:{dispatch_id}",
       kind=ATTESTATION_KIND_TASK_CONTRACT,
       source_events=("evt-arch-...", "evt-critic-...", "evt-user-..."),
   )
   ```

3. 把 `spec_ref` 写进 `task.contract.spec_ref`

### dev/review/test/judge dispatch 时

读 task.contract.spec_ref 并校验 attestation hash 匹配:

```python
from zf.core.security.attestation import verify_object_artifact
result = verify_object_artifact(
    state_dir, obj=current_contract, artifact_path=f"task-contract:{task_id}:..."
)
if not result.matched:
    # emit memory.note + 升级 — 不要直接继续
    ...
```

## 反模式

- ❌ Spec 只存在 user.message 聊天里
- ❌ task.contract.spec_ref 是空字符串
- ❌ Spec 被修改但没重算 attestation

## 关联

- ZF-PWF-ATTEST-001 (6 类 attestation)
- ZF-LH-SP-001 (State Packet refs.task_ref)
