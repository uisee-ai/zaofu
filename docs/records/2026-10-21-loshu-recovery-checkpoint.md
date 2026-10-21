# 洛书运行恢复检查点（2026-10-21 UTC）

来源：[设计 183](../design/183-runtime-result-authority-and-recovery-closure.md)。
授权：操作者要求“新建 worktree，基于 P0/P1 第一批、第二批生成 backlog 然后实施，待都实施完成后从断点恢复运行，验证 Q01–Q03”。

## 修复及现有证据

- harness 源码修复 `6ef7593`，归档 `6e1c221`；530 项聚焦通过，51 sentinel、10 serial、10 flow-smoke 通过。完整套件的 20 项基线失败已复现并记录在 P0/P1 任务。
- 旧 Run `loshu-impl8-20261020` 的 `evt-3f8927ad7587` 为历史 blocked；升级后仍保留。
- 原结果拒收的 RTK `evt-3c4c0c4505cb` 返回当前 authority，而旧 child payload 覆盖身份；新源码按 pinned operation 绑定并保留显式冲突，仍拒绝真正旧结果。
- legacy implementation adapter 现在保留 impl_self_check 和引用；故障模板不再预填 task_contract_unsatisfiable。不能将历史 29 次同类拒收当作 29 个产品设计缺陷。
- 新服务均从 recovery-closure-20261021 worktree 启动；当前配置仅将两条技能来源路径指向新 worktree。已通过 validate，并记录 run-contract-review；resume policy 为 WARN、非 strict，未绕过 STOP。

## 续接约束

1. 使用 escalation `evt-02cf83149de4` 的 `start_new_generation`，通过 ControlledActionService 追加新阶段代次；不编辑旧事件或冻结 Plan。
2. 保留完整 62 任务、274 mandatory AC 及完整源文档；从失败检查点核查已有提交和证据，禁止重新编造来源或任意删除需求来清除阻塞。
3. 本次是 harness 接收/恢复缺陷修复；原本地业务切片的提交应先重新核对合同和测试，不应因旧队列身份误拒收而重新设计产品。
4. `flow-discovery` worktree 有未提交的 local-material-categories 诊断证据，保留；所有 worker 分支与工作区保留。
5. 8 个 Impl 与 8 个 Verify 为各阶段容量，只有真实派发和正在工作的任务区间才能计入并发。

## Q01–Q03 外部事实

目前已证实本地 React/TypeScript/Vite 前端启动授权和 app 工程；正式后端复用、具名负责人、真实 API/联调环境、登录/会话/网关/撤权事实仍缺。
已向操作者请求正式仓库、API 契约或联调入口、身份服务和负责人，或明确新建后端与认证服务的验收边界。无回复前不把本地 demo/mock 计为 Q01–Q03 closed。
相关源文档：产品仓库 `docs/source/loshu-fms-handoff.md`、`docs/implementation-8x8-request.md`。应继续无外部依赖的真实工作，逐项保留阻塞原因。

## 断点恢复实证

- P0 在真实运行中重新准入四个保留实现结果：VEHICLE-MODELS `evt-ce46e014a97d`、SCENES `evt-a432f7e690fc`、ADMINISTRATORS `evt-b8515a8eddf4`、ATTACHMENTS `evt-8bc6468370a6`。这是 child 准入，不等于产品完成；局部集成仍出现 candidate_product_quality_failed。
- 补充修复 `5faa91a fix: bind reader recovery to its causal task context`；299 项聚焦（含两个 mock E2E）、51 sentinel、10 serial、10 flow-smoke 通过。先复现两个 RED，未重复已确认存在 20 项基线失败的完整套件。
- 为纠正首次恢复暴露的串任务上下文，操作者通过 EventWriter 追加取消事实：`evt-06baf7f0be56`、`evt-c0ae70574c87`（错误 reader 输入）及 `evt-5ea9f7ae7667`（已准入任务的过时请求）。取消前均核实 pending、无 operation，不修改旧事件、不伪造完成。
- ControlledActionService `stage-retrigger` 将原始 `evt-5752824e7541` 重发为 `evt-42797c399abb`；受控 role restart 后，`zf runs resume` 产生 `evt-e127925360b5`（running）。
- `evt-72fec19d8be2` operation requested，`evt-5b13cfcb745e` started，`evt-937b48df4f18` 实际派发；task 为 LS8-LOCAL-API-RULES，候选与 reader HEAD 均为 `4f9959e10914570ff2f6498529fecd13d960927a`。provider 已读取当前 briefing、受控 contract/target/plan 输入并开始审查。
- reader 重新绑定候选会清理旧工作目录，故提前保存并逐文件验证 19 份旧证据。已通过 sanctioned sidecar writer 转为持久证据：`artifacts/operator-recovery/2026-10-21-reader-evidence/manifest.json`，SHA256 `38bef52f7e46f2449ab661ee63c8c86425ec47cede6a3f28d1879a02e98130b3`；事件 `evt-938147b138fb` 绑定引用。旧 material 目录不继续混入 API 候选。
- 产品配置仅改 skill_sources 来源路径，已独立提交 `3088d85 chore: align Loshu runtime skill sources with recovery fixes`；既有 `.codex/`、`artifacts/`、review.md 未提交文件未动。

