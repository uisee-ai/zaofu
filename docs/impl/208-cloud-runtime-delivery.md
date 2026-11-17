# 云端 Runtime 镜像、入口与联验纠正

本轮承接 203 §4.6、§5、§11 与 204 §4.7 的完整工作台合同；任务见
[Z05](../../tasks/active/2026-11-17-1250-z05-cloud-runtime-delivery.md)。Controller 侧对应
`docs/design/06-cloud-runtime-delivery-contract.md` 与 Q01b，Keycloak 路线保持不变。

## 可构建交付

从仓库根目录运行 `docker build -f deploy/cloud/Dockerfile -t zaofu-runtime:<version> .`。
镜像固定 Python/Node 基础镜像摘要，按 uv.lock 与 package-lock.json 安装，构建完整
Web bundle。仅允许列出的源码、skills、包元数据和 Web 进入构建上下文；用户凭据、
Git 历史、运行目录不进入镜像。

实例包含当前 CLI、源码内模板、规范 skills、Web 及 git/tmux/Node/npm/tini；默认
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
| 新建项目报 EventLog is outside the managed Project scope | **仍在实施**：在首个业务事件前完成受限项目接入；不绕过 EventLog 授权检查 | 需要 Controller scope 与本地初始化的可恢复接线、额度/断连负例 |

claw02 已通过完整前端构建、非 root/只读根 CLI 与 tmux 启动、gVisor+Longhorn RWOP
基础设施探针，以及真实 OIDC/TLS Gateway 到两名用户空工作台的访问。原有
standalone ZaoFu 8002 保留。项目、Session、终端、用量、恢复和 Provider 仍按各自
证据签收，不能以空工作台成功宣布 203/204 全部完成。

本轮 Web 准备线程变更采用模块、调用入口和共享准备合同的聚焦闭包，不因自动规划器
按 96 个候选文件触发全套阈值而把数万测试作为默认门槛。实际测试命令、结果与浏览器
证据随任务归档；此处不把聚焦结果标为全套验证。
