# 技能分发和调用兼容

> 状态: done

实现 commit：`99e6e71b chore: streamline ZaoFu review skill with paired evaluation`

来源：用户 2026-10-20 已批准在独立 worktree 生成并实施本批次。

范围：将现有 provider UI 元数据纳入 canonical；真实 materialize / briefing / channel 调用覆盖完整资源，不改变 runtime 实现。

验收：新增回归与既有直接调用测试 -> verify: Codex/Claude copy/symlink 资源完整；metadata 按需加载保持；临时自定义状态目录和源技能不被污染。

基线：`7bee177aabe46b4f94dc7cf7b74551f6b8d4c72b`；参考 `/tmp/gm/agent-skills@b8caa26` 的渐进披露结构，不导入其框架规则。

非目标：runtime bug 修复、前端框架迁移、全量技能改造、合并或 push。

## 实施与验收记录

- 完整包回归 -> verify: `tests/test_review_skill_package.py` 19 passed。
- Codex/Claude copy/symlink、briefing on-demand、Channel 自定义 state_dir -> verify: 实际函数调用通过，source package 不被改写。
- injection、channel templates、self-repair、provenance 与结构闭包 -> verify: 最终 dev-verify 自动选择 10 个文件，173 passed、2 skipped、1 个基线失败。
- 基线失败：公开导出无 `.gitlab-ci.yml`；原始基线同一测试已在隔离目录复现。没有修改或跳过该测试，也不声称全绿。
- manual-docs currentness、技能 validator、diff whitespace -> pass。

命令、原始回执与限制见[对照报告](../../docs/records/2026-10-20-review-skill-evaluation.md)。
