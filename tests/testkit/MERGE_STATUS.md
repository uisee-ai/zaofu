# TestKit 分批整合状态

更新：2026-09-30。本轮选择性文件整合已落盘；不是 Git merge 完成或发布验收通过。

本批来自 v7，目标工作树为 `feature-issue-stability`。已迁入测试框架、证据/运行器
基础模块、配套自测、conformance 测试及必要夹具；没有覆盖或替换 feature 的
`/issue-merge`、Triage、External Issue、Feishu 或前端生产行为。

## 已完成

- `tests/testkit/` 的 runner、报告、验收、失败证据包、快照、故障模型、命令执行、
  环境证据和 CLI 已迁入；`tests/testkit/__main__.py` 可作为显式入口。
- 产品项目通用测试框架已迁入：`tests/product_testkit/` 共 92 个模块/自测文件，
  `scripts/product-testkit.py` 入口已补齐；不包含任何具体产品项目实现。
- v7 的 `tests/conformance/*.py` 共 116 个文件已迁入，注册面未删减。
- 必要 fixtures、Codex hook hash 向量、blocking Task Pipeline 夹具及 S8 辅助函数已补齐。
- 2026-09-30 收尾补齐 46 个注册/台账引用的测试和脚本、6 个覆盖矩阵引用文件
  （含两套 Playwright 配置及 spec）、CI、覆盖矩阵和台账快照。仅新增缺失文件；
  同名文件保留 feature，通过最小测试补丁适配，不整文件替换。
- 测试依赖已同步：Hypothesis、Schemathesis、pytest integration 标记及缓存排除规则。
- 仅迁入有台账依据的通用修复：ZF-TEST-020（回归 artifact 原子写入）、ZF-TEST-110（fanout 丢派发
  恢复去重）、ZF-TEST-045（验证结果消费重放）、ZF-TEST-048（Codex restricted/allowlist 启动兼容）、
  ZF-TEST-063/066（Task Pipeline respawn/preflight 合同）、ZF-TEST-065（writer 已完成任务保留）、
  ZF-TEST-069/070/071（stream-json 终态、停止和自有进程组清理）、ZF-TEST-079（Claude 按实际 spawn cwd 恢复）、
  ZF-TEST-089/090/091（retry briefing
  Task 锚点、Composite 生命周期快照转发、ready deadline 诊断保留）、ZF-TEST-092（逐 claim 绑定合同）、ZF-TEST-093/094/
  095/096/097/098/099（并发配置身份、submit scope 透传、拒绝重放去重、活动 Task
  保护、target_root 与相对写范围分离、planner writer scope 包含校验）和 ZF-TEST-108
  （tmux 独立后代进程清理）。
  受保护的 issue/Triage 生产修复不迁入。
- 前批 ZF-TEST-111 的 EventLog 锁内 flush/fsync 三行补丁已保留；只证明该追加边界，
  不代表断电/文件系统损坏验收通过。

## 当前验证证据

| 项目 | 结果 | 说明 |
| --- | --- | --- |
| 完整注册表收集 | 4032 collected，1 deselected，0 collection error | SUITES + MAINLINE 的显式节点；仅收集，不是执行通过 |
| S0 注册审计 | 19 passed | registry-audit-final2.xml |
| 048/079/108 聚焦回归 | 131 passed，1 deselected | fixes-final3.xml；spawn、restricted config、hook trust、tmux cleanup/lifecycle |
| 更宽的调用方回归 | 153 passed / 1 failed，1 deselected | fixes-closing-final2.xml；额外含 backend adapters、CLI Task-stage cwd，CLI 配置红灯保留 |
| 生命周期、runner 与 rerun 证据 | 51 passed | lifecycle-runner-closing.xml；覆盖前批两个子进程 PID 时序失败及缺 rerun fixture 问题 |
| 预合并哨兵 | 45 passed / 6 failed | premerge-closing.xml；与 testkit-core 前批报告的失败节点集合完全相同 |
| 093–099 直接闭包 | 178 passed | direct-final-with-light.xml；隔离、scope、拒绝、准入与 light 回归 |
| 089/090/091 聚焦回归 | 72 passed | 089-091-direct-final.xml；retry、生命周期、watchdog，早于新增 Composite caller 参数化 |
| 045/090/092 调用方 | 76 passed | 045-090-092-callers.xml；含 Composite watchdog 参数化、完成门禁与 module-gap-plan |
| 045 正式重放/identity | 21 passed | 045-formal.xml；S3 generation 与 S8 IssueFlow 重放，不是 /issue-merge |
| 096 正式身份/归属及 099 planner/终态 | 6 passed / 4 failed | 093-099-formal.xml；四红停在 feature 有效配置差异处，未到达 planner 范围校验 |
| Ruff / git diff --check | pass | 只做已迁入/适配范围静态检查，不代表全仓验证 |
| 产品 TestKit 自测 | 360 passed / 3 harness failures | 已在 `.venv` PATH 下重跑；3 项均为 Schemathesis 期望 4.26.1、当前安装 4.28.0 的版本契约差异，未改测试伪造通过 |