## 浏览器与运行边界

预览：<http://10.9.100.141:8002/?project=loshu-plan182-eae8133e&page=agents>。
Docker `mcp/playwright:latest` 实测 HTTP 200、title zaofu、0 pageerror；证据 `/tmp/zf-loshu-browser-evidence/dashboard-after-resume.png` 与 after-result.log。
`/health` 报运行源码 `5faa91a`，state_dir 为真实洛书状态；原端口 8001 未修改。
watcher / Autoresearch 使用当前 worktree；Run Manager 已受控重启。Orchestrator 为 shadow 建议角色，source_repair/reflect 原有禁用配置未冒充自动修复已开启。

Agents 投影仍显示旧 RTK 绑定及旧 running_task；agents 请求约 54 秒，不能拿 UI 数量证明 8 个 Impl/Verify 真正同时执行。恢复后的实际新工作是上述 API-RULES reader；Verify 尚在等待可验收候选。该投影一致性/性能问题没有被本批次的页面可访问检查覆盖或宣称修复。

## Q01–Q03 当前逐项结论

| 项目 | 已有事实 | 尚缺关闭证据 | 状态 |
| --- | --- | --- | --- |
| Q01 | 本地 React/TypeScript/Vite 工程及前端实施授权；PG 默认选型 | 正式前后端仓库/版本、逐域复用决定、服务与持久化责任、具名负责人 | 未完成 |
| Q02 | 原 PRD 页面/字段需求与本地切片 | IC01–IC09 真实 API/Schema/权限及成功和边界样例、联调入口、其他页映射计划 | 未完成 |
| Q03 | 原 PRD 身份/能力/撤权需求与 ACCESS 任务 | 实际身份/会话/网关服务、授权资源真源、能力计算、撤权传播实测 | 未完成 |

依据 `docs/source/loshu-fms-handoff.md` §9.1.1（产品仓库 2848–2850 行），职责角色不能代替具名负责人，草案不能标 closed。已经索取缺失事实；没有收到外部接入信息或新建后端/认证服务的明确边界。本记录不改变批准的 62 任务、274 mandatory AC，也不以本地 mock 代替以上证据。


## 后续 Autoresearch 环境验证

rmar-7214d8508c92 通过原技能来源校验后，在 inner runner 暂存生成的 docs/prd/autoresearch.md 时因 docs/ ignore 规则失败，returncode 3、尚未派发。事件 evt-9d5b3eccc862 正确保留 HIC-68D9B81837 为 unverified，未 dismissed。
补丁对明确生成的唯一 source_ref 使用 git add -f --，保留唯一提交路径。RED/GREEN 与直接调用方共 66 项通过；在原隔离失败 checkout 只重做 prepare 已成功，生成 0f420aa，仅包含输入文档，原正文及 dirty zf.yaml 未改。
这证明输入准备故障修复，不是完整 Autoresearch、真实产品恢复或 Q01–Q03 的通过证据。


## 验收输入重绑定

