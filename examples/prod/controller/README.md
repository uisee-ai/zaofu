# Production Controller Examples

这些示例是 operator 面向生产 run 的短 YAML 入口。它们展示 typed
controller + profile composition,不是 runtime 执行时的第二控制面。
这是新的推荐产品入口;`examples/prod/new/*.yaml` 只作为 expanded LKG/E2E
回归样本保留,不推荐 operator 手写复制。

## 入口选择

| 入口 | 适用场景 | YAML | 主要 profile |
|---|---|---|---|
| PRD build | 从 PRD / idea 构建新功能或产品 | `prd-fanout-v3.yaml` | `prod-runtime/v1`, `prd-codex-lanes/v1` |
| Issue fix | 从 bug / backlog / failure report 修复问题 | `issue-fanout-v3.yaml` | `prod-runtime/v1`, `issue-codex-lanes/v1` |
| Refactor | 从已有系统重构或迁移到新实现 | `refactor-lane-v3.yaml` | `refactor-controller-runtime/v3`, `RefactorFlow v3` |

## 组合规则

- `profile_sources` 只在 load/render/start 前解析。
- `uses` 引用 profile name/version,不直接把 profile 文件当 runtime include。
- `Workflow` / `RefactorFlow` 只编译 canonical `workflow.stages`、roles、
  pipelines 和 schema;runtime 仍只消费 expanded `ZfConfig`。
- common profile 放跨 PRD/Issue/Refactor 复用能力;workflow/project 专项事实
  不应混入 common。

## 验证

```bash
uv run zf config render --config examples/prod/controller/prd-fanout-v3.yaml \
  --output /tmp/prd.rendered.yaml --lock /tmp/prd.render-lock.json
uv run zf config render --config examples/prod/controller/issue-fanout-v3.yaml \
  --output /tmp/issue.rendered.yaml --lock /tmp/issue.render-lock.json
uv run zf config render --config examples/prod/controller/refactor-lane-v3.yaml \
  --output /tmp/refactor.rendered.yaml --lock /tmp/refactor.render-lock.json
```
