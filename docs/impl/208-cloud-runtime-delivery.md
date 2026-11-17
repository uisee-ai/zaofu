# 云端 Runtime 镜像、入口与联验纠正

本轮承接 203 §4.6、§5、§11 与 204 §4.7 的完整工作台合同；任务见
[Z05](../../tasks/active/2026-11-17-1250-z05-cloud-runtime-delivery.md)。Controller 侧对应
`docs/design/06-cloud-runtime-delivery-contract.md` 与 Q01b，Keycloak 路线保持不变。

## Q01c 真实默认 Workflow 暴露的运行合同

claw02 gVisor 中 Codex 已进入终端前台，tmux 的 `pane_current_command` 仍报告
`bash`。不能据终端文字认定 Provider 存活：仅当 Linux procfs 证明前台进程组、TTY、
session、父子链及进程启动时间均绑定当前 pane 时，允许识别该前台 Codex/Claude。
取消先通知这个已验证前台组和 pane 组，再按既有时限升级终止；PID 重用、后台进程、
无 procfs 或角色归属不明均不构成运行证据。

动态 resident/fanout 角色由实际 Transport 注册启动合同，纳管执行取该合同并逐轮申请
租约及预算；不能只重新读取静态 `zf.yaml.roles`，也不能因角色动态产生而免除准入。
计量读取配置 state_dir 下对应角色独立的 `codex-home/sessions`，并核对实际启动
工作目录（包括受管工作树），不扫描其他角色或用户的全局会话作替代。

Codex TUI 常驻进程按原生 `task_started` → usage → 同 turn_id `task_complete`
结算，完成后释放本轮名额，无须等待整个 Workflow 或进程退出。部分 token 更新、
提前发出的业务完成事件、错配轮次和不完整 JSONL 均不证明原生调用完成。首轮零基线
仅在完整原生日志证明没有先前用量时成立；取消后缺少计量的前序轮次继续保留未知责任。
该资格绑定实测 Codex 0.166.0；其他 Provider/日志形状不能据此自动宣称同等资格。

验证工具也有独立边界：本节点 `kubectl exec` 读取 100,000 字节时实际只收到
65,537 字节但返回 0，复现于独立 Python 输出，不属于 ZaoFu JSON 格式缺陷。
长证据必须先落在 Pod 文件，再分块读取并核对长度与 SHA256；不能据 exec 退出码
认定证据完整，也不能关闭验证或修改业务输出去掩盖传输截断。

随后真实 stop/adoption/start 暴露 Project 槽位恢复缺口：Controller 已因旧 Pod 的
物理停止证据释放租约，本地持久义务仍为 starting。恢复必须查询当前机器有权读取的
同 Project 租约证明，绑定 Workspace、租约 ID、当前 deployment/generation；跨代
仅接受 Controller 核验旧部署及已完成 stop 的物理 fence。此确认只解除本地 Project
槽位，不清除用量或预算未知责任。原运行锁或同代存活 owner 仍阻止启动；更新本地义务
使用条件更新，不能覆盖查询期间已替换的新租约。CLI start 和 Controller project.load
共用这条路径，之后重新申请运行租约；同 Project 并发申请在 Controller 串行拒绝第二个
运行实例。旧接口不支持、失联或证据不完整时保持拒绝。

## 可构建交付

从仓库根目录运行 `docker build -f deploy/cloud/Dockerfile -t zaofu-runtime:<version> .`。
镜像固定 Python/Node 基础镜像摘要，按 uv.lock 与 package-lock.json 安装，构建完整
Web bundle。仅允许列出的源码、skills、包元数据和 Web 进入构建上下文；用户凭据、
Git 历史、运行目录不进入镜像。

实例包含当前 CLI、源码内模板、生产控制器配置及 profiles、规范 skills 和 yoke 依赖、Web 及 git/tmux/Node/npm/tini；默认
非 root，HOME 为 `/workspace/home`。部署侧提供独享 `/workspace` 持久卷、限额
`/tmp`、只读 WorkspaceConfig 和独立 TLS Secret。使用只读根文件系统，无 Kubernetes
ServiceAccount token。镜像带校验和固定的 Herdr 0.9.5 与 npm lock 固定的 Codex 0.166.0。模型登录、
API 凭据与真实调用另行验证，
不能把镜像能启动等同于已授权模型调用。

入口为 `zf controller serve`，在绑定机器身份并完成首次同步后开放 8443。Controller
与 Gateway 的 TLS 信任保持校验；私有 CA 使用完整系统信任包，不能关闭证书验证。
持久 home 保留 Connector pin、registry、Provider 登录及用户项目；机器代次变更
由现有 bootstrap/单写隔离合同处理。

