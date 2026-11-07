# R39：证据义务与可写范围一致性

> 状态: done — 9626154e fix: reconcile planned evidence output permissions

来源：用户授权持续监工、修复并记录问题；LS-v1 Grants 当前合同要求三份证据，但冻结写入范围不含其目录。只读 40 任务核对发现 32 项路径缺口，其中 done 16、backlog 15、in_progress 1，另 1 项 base_path 未明确。此统计不判定历史产品失败。

## 本批范围

通用规划/审查方法明确证据生产阶段、写入者、路径与消费入口；不在 Kernel 解析任意项目语义字典自动扩权，不改冻结任务。同步两 provider 分发；LS 活动合同由独立受控恢复处理。

## 验收

- 复现路径冲突 -> verify: LS docs/records/2026-11-07-evidence-scope-audit.json 与 hook_recv._evaluate_allowed_paths_guard 对照。
- 补齐规划和审查方法 -> verify: 指定文件输出、独立 Verify 与历史缺口分别处理；不删除证据或扩大共享根目录。
- 验证角色物化和分发 -> verify: test_plan_readiness_skills / test_skill_provenance、dev-verify 与 premerge sentinel。
- 合并同步 -> verify: dev 合并、LS 源技能精确同步，保留其原有未提交 SKILL.md，活动冻结副本不冒充热更新。

## 边界

此批完成表示方法修复，不表示 40 项任务已完成证据验收。LS 合同修订、实际恢复和历史对账在监工记录中分别跟踪。

验证：dev-verify docs/skills 层 41 passed / 1 skip；角色物化与 metadata 37 passed / 1 skip；sentinel 53 passed。未运行全量 pytest 或新真实 Provider E2E。
