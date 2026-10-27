# 设计 182 实施与验收记录

> 状态: implemented。B01–B07 的实现与验证保留；后续补审及 B08–B11 见下方记录。
> 设计入口：[182](../design/182-plan-review-task-slicing-and-evidence-closure.md)。
> 补充修复：[语义审查、角色图与格式化 Preview](../records/2026-10-20-0528-plan182-semantic-followup.md)。
> 后续执行：[Task map 执行一致性与受控重规划](../records/2026-10-20-0725-taskmap-semantic-repair.md)。
> 交接修复：[引用式端口搬移与大小边界](../records/2026-10-20-0806-taskmap-artifact-handoff.md)。
> 本轮关闭：[B08/B09/B11 与 B12–B17 的正式 Critic、下游读取和新 Preview 验收](../records/2026-10-20-0847-taskmap-repair-verification.md#最终正式验收)。
> 以下为上轮实施时的证据，Plan 当前状态与语义结论范围以后续记录为准。
> 案例评审：[洛书重规划与综合验收](../records/2026-10-20-0327-plan182-loshu-replan-review.md)。

## 已选择的边界

- D01：正文优先使用完整 Plan，Digest 单独标识；旧请求只从 exact task_map.ready 找回引用。
- D02：使用现有 verification_owner 分层校验，不引入新状态/schema。Impl 的未来 AC 保持 blocked；
  Task Verify 可省略未来行或标记当前阶段 not_applicable；canonical mandatory 不改变。
  Candidate Verify 要覆盖所有非 human AC，不能拿局部结果替代候选验证。
- D03：Planner/Critic 负责早期接线与阶段集成设计；全图共享路径仍只允许一个 writer owner。
- D04：TDD 绑定写入现有证据工件；环境或测试自身错误不算功能 RED。
- D05：本轮明确允许重拆受影响任务、依赖和 AC，保留来源与旧新映射。

## 实现入口

| Backlog | 实现或执行入口 | 验证 |
| --- | --- | --- |
| B01 | orchestrator_fanout → plan_preview_artifacts → operator_plan_preview → Plan API/React | 14 项 API/Runtime 回归；4 项真实 React/API 浏览器测试；新旧洛书 Plan 实测 |
| B02 | verification_commands 的 AC 路由；Impl self-check、Verify result、模板及实际 writer handoff | tests/test_stage_acceptance.py；既有 typed admission / handoff 回归 |
| B03/B04 | zf-plan-execution-readiness；Planner/Critic 的实际依赖链 | tests/test_plan_readiness_skills.py；真实新 Plan 经 Critic 返工及最终独立复核通过 |
| B05 | yoke/tdd-evidence；开发及 Verify role context | 技能物化测试；本轮 RED/GREEN 输出；不认证未来洛书实现已 TDD |
| B06 | 新洛书项目、正式 flow intake/submit、Plan 人工审批门 | 新 Plan evt-3f6b832282a3 待人工审批；来源、旧新映射及语义评审完成 |
| B07 | 真实 Runtime 的确定性回归、自动验证层、浏览器验证 | 确定性影响闭包、浏览器及正式新 Plan 案例完成 |

## 协议说明

引用式 `plan_ports` 支持 `{logical_name, ref, sha256}` 的工作目录文件；reader 汇总的
正式搬移入口会定位作者文件，固化到当前 fanout 工件目录并改写显式端口描述符，
兼容顶层及 `plan_synthesis_result` 封套。保留原摘要，后续 Package 读取继续拒绝
摘要失配和路径越界；不递归改写任意语义正文中的路径。该路径让大型规格拆成独立的
受控端口，不必把全部语义/历史副本塞入一个超限 source_index，也不提高 4 MiB
Package hydration 或 8 MiB Preview 限制。测试经过实际汇总 caller、端口物化和
Package hydration，在删除作者目录后读取正文，并覆盖篡改与符号链接越界。

Preview 新增 content_kind、content_error、digest_markdown、digest_source、source_event_id、artifacts 和
artifacts_error，原字段兼容。已声明全文而不可读时显示 unavailable，不冒充摘要是全文。
Owner checkpoint 现在在首次审批事件前生成不可变 `plan-preview-bundle.v1`，只收集审批 payload
明确绑定的完整 Plan、需求 Markdown、task map、source index 和 inline plan ports；读取时校验 bundle
digest、workflow 以及 Plan/task map 路径绑定。Preview payload 的 `plan_content_profile` 让网页区分
完整派生 Plan、仅摘要和未声明完整性的旧请求，侧栏提供需求原文、任务图和矩阵等附件入口。
正式请求固定 Plan 内容 digest；附件从同一 Plan 的 Package 提取，读取时验证 digest，
路径必须在当前 project/state 内，拒绝越界及符号链接逃逸。附件端点只接受已绑定的逻辑名，
返回纯文本并禁止 MIME 嗅探。GET 不发审批事件。

格式化阅读器的章节、翻页和阅读工具位于独立导航区，正文在下方单独滚动，避免 sticky
导航与带内边距的正文交叠。窄屏的上一页、页码、下一页保持同组；源码和下载保留可访问
名称，以图标压缩空间。正文可聚焦并使用 PageDown；换页重建正文滚动区，取消上一页尚未
结束的键盘滚动。`plan-preview-navigation.spec.ts` 覆盖 1485、390、320px 下的导航与
正文边界、按钮布局、换页归顶和末行可达；全文精确重建与下载仍由 content 用例验证。

CLI review 使用相同的受限引用解析，兼容 state-relative 和显式 state 目录前缀。
摘要使用 canonical `validation.commands` 解析，非法 canonical 命令不能被 legacy 字段掩盖。
网页对 hash 绑定的 task_map 生成当前机械摘要，标记 `digest_source=bound_task_map`；
绑定文件失配时摘要不可用，历史 digest 文件、事件和 Package 不改写。旧未绑定请求仍显示
stored_digest，保留向后兼容。

分层证据沿用唯一 contract。Impl 仍覆盖所有 mandatory ID，当前阶段必须 passed，未来
candidate/human 行必须 blocked。Verify 依据 immutable criterion owner 校验，不能通过结果
行改 owner 来掩盖本片失败。旧单一 owner 合同保持原完成要求，attempt/target/revision 绑定不变。
not_applicable 只描述当前 Verify 阶段的适用性，不豁免后续 mandatory AC。

Verify 阶段由 Runtime caller 或绑定的 contract route 决定，报告不能自行改为 human 或其他
阶段以改变必验范围。普通 fanout 归一化和 typed result 准入共用条款校验：当前阶段的
mandatory 行必须存在且 passed；每行 owner 必须与合同一致；未来阶段不能预填 passed/waived。
候选阶段仍要求所有非 human mandatory 结果，人工结果不能由候选验证者提前签收。
Reader fanout 同样从已验证的冻结合同取得 owner/tier，保留合法的 candidate_verify 路由；
正常结果与错误回退都不能把它强行改成 task_verify。

## 证据与状态

功能 RED：B01 原代码 12 项回归失败（其中 API fixture 初始化问题另行修正，不计功能证明）；
B02 5 项失败，3 项原失败边界通过，包含真实 writer handoff 的拒绝。
GREEN：分层契约及相关 caller 回归 45 项通过；Preview 及附件 API 新回归 13 项通过；
角色方法/注入/相关回归 83 项通过、1 项环境条件 skip。自动扩大回归：11,498 passed、39 failed、19 skipped；其中新增技能索引缺项已修复，
其余 38 个失败节点在原 dev 工作目录逐项复现。主要为公开导出缺少 CI/文档/fixture、
既有 provider hook 哈希基线、测试进程的 python/pytest 环境及导出后遗留的 CLI 占位路径。
不修改这些基线以掩盖失败。索引修复后 sentinel 51 passed；三流 mock smoke 10 passed；
影响闭包 340 passed、1 skipped。前端 build/typecheck 与自动 Web 单元层通过。
补齐子进程 PATH 后，相关环境/调用者测试 158 passed、2 failed；剩余两项直接写死
不存在的 `/path/to/zaofu/.venv/bin/zf`，在原 dev 同样失败。

追加阶段权威审查各先复现 7 个普通 Verify 失败和 7 个 typed admission 失败，随后修复。
两入口聚焦 39 passed；包含实际 fanout、candidate、task pipeline、typed admission、
goal coverage 及结果消费方的影响闭包最终 867 passed；其中追加真实 reader caller 的
候选路由成功/失败回退用例，先复现 2 failed 再修复。更新后 sentinel 51 passed，mock smoke
10 passed。此轮扩大范围由新发现的共享准入边界决定，没有重跑未受影响的全量环境传感器。

Docker 使用现有组权限 `sg docker -c 'docker ...'`，没有修改 socket 权限或安装主机浏览器。
镜像为 `mcp/playwright:latest`，host networking，fixture API 监听 `0.0.0.0:8003`；
挂载源代码只读、证据目录可写，容器使用本机 UID/GID。容器通过 XDG 临时目录解决浏览器
crashpad 写入问题。真实 React/API 用例 4 passed：完整长文和矩阵、缺失全文与旧摘要标识、
关闭并切换 Plan 后旧响应不得覆盖新内容，以及超长正文分页后逐字重建。分页样本含
12,000 条中文和 emoji 条款，验证 Unicode 边界与最终末尾；不取消现有长文渲染保护。
夹具缺配置导致的首次失败已修正，不计作功能 RED。
补充布局测试 2 passed、1 failed；手机 contract-health 表格溢出在原 dev 单独复现，
其 CSS 和测试未被本批修改，不据此声称整个 Dashboard 手机布局已通过。

旧洛书 Plan 的真实 Docker 浏览器核验：正文 45,491 字节，与绑定正文一致；12 个附件可读，
桌面及 390×844 手机视口可读末尾，Q15 可定位，摘要单独展开，审批状态仍为 pending，
页面没有非 GET API 请求或 JavaScript 错误。脚本、截图、摘要和原始输出保存在本 worktree
`reports/plan182/`。真实新 Plan 已完成正式 Critic 与语义复审；正文 222,432 字节、
12 个附件均经 Docker 浏览器读取及哈希核对，前后审批 pending，没有产品 writer。

补充收尾回归：CLI state 引用先复现 2 failed，canonical 命令摘要先复现 2 failed，
已绑定 task_map 的旧摘要投影先复现 1 failed；修复后相关 CLI、Preview、摘要、Feishu
投影、审批和 fanout 调用者 230 passed。sentinel 51 passed、三流 mock smoke 10 passed。

实图检查发现纯文本保护继承聊天消息的 320px 内层滚动框。已有逐字比对证明内容未丢，
却不足以证明阅读区域可达末尾；追加浏览器 Range 位置断言先复现 1 failed。修复仅作用于
Plan Preview：正文使用主阅读区滚动，保留分页和渲染保护。最终 4 项 Docker 浏览器回归
通过，桌面与手机实际末行进入可视区；真实新 Plan 同样通过并更新截图。前端 build、
typecheck、35 个 chunk 的体积预算通过；文档与指令检查 9 passed。

## 重新规划输入

Dashboard 从本 worktree 启动，入口为
[新洛书 Plan](http://10.9.100.141:8002/?project=loshu-plan182-eae8133e&page=inbox&plan=evt-3f6b832282a3)。
新项目 `/home/min/workspace/loshu-plan182` 使用独立 `.zf-plan182` 状态目录及
`loshu-plan182-review` 工作流，沿正式 flow intake/submit 进入扫描和规划；人工 Plan 门保留。
脱敏 MD 与旧规划输入同一 SHA-256，未将原文凭据带入新项目。

首次准备输入时遗漏了 MD 引用的 24 个图片/数据库配套文件，两路扫描据实记录缺失。
这些文件随后从原交接包逐字节补齐，未执行 SQL，并通过 operator intent 记录补充事实。
活动 worker 的 checkout 未被手工修改；通过受控拒绝与已有 admission_replan 能力重新规划。
最终 Planner/Critic 实际读取 24 个文件、查看 8 张图片，核对 26 个旧任务和 82 个旧 AC。
原历史扫描保留，后补来源与消费证据明确登记；不以文件存在代替实际消费。

本记录的通过范围限于 ZaoFu 修复与规划质量，不证明洛书产品已经实现、迁移或部署。

## 本轮通用人工审批补充

Plan Preview 的人工入口现在按四层职责处理：Planner/Critic 通过既有 plan ports
提供 PRD、SDD、TDD 和 Decision Register 语义；Runtime 只绑定 artifact、校验
digest/引用和跨工件一致性；Web 负责目录、状态、Task Map 和下载阅读。缺失的系统
合同、测试预期、证据路径或决策登记不能由 Runtime 猜测成完整 Plan。

审批摘要会从绑定的 Plan decision register 回填 owner decision，避免 slim payload
缺字段时误显示“无决定”；原始 Plan 与包含人工摘要的审核包分别下载。该补充不改变
`plan.approval.requested=pending`、impl/verify gate 或任何产品运行证据状态。

## 人工审核包完整性（2026-10-25）

Plan Preview 现在额外返回 `review_packet`（`plan-review-packet.v1`）。它把人工审核
拆成 PRD、SDD、TDD、Traceability、Task Map、来源条款映射、Owner Decisions 和
Provenance 八个分区；每个分区保留 `status/counts/refs/notes`。来源条款使用绑定的
`source_index.traceability` 与 `coverage_ledger.entries` 中的标识，不限定项目编号格式。
分区只从审批事件已经绑定的 artifact 读取，缺少 port 或来源条款没有账本映射时
显示 `missing`/`partial`，不推断 PASS。

审核包还报告 acceptance 数量声明与实际矩阵不一致、不同 workflow/pdd 标识混用等诊断。
这类诊断会在摘要和 Web 侧栏显示，供人工在 approve 前通过 Chat 修订；它们不会修改
事件、任务或 runtime state，也不会绕过 controlled-action 审批路径。

合并后的阅读入口保留审批概览、模块规格、验收场景、完整原文四种视图，完整原文可
切换连续阅读与章节分页；连续模式复用有界 Markdown 分块，避免长表格退化或遗漏正文。
`plan.md` 保持 canonical Markdown，`plan-review.md` 包含人工摘要与原文。
Owner 决策深链接根据 Preview 的动作类型和同一 Plan 的 Inbox 审批身份提交；身份尚未
加载时禁用审批，不能回落到普通 Plan 审批。相关回归见 `test_plan_review_packet.py`、
`test_operator_plan_preview.py`、`planReviewModel.test.ts` 和 Plan 浏览器测试。

## GitHub 与本地并行流程合并校验（2026-10-26）

审批指纹统一绑定 Run、Task Map generation、需求摘要与 Plan package digest；
缺少 package digest 时保留原始 Plan artifact digest 回退。OA 的语义采纳仍不能替代
Owner 审批，重复审批抑制仍会在旧决定结束后重新判断。对应入口回归见
`tests/test_plan_approval_merge_identity.py`。

Task Pipeline 工作目录授权只采用当前 running operation 匹配的派发身份；
不能把同一任务历史派发中的所有 workdir 都加入白名单。旧 dispatch、placement epoch
和 Task Map generation 的写入拒绝覆盖原生文件工具及 shell。

Plan Preview 在 760px 以下将侧栏限制在独立的 120px 滚动区域，
为长导航下的正文保留阅读空间；320px、390px 与桌面视口由浏览器用例验证。

## 实跑缺口补齐（2026-10-26）

显式绑定的 `source_index` / `coverage_ledger` 兼容 `rows` 表示；任务级来源摘录单独
计数，不用覆盖账本反推来源全集。读取/摘要错误进入审核包诊断，Web 展示每个分区的
全部说明，避免隐藏未覆盖来源 ID。作者端约定见
[review ports](../../skills/zf-plan-task-map-contract/references/review-ports.md)。
旧 Plan 缺少绑定端口仍显示 incomplete，不通过目录猜测或重写旧摘要修复。

同批修复独立实验父环境泄漏、完成 reader 的旧 skill treatment 回收；新增行为未放宽
原有状态/技能保护，未触发产品批准。具体修改前后证据、验证层级及 BMAD 对照限制见
[实跑缺口复评](../records/2026-10-26-planning-gap-bmad-assessment.md)。
