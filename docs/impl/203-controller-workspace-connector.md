# 203 部署级观测 Connector 的第一批接线

本批对应 [Z01a](../../tasks/done/2026-11-10-1226-z01a-workspace-connector.md)，承接
[接入计划](201-controller-integration-plan.md)、设计 203/204 与 Controller `bb06981` 的公开 HTTP 合同。
实现部署注册、空工作台心跳、多个本地 Project 的授权观测、安装凭据更新及可靠重放。
**能力为 `observed`，仍保留 standalone 的本地执行规则。** 完整 managed 访问、执行名额和
预算限制由 Z02/Z04 接续，不能将这一批的成功显示为全实例纳管完成。

## 配置归属

部署 Connector 使用自己的 `zf.yaml`，kind 为 `WorkspaceConfig`。它不是业务 Project，
没有 roles、Kernel、TaskStore 或虚构的 project.name；通用 Project loader 不接纳该 kind。
本批只有 `zf controller --workspace-config` 读取它。原 Project `zf.yaml`、Workspace registry、
Web 和 Workflow 的 standalone 入口继续沿用原合同。运行工作台与 Connector 是两个明确入口，
尚未把 Connector 嵌入云端镜像的启动/停止流程。

```yaml
version: '1.0'
kind: WorkspaceConfig
workspace:
  name: default
  state_dir: controller-state
  controller:
    management: observed
    endpoint: https://controller.example.com
    issuer: https://identity.example.com/realms/zaofu
    workspace_id: replace-with-approved-workspace-id
    mode: self_managed_cli
    projects: []
    interval_seconds: 15
    batch_size: 16
```

`workspace.name` 选择现有 `WorkspaceRegistry`，其根目录仍由 `ZF_WORKSPACE_HOME` 或
既有默认路径确定。`projects` 是安装所有者明确同意上传的 registry **精确 Project ID**，
不是路径、别名或任意可发现项目。Server policy 是另外一道限制：远端范围必须全部已获
本地同意；本地同意但未获远端批准的项目不上传。增加或删除项目不改写其业务配置。
Project 状态路径每次从所属 `zf.yaml` 重新解析，不信任 registry 的历史 state_dir_hint。

`workspace.state_dir` 是 Connector 的部署级状态位置；Project outbox 继续位于各自
`project.state_dir/controller/`。机器私钥/密文只在部署目录的 `installation/` 下，
不会写到每个 Project 的 YAML、环境文件、事件或 outbox。旧的单 Project
`observability.controller` 仍支持；同一 Project 已绑定其他身份时拒绝同时接管。

## 注册与运行

先安装可选依赖 `uv sync --extra dev --extra web --extra controller`。部署配置放在单独目录，
例如 `/srv/zaofu-connector/zf.yaml`；以下路径均需替换为实际安装位置。

```bash
zf controller installation-key --workspace-config /srv/zaofu-connector/zf.yaml
```

该命令输出安装公钥和指纹。所有者在 Controller 已获批的外部 Workspace 页面提交该公钥
及 Project ID，取得十分钟有效的注册授权码；将授权码保存至一个 `0600` 文件，随后执行：

```bash
zf controller enroll --workspace-config /srv/zaofu-connector/zf.yaml --code-file /private/enrollment-code
zf controller sync --workspace-config /srv/zaofu-connector/zf.yaml
zf controller sync --workspace-config /srv/zaofu-connector/zf.yaml --watch
zf controller status --workspace-config /srv/zaofu-connector/zf.yaml
```

授权码不作为命令参数值传入，也不输出到日志；用后由操作者删除授权码文件。`status` 是
本地状态，明确 `online_verified=false`，不将缓存身份当作实时在线/未撤销证明。
无 Project 时仅发部署心跳；配置项目后先成功同步一次，建立来源计量起点，再运行新工作。
纳入前的历史事件不自动计入本期消费。新部署不能重领已有 outbox 的来源身份。

远端权限升级为 managed、增加未同意项目、策略回退、身份错配或缺凭据时，同步失败并
返回非零状态。认证失败不清空 outbox，不尝试换账号、不修改管理模式。观测安装的本地
Workflow 保持原 standalone 执行语义，不能据此宣称它具备平台硬限额。

## 凭据与协议

- RSA 安装私钥只生成一次。RSA-OAEP-SHA256 + Fernet 解密 Controller 交付后，核对预配置
  Controller origin、issuer、Workspace、部署和 generation。只持久化密文及安装私钥；
  目录 `0700`，私钥/密文/绑定/策略文件 `0600`，原子写入并 fsync 文件和目录。
