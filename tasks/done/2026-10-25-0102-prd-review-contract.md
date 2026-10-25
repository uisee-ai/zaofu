> 状态: done
> 实现 commit: 1e044a7 feat: strengthen generic plan approval review contract

# PRD Preview 人工审批合同

## 目标

把 PRD Preview 的人类审批入口标准化为 PRD、SDD、TDD、Decision Register 和审批检查清单，适用于所有带 authority bundle 的 Plan workflow，不写项目专用规则进 Kernel。

## 范围

- 更新 `zf-prd-plan-synth` 与 `zf-plan-execution-readiness` 的方法合同。
- Planner/Critic 必须提供可审阅的用户结果、系统边界/集成合同、验收到命令和证据映射、风险/决定及影响范围。
- `derived_plan_full` 只在完整合同存在时允许；缺项进入 rework。

## 验收

- step -> verify: skill contract 明确 PRD/SDD/TDD/Decision Register 必填内容，并同步 `skills/`、`.codex/skills/`、`.claude/skills/`。
- step -> verify: Planner/Critic 的既有 plan ports 能表达每个合同，不能以一句 summary 代替行为、接口或证据。
- step -> verify: 新增或更新聚焦测试验证缺失 review contract 时不宣称 `derived_plan_full`。

## 验证

- `pytest tests/test_skill_provenance.py tests/test_operator_plan_preview.py tests/test_reader_fanout_runtime.py tests/test_kanban_turn_context.py -q --no-cov` -> 185 passed, 1 skipped。
- `scripts/dev-premerge-gate.sh`（使用已安装依赖环境）-> 51 passed。