## 实测纠正

| 反例 | 修正合同 | 验证边界 |
| --- | --- | --- |
| WorkspaceConfig 的目录被准备线程视为默认 Project，持续报配置格式错误 | workspace-only Web 不登记默认项目根；registry 后续项目仍由原准备线程消费 | 覆盖实际 Web factory、后续登记项目与受管 serve caller |
| 用户已登录 Gateway，但 Runtime 复核返回 managed_access_denied | Controller 必须用 Gateway confidential client 刷新 Gateway 会话，并绑定会话来源 client | claw02 真实 Keycloak 证明 Console 刷新 invalid_grant，修正后两名用户可进入各自工作台 |
| 新建项目报 EventLog is outside the managed Project scope | 限额内 Project 准入 → 原策略 ACK → 首个业务事件；持久 intent 支持失联重试，保留 EventLog 授权检查 | 聚焦覆盖额度拒绝、响应/ACK 丢失、同目录并发和路径越界；真实 Gateway 简易 Project 返回 201，snapshot 返回 200 |

claw02 已通过完整前端构建、非 root/只读根 CLI 与 tmux 启动、gVisor+Longhorn RWOP
基础设施探针，以及真实 OIDC/TLS Gateway 到两名用户空工作台的访问。原有
standalone ZaoFu 8002 保留。项目、Session、终端、用量、恢复和 Provider 仍按各自
证据签收，不能以空工作台成功宣布 203/204 全部完成。

本轮 Web 准备线程变更采用模块、调用入口和共享准备合同的聚焦闭包，不因自动规划器
按 96 个候选文件触发全套阈值而把数万测试作为默认门槛。实际测试命令、结果与浏览器
证据随任务归档；此处不把聚焦结果标为全套验证。

## Project 接入和重建的新增合同

`ProjectInitializer` 是 CLI/Web 共享的接入入口。云端创建先验证私有根和 state_dir，
准备配置/registry 稳定 ID，再调用 Controller 的 `/internal/v1/me/projects/admit`。
策略应用及 ACK 成功后才初始化业务状态。失败时保存安装级 intent，重试不需要 force；
只允许含 Controller 元数据且有匹配 intent 的目录恢复，不能把任意残缺项目清空。
standalone 保持本地使用；external 不能通过云端自助接口扩大本地同意范围。

初始化同一目录受文件锁保护。当前全局策略版本会撤销旧执行，因此 Controller 在
有执行租约、未结算预算或待执行命令时返回可重试冲突，不隐式终止用户已有工作。
无中断并行增加项目范围仍需单独设计并验证，不能把该限制当作已经完成的能力。

卷恢复必须同时保持身份文件的 0700/0600 权限。Controller 已发现并修正 fsGroup
默认递归放宽权限问题；Runtime 继续拒绝非 owner-only 文件，不自动递归 chmod。

云端新代次在传输历史 Outbox 之前协商 `cloud_receipt_handoff`，验证同 Workspace、
uploader、origin、stream、max_seq 与有效期。Controller 必须有旧写者的物理 fence。
旧 usage 不改 origin；历史 heartbeat 只闭合收据序列、不刷新在线态。自动交接不能
扩大首次确认的历史序号上界；迟发现的旧记录仍须运维显式复核。未获授权时记录继续
留在本地，不吞掉错误后清队列。详情见 Controller 设计 06 的修订 spec。

验证分层：初始化及管理路径扩大回归 131 项；增加历史传输与新进程 import 后相关
闭包 60 项；预合并 sentinel 53 项。真实 Provider 与公网 SMTP 仍未因此获得资格。

构建验收由 `deploy/cloud/check_runtime.py` 执行：在隔离 HOME/registry 下以非 root
初始化默认 PRD v5，并校验传递 skills 依赖、profile、Git 和 Session。真实联验先发现
生产目录/v5 配置缺失，构建验收进一步发现 yoke 方法依赖缺失；两者均已补入允许列表。
这项验收只初始化项目，不执行 Provider 任务，也不代替云端预算/认证联合验收。


## 终端工具交付补齐

反例：真实云端 `terminal-sessions` 显示 enabled=true，但 capability=false、Herdr
binary unavailable。本地 install.sh 已有工具安装，云镜像不能遗漏同一运行依赖。
Docker 构建复用 `tools/install_herdr.py` 的官方版本/SHA256，不再维护第二份下载逻辑；
Codex 使用 `deploy/cloud/providers/package-lock.json` 的固定版本和 integrity。
工具安装到镜像只读路径，不写入租户 HOME，也不携带任何登录材料。

