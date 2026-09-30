# Issue Triage v22 受控筛选、生命周期与证据资产

> 状态: accepted

## 目标

为 v22 Issue Triage 页面提供真实后端能力：Source 多值筛选、Issue number 全量排序、queued Triage/Fix 取消、事件驱动生命周期投影，以及所有原始证据文件的可下载链接。

## 约束

- `source` 保持兼容；多值使用 `sources` JSON 数组，空数组表示不限制。
- 排序必须在全量镜像集合上完成，再分页；Issue number 不得映射为日期或仅排序当前页。
- queued 取消使用专用受控 action，携带 revision/CAS、幂等键并写入事件；不得生成虚构 Run。
- 生命周期只消费 EventLog 与现有 Store/sidecar 证据，缺失阶段返回 `unknown`。Repository 可由持久化镜像证明接收事实，进入后续阶段后标为完成；提示保留镜像的 Open/Closed，不被 received 事件中的内部 Mirrored 标记覆盖。其他阶段不能仅凭顺序推导完成。
- Python Web 进程不会因前端构建而重新加载投影代码。更新投影后需重启 Web，并检查详情接口节点的 `state/outcome`；只有 `status/evidence_refs` 的旧响应不足以证明完成。
- 原文件以每个 Triage 的 GitHub Release asset 保存，Evidence comment 发布下载链接、digest 和状态；媒体可额外使用原生 comment 附件。

## 验证

覆盖 API 契约、事件投影、幂等取消、Release 上传重试和 v22 浏览器交互。测试不得执行真实 GitHub 发布、取消或合并。

Issue workspace 的本地验证入口为 `cd web && npm run build && npm run test:issue-workspace`。
默认使用本地 Playwright；可选 `npm run test:issue-workspace:docker` 使用与项目实际安装
Playwright 版本匹配的 `mcr.microsoft.com/playwright:v<version>-noble` 固定版本镜像。
Docker 入口检查镜像 ENTRYPOINT 并显式替换为 node，直接执行项目 CLI；不指定浏览器路径。
测试、浏览器与 Vite 同容器运行，使用容器默认网络，Vite 绑定 `127.0.0.1:4176`，
保留 strictPort、不复用已有服务，并输出服务器启动日志。fixture 拦截项目 API，无需真实后台。
容器以当前 UID/GID 运行，工作目录挂载到同一路径；报告、截图和 Trace 写入
`web/test-results/issue-workspace/`，退出后保留。HTML 报告在 `html/`，完整 JSON 结果在
`results.json`，失败截图及 Trace 在 `artifacts/`；传入 `--trace on` 可保留成功用例 Trace。
可用 `npm run test:issue-workspace -- --grep "GitHub sync"` 运行指定用例。

### 刷新与浮层修复（2026-09-14）

- `zf web` 通过 `build_external_issue_poller` 启动后台同步；当前 `zf.yaml` 的
  `project.self_issue.ingress.poll_interval_seconds` 为 300，页面每 10 秒读取本地镜像。
  后台同步无需浏览器页面保持打开，但要求 Web 进程运行。
- 手动刷新保持强制完整同步语义；每页 Issue 的评论请求最多 4 路并发，镜像和
  评论持久化仍在持有同步锁的主线程按顺序执行，不跳过评论或吞掉失败。
- 前端按项目保留在途刷新 Promise，路由卸载不会解除忙碌状态；重新进入页面
  订阅同一请求，并在完成后重新加载镜像。后台返回 `syncing` 也禁用刷新按钮。
- Kanban Agent 浮层位于 Issue 普通下拉菜单上方；搜索输入框去掉内部焦点框，
  保留搜索容器的焦点圈。
- 飞书网关已接入专用 Issue summary/list/show、主卡投影与 start/cancel/needs-info/
  candidate delivery 回调。读取路径复用 Web 同源投影，写入回调继续受用户级别、
  精确项目群路由、签名 token、nonce 和 source revision/triage attempt 约束，并只能进入
  `ControlledActionService`。本地 Mock 已验证；真实飞书凭据/群组的隔离验收仍为显式部署层。

### 增量刷新（2026-09-14，取代上文手动强制完整同步规则）

普通 Refresh 仍传 `force=true`，含义改为绕过最小刷新间隔；同步使用上次
**仓库级成功时间**减 2 秒查询增量。单 Issue 查询不能推进该游标。只有首次同步、
距离上次完整校验满 24 小时，或显式 `POST /api/projects/{id}/issue-triage/refresh?force=true&full=true`
才做完整校验。旧同步记录缺少 `last_full_sync_at` 时先完整校验一次，成功后才记录时间。
失败不能推进增量或完整校验时间。完整校验仍需 Web 进程运行，在下一轮轮询执行。

