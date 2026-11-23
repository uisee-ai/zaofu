# 审核方法与证据表达补齐

> 状态: done — `a9ade0e4 docs: ground review methods in scoped evidence and runtime authority`

用户批准借鉴本地 my-ai-coding-workflow 的审核方法并继续现有运行。基线为
`b8c718b0`；沿用既有 P0 的身份、权限、收据与恢复实现，不新增状态机或扩大验收范围。

## 范围与验收

- 实际产物与真实入口 -> verify: 明确 staged/unstaged/untracked、固定 base/target、
  工作树与提交的区别；Reader 仍只走原获准读取入口。
- 审核结论与未验证项 -> verify: 覆盖当前条款、触发路径、观察与证据；无 finding 仍
  报告检查范围，沿现有 summary/profile 输出，不让 Critic/Judge 修改被审对象。
- 相邻回归与错误记忆 -> verify: 选择有实际影响的相邻行为，复用已有 TDD/回归方法；
  教训含来源与适用范围，不增加重复测试或 runtime 直接写入路径。
- 分发与启用 -> verify: canonical 技能与已跟踪 provider 镜像一致；隔离物化校验依赖、
  reference 和 digest；当前在途 attempt 保留原方法，后续启用状态单独记录。
- 运行延续 -> verify: 保留原 Run、16 项完成事实、候选与历史资格结论，不重开 Planner。

来源：`/home/min/workspace/my-ai-coding-workflow` @ `5890e0d`，只提炼方法，未执行其
安装器、hook 或审查过程中直接修代码的流程。记录实际验证层级，不宣称语义效果已证明。

## 实施与验证

更新既有 verification checklist、项目验证 reference、Critic role context 和 persistent
working memory；同步两套已跟踪 provider 镜像。保留现有 TDD、Thin Judge 和恢复代码。

- 按 dev-verify 的 docs/skills 影响闭包执行：41 passed、1 skipped。
- dispatch treatment 与 idle recycle 隔离测试：8 passed。
- 当前配置中 Critic、Verify、Judge 三类角色的隔离技能物化、reference 字节一致性和
  frozen treatment 验证通过；未调用真实 Provider，未写入 live runtime state。
- ZaoFu 原生 metadata parser 校验三个修改的 Skill 均无 warnings。通用 Codex
  quick_validate 对 memory Skill 通过，对另两个已有 ZaoFu 扩展字段不兼容；保留
  stages/dependencies/auto_inject 等运行时元数据，未为通用检查器删除字段。
- 本次只证明方法包与分发契约正确；改善真实语义判词的效果等待实际运行证据。

启用边界：更新配置指向的 canonical 技能源，保留在途 Critic 的物化包和原冻结 digest。
后续原生派发重新物化并冻结新方法；不修改当前 operation，不重启控制面或 workers，
不把 source HEAD 更新宣称为在途模型已经读到新 Skill。

2026-11-23 05:05 UTC 已将 live 技能源快进至上述实现提交；部署前后在途 Critic
技能包逐文件 SHA256 及 manifest SHA256 完全相同。原运行保持 running、16 项业务
任务 done，历史 qualification=closed_failed 保留。未合并 dev、未 push、未重启服务。
