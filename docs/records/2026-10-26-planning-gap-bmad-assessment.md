# 规划实跑缺口修复与 BMAD 复评

> 状态：实现已修复并完成针对性验证；真实模型 A/B 未完成，不宣称规划质量已得到统计证明。

基线：ZaoFu dev `50eb6ba1574a9869c18d2e0cab322bff529462d1`。
候选工作区：`zaofu-planning-gap-fixes`，分支 `wip/codex-20261026-planning-gap-fixes`。
BMAD 本地对照：`/home/min/workspace/gm/BMAD-METHOD`，HEAD
`61e01f82644c4bb533750cfa0d6d61ab61d2cc9b`。本次未改动该仓库。

## 结论与实现范围

这轮应同时修复方法、产物交接和运行可靠性；只改 skills 无法阻止父项目被实验初始化
覆盖，也无法让有正确工件的 Preview 显示正确。另一方面，依赖、权限和业务场景的
语义仍由 Planner/Critic 判断，不把本次 FMS 业务词汇写进 Runtime。

| 缺口 | 原因 | 本轮修复 |
| --- | --- | --- |
| 实验可能覆盖父状态 | Autoresearch / mixed runner 继承父 ZF_PROJECT_ROOT/ZF_STATE_DIR | 只在独立实验子进程边界剥离这两个变量，保留 provider 配置；同项目 worker 的上下文解析不变 |
| 完成 reader 回收失败 | 旧 task skill treatment 缓存阻止 idle 物化 | 已无 Task/活动 fanout 时清理会话缓存；保留活动工作保护，记录准备失败原因；提取 idle recovery 模块，未放宽超大文件冻结上限 |
| Preview 来源/覆盖错位 | 仅接受 traceability/entries，作者有 rows 或仅任务摘录；coverage 未显式绑定 | 支持显式绑定 rows，分别报告任务来源与来源全集；未绑定/缺失仍 incomplete，摘要损坏显式 blocked |
| 缺失诊断不可见 | Web 只渲染 section.notes[0] | 显示全部说明，缺失来源 ID 可见 |
| Planner→Preview 交接不足 | artifact_refs 被误认为正式阅读端口 | 增加 review-ports 作者约定，独立来源全集与 coverage_ledger 端口，页面/文件分别计数，旧产物只经受控修订 |
| 任务/用例时点错误 | 以模块 owner、wave 或权限名称代替真实动作前置 | 逐动作 producer→依赖路径→command→最早阶段；竞态先验证无竞争成功，再核对竞争提交后的不变集；assembly 本片接线检查与最终验收分开 |
| 方法存在旧路线残留 | 一律禁止片内浏览器验证、任务总数不超过池容量、旧 dependencies 示例 | 按有效 profile 判断真实可运行入口与池复用；使用 blocked_by；不要求未来 candidate 测试在本片通过，不擅自删减来源要求的全套验证 |

所有 skill 修改同步到 `skills/`、`.codex/skills/`、`.claude/skills/`。
未删除任何 skill，未改写 FMS 已接纳的需求、Plan、Task Map 或摘要。

## 同输入修改前后证据

证据目录：`/home/min/workspace/public/zaofu-evidence/planning-gap-20261026T2319Z`。

| 检查 | 修改前 | 修改后 | 证明范围 |
| --- | --- | --- | --- |
| 真实 CLI 子进程 force-init，两种 runner | 父 ledger/TaskStore 被覆盖，2 项回归失败 | 父字节不变，子自定义 state_dir 生效 | 独立子进程状态隔离；不是产品 build |
| 同一 completed-reader 状态与事件 | worker.recycle.failed | worker.recycled，下一次派发成功 | 真实协调器+fake transport 的恢复链；不是 provider 上下文恢复实跑 |
| 显式 rows：来源 R1/R2/R3、覆盖 R1/R2 | 0 来源/0 映射，不能看到 R3 | 3 来源/2 映射/缺 R3/1 pending | 绑定工件读取和投影兼容；映射存在不等于验收通过 |
| 同版 Preview 浏览器 | 旧 UI 只显示首条说明 | 显示来源计数及缺失 ID；原文/模块/场景仍可读 | Docker Playwright + 真实 API + 明确标记的合成 fixture |

`before-after.json` 与 `compare.py` 保留旧 dev 函数和候选函数的同输入比较；
`isolation-before.txt` 保留修复前真实子进程失败。没有用删除未覆盖项来提高覆盖数字。

验证运行记录：