报告目录：`/home/uisee/zaofu-merge-backups/testkit-reports-6fQ8QtMG/`。
本轮补充报告目录：`/home/uisee/zaofu-merge-backups/remaining-096-099-vtz4SAzF/`。
后者的 premerge-093-099.xml 仍为 45 passed / 6 failed，与前批同一失败集合。
各行是独立运行，存在测试重叠，不能相加得出通过率或框架完成率。
收集数量来自 pytest 控制台；collect-only JUnit 的 `tests=0` 不是零测试执行成功。
此前 149 passed 是较早的 048/108 回归，不包含后来迁入的 Claude cwd 探针，
不能作为本次最新闭包结果。Codex 夹具现有 5 秒启动/故障预算并核对具体错误原因，
不再把任意提前超时当作 oversize/RPC 故障证据；生产超时未修改。

## 尚未完成/明确限制

- 尚未执行完整注册套件/MAINLINE，也不把收集数或通过数当作端到端可信闭环完成率。
- 预合并哨兵当前仍为 45 passed / 6 failed；失败集合与迁入前一致，属于 feature 既有
  Event 注册、结构体积和技能镜像红灯，本批未放宽门禁。
- 上述调用方回归有 1 个 feature 配置兼容性红灯：CLI 重启加载渲染配置时拒绝
  `self_issue.settings_config_path`。仅确定该失败现象，不将它误判为 cwd 修复失败；
  不修改 Self-Issue schema/render/loader 以消除红灯，详见台账的合并观察记录。
- ZF-TEST-093/094/095/096/097/098/099 的通用运行边界已按最小调用面迁入；运行
  隔离/拒绝/Task-scope 直接闭包 149 passed，light scope 直接回归 29 passed。正式
  S8 跨流程仍有 feature 既有配置/route 语义差异，保留红证据，
  不改测试为 skip/xfail，也不以跨流程全绿作为迁入条件。
- ZF-TEST-089/090/091 的聚焦回归 72 passed；其中 090 只转发已有底层生命周期快照，
  不改变 watchdog 判定或重派策略。
- 并发四红的快照逐字段比较已定位到 `self_issue.settings_config_path`：General
  的临时候选配置路径与 PRD/Refactor 的正式 zf.yaml 路径不同。保留完整配置比较，
  不忽略该字段、不修改受保护的 Self-Issue 配置实现。
- 两次提前中止的诊断批（conformance-after.xml：4 failed/exit 2；light+scope：
  29 passed/6 failed/exit 2）不计完成验收；未中止的完整批以上表精确报告为准。
- 补丁调用边界及验证限制见 [通用修复实现说明](../../docs/impl/selective-merge-runtime-fixes.md)。
- `test_web_auth.py` 改为探测 feature 的 server 内共享授权状态，不迁入 v7 的 Web 模块拆分；
  4 个 HTTP/WebSocket 探针通过（closing-probes.xml），未改变前端或授权生产实现。
- 未运行真实 Codex/Claude Provider、VM、浏览器或外部 Feishu；这些属于显式 host/provider
  验收层，不能用本批确定性测试冒充。
- 直接收集 conformance + testkit 时是 2219 selected / 129 deselected，按项目默认
  `not perf and not host and not real_provider` 过滤；这与显式注册表的 4032/1 是不同集合。
- Playwright 配置/spec 已迁入；本机 `--list` 可发现 2 + 1 项，但默认 Docker 输出目录
  `/zf-run` 在宿主上写报告报 EACCES。因此不算浏览器验证通过，正式执行仍要求 Docker
  `mcp/playwright:latest` 及对应挂载，不安装宿主浏览器、不修改共享目录权限。
- 迁入的覆盖矩阵和台账为 v7 历史快照；除明确列出的 feature 本轮证据外，历史 fixed/pass
  不代表修复已全数移植或 feature 验收通过。研究/方案全文、attachments 和重启备份仍保留
  在来源目录，未复制为当前运行证据，也未删除来源目录。
- 当前工作树含用户原有大量未提交改动；本批没有暂存、提交、merge、push 或清理无关文件。

## 收尾边界