构建检查以非 root 用户执行真实 Herdr schema/observe/control/tab rename 能力探测、
Codex 版本检查和默认项目初始化。其他 Provider CLI 未被这次镜像资格覆盖；不能用
Codex 已安装推断 Claude/CPA 已接通。实际终端 CLI 启动与模型推理分别验收，缺失
usage 必须保留 unknown/pending，不能伪造为零成本。

本次新增改动属于镜像工具交付；55 项既有安装器/终端适配测试通过，实际构建通过。
默认 PRD v5 的真实 Web 创建、Project 上报、云端项目额度拒绝和导出下载已通过。
完整模型调用的 Session/tokens 联验仍等待明确的凭据范围与 Provider 输入。


真实沙箱首次启动进一步暴露 readiness 竞态：Herdr server 已启动，但第一轮 2 秒
workspace list 探测超时直接中止了整个 5 秒启动等待。修正只在原有总时限内重试
`herdr_timeout`，不重复 spawn、不吞掉其他错误、不延长无界等待。新增冷启动探测超时
回归，终端适配、Web 路由及安装器闭包 70 项通过；claw02 冷启动创建/停止均返回 200。


跨代次重试反例：先因额度拒绝创建，之后实例升级，持久 intent 仍携带旧 generation，
导致重试一直被机器权限入口拒绝。现在只在用户再次初始化时，为更高的已验证机器代次
创建新的 request_id/current generation/current policy 请求；Project/root/state_dir 身份
保持不变。旧决定不被当作新授权、不会自动登记新范围；额度、忙碌、当前策略与 ACK
检查继续执行。检测到 intent 属于更新代次时拒绝倒退。失败不写业务事件。
新增换代重试回归后，项目接入、Workspace、managed 路径、终端适配/Web 与安装器
闭包共 131 项通过；未运行全套 pytest。


## Gateway 终端与 Session 投影纠正

终端 Origin 只取 ManagedAccessMiddleware 已验签、完成当前授权复核的请求 scope，
不能信任转发 Host。standalone 的 Host/scheme/allowed_origins 与一次性 ticket 保持原有
检查。实际路由与鉴权闭包分别通过 62/59 项；claw02 收到 full terminal.frame 与 3478
字节二进制画面。真实 Keycloak logout 后活动 WebSocket 以 4403 关闭，后续 API 401；
重新认证后通过受管接口停止进程，另一个用户继续访问。没有输入模型凭据或推理提示。

更强的终态验收发现：终端注册表的 stopped 已 ACK，但晚到的 controller.execution.finished
把同一 Session 投影为 unknown/idle，较旧的 stopped 版本随后无法覆盖它。修复只让
TerminalRegistry 提供终端 Session 生命周期；execution 收据保留 Operation 和用量投影，
不再把终端当成可复用 Session。新增测试从实际 EventWriter/collect_project caller 复现
先红后绿，闭包 131 项通过。错误历史收据保持原样，不改账、不重新解释同一 message_id；
当前实验旧行保留为反例，生产若已有同类数据须单独评审显式纠正方案。新 Session 已在 claw02 复验为 stopped；对应缺失用量保持 pending/null。

最终验证：默认 PRD 项目的真实 Kanban 已完成 snapshot 渲染；新终端停止状态及未知
用量已通过 Controller API 复核。脱敏证据、失败轮次、运行脚本、镜像/源码摘要及清理
结果归档到 `/home/min/workspace/.verification-archives/zaofu-controller-q01b-claw02-20261117/`。
原 standalone 8002 保留，实验资源已清理；本轮未获得真实 Pod Provider/CPA 或生产灾备
资格，Z05 继续 active。本地实现提交已完成，未合并/推送。

## Q01c：策略拒绝的存活通道

真实 claw02 验证发现控制面接受 batch_size=100，而 Runtime 合同上限是 32。实例
拒绝应用本来正确，但同步异常使心跳停止，Controller 又以过期心跳拒绝更正策略，
形成无法经产品接口恢复的闭环。Controller 修正严格数值边界；本地实例在可信策略
已返回拒绝 ACK 后，继续以既有安装身份上报存活并排空部署级 Outbox，然后保留原
拒绝结果。没有应用新策略，不执行命令/维护，不声明 Kernel ready。

