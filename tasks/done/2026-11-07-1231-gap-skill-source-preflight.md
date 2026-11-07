> 状态: done

# Discovery gap 技能方法与实际来源对齐

授权范围：主会话批准只补现有 Split-quality Preflight 新段到 canonical 技能及两个 provider 分发副本，记录真实部署来源与后续机械预检缺口。主会话统一提交和产品升级；本任务不操作产品运行态、不改 validator。

- 补入已审阅方法段 -> verify: 与主工作区该新增段一致，原有正文不改。
- 同步分发与生成 -> verify: 三份技能逐字节相同；角色技能 materialization/来源优先级相关检查通过。
- 给出部署检查路径 -> verify: [实施说明](../../docs/impl/gap-skill-source-deployment-20261107.md) 记录 source manifest、真实旧/新摘要、新 dispatch materialization 核验，以及保留 Core 的操作边界。
- 记录改善边界 -> verify: global-rescan 提交前预检缺口仅记录，不自动添加 smoke 标记、不放宽 cap 或 gate。

验证：`dev-verify.py plan/run --base dev --paths` 精确五文件，`--tests tests/test_skill_provenance.py --no-parallel`；实际闭包为 instruction-stack contracts、skill provenance、structure discipline，39 passed / 2 skipped。另在自动清理的 `/tmp` 隔离目录调用真实 `materialize_role_skills`，以 `zf-goal-closure-replan-contract` 依赖链生成 Discovery 技能，确认项目源、dependency_of、manifest SHA 和三份分发逐字节一致；新 SHA 为 `043c348cd3d8802052b28d23e37fdee270943dc3b3e681055c751b6eaac6d3d9`。`git diff --check` 通过。无产品运行态写入。

实现提交：`26f37d37 fix: require complete gap task preflight in planning skills`。

真实部署：产品源已升级；新 Planner dispatch `run-fanout-prd-plan-evt-64319b1a-prd-planner` 的 runtime/skills-manifest.json 确认 source_name=project、dependency_of=zf-yoke-planner-role-context、SHA 为上述043c348c；其 codex-home 副本逐字节一致。旧 Discovery 冻结副本保持860e487b。Planner已真实读取本轮 briefing；技能部署不代表新Plan或业务验收通过。