- `orchestrator.py`、`self_issue_target.py` 再次与各自 feature 整合前备份逐字比较一致；
  `orchestrator_lifecycle.py` 仅包含已登记的 Task-stage/stream 生命周期最小调用面补丁。
  其他受保护文件按既有分批记录保留；不能把整个脏树相对 HEAD 的 diff 都归因于本次迁入。
- ZF-TEST-040/047/085/087 按操作者规则保持 open，历史 v7 修复证据不删除。
- 未发现本次 pytest/Playwright 测试进程残留；目标工作树没有发现本次 test-results/
  playwright-report 临时输出。证据报告保留在工作树外的合并备份目录，没有删除。
- HEAD 保持 `5c5fe05`，分支为 `wip/feature-issue-stability`，暂存区为空。
  由于预合并门禁仍红，本次不执行合入 dev；按安全合并技能保留两侧工作树及原有脏改动。

`/issue-merge`、Triage、External Issue、Feishu 相关生产行为只保留 feature；v7 检出问题
只登记到台账，不回写 feature 生产实现。EventLog 独立主干修复边界见
[追加耐久性说明](../../docs/impl/event-ledger-append-durability.md)。

## 最终收尾登记（2026-09-30）

本次约定的本地选择性整合完成，目标为 `/home/uisee/zaofu-worktrees/feature-issue-stability`。
未执行 Git commit、合入 dev、push；未删除任一工作树，未清理用户已有改动。

原始 147 项生产源码差异已逐项审核，结果与最终两侧 SHA-256 见
[MERGE_AUDIT.json](MERGE_AUDIT.json)：

| 裁决 | 数量 | 含义 |
| --- | ---: | --- |
| feature 保护路径 | 71 | 62 文件保留原字节；9 个来源专有模块不引入 |
| 修复与 v7 对齐 | 33 | 已登记的通用修复，不含受保护产品行为替换 |
| 按 hunk 整合 | 4 | start、lifecycle、fanout、Task Pipeline；保留 feature 根选择和 readiness 等待 |
| 行为等价 | 8 | 已迁入逻辑，仅注释、空行或格式不同 |
| 审核后保留 feature | 31 | 每项有理由；包括085/087、040拆分、feature新增权限/附件/幂等实现 |

无 `pending_hunk_review` 项；不要求两个目录完全相同。源清单由文件名自动关联的历史
台账编号不是修复因果证明，本裁决以实际 hunk 为准。原始 source-differences.json 保留为
历史，不再作为当前待办列表。

测试框架核对：`tests/testkit` 和 116 个 conformance 文件没有来源缺失项；仅
`native_run.py` 的非 host 导入适配、blocking fixture 提取保留目标差异。通用产品框架
92 个模块/自测、CLI 入口和 manifest 示例已补齐；产品 issue_loop 只提供测试回调合同，
未接入或替换 feature `/issue-merge` 的任何生产实现。研究文档、附件和历史证据继续在
原 v7 文档目录保存，不作为本轮新验收证据，也没有删除。

最终增量验证（各批有重叠，不相加算进度）：

- 新增通用修复直接回归：136 passed；含 CLI 配置探针的批次为137 passed / 1 failed。
- 恢复接线/终止/Task-stage cwd/briefing/queued writer candidate：50 passed，报告
  `wired-recovery-final.xml`。
- tmux 同源身份、双窗口快照与 watchdog 调用方：136 passed / 22 deselected，报告
  `tmux-snapshot-final.xml`；host 明确不执行，不把 deselection 当通过。
- stream 取消/清理：10 passed / 1 skipped / 28 deselected，不冒充真实 Provider/宿主验收。
- 产品框架完整自测：360 passed / 3 failed，报告 `product-kit-venv-final.xml`。3 项均为
  Schemathesis version_mismatch（要求4.26.1、安装4.28.0）；未更改版本基线或放宽断言。
  未激活环境的333 passed / 30 failed为此前诊断批，其中缺少PATH python的问题通过
  激活现有 `.venv` 解决，不能将该旧批归称全部环境失败。
- Ruff、py_compile、git diff --check通过；恢复文件超长的新红已通过将工作目录解析
  移到 `src/zf/runtime/lifecycle_workdir.py` 消除，没有调宽cap。最终哨兵45 passed / 6 failed，
  失败节点集合与整合前一致；不代表失败内部所有数值或全部产品行为都无变化。

上述 XML 位于 `/home/uisee/zaofu-merge-backups/remaining-096-099-vtz4SAzF/`。
保留红灯：feature Self-Issue 配置重载及并发配置差异、6项既有哨兵、3项工具版本契约。
未运行完整 MAINLINE、真实Provider、VM、Docker浏览器或Feishu；不以本地合并宣称端到端验收。
以后验证需先 `source .venv/bin/activate`；没有为本次修改系统Python或共享工具版本。
