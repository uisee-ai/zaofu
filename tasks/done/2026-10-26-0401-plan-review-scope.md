> 状态: done
> 完成提交: cd496b5 feat: harden plan review lineage and runtime guards
> 验证: CLI/API scope 相关测试包含在 103 个 focused tests 中并全部通过。

# Plan review workflow-run scope

## 问题
`zf plan review` 同时展示不同 workflow run 的 pending plan，R2 与当前 R4 并列，容易误批旧计划；Dashboard pending API 也缺少显式 run scope。

## 范围
- CLI review 支持可选 workflow-run filter，并默认显示 workflow_run_id、generation 和 created timestamp。
- Web pending projection 返回稳定的 workflow_run_id/generation 字段，前端可以按当前 run 过滤。
- 不删除历史 pending plan，不改变审批语义。

## 验收
- step -> verify: CLI 不带 filter 仍向后兼容，并能区分不同 run。
- step -> verify: CLI 指定 workflow_run_id 只返回该 run 的 pending plans。
- step -> verify: 计划审批 API 的 pending 项包含 workflow_run_id 和 task_map_generation。
- step -> verify: `uv run pytest tests/test_plan_approval_cli.py tests/test_web_server.py -q --no-cov` 相关用例通过。
