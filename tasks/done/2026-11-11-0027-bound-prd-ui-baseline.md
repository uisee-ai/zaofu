# 两入口冻结验证基线

> 状态: done — dfb4778f test: bind product delivery checks to frozen inputs and candidates

来源：用户批准执行 206 下一批目标；基线 dev@3ffacda5。

复用 PRD UI runner，记录完整 Skill 摘要，支持两入口共同产品输入；产品验收与流程协议分开。

## 验收

- 两入口 fake 验证 -> verify: 原始运行与独立审计通过
- 新真实运行 -> verify: 同一候选、冻结输入、实际 Skill 送达和 Plan 身份可追溯

## 边界

仅独立 worktree 和 /tmp 隔离项目，不操作真实 8001，不 push。

## 实施与验收记录

最终 fake Kanban/Channel 均通过审计；两个新真实入口同一冻结产品输入与 Skill 来源可追溯。Kanban 经协助恢复形成合格 Preview；Channel 三次工件准入失败最终 blocked，无合格 Preview。完成的是测试基线和真实结果评估，不能把负例计为成功或宣称阻塞已修复。

验证：dev-verify plan/run --base dev（显式 tests/test_plan_readiness_skills.py、test_prd_ui_provider.py、test_prd_ui_audit.py、test_readme_cli_oracle.py）→ Python 90 passed、1 skipped，Web typecheck/单测通过；生产 build/bundle 与 Docker 34 项通过；manual-docs check 通过。非全量 pytest。

详细结果：docs/impl/206-spec-quality-evaluation.md 与本地 reports/spec206-delivery-20261111/evaluation.md。原始失败、协助恢复、Channel blocked、评分与后续三项 proposed 候选均保留；真实 8001 与主 checkout 未修改，未 push/merge。
