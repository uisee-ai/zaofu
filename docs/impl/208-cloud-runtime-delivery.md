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
ServiceAccount token。模型登录、API 凭据和 Provider CLI 的版本资格另行验证，
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
