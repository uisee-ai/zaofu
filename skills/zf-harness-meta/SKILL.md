# Skill: zf-harness-meta

> Sprint: ZF-LH-META-SKILL-001 (doc 26 §5.1)
> 目标角色: dev / orchestrator (selectively)
> 状态: net-new (P2)

## 目的

**教 agent 学会修 zaofu 自己**。当 worker 任务涉及修改
`src/zf/`、`docs/design/`、`docs/impl/`、`backlogs/`、
`skills/zf-*/` 时，本 skill 提示 worker 遵循 zaofu 自己的
工程纪律（CLAUDE.md），避免循环依赖 / 自我破坏。

核心约束：让 agent 在改 harness 自身
时不要走捷径。

## 操作规约

当 task scope 包含 zaofu 自身代码 (`src/zf/...`) 时，**额外检查**：

1. **CLAUDE.md hard rules** 是否被破坏：
   - 引入第二控制面（除 zf.yaml）？→ 拒绝
   - worker 直接写 truth？→ 拒绝
   - 跳过 wire-up grep proof？→ 拒绝
2. **Validate-First**：实施前 `git grep` 确认问题仍存在（避免修
   已经被上游 commit 解决的)
3. **Wire-Up Discipline**：新组件必须有 caller，否则 Class D
4. **不破 1700+ 测试**：每次 `pytest --no-cov` 全绿是 hard gate
5. **Conventional commit prefix**：feat/fix/refactor/docs/test/chore

## 反模式

- ❌ 改 zaofu kernel 但跳过 CLAUDE.md rule check
- ❌ 加新 module 但没写 caller（典型 Class D）
- ❌ 改 events.jsonl 写法（必须走 EventWriter）

## 关联

- `CLAUDE.md` (zaofu 工程纪律)
- `docs/design/40-long-horizon-runtime-master-plan.md` (long-horizon
  实施路径)
