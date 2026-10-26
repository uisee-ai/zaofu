> 状态: proposed

# Runtime result and task workdir guard compatibility

## 问题
历史 worker 结果把 verdict 放在 `impl_self_check`，projection 仍显示 pending；Task Pipeline worker 在合法 task-stage worktree 写入时被 scope guard 拒绝。

## 范围
- implementation result adapter/projection 从 immutable control-result sidecar 恢复 impl_self_check verdict。
- workdir guard 接受当前 actor/task dispatch 记录的 task-stage workdir，同时继续拒绝项目根和 state dir越权写入。

## 验收
- step -> verify: nested impl_self_check verdict=passed 会解锁 verify projection。
- step -> verify: 合法 task-stage worktree 写入通过，项目根越权写入仍拒绝并产生 worker.scope_write.rejected。
- step -> verify: `uv run pytest tests/test_call_result_profiles.py tests/test_hook_recv_codex.py -q --no-cov` 通过。