增量响应中，Issue 的更新时间、评论数量均未变，且评论侧文件的 digest 和记录数量
有效时，不请求评论；元数据存储原有内容比较继续避免无变化重写。变化、新 Issue、
缺失或损坏的评论缓存会重新获取评论。最后一条评论删除时清空缓存。
完整校验重新拉取评论，不借用 Issue 列表 ETag 推断评论内容未变，因此能修复
未反映到 Issue 时间戳上的评论修改。此类修改最迟由低频完整校验发现。

GitHub 的 `since` 查询和条件请求说明：
[Issues API](https://docs.github.com/en/rest/issues/issues#list-repository-issues)、
[REST best practices](https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api)。
Issue 列表分页 ETag 不能作为整个仓库或评论集合的完整性证明。

诊断日志区分仓库验证、Issue/评论同步以及含入库处理的总耗时，超过 5 秒记录 warning。
隔离镜像副本对真实 GitHub 的一次只读测量：旧完整刷新 10 请求 / 8.934 秒，
已有完整校验基线后的增量刷新 2 请求 / 3.188 秒，均 changed=0；不代表完整 Web
操作固定耗时，也未复现用户报告的 54 秒。测试覆盖增量、失败重试、24 小时及显式
完整校验、缓存修复、评论删除、单 Issue 游标隔离和 API 的 ingress 开关分支。

实际 Web 日志还捕捉到含入库总耗时 50.298 秒。后续发现附件采集缓存原为服务实例
内存缓存，而每次 API 请求都新建实例，导致相同图片/视频反复下载；入库资格判断
也在采集附件之后。现在先过滤不符合入库策略的历史 Issue；附件采集使用
`issue-triage/attachment-cache/` 下的可重建投影缓存，以正文和评论 digest 为键。
成功缓存有效期 24 小时，部分/失败缓存 5 分钟后重试，每次复用验证附件 ref 范围
和内容 SHA-256。不可变证据文件保持原有写入契约，损坏文件不会被当成有效缓存。

最终实际 Web Refresh 接口连续验证：HTTP 200，3.351952 秒 / 3.302464 秒；两次均
`changed=0, received=0, triage_queued=0`。此测量包含入库处理，发生在首次缓存建立后。
首次建立附件缓存、缓存过期后重新捕获或新增附件时仍可能更慢，不能承诺每次固定 3 秒。
本次 focused 回归 84 项通过；结构门禁存在 4 项既有超长文件约束失败，未修改阈值。
测量原始 JSON、请求耗时与测试日志保存在 `web/test-results/refresh-incremental/`。

### Refresh 不等待附件采集（2026-09-14）

实际 `test` 项目的普通 Refresh 仍出现约 18 秒等待，附件重试不能只靠缓存解决。
现在 API 先完成 `refresh_mirror` 并返回，再由 FastAPI 后台任务执行 `process_mirrors`。
入库资格激活时间仍在首次镜像同步前建立；自动轮询仍执行完整流程，手动 Start Triage
仍走原有单 Issue 采集和入队路径。没有绕过证据流程来提前伪造 Triage 结果。

同一 router 按 state_dir 合并后台任务：处理期间再次刷新只标记需要重读最新镜像，
本轮结束后再处理一轮，不启动并行重复入库。后台处理与自动轮询使用同一入库文件锁。
Refresh 响应及 summary 的独立 `intake.status` 为 idle/processing/ready/failed；
不再在同步响应中返回尚未处理的 received/triage_queued 数字。后台错误通过现有
通知卡持久显示，不把成功的 GitHub 元数据同步改成失败。页面 10 秒轮询读取最新结果。

后台调度状态属于进程内投影；进程退出期间未完成的处理由下一次刷新或适用的自动
轮询重新执行，底层镜像、证据和已提交事件仍持久保存，不声称内存队列具有持久作业保证。
验证：阻塞证据处理时 HTTP 响应已发出；多次刷新合并并重读最新镜像；后台失败可查询
且前端持久提示。Focused 后端 95 passed，浏览器 3 passed，前端构建通过。

同一个慢请求项目 `test-f1c6f35c` 的最终实测：两次 Refresh 为 3.437285 秒、
3.445626 秒，均 HTTP 200；响应时 intake=processing，随后 summary 的 intake=ready。
原始请求结果、耗时和测试结果保存在 `web/test-results/refresh-background/`。

### 附件评论代码块与 PDF 提示修复（2026-09-14）

Issue #41 的 Markdown 附件包含内层三反引号代码块，旧发布器仍用三反引号包裹
全文，导致评论尾部的媒体链接落入代码块。新发布器根据附件摘录中的最长反引号串
选择更长的外层围栏。本地 Markdown 展示对带 ZaoFu evidence 标记、且媒体尾部
被未闭合代码块吞入的旧评论做兼容修复，普通用户内容不走此修复分支。

同一评论的 PDF 已通过 Release assets 发布，旧发布器却保留先前媒体分支生成的
`github_cli_media_only` 原因和省略说明。现在 Release 成功时清除对应旧原因和说明；
本地展示旧评论时，仅在原始下载区确有同名 Release 链接时去除该矛盾提示。
未发布文件和真实失败仍保留提示，不自动编辑远端旧评论。

验证：Self-Issue service/forge 82 passed，嵌套代码块、媒体与 PDF 的浏览器回归
1 passed，前端构建通过。Issue #41 PDF 下载链接经 HEAD 验证 HTTP 200。
### Portable Issue 工作区：报告轮次与可编辑 Draft 分离（2026-09-23）

- 受控动作 `self-issue-session-reanalyze` 接收字段选择和 comment，保留此前报告、
  evidence 和 activity 的轮次历史；生成 Draft 不再要求先把分析会话置为 completed。
- `self-issue-session-pause` 将最近已提交 Working Set 置为 paused，尝试终止对应
  provider turn；revision/status 校验拒绝迟到结果。恢复沿用同一 Agent thread 和
  Working Set，不承诺从被中断命令的执行中间位置继续。
- `self-issue-session-materialize` 每次生成 Draft 版本；`self-issue-session-save-draft`
  以 revision CAS 保存编辑后的标题和完整 Markdown，只有内容变化才增加版本。
  版本 sidecar 通过 `self-issue-session-draft-version` 查询。发布 payload 使用该
  Markdown（保留脱敏和幂等标记），不另行重建用户已编辑的正文。
- 工具完整结果保存为带 digest 的 sidecar，`self-issue-session-activity-result`
  只允许按本 Session 的 activity id 读取。轮询仅带摘要和引用，不泄露模型推理。
- React 工作区持续展示各轮报告/活动，预览本地切换。关闭 X 在最小化旁，
  清理恢复引用并阻止旧轮询响应重新打开已关闭卡片。

#### Draft 自动保存

- `SelfIssueDraftEditor` 独立持有编辑状态，输入不刷新父层报告或工具日志；
  编辑模式不挂载 Markdown 渲染器，预览直接读取内存正文，不请求后端。
- `issueDraftAutosave.ts` 将本机备份与服务端保存分离：输入期间按 300ms
  节流异步写入 IndexedDB；停止输入 1 秒后同步服务端，持续输入最多等待
  5 秒触发同步。没有内容变化不产生新版本，同一编辑器只有一个服务端写请求。
- 后台轮询不覆盖正在编辑的正文；慢请求完成后继续保存后续编辑。发布、再次分析、
  重新生成 Draft 及关闭前等待 flush，失败时不执行后续动作。最小化只折叠、不卸载
  编辑器，触发本机备份但不等待网络保存，也不受发布 busy 或草稿冲突阻塞。
  页面隐藏或 pagehide 时尝试提前备份和同步，但不依赖浏览器退出时的网络请求成功。
- IndexedDB 以 Session/Draft/随机编辑器实例隔离备份，刷新后恢复尚未同步的内容。
  恢复副本基于旧服务端版本或存在不同本机副本时展示冲突，用户明确选择后再同步；
  清理恢复副本采用内容条件删除，不误删其他标签页更新后的副本。
- 服务端 revision CAS 冲突保留本机内容并显示远端版本；已发布或锁定的 Draft
  不允许用本机副本覆盖。存储不可用、网络失败均显示错误，可重试同步。
- 本机保存不是逐键零丢失承诺：突然崩溃可能丢失最近约 300ms 或尚未提交的
  IndexedDB 事务；浏览器调度、存储配额和磁盘错误也影响持久化。5 秒是正常
  调度下发起同步的等待上限，不是弱网下完成同步的时限。

验证入口：`tests/test_self_issue_workspace.py`、`tests/test_self_issue_session_host.py`、
`tests/test_issue_core_session.py`；Docker `mcp/playwright:latest` 下运行
`web/tests/portable-issue-workspace.spec.ts` 与 `web/tests/self-issue-workspace.spec.ts`。
自动保存专项：`web/tests/issueDraftAutosave.test.ts` 覆盖保存节奏、单写入请求、
慢请求期间编辑、刷新恢复、跨标签页副本保留、存储/网络错误和发布锁定；浏览器
测试额外覆盖 IndexedDB 未同步副本恢复、保存失败阻止发布及冲突的显式恢复。

#### 只读轮询与防重记录的内存边界（2026-09-23）

- 现场 `web-actions/idempotency.jsonl` 达 1,595,054,888 bytes，约
  1,591,960,106 bytes 来自 Draft/Session 只读轮询。原查重路径使用
  `read_text().splitlines()` 并保留所有解析对象，产生全历史字符串/对象副本；
  Web 进程在 12:47:17 被 Linux OOM killer 终止（匿名 RSS 约 15.9 GiB）。
- `self-issue-get`、`self-issue-intake-get`、`self-issue-session-get`、
  Draft 历史版本与活动详情读取保留 token 和输入校验，但不走 mutation receipt
  或请求/完成事件写入，不重放旧的读取响应。浏览器读取也不失效整个项目缓存、
  不触发全页事件/看板刷新；读取超时为 10 秒。
- `src/zf/web/idempotency_index.py` 建立可重建 SQLite 字节偏移索引，JSONL
  仍为防重记录的权威来源。持同一 JSONL 文件锁按行增量更新索引，仅按 key
  读取对应完整 receipt；不把历史响应复制进数据库或 Python 内存缓存。
  替换/截断后重建，未完成行拒绝 mutation，防止把未知结果误判为未执行。
  原有 pending/conflict/replay 语义和启动恢复保留，历史记录不删除。
- 现场旧文件在 512 MiB 地址空间约束下首次建索引峰值 RSS 25.6 MiB，
  首次建索引 43.5 秒，热查询约 2.1ms。该数据属于独立索引验证进程，
  不是 Web 服务总内存或端到端请求时延。
- Self-Issue mutation 的事件列表补刷不再阻塞 action 返回，创建 Session 成功
  即可展示分析工作区；其他动作维持原有反馈次序。

回归入口：`tests/test_web_idempotency_index.py`（内存上界、并发防重、迁移与
恢复），`tests/test_self_issue_read_actions.py`（只读不写盘及鉴权），
`tests/test_web_headless_recovery.py`、`web/tests/actionPolicy.test.ts`。

#### Report → Draft 字段与证据下载对齐（2026-09-23）

- 发布投影保留报告环境别名、带来源的 Python 版本、scope、suggested_components 和
  provisional 标记；复现步骤数组渲染为编号列表。不得用生成 Draft 的宿主环境填补
  复现环境，不得把 fixture revision 当成产品源码 revision。
- `repository_revisions.issue_project_root` 与相关源码 checkout 分开记录；相关源码
  revision 不等于 wheel 的构建 revision。仅呈现已知版本上下文，不输出尚未进入
  fix/candidate/PR/merge 阶段的空 SHA。产物来源/版本由 Agent 调查，不从散文机械猜测。
- legacy Draft 模型默认的 P2/unverified/high/static_only 不能冒充报告结论。
  portable 发布使用显式 report facts；未分类时显示待分诊，不发 unknown 标签；
  未明确给出 P0–P3 时也不以默认 P2 发标签。Agent prompt 要求明确输出分类、
  复现状态、置信度和证据模式，并区分附件复现记录与本轮现场执行。
- 仅快照 Working Set 明确引用的现存普通文件（项目/其包含 checkout、受控 state、
  临时调查产物），每文件最多 20 MiB、Agent 文件合计最多 100 MiB。密钥目录和
  `.env*` 不采集，文本脱敏，图片清除元数据；已声明 digest 不匹配则拒绝。
  缺失、敏感、过大等拒绝原因进入 Draft 附件警告，不把证据引用误称已上传。
- Agent 快照通过 sidecar writer 落盘，来源为 agent，用户附件为 user；保存说明、
  用途、hash 和本地原始引用。公开源码引用转为相对路径及行号，隐藏个人路径前缀。
  新 Draft 只携带当前 Working Set 的 Agent 文件，历史快照不删除。
- 发布前可展开附件元数据清单；不内联或预览文件内容。GitHub 评论仅发布 Release
  下载 URL、source、说明、purpose 和 SHA-256，不再同时用 gh 内联图片/视频。
  Release 未完整上传时保留部分发布/可重试状态，不写带成功幂等标记的不完整评论，
  不用评论 URL 冒充下载 URL。不会自动改写已发布的 Issue 或其冻结 Draft。

验证：`test_self_issue_workspace.py`、`test_self_issue_session_host.py`、
`test_self_issue_markdown.py`、`test_self_issue_service.py`、
`test_self_issue_forge_contract.py`；浏览器 `portable-issue-workspace.spec.ts`。

#### Report → Draft 无损投影与可读分析

- 分开保留 `reported_environment` 与 `investigator_environment`，以及 Issue 工作区、
  调查 checkout、wheel 来源 revision、dirty 和产物摘要；未知构建来源不得用 HEAD 代替。
- Analysis 用“一、/1./(1)/a.”层级；Claim 同级，按 Claim、Confidence、Status、Support
  排列，公开正文不展示内部 Claim ID。Conflict 先问题再证据再解决方案。
- 证据引用附带 Agent 给出的支撑结论；旧报告没有逐 Claim 支撑说明时，展示对应证据
  已记录的 result/summary，不另造结论或置信度。证据命令、位置、执行范围及限制不丢弃。
- 缺少 Severity 时仍显示“待评估”，不得回填 typed store 的默认 P2；新调查提示要求
  Agent 明确评估并说明理由。根因、测试评估及分类/置信度理由保留到正文。
- typed gap 保留 description、blocking、kind 和明确阶段；不因为来源限制提到
  clean-install 就把它变成修复后任务。兼容旧的字典字符串，公开正文不输出 Python repr。
- Agent 文件索引同时识别 ref、locations、artifact、metadata_ref，复用同一边界、脱敏、
  大小与摘要校验。主产物摘要不误用于其 metadata 文件；不支持的产物生成附件警告。
  只有命令、没有结果文件引用的证据不能伪装成已上传日志。

验证：Self-Issue markdown、workspace、session-host、service、forge focused tests；
浏览器 portable-issue-workspace 的同级列表和预览检查。

#### Settings 源码目录与发布目标

- 将“源码仓库远端（origin）”与只读的“Issue 发布仓库”分开展示。发布仓库来自
  当前项目继承后的有效发布策略（targets 优先于 target_project），不从 Git origin
  推断；支持多发布平台，未配置时明确提示。编辑源码路径不会改变发布目标。
- 当前项目的 Settings 提供“当前项目目录／指定本地源码目录”；路径指运行 Agent
  的机器。先检查目录，显示解析路径、Git root、origin（去除认证信息）、分支、
  HEAD、包含未跟踪文件的 dirty 状态；非 Git 目录明确警告，不虚构 revision。
- `self-issue-source-get/inspect` 是鉴权只读 action，不产生 receipt/event。
  `self-issue-source-save` 经项目级受控 action 修改当前项目 `zf.yaml` 中的
  `self_issue.source_mode/source_root`；配置 digest CAS 防止覆盖其他编辑，原子写入，
  只更新 YAML 字段 span，保留其他配置、注释及多文档。目录无效时不保存。
- 每次新建 Session 读取当前项目的最新源码设置，无需重启 Web/runtime；
  发布目标的 workspace 锁定策略不得覆盖项目自己的源码路径。Settings 不改变
  triage/fix 的源版本选择策略或 GitHub 发布目标，不 fetch/checkout/修改远端。
- Session 创建时记录源码目录、revision、branch、remote 和 dirty；后续
  retry/reanalysis/materialize 及只读投影使用已保存的 repository_root，不能因
  Settings 更新而静默切换目录。HEAD 只标识 checkout 基线，不证明 wheel 的构建来源，
  dirty 状态也不等同于完整保存未提交补丁。旧 Session 无绑定时保留兼容行为。

验证入口：`tests/test_self_issue_source_settings.py`（配置保留/CAS、鉴权、热生效、
新旧 Session 目录隔离）、`web/tests/issue-source-settings.spec.ts`。

#### 公开证据过程与路径呈现（2026-09-24）

- Agent 证据附件之外，发布 Draft 时会将已保存的只读工具调用、命令参数、结果和截断标记按执行顺序写入 `investigation-process.log`，作为 Evidence comment 的过程附件；缺失的完整结果不会被伪造成已执行。
- 证据 `Locations` 保留具体路径，仅继续对凭据、Token、密码和邮箱等敏感内容脱敏。公开 Markdown 中记录字段使用字母层级，记录字段内部的下一层使用小写罗马数字；Verification summary 的 Evidence refs 使用独立对齐的多行列表。

验证入口：`tests/test_self_issue_markdown.py`、`tests/test_self_issue_workspace.py`、
`tests/test_self_issue_service.py`、`tests/test_self_issue_log_evidence.py`。

#### Settings 与 queued Triage 交付检查（2026-09-24）

- Developer Token 仍只由后端 HttpOnly 会话保存。会话有效时，首次进入 ZaoFu、刷新页面或重新进入应用优先恢复 Developer 模式；用户手动切换到 User 仅影响当前页面，撤销或过期后回到 User。Token 不写入 localStorage/sessionStorage。
- Settings 已逐项检查个人偏好、Self-Issue reporting、Browser capture、Investigation source、Refresh & data、List & details、Hover & motion、Queue & sync、Runtime boundaries 和 Developer access 的现有接口/消费者；Feishu 联调按要求不检查。项目配置只通过现有 GET/PATCH 与受控 source actions 修改，个人偏好不发 PATCH。
- queued Triage/Fix 的取消只接受当前项目事件账本中与该 Issue、该队列 lane 匹配的最新本地队列请求。GitHub 的 queued 标签没有对应本地 `triage_id` 时保持展示状态，但取消按钮禁用并提示刷新或切换到实际排队项目；不构造 ID、不跨项目取消。

验证入口：`tests/test_settings_integration.py`、`tests/test_issue_triage_mirror.py`、
`web/tests/settings-workspace.spec.ts`、`web/tests/issue-triage-actions.spec.ts`。

#### Triage 加载与跨项目只读缓存（2026-09-28）

- 跨项目 owner 解析按请求批量执行：项目注册表遍历一次，每个去重后的 state_dir
  读取一次镜像并检查一次事件指纹，复用现有 workflow projection 缓存。
  列表、统计、详情和取消使用同一 owner 规则：按 Issue 身份匹配，选最近排队记录，
  保留终态，不以滞后的 GitHub 标签复活已取消请求。取消仍由 owner 项目的受控 action 执行。
- 前端按项目、筛选条件和详情编号保存最后成功的只读响应，最多 100 项、保留 5 分钟，
  仅存在当前页面内存。返回已访问项目时先显示该项目快照，同时重新请求；未访问项目
  不借用其他项目的记录。浏览器整页刷新后重新加载，缓存不是业务状态或权限依据。
- 列表与统计各自返回即显示，不再相互等待；同路径在途请求合并，避免切页、轮询叠加。
  请求失败保留最后成功内容并沿用错误提示；启动、取消、刷新和受控修改后清除所有项目的
  Triage 快照，避免 owner 变更导致其他项目残留旧记录。
- 凭据变化、Developer access 变化、401/403 清除缓存；失效前的在途响应不能重新写入缓存。
  所有新 GET 仍走后端鉴权，缓存不持久化到 localStorage/sessionStorage。

验证入口：`tests/test_issue_triage_mirror.py`、`web/tests/issueReadCache.test.ts`、
`web/tests/issue-triage-loading.spec.ts`，以及原有 controls/actions 浏览器回归。

#### Settings 行为与精简（2026-09-28）

- Triage（包含 portal 弹窗）跟随 light/dark/system；Settings 的暗色表面、侧栏、文字
  使用应用全局 token，不再维护另一套蓝灰色暗色背景。浅色布局保持原有样式。
- 时区是显示偏好，不改服务器时区、原始时间戳或证据正文。公共日志/看板时间、Agent
  消息、Trace/交付/Loop、审批日期、Triage、PR 合并时间和 Settings 授权时间使用同一偏好。
  相对时长不受时区影响；事件原文中的 ISO 时间不重写。
- List & details、Hover & motion、Notifications 页面与过程通知设置删除。列表固定
  每页 100，成功和信息通知均 5 秒，其余沿用原默认值。旧 localStorage 中这些选项
  不再覆盖默认行为。错误和需要人工处理的通知继续保留到手动关闭。
- Refresh & data 保留自动刷新、间隔和返回刷新。总开关关闭停止轮询及返回触发；开启
  时按配置间隔查询本地镜像，不执行 GitHub intake。不再提供隐藏标签页暂停选项；浏览器
  自身后台节流仍可能延后定时器。返回刷新仅在其开关开启时触发。
- 新报告的受控 capture/intake 入口读取当前配置，`enabled=false` 返回
  `self_issue_disabled`，不创建报告或调用 Agent；已有会话读取/取消仍可使用。自动发现
  要求 `enabled` 和 `automatic_detection_enabled` 同时为真。
- 已有评估收集器每次读取当前 browser capture 设置；关闭时不调用截图进程，命中
  denied URL 时返回 `blocked_by_policy`。开启仅允许已有安全约束下的本地被动取证，
  不代表任意远程网页都可取证。Settings 测试覆盖隔离/mock runner，并提供显式 Docker
  验证：临时本地 HTML 页开启后生成 PNG，关闭和禁止 URL 时不发请求，不访问用户服务。
  此开关约束该受控自动截图入口，不是第三方 Agent 任意工具的通用网络隔离策略。
- GitHub 同步间隔文案说明 Issue 状态从 GitHub 同步至 ZaoFu；合法范围仍为
  60–604800 秒，与页面本地数据刷新不同。

验证入口：`tests/test_settings_behavior.py`、`tests/test_settings_integration.py`、
`tests/test_self_issue_entrypoints.py`、`web/tests/settings-behavior.spec.ts`、
`web/tests/settings-workspace.spec.ts`、`web/tests/settingsPreferences.test.ts`。
真实浏览器验证命令：`ZF_TEST_BROWSER_CAPTURE_DOCKER=1 .venv/bin/pytest tests/test_settings_behavior.py::test_capture_policy_with_real_docker_browser_on_isolated_page -vv --no-cov`。

#### 取消排队后的手动重启（2026-09-28）

- 手动 Start Triage 保留开发者鉴权及 Self-Issue 总开关校验，不再受后台
  `self_issue.ingress.enabled` 限制；仅刷新所选 Issue，不启动仓库级同步或开启自动轮询。
- `external_issue.triage.queued_cancelled` 是可人工重试的终态。同一内容版本重新排队
  复用 Task 身份、创建新 Triage Run，并记录前一轮引用；重复点击仍返回 `already_queued`。
  取消和重启仍走受控 action、事件及既有 Store writer，不直接修改运行态文件。

验证入口：`tests/test_issue_triage_mirror.py` 的排队→取消→关闭自动 ingress→重启 API 回归，
以及 `tests/test_external_issue_ingress.py` 的重试、总开关及后台轮询隔离测试。

#### 取消标签同步与界面语言（2026-09-28）

- queued Triage/Fix 取消先通过受控 action 持久化取消事件，再调用生命周期标签 writer
  替换 GitHub 的旧生命周期标签，保留普通标签；同步成功后由 mirror writer 更新本地标签。
- 返回 `lifecycle_label_sync` 明确区分同步成功、待重试和已被新排队取代。远端失败不回滚
  已落盘的取消，刷新可重试；旧取消记录不得覆盖新一轮排队。
- 界面语言统一使用偏好存储和应用文案字典，支持 English、中文、한국어、日本語；
  工作区导航及各页面的自有文案订阅偏好变化。Issue、证据、Agent 输出和用户内容保持原文。
- 验证：`tests/test_issue_triage_mirror.py` 的取消与标签同步用例、
  `web/tests/issue-triage-actions.spec.ts`、`web/tests/interfaceTranslations.test.ts`、
  `web/tests/settings-workspace.spec.ts` 的四语言切换与刷新持久化用例。

#### 刷新锁竞争与取消标签批次（2026-09-28）

- intake 锁已被轮询或手动操作持有时，后台刷新返回 `syncing`，保留最新请求并等待重试，
  不再等待 60 秒后误报取证失败。仅锁获取竞争走此分支；实际取证异常仍保持失败提示。
- 刷新补偿取消标签时按 Issue 去重，并排除已重新排队的旧取消记录；最多 4 个并发远端
  请求。网络线程不写事件或 Store，由主线程汇总已确认结果后通过 mirror writer 单次
  原子写入全部成功项的标签名称和 GitHub 颜色，失败项保留原镜像并可重试。
- 批次期间与上游镜像刷新互斥，避免刷新读取远端更新到一半的批次；不删除锁文件，
  不直接改写事件或 canonical stores。GitHub 不提供跨 Issue 事务，远端仍分别调用 API，
  本地整批发布不等于承诺多个 GitHub Issue 在同一瞬间变化。
- 验证：`tests/test_issue_label_batch.py`、`tests/test_issue_ingress_background.py`、
  `tests/test_issue_lifecycle_labels.py`，并覆盖现有 ingress、mirror 和 Forge 调用链。

#### 手动操作响应与远端投影解耦（2026-09-29）

- Web 的 Start Triage 及取消排队先在本地事件账本中完成确定性入账并返回最新队列状态；
  不在点击请求内等待 GitHub 刷新、附件下载或生命周期标签 API。
- 已有 Mirror 用于手动准入时，GitHub 刷新、附件证据和生命周期标签收敛由响应后的后台任务执行；
  intake 锁只等待极短时间，竞争时快速返回 `syncing`，避免远端超时把页面拖到分钟级。
- 远端投影失败不撤销本地队列或取消事件，沿用现有生命周期补偿入口在后续刷新中重试。

#### 跨项目取消标签补偿（2026-09-28）

- 生命周期补偿通过 WorkspaceRegistry/ProjectResolver 解析已注册项目的 `zf.yaml`，
  读取所属项目的活动及归档事件，不信任 `state_dir_hint`，不在 GET 请求里触发远端写入。
- 对同一仓库级 Issue 身份按事件时间关联最新请求；Run/Task 绑定保留项目作用域。
  `run.cancelled` 通过原请求恢复 Issue 身份及 Triage/Fix 阶段，分别同步
  `triage_cancelled`/`fix_cancelled`。旧 Run 的延迟取消不得覆盖新的请求。
- 刷新及后台轮询沿用受控 ingress 的补偿入口；自动 ingress 关闭时手动刷新也能补偿。
  仍只处理当前配置仓库的镜像；远端确认后一次发布本项目成功项的标签与颜色，保留普通标签，
  不伪造取消事件、不重新启动工作流，其他项目的镜像通过其刷新获取远端结果。
- 验证：`tests/test_issue_label_batch.py` 覆盖跨项目读取、Run 取消、Fix 阶段、旧取消与新请求，
  并通过真实注册表解析及刷新 API 验证自动 ingress 开关两种路径（远端适配器使用模拟实现）。

#### 全生命周期标签一致性（2026-09-28）

- 问题并非 Triage 专属：旧标签曾阻止 ingress 后续状态投影，历史 Triage outcome 可重复
  写回旧标签，候选评审/PR 更新也曾只写远端而未即时更新本地镜像。统一补偿覆盖
  `lifecycle_states.py` 的全部 32 个状态，包括 Fix、验证、人工评审、发布和 PR/合并状态。
- Web 与同步器复用 `runtime/issue_lifecycle_projection.py` 的事件投影。同步时关闭远端
  标签覆盖，避免把自身旧输出当成新的期望状态；取消专用入口保留为兼容包装，手动刷新
  （含关闭自动 ingress 时）及后台处理统一走全状态补偿，不再注入无事件来源的状态字符串。
- 注册项目按最新 admission 选择运行归属；仅收到镜像不抢占已 admission 的项目。
  Run 身份隔离旧取消、旧评审、旧 PR 及旧子任务 dispatch；已被其他项目 admission、
  但远端标签尚未写成的 Issue 不因刷新而重复自动排队。无本地工作流证据时保留远端状态，
  `open`/`closed` 以 GitHub Issue 自身状态为准，不由 Run 推断。
- 每轮先完成事件处理再输出最终标签，不依次发布过程中的旧 outcome。最多 4 个远端请求
  并发，主线程单次原子投影整批成功项的名称及颜色，普通标签不变；同名缺颜色也可修补。
  请求期间若发现状态推进或新 admission，旧响应不写入镜像，并留下待重试记录。
- 候选评审、发布准备及 PR 更新共用刷新锁；Forge 成功后经 `IssueMirrorStore` 立即投影
  本地标签与颜色。网络失败、锁竞争或新状态抢先时保留原镜像，业务事件不回滚，后续刷新
  从持久事件重试。GitHub 不支持跨 Issue 事务，远端逐 Issue 写入不宣称为跨 Issue 原子事务。
- 验证：`test_issue_all_lifecycle_sync.py` 覆盖 32 状态的本地/跨项目同步、失败重试与
  刷新 API（自动 ingress 开/关），以及混合状态单批发布、跨项目归属、旧 Run 和并发推进；
  `test_issue_candidate_delivery.py` 覆盖评审即时投影、网络失败、刷新锁竞争及新 attempt。
  原有批次、镜像、ingress、澄清及前端状态展示回归继续保留。