- 首轮 focused closure：144 passed，包含项目上下文、实验 runner、生命周期、Preview、Package、skills 物化。
- idle recovery 提取后：53 passed，覆盖完成→回收→再次派发、活动 reader 保护、失败诊断、context recycle mock E2E、skills 物化。
- 三流 smoke：10 passed，包含 task-pipeline v4 runtime-noise mock E2E。
- 哨兵：50 passed、1 skipped；曾发现超大文件增长，提取职责后通过，没有提升上限。
- 前端 `npm run build` 通过，含类型检查与 bundle budget。
- 最终 Task Map/方法契约检查：88 passed。Web typecheck 与 unit suite 通过。
- Docker Playwright：10 个不同场景通过；首跑 8 passed、2 项长文在默认 5 秒等待超时，延长等待重跑后 2 passed。保留冷启动长文等待偏慢的限制，不宣称性能改善。

`dev-verify.py plan --base dev` 保守推导出超过 80 个测试文件、建议 broad deterministic。
本次没有改变通用事件/Task schema，没有运行全量 pytest；以明确的模块/直接调用者/
共享合同及上述 mock E2E 验证闭包。`dev-verify.py run` 的 broad deterministic、serial、
contract、mock_e2e tiers 显式跳过：前两项以 focused closure 替代，后两项已单独执行；
该 runner 的 Web 自动 tiers 独立记录。不得将该收据表述为“全库验证通过”。

## 与 BMAD 再次对照

以下为本地方法审查，不是两个系统跑同一完整产品的性能 A/B。

| 维度 | BMAD 依据 | ZaoFu 本轮后判断 |
| --- | --- | --- |
| 切分与早反馈 | `skills/bmad-agent-po/references/slice.md`：生产可交付 epic、单会话 story、tracer path、每故事带测试 | 方向一致。补去旧全局禁止早期浏览器和总任务池上限；继续按独立行为及故障重做范围切，不按 AC 个数硬拆 |
| 依赖与并行 | slice.md：共享代码有序 lane、共享接口早建；tree check 清理 unpinned_after/order_conflict | ZaoFu blocked_by、路径所有权及独立 Verify 更适合执行调度，但语义前置仍需逐动作复核。八槽不等于八路真正独立 |
| SDD | `skills/bmad-build/plan-template.md`：冻结 Intent/Boundaries/I/O matrix、Code Map，紧凑计划 | ZaoFu 大 Plan 的完整正文必须保留，用同版端口按任务交付；不能照搬单叶计划 token 目标来删减千条来源 |
| TDD/验证 | `skills/bmad-build/step-03-implement.md`：每个矩阵行有实际运行且通过的测试，不能改预期迎合实现 | 本轮加强了前态/权限/竞态 oracle，以及本片/最终责任。Plan 中写了 RED/GREEN 仍不是运行证据；需后续 Impl/Verify 证明 |
| 人工与状态权限 | `tools/ticket-tree-rules.md`：build 最终 built，用户/orchestrator 才能 done；保留 baseline_revision | 与 ZaoFu 由 Kernel 准入完成证据、人工 Plan 审批的方向一致。不把 ticket frontmatter 复制成第二运行状态机 |
| 变更与追踪 | tree rules 和 plan template 保留基线、变更/审查日志 | ZaoFu 显式 refs/digests 与不可变 package 更适合多 agent 交接；本轮补作者和阅读端口的一致性，仍需新 Plan 实际产出 |

BMAD 更值得借鉴的是简洁的叶级任务、早期可运行检查与逐行真实测试审计；ZaoFu 应保留
自己的独立验证、并发隔离、受控批准和不可变证据绑定。两个体系都有方法约束，文本
更详细不能直接证明 ZaoFu 质量更高或 BMAD 更低。

## 尚未取得的证据与下一步

准备了跨业务预约系统案例、同输入 baseline/candidate skills 快照、六类缺口 rubric，
用隔离 Docker 调用真实 Codex。首次失败是镜像默认用户/只读缓存目录不匹配；统一
容器用户及临时 HOME 挂载后，两组仍在 provider workspace routing discovery 失败，
没有 turn.completed、没有回答、没有 token usage。无 600 秒截断，失败来自服务返回。

因此没有可评分的真实模型 A/B，不能报告“新版发现率更高”“规划更快”或“返工更少”。
该实验也不是完整工作流派发，更不是 FMS 产品构建。病例评审方法已补齐，持续效果仍待证实。

后续验证入口已准备：provider 通路恢复后，在最终提交冻结两组 skills，用同输入、同模型、
交错次序至少各三轮，按来源保留、六类缺口、误报、token/时长及返工轮数比较；再通过
受控新 revision 重生成 FMS Plan/Task Map，核对来源全集/coverage 端口与阅读层后，停在
人工批准。禁止手工修补旧 accepted artifacts。现有 31 任务的合理性不能因本轮改了
skills 自动升级为已验证；产品 Impl/Verify 和真实八并行仍需批准后执行。

8001 与 dev 未部署本候选；读回现有 FMS Plan `evt-1e7125ecaa7a` 仍为 pending、31 tasks。
本批只在独立 worktree 本地提交，不 merge、不 push、不点击真实产品 Approve。