API-RULES reader 实际运行合同浏览器命令时得到 wrong HEAD。检查确认 app/evidence/g4/candidate-context.json 是 gitignored 的旧 Material 任务输入；它不由 reader HEAD 切换自动更新，也不是本次 operation 的证据。
操作者保留旧输入，然后从当前 manifest、running operation、实际 HEAD 和 source manifest 重新派生上下文，通过原子写入提供给现有原命令；事件 `evt-63dd3c798d59` 绑定旧/新输入 sidecar，新输入 SHA256 `09223a64405d74c42ac2d5efed56d61b01708425fb16049098ed902f14739306`。
已通过已配置 transport 向同一 reader 发送带事件引用的更新，请其独立核实并重跑。没有写测试通过证据，没有清除第一次失败或改变验收命令；这一步是明确记录的操作员修复，不宣称 Run Manager 已自动完成。


## 真实浏览器验收结果

04:19:13–04:19:20 UTC，独立 reader 重跑原命令得到 candidate_verify passed / exit_code 0。receipt：产品 reader 工作区 `app/evidence/local-api-rules/browser/run-fanout-prd-post-verify-discovery-evt-42797c39-flow-discovery/receipt.json`；SHA256 `9d8f5bfc18f030b408b2dcaa7f291b1a65ffd78e8f229e0362c3e601c2928c47`。
已独立核对 receipt 的 operation/attempt、target_commit 4f9959e 和 context SHA256 与当前绑定一致。1440、768、390 三种宽度均通过键盘复制、冲突保留草稿、修正后原子保存、治理权限拒绝、确认删除、四态区分、无横向溢出及无页面错误；截图、trace、observations 位于同目录。
这是一个明确授权的本地 API-RULES slice 浏览器通过；不等于真实后端 API、身份服务或 Q01–Q03 闭环。提交给 Kernel 的全局 rescan 判定继续由独立 reader 和结果准入路径处理。


## 结果准入审计（阻止错误完成声明）

04:20 的 evt-37adee35a439 / evt-4a397a7bd97f / evt-80f89650d1ad 虽被旧 Kernel 接受，但独立复查控制结果 854986331750ef20f4db9bb203a09b8d111ffd47c1467149f571ab824371283b 发现报告/AC/三个非浏览器 probe 仍保留模板引用及占位 target。因此不能将这些事件作为正确完成证据；真实 browser receipt 另行成立。
操作员再次受控暂停 Run，先修旧 emit 跳过 typed admission 的缺陷。新代码只读验证原控制结果已返回 verification_result_invalid / unresolved placeholder。将通过新受控阶段重跑实际非浏览器命令和浏览器，再核验新结果；绝不改写旧事件或直接设置 task done。


## 最终影响闭包回归与账号切换

补丁 `25466e9 fix: enforce verification evidence checks on legacy results` 已部署。扩大确定性套件：11562 passed、19 skipped、44 failed；其中 20 项为先前已在独立基线复现的已知失败，22 项因测试进程 PATH 没有 virtualenv 导致 python/pytest 子命令或 preflight 失败，另 2 项仍使用不完整的旧验证 fixture。两项 fixture 已改为持久合同/目标与明确 mock 收据，保留原协议模式和生命周期断言。补齐 PATH 后精确复跑其余 24 项全部通过；两个受影响模块共 49 项通过。serial 10、sentinel 51、flow-smoke 10 再次通过；不宣称全套全绿或真实 provider E2E 全通过。

日志：`/tmp/zf-recovery-post-admission-dev-verify.log`、`/tmp/zf-verification-environment-rerun.log`、`/tmp/zf-verification-fixture-followup.log`、`/tmp/zf-recovery-post-admission-tiers.log`。

新的 reader `fanout-prd-post-verify-discovery-evt-9fbc3261` 实际执行四条原始命令：browser 三视口通过；三条 Vitest 在 reporter 启动阶段因无 task Verify/author context 退出 1，业务断言未运行。产品验证器 task 模式明确限制 verify-lane-0..7，当前 flow-discovery 不可冒充。它已生成完整 blocked 报告与 31 份本轮证据，typed admission 只读检查无 issues；这不是产品通过。

reader 在正式提交前遭遇 provider usage limit。用户告知更换账号后，通过 zf restart flow-discovery 重新载入；只检查 role auth 是全局 auth 的同一 symlink，不读取或记录凭据内容。重启前逐文件保留新证据，事件 `evt-51a458747f10` 绑定 `artifacts/operator-recovery/2026-10-21-account-restart/evidence-manifest.json`。最终准入及恢复结果待下文确认。
