# Production Controller Examples

这些示例是 operator 面向生产 run 的短 YAML 入口。它们展示 typed
controller + profile composition,不是 runtime 执行时的第二控制面。
这是新的推荐产品入口;`examples/prod/new/*.yaml` 只作为 expanded LKG/E2E
回归样本保留,不推荐 operator 手写复制。

## 入口选择

| 入口 | 适用场景 | YAML | 组合来源 |
|---|---|---|---|
| PRD build(fanout) | 从 PRD / idea 构建新功能或产品(多 lane) | `prd-fanout-v3.yaml` | `prod-runtime/v1` + `PrdFlow` 展开(roles/stages/pipeline 由 flowProfile 生成) |
| PRD build(light) | 塞得进单上下文的小 PRD(单 lane,免 scan/plan 扇出;kernel 入口合成 task_map + 铸 run goal;judge.passed 后自动 ship + goal 终态) | `prd-light-v3.yaml` | `prod-runtime/v1` + `PrdFlow topology: light` 展开 |
| Issue fix | 从 bug / backlog / failure report 修复问题 | `issue-fanout-v3.yaml` | `prod-runtime/v1` + `IssueFlow` 展开 |
| Refactor | 从已有系统重构或迁移到新实现 | `refactor-lane-v3.yaml` | `refactor-controller-runtime/v3` + `RefactorFlow v3` 展开 |

注:roles 由 flowProfile 展开生成;`common/profiles.yaml` 里的 RoleSet
(`prd-codex-lanes/v1` 等)是可选组合件,v3 入口并不依赖它们。

## 技能与执法姿态(2026-07-08 起,以 `common/profiles.yaml` 为准)

- **技能只来自仓内两目录**(2026-07-08 起,agent-skills 外部基线经 1v1
  核实后退役):`skills/` 承载 zf-* 边界/合约技能与 `zf-yoke-*-role-context`
  角色 wrapper(planner / dev-worker / test-evaluator / quality-gate,已接
  入各 stage bundle);`yoke/` 方法论族经 wrapper 的 frontmatter
  `dependencies` 闭包自动物化(tdd-evidence、verify-review、
  vertical-slicing、grill、git-evidence、source-verification 等),briefing
  索引标注 "dependency of";无 wrapper 的 stage(scan / discovery /
  module-parity-scan 等)按需直挂裸 yoke 名,经 yoke overlay 解析。
  `common/profiles.yaml` 使用相对仓库路径 `../../../skills`,避免 controller
  示例绑定某台机器的 checkout 路径;`zf profile bootstrap --apply` 会把启用
  的 skill/yoke 依赖闭包 vendor 到目标项目本地 `skills/`,并把拷贝后的
  profile source 重写为 `skills`。
- **执法档**:flowProfile 展开默认 `schema_profile: canonical-dag/v3`(读者
  子报告证据档:child 完成事件 `non_empty[summary, evidence_refs]` +
  `report.requirement_coverage_matrix` 至少一行);两个 prod 预设开
  `verification.event_schema.mode: blocking`(违约完成事件落盘即换
  `discriminator.failed` 走返工)、`report_evidence_gate: fail_closed`、
  `runtime.skills.strict: true`(启用技能缺失在 validate/start 前暴露)。
- briefing 样例由 FIX-14 教育机制按 schema 规则自动镜像必填字段,合规
  agent 照抄模板即过档。
- **合并候选树质量门(多 lane 必配)**:多 lane fanout_writer 流不配
  `quality_gates` 时,candidate 合成树不经任何验证即进 judge(跨 lane
  偏斜 per-lane verify 原理上不可见,r4 F10)。`zf start`/`zf validate`
  对此 **fail-closed 拒绝**——按各 yaml 内注释模板填项目真实命令
  (typecheck + 单测)启用,或显式豁免
  `workflow.allow_unverified_candidate: true`(观测型运行)。
  单 lane(light)无此风险,保持 WARN。通过 `zf profile bootstrap --apply`
  生成新项目时,若探测器已经得到栈级 gate 命令,会自动写入
  `quality_gates.static.required_checks`,避免生成后立刻被 candidate gate
  拒绝。
- **auto-ship parity**:8 个入口统一 `auto_ship_on_judge_passed: true`
  ——judge.passed(终局门)后 kernel 自动把 candidate 合入
  `ship_target_branch`(默认 main),不再手工 `git merge candidate/*`。
- **evidencePolicy 驱动执法**:`evidencePolicy: strict_refs` 由 loader
  派生 `event_schema.mode: blocking` + `report_evidence_gate: fail_closed`
  (单一控制点;显式 `verification.*` 配置优先,是逃生门)。

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
uv run zf config render --config examples/prod/controller/prd-light-v3.yaml \
  --output /tmp/prd-light.rendered.yaml --lock /tmp/prd-light.render-lock.json
uv run zf config render --config examples/prod/controller/issue-fanout-v3.yaml \
  --output /tmp/issue.rendered.yaml --lock /tmp/issue.render-lock.json
uv run zf config render --config examples/prod/controller/refactor-lane-v3.yaml \
  --output /tmp/refactor.rendered.yaml --lock /tmp/refactor.render-lock.json
```
