# 云端 Runtime 镜像、入口与联验纠正

本轮承接 203 §4.6、§5、§11 与 204 §4.7 的完整工作台合同；任务见
[Z05](../../tasks/active/2026-11-17-1250-z05-cloud-runtime-delivery.md)。Controller 侧对应
`docs/design/06-cloud-runtime-delivery-contract.md` 与 Q01b，Keycloak 路线保持不变。

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
