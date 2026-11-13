> 状态: done — `a585f2de` chore: clarify work record handoff for worker roles

# WRC 证据封装与角色方法交接修复

授权：用户要求监工遇问题修复并记录；主监工明确授权本批在独立 worktree 实施并提交，主监工负责合并，不 push。
基线：dev `5bb322b666139ad60a89ade91a55efdd82def769`。

## 实证动机

LS-v1 Verify 在 2026-11-13 06:06 将项目根的原生 CommandExecution JSON 直接 publish，被 pinned workdir 路径校验拒绝；复制到任务工作区后于 06:08 再 publish，被四字段 schema 拒绝。CLI help 只有参数，Verify 可用技能未包含 Impl done-contract 的 WRC 说明。监工最初提供原文位置，没有同时解释 wrapper/附件用法，也承担提示不完整的责任。两次 CLI 试错不是产品失败，不要求 OA/RM 接管或重跑已通过 E2E。
完整只读记录：`/home/min/workspace/zaofu/ls-v1/docs/records/2026-11-12-delivery-entry-resume/t2-wrc-handoff-review.md`，SHA `9b962a0ff9ec5fff36af81275c185297097d5efd360bb54261b581fc25706492`。

## 范围与验收

- 复用通用 evidence-collection 技能提供唯一 WRC 方法和四字段 JSON 示例，Impl/Verify 能按需得到同一正文 -> verify: 实际角色依赖解析与 materialize_role_skills 物化测试，含 references 文件和两 provider distribution 一致性。
- publish help 解释 wrapper 与原生附件区别、pinned workdir 与 evidence_files 相对路径 -> verify: 从公共 CLI help 取 JSON 示例，走现有 publish 实际 caller 并检查记录/附件，不改变运行行为。
- 默认原结果 scratch/已存在 refs 继续有效，不新增 WRC 完成要求或写权限 -> verify: 审查共享方法边界及既有 work-record 路径/身份/schema 拒绝测试。
- 仅维护上述方法、CLI 帮助、必要 caller 测试及现有实现文档 -> verify: dev-verify plan/run + premerge sentinel；不运行全量 pytest，不改 MAIN/LS worker/runtime。

## 交付

待实现后记录精确验证、实现 commit、任务归档 commit，并保持工作树干净供主监工合并。

## 实施与验证进展

- 已将唯一 WRC 正文/附件方法放在现有 `zf-harness-evidence-collection/references/work-record.md`，Impl/Verify 直接依赖并按需触发，done-contract 委托共享方法。已同步两 provider distribution；不新增技能、状态机或 gate。
- CLI `publish --help` 示例通过真实 publish caller；已有路径、身份及 schema 反例回归保持。首轮聚焦 79 passed / 1 skipped；dev-verify 7 文件闭包 104 passed / 1 skipped。
- 首次 premerge gate 报 CLI 索引过期，按已有 `scripts/manual-docs.py generate` 重生成；仅两个 CLI reference 索引有内容差异，不改其它生成输出。
- 下一次技能消费只读核实：Task Pipeline 新 task-stage 绑定会通过 `_ensure_role_active` 的激活路径实时物化；同一活跃绑定不保证热更新。LS 项目 `skills/` 为实际 vendored 源，优先于 harness，因此 MAIN 合并后还需主监工受控同步对应项目技能包，再在下一次正常 task-stage 激活消费。单重启 watcher 不能替代源同步，本批未动 LS 或重启控制面。

## 最终验证记录

在本独占 worktree，使用 `PYTHONPATH=src /home/min/workspace/public/zaofu/.venv/bin/python`，确保导入本分支代码而非 MAIN：

- `scripts/dev-verify.py plan --base dev --tests tests/test_work_record_help.py --tests tests/test_work_record.py --tests tests/test_work_record_dispatch.py --tests tests/test_plan_readiness_skills.py --tests tests/test_skill_provenance.py`：选出 backend/docs/skills/tests 7 个测试文件，无未知源路径。
- 相同参数的 `scripts/dev-verify.py run`：104 passed，1 skipped（可选 dev-codex 示例的 `./skills/external`、`./skills/critic` 外部源不存在），没有跑全量 pytest。
- `PYTHONPATH=src bash scripts/dev-premerge-gate.sh`：manual docs currentness ok，53 passed。首次索引过期已按生成器修正，未跳过门禁。
- 4 个改动技能的 `read_skill_metadata(..., expected_name=...)`：无 warnings；3 份源码/分发副本及共享 reference 一致，真实角色物化测试通过。
- `git diff --check` 通过。没有改 MAIN 的 AGENTS/index、LS 技能源、worker、runtime 或 scratch；没有重启、merge、push。

## 交付结论

本批 4 条验收均完成；实现 commit `a585f2de`（chore: clarify work record handoff for worker roles）。所有运行/权限门禁保留，未引入 WRC 必交要求。归档提交仅更新任务状态并移动到 done。

补充核验：`tests/test_skill_provenance.py::test_dev_codex_example_materializes_each_role_skills_and_dependencies -q --no-cov -rs` 明确 skip 原因为 external skill source missing，不是示例 YAML 不存在；本记录已更正首次解释。生产 LS config 中 dev-lane-0/verify-lane-0 均存在且 materialize=copy；已向主监工提供以 `load_config` + `materialize_role_skills`、只写 `/tmp` 临时根，对 Impl/Verify × Codex/Claude 四组合核对共享 reference 与角色依赖的方法。待主监工显式补丁同步 LS vendored skills 后执行；本批未扩大到生产项目写入。