覆盖入口为 `synchronize_workspace`；回归同时证明失败版本不覆盖本地 policy、没有
applied=true，只有真实 heartbeat，后续合法版本仍可正常应用。身份/协议校验失败
维持原拒绝，不把信任边界失败掩盖成配置问题。对应 Controller 合同见兄弟仓库
`docs/design/06-cloud-runtime-delivery-contract.md` §17。真实环境资格由 Q01c 任务记录，
本段不是整批完成声明。

随后真实重启又复现首次同步抛出 `PolicyNotApplied`，使 Web 入口尚未监听就退出。
`lifecycle.serve` 仅捕获这一已验身份、已发送拒绝 ACK 的类型，继续启动受管 Web 与
Connector，等待后续合法策略；普通身份/协议错误仍在监听前失败。无已应用策略和
保留旧策略均覆盖，未签名请求始终 403。启动聚焦 21 项、直接调用闭包 68 项通过；
claw02 的失败轮次 `recovery-2` 保留，真实恢复以随后复验结果为准。

## Q01c：真实订阅用量与会话身份

claw02 的 gVisor Pod 经 TLS Gateway 完成真实 Codex Kanban turn；按账号的 `model/list`
选用可用模型后返回指定正文，Provider 总量 23,413（输入含缓存 23,401，缓存读 10,880，
输出 12）。Controller 标准化为新输入 12,521 + 缓存读 10,880 + 输出 12，恰好一次
入账；额度、Project/Session/Operation、排行和实际 Dashboard 相互核对通过。先前
模型不受订阅支持的失败用量保持 pending/null，账单金额未知；不将它归零。

进一步复盘发现 headless 的内部 thread binding 与公开 `run_thread_id` 不同，Connector
此前使用前者形成第二个 Session，也使按公开 Session 提交的停止 fence 无法匹配
该执行。`managed_turn` 改用已有的 `run_thread_id`，缺省仍用 `thread_id`；Provider
内部绑定及 native session ID 保持各自职责。回归从真实 decorator、EventWriter、
Collector 到受管 Session stop command 先红后绿，覆盖唯一 Session、费用归属、
原生会话标识不改名及停止后再次调用在获得 lease 前被拒绝。直接调用与三类 headless
后端、usage 和命令闭包 200 passed。历史已 ACK 的错误别名不改写，不重发不同正文；
新部署还需使用新 Session 做真实取消复验。

## Q01c：调用前拒绝不构成缺失消费

真实清空可用额度后，Controller 在预算预留前拒绝了 Kanban 调用，Provider 未执行；
但 Web 通用失败事件没有区分 admission，Collector 将其投影为 pending/null 用量。
这会把无需对账的拒绝混入未知消费。新增确定性的 `ProviderCallNotStarted` 边界：
只有 managed decorator 的 `Execution.begin` 抛错、尚未调用 Provider 时使用该类型；
Web/Channel 保留失败事件并写 `provider_started=false`，Collector 不为其生成消费或
Provider 故障样本。没有这个证据的失败仍保持未知，不按错误字符串猜测零消费。

原预算预留、租约释放、可能丢失响应的责任恢复逻辑不变；已启动后失败不能走此豁免。
回归覆盖真实 decorator → EventWriter/Collector，以及实际 Web action caller 的拒绝
与普通 Provider 异常。实验中旧错误记录继续保留，不修改已 ACK message 的正文；
新部署使用新 turn 验证拒绝记录增量为零。

真实 `budget-bob-1` 已验证新部署的额度拒绝不会制造消费、公开 Session stop 能终止
实际 Provider 子进程、关闭后拒绝执行、显式 resume 后恢复。取消调用没有独立原生
usage，恢复后的累计 token_count 不能唯一分摊给两次调用；未结预留保留。这是明确
的计量缺口，不能以停止成功或后续推理成功宣称取消消费结算通过。

## Q01c：gVisor cwd 与派发前检查

独立 tmux 探针复现 `pane_current_path` 为空，而同一 pane 的 `/proc/<pid>/cwd`
能返回真实项目目录。不能将空值当成目录匹配，也不能跳过路径授权。仅在 tmux
成功返回空值时，读取已验证归属 pane 的进程 cwd；读取前后核对进程身份、读取后
再核对 pane PID，目录必须存在且为绝对路径。PID 更换、缺失或所有权不明均拒绝。

同时将 send/compact 的存活、目录和模型切换检查移到 managed budget admission
之前；真正 clear/paste/send 仍在预算与租约保护内。这样确定未发送输入的检查失败
不会创建未知消费预留，已发送或无法证明是否发送的失败仍须保留责任。真实旧失败
记录不追溯改写。直接影响闭包 142 passed、3 deselected；sentinel 53 passed。
完整默认 Workflow 仍须部署后真实恢复验收。

