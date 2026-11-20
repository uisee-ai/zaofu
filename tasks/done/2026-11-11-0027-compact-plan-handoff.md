# 紧凑中文 Plan 与完整任务阅读

> 状态: done — 369f2003 fix: make Plan handoffs concise and keep execution details reachable；092704c8 fix: map generated Chinese Plan headings into approval overview

来源：用户批准执行 206 下一批目标；基线 dev@3ffacda5。

改进作者与 Critic 方法、默认审批概览、Plan 身份、旧正文未分配章节导航。

## 验收

- 同版来源语言及共享单一正文 -> verify: Skill 同步与生成样本核验
- 本片义务集中、旧版相邻章节可导航 -> verify: 单元与 Docker 双视口浏览器回归；全文下载字节不变

## 边界

仅独立 worktree 和 /tmp 隔离项目，不操作真实 8001，不 push。

## 实施与验收记录

Skill 三份同步；真实 Planner/Critic 分发摘要各 11/11 一致且实际读取方法。Kanban 同版正文摘要不变，桌面/手机默认概览与任务正文可达；Docker 阅读 34 项通过。单任务与共享/修订试点核验通过；真实正文仍偏长，未宣称普遍减重。

验证：dev-verify plan/run --base dev（显式 tests/test_plan_readiness_skills.py、test_prd_ui_provider.py、test_prd_ui_audit.py、test_readme_cli_oracle.py）→ Python 90 passed、1 skipped，Web typecheck/单测通过；生产 build/bundle 与 Docker 34 项通过；manual-docs check 通过。非全量 pytest。

详细结果：docs/impl/206-spec-quality-evaluation.md 与本地 reports/spec206-delivery-20261111/evaluation.md。原始失败、协助恢复、Channel blocked、评分与后续三项 proposed 候选均保留；真实 8001 与主 checkout 未修改，未 push/merge。