- 已有安装目标不能通过修改 URL/Workspace/计量模式重新绑定。私钥丢失不自动新建身份；
  输入文件拒绝符号链接、非普通文件、错误所有者、宽松权限及超限内容。
- machine Client Credentials 只发往固定 issuer；HTTP 仅允许显式 development 的 loopback。
  禁止跟随重定向，不采用进程代理环境；机器 access token 内存缓存最多 15 秒。
- hello 和 control 均核对部署、代次及协议。只有本地已支持的 `observe` 策略可回报 applied；
  poll 返回的命令不执行、不回报 accepted 或终态。源消息按服务端 JSON Schema 检查后上传，
  拒绝会触发外部获取的 schema 引用。
- ACK 丢失重传同一消息；只有匹配消息身份的 accepted/duplicate 才推进 outbox。
  429/503 留存未确认数据，隔离消息显示 partial；单项目采集错误不阻塞其他项目上传。

所有者通过 Controller 的强化认证轮换接口取得新的加密交付，将 JSON 存到 `0600` 文件：

```bash
zf controller accept-credentials --workspace-config /srv/zaofu-connector/zf.yaml --credentials-file /private/rotation.json
```

先验证新凭据能通过当前服务器 hello，再原子替换本机密文。部署/generation 不变，已有
outbox、stream、来源起点和消息不变；过时交付不能回滚为已被 Controller 撤销的旧身份。
本命令消费已获准的轮换结果，不持有用户浏览器 token 或 IAM 管理权限。

## 验证与下一批边界

`tests/test_controller_workspace.py` 通过实际 CLI 及 wire fixture 验证配置、秘密边界、注册
丢响应、跨目标拒绝、空心跳、多项目计量、策略拒绝/回退、凭据轮换、源故障隔离和重传。
既有 observation/native-usage 测试覆盖单机默认无依赖、原始计数和期初基线回归。

隔离联合检查使用真实 PostgreSQL、Keycloak 与 Controller HTTP。脚本的 owner/session 为
隔离数据库 fixture，不代表邮箱、BFF 浏览器和审核流程的上线资格。默认使用确定性计量
fixture；`--codex` 明确授权一次本机只读订阅调用，通过既有 Codex backend、原生 usage
reader、EventWriter 和 Connector 链路核对计数。不会读取其他原生会话或将订阅 tokens
折算成实际账单。示例：

```bash
uv run --extra dev --extra web --extra controller --with /path/to/zaofu-controller \
  python scripts/check-controller-workspace.py --controller-root /path/to/zaofu-controller --codex
```

脚本用临时 Docker Compose 创建自己的服务、端口和凭据，结束时销毁自己的容器/卷及
`/tmp/zf-controller-workspace-*` 状态。不会访问现有共享 Realm/数据库；Provider 自己产生
的该次原生日志沿 Provider 规则保留。检查结果记录实际 Controller commit、计数与覆盖范围。

Z01–Z04 仍需完成：云端受信启动/完整 managed 接管，Gateway 与本地认证，各 Workflow/
Kanban/Channel/helper 的执行准入与预算/名额，受控命令、活动上报、导出/退出，以及撤销
后的有限补报、隔离处置、水位恢复和保留策略。当前项目被移除后停止上传其队列并保留
原始数据，未把“停止上传”冒充最终回执已结清；不会自动处理旧来源/恢复档案。

本批设计输入为本地 203/204 文档，SHA-256 分别为
`1c9521aeff255aa6051ed860e545b807a58c37f43f8ba4fe5036d11a930b33a3`、
`e07e8336a85a5d68ad4672de74cb72ea91ae83f44e698cbe779c36e26e5bb727`。

## 本批验证结果（2026-11-10）

最终聚焦测试 68 passed；136 文件影响闭包 2,165 passed / 8 skipped；串行锁测试 10 passed、
premerge sentinel 53 passed、flow mock E2E 10 passed。闭包超过工具的 80 文件阈值，
所以按 AGENTS 保留 `plan.selected_tests` 精确目标，未扩大为全仓 pytest；自动门禁部分
通过 `dev-verify.py run --base dev --skip-tier deterministic` 继续运行，未省略闭包测试。

真实联合检查使用 Controller `bb06981`、独立 Compose Keycloak/PostgreSQL 和真实 HTTP。
一次本机 Codex 订阅调用：input 14,936（其中 cache read 12,160）、output 8、total 14,944，
上传的 fresh input 为 2,776，重复同步不重复计量，`billed_cost_usd=null`。该次原生 usage
经既有 reader/factory 和 EventWriter 进入账本；Session/Operation 的另一项目为确定性
fixture。此结果证明部署 Connector 与该计量来源，不替代所有入口的完整 Workflow 验收。
具体命令与原生会话标识见 Z01a 记录。