## Q01c：原生取消用量的完整性与历史纠正

真实 Run Manager 在停止时已观测 419,492 tokens，但没有原生 `task_complete`；
旧逻辑把非空 counters 标成完整。修正后，tmux Codex turn 必须有匹配的原生完成边界，
Web Terminal 的整个新会话必须全部正常完成；取消、未完成和先前中断均只报告部分
用量。headless 非 completed 结果同样不能凭非空 counters 声称完整。保留已知 counters
和未知责任，订阅计量不变为 API 账单。partial receipt 不请求最终 settle。

新增 `zf controller usage-review --execution-id ID --reason REASON --evidence-ref
REF#sha256=DIGEST`，仅将已释放、已结算且原始 usage 已 ACK 的当前项目执行降为部分
用量。命令不能更改 tokens、费用、身份或核销责任；原始收据和远端结算证据保留，
追加同 record/origin/occurred_at 的 revision 2。依据安装 Ledger CAS 先持久化纠正，
再通过 EventWriter/Collector 追加；正常 Connector 可跨部署重放该纠正，幂等重试不
修改旧 ACK。Controller 既有修订对账冻结后续预算，保留已知消费；不得借旧 settle
ACK 在纠正尚未上传时再次标成已结算。证据引用和理由保留在事件及纠正义务中。

验证：原生 observer、managed caller、Terminal 影响闭包 133 passed；纠正补充测试
初次揭示旧 ACK 竞态与 CLI 未应用受管项目配置，两处均修正；CLI/Workspace 38 passed，
其余纠正/Collector/managed 闭包 90 passed（同轮 CLI 失败另由后续 38 项复验覆盖）。
sentinel 53 passed。集合有重叠，不累计为唯一测试数；真实部署复验另记。原默认
Workflow 保留在 paused 状态，不能借此宣称完整 Workflow 已通过。

进一步收紧 Terminal 结算：完成边界与计量 counters 必须来自同一次最终原生读取，
避免停止前快照比最后完成记录更早却被标为完整。新增不同快照数值的回归；Terminal
observer 与 managed callers 35 passed。

真实 `partial-adopt-1` 正常接纳新镜像，1,465 个运行源码摘要一致。两条中断收据通过
CLI 追加 revision 2 后，旧流 max_seq=20 正确拒绝新序号 21、22；审核精确两条纠正
和旧 writer fence 后，使用 Controller 既有 `grant-handoff` 将该 uploader/origin/stream
的历史上限授权到 22。未修改数据库或原消息，随后均 ACK。`delta-delete-2` 的独立
前置检查及 Dashboard 已确认已知 1,976,314 tokens 保留、两条 partial、完整总量 null、
账单 null 和预算冻结；删除后续步骤失败不影响这些已完成断言，失败原因另记录。

## Q01c：验证边界与 Workflow 资源复盘

后续 `delta-delete-3` 已完成真实 CSI 备份阻止删除、实验保留期、备份清除与审核、
PVC/PV/Longhorn 卷清理和账号关闭；并再次核对上述 partial Dashboard。不将任意
应用 HTTP preview 与已通过的 artifact preview 混淆。

默认 PRD 路由仍未完成，保留原请求和 paused/safe-halt 证据。实验配置 800m CPU、
2 GiB、2 agent 槽位与单调用 600 秒：常驻 Run Manager 和 product scan 已占满槽，
tech scan 被容量准入拒绝；多次受控读取耗时十几至几十秒，返工触及期限。600 秒
是夹具配置，Runtime 默认是 3,600 秒，不据此修改产品默认或削掉默认角色。

首轮只转发工具 stdout，缺少完整执行状态用于诊断；复查第二轮原始记录则证实
`wait` 与成功的受控读取存在，不能继续归因于未等待或工件不可读。agent 方法应
保留工具 handle、退出状态与最终正文；空 stdout 不等于空输入。资源/期限配置应
覆盖默认角色及其调用耗时，预算期限不能通过租约续期暗中延长。未知责任必须保留，
资源调整不构成旧 Run 成功证明；需从原请求走完完整门禁后才签收真实 Workflow。

最终 `terminal-final-adopt-1` 通过正常 stop/MFA adoption/start 更新最后的 Terminal
快照修复；实际 `/opt/zaofu/src` 的 1,465 文件摘要全部一致，Pod ready 且重启数 0。
该结果证明候选部署与源码绑定；Terminal 最终快照行为由 35 项定向回归覆盖，不
据此宣称未完成的真实默认 Workflow 已恢复成功。
