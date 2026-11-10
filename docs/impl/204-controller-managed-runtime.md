# 204 ZaoFu managed 实例接线

承接 [接入计划](201-controller-integration-plan.md)、[部署 Connector](203-controller-workspace-connector.md)
及设计 203/204。本批实现 Z01–Z04 的 Runtime 责任；生产集群、CPA 产品适配、邮件认证、
备份/灾备与容量资格仍属于 Controller Q01。代码接通不等于生产环境已经上线。

## 模式和启动

| 模式 | 入口与权威 |
| --- | --- |
| standalone | 现有 `zf start` / `zf web`；未绑定安装时不请求 Controller，也不要求 controller extra |
| observed | 部署 Connector 采集已获本地同意和远端批准的 Project；不限制本地执行 |
| managed external | 安装所有者完成注册、排空并显式 takeover；保留完整本地 Kernel 和工作台，执行受平台权益限制 |
| managed cloud | Controller 注入机器身份与只读 `/etc/zaofu/zf.yaml`；`zf controller serve` 从零项目工作台启动 |

在 WorkspaceConfig 的 `workspace.controller` 增加以下配置。机器密钥不写入 YAML：

```yaml
management: managed
hosting: external
gateway_origin: https://workspace.example.com
mode: self_managed_cli
projects: [exact-local-project-id]
request_tokens: 32768
request_money: '0'
price_version: unknown
lease_seconds: 30
request_seconds: 300
receipt_retention_seconds: 604800
```

`request_*` 是每次 Provider 调用的申请上界，Controller 仍负责原子核对用户/Workspace
共享额度。`request_seconds` 不得超过平台预算策略；执行租约最长 300 秒，续期不延长
预算有效期。金额是十进制字符串，不能把未知消费当作 0。
个人 CLI/订阅内部的每个模型请求不受 Connector 逐 token 截断；在途用量可能超过预留。
真实回执按实际数量结算，平台依 overrun 策略冻结后续调用并保留差额责任，不能宣称
这等同于 Provider 账单的绝对硬上限。

external 的本地 `projects` 是同意范围；cloud 的实际范围来自 Controller 已应用策略，
且必须设置绝对 `project_root`，例如 `/workspace/projects`。云端 Project 和其 state dir
必须位于该私有目录中。新建 Project 后仍须加入远端策略，才可启动受管调用。

```bash
# observed 注册流程沿用 203；已有 watcher/native terminal 先停止。
zf controller takeover --workspace-config /srv/connector/zf.yaml
zf controller sync --workspace-config /srv/connector/zf.yaml
zf controller serve --workspace-config /srv/connector/zf.yaml --host 127.0.0.1 --port 8002

# 云端镜像：Controller 挂载只读配置、机器 Secret 与内部 TLS 证书。
zf controller serve --workspace-config /etc/zaofu/zf.yaml --host 0.0.0.0 --port 8443 \
  --ssl-keyfile /etc/zaofu/tls/tls.key --ssl-certfile /etc/zaofu/tls/tls.crt
```

云端 bootstrap 先用机器 Client Credentials 校验 `/internal/v1/me` 的部署/generation，再
封装保存密文并移除当前进程的原始 Secret。受保护的系统配置优先于环境指针。external
在 Workspace home 和安装目录保存一致的绑定；删除凭据、修改模式/目标、换 Workspace
或遗漏启动指针均不能自动变回 standalone。高代次云端重启保留旧来源身份，代传仍必须
取得 Controller 的交接授权。

这些检查约束正常产品入口。external 的 OS 所有者仍控制自己的主机；任意修改应用代码、
绕过产品直接调用个人 Provider、root 管理员行为不属于平台实际账单硬限额。云端的只读
应用、进程隔离、网络和密钥隔离必须由 Q01 的部署资格证明。

## 访问和执行

所有 managed HTTP、WebSocket 和导出下载都经过 ASGI 验证：固定 Gateway origin 的
Ed25519 JWKS，签名、25 秒有效期、Workspace、deployment/generation、HTTP 方法和路径。
随后机器向 Controller `/internal/v1/me/access/check` 核对当前人类 Session、归属、
auth/policy 版本及已发布路由；不能仅相信签名未过期。本地 trusted flag、passcode 和
Web action token 不替代此授权。变更请求及 WebSocket 还必须匹配 Gateway Origin。
长请求每 8 秒重验，撤销或无法证明当前权限时取消处理器并关闭连接。

| 真实调用入口 | 受管检查 |
| --- | --- |
| `zf start`、Workflow 新 Run、受控恢复 | 当前策略、outbox 容量、预算 readiness；readiness 不消耗额度，也不替代后续原子预留 |
| Kernel foreground watcher | 持有 Project 租约；Controller load 启动的子进程承接同一租约。managed 不允许无人监控的 `--no-watch` |
| Claude/Codex/Kimi headless | 每个 turn，包括 Kanban/Channel 和 Session 复用，分别取得 Agent 名额及预算预留 |
| Workflow stream-json / tmux TUI | 每次 dispatch、retry、compact 受管；沿原 transport 取消，读取原生 turn 边界 |
| Web native terminal | 创建前预留，复用沿用同一责任；终止时结算。既有无有效授信终端不能直接接管 |
| Evolution evaluator/optimizer、Autoresearch reflection | 辅助 Provider subprocess 同样预留、限制期限、可取消并产生用量回执 |

调用前先持久化 Connector obligation、预留本地回执容量，再取得共享 Agent 租约和预算。
记录 running 后才进入 Provider。网络故障不续长已有许可；本地使用单调时钟限制剩余
有效期，已知撤权立即取消。撤销/超时后缺用量的调用仍保留未知财务责任。

Kernel、TaskStore、SessionStore、Gate 和工作流产物合同不变。Connector SQLite 只保存
授信、命令、导出和补报责任；它不是第二个业务状态机。

## 用量、成本和恢复

- Headless 复用读取同一原生 Session 的前后累计差；Codex 的单条 `last` 计数不能冒充
  完整工具调用 turn。只有可证新的 Session 才从 0 起算，未知/断流保留 missing。
- managed 调用产生 `controller.execution.started/finished` 与 `controller.usage.receipt`。
  后者是上传回执，不再给本地 CostTracker 追加第二笔成本；已有外层 Session 汇总、
  原生 disk reader 和 terminal cost projection 避免重复上传相同调用。
- 订阅保留实际 tokens、cache/reasoning 包含关系和原生 Session ID，金额未知。
  API/enterprise 只有完整价格条件、确定模型和与平台相同的 catalog version 才上报
  `estimated_cost_usd`。默认模型未知、价格不匹配或条件不完整时保留未定价；估算不等于
  Provider 实际账单。CPA 产品自身的计量/凭证撤销能力仍须 Q01 核验。
- receipt 先写 Connector ledger 再追加 EventWriter。写入/上传失败可重放同一计量 ID；
  执行完成、名额释放和财务结算是不同状态。丢响应重试使用相同 request ID/结果摘要。
- 准入前同时预留记录数/字节余量；满队列停止新增调用。ACK 后保留完整回执，Controller
  恢复水位下降时重发原 ID；仅已处理水位之前、超过保留期的 ACK/disposed body 可清理。
  已清理 body 无法覆盖的新恢复缺口显式失败，不能伪造 ACK。
- 按处置协议消费 retry/close/corrected，隔离记录不会假装成功。保留旧 deployment/source
  身份，新的 uploader 不能重贴归属。无法证明已停止的旧执行保留 unknown，须对账处理。
- managed Project 移出策略后，新调用拒绝，已发生用量继续补报；旧元数据若被隔离，
  需要 Controller 明确处置。observed 移出则按本地同意边界停止上传。不要删除仍有未结算
  责任的 Project registry/config；解析不到原归属时会阻止补报/解绑。

`sync` 返回每个 outbox 的待发/隔离数量及 `unresolved_executions`。一次 sync 处理有限批次；
`synchronized` 表示本轮传输成功，不表示所有历史责任已结清。未知金额责任也不能自动退款。

## 受控命令与退出

命令核对 Workspace、deployment/generation、策略、有效期及精确 Project/Session/
Operation/Execution。执行前调用 `/control/commands/{id}/authorize` 强制重验人类 Session；
accepted ACK 不等于授权永久有效。命令经 `ControlledActionService` 进入专用 handler：

- `project.load/unload` 启停实际 `zf start/stop`；load 等待该进程的 watcher/loop 证据，
  unload 等待受管调用停止。未知进程或租约未确认不会回报成功。
- `session.stop` / `operation.cancel` 写入 Connector 准入 fence，由持有该原生执行的
  watchdog 取消；确认执行离开 running/preparing 后才完成。
- `session.resume` 打开原 Session 的下一轮准入，不凭空生成用户 prompt 或新 Session。
- 同一命令重复投递只重传终态；崩溃落在副作用之后而无完成证据时回报 unknown failure，
  不重放未知副作用。不会直接改写 Kernel 的任务终态。

导出使用已授权的项目集合，在无在途受管调用时生成有限大小 ZIP，逐文件哈希和文件集合
二次核对形成 checkpoint。排除平台/CPA/机器凭证、原生登录目录、环境文件和私钥，
拒绝符号链接父路径、硬链接、特殊文件及已知平台秘密。artifact 只通过受管下载路径交付，
核对当前 owner/generation/期限；过期清理本安装的 artifact。活跃修改会导致重试，不能
把可变目录包装成一致快照。

```bash
zf controller drain --workspace-config /srv/connector/zf.yaml
# 继续 sync，确认无在途执行、隔离回执和未结算责任；在 Controller 申请撤销。
zf controller sync --workspace-config /srv/connector/zf.yaml
zf controller unenroll --workspace-config /srv/connector/zf.yaml
```

`drain` 关闭新执行并走现有 stop 入口；`resume` 仅在平台仍授权时退出维护。
external 的最终 unenroll 要求本地排空，并用安装私钥签署随机挑战请求
`/enrollment/v1/release-status`。只有 Controller 确认机器已撤销、IdP 撤销完成、名额与
预算责任结清，才删除机器密文/解密私钥与 managed pin；保留项目、EventLog 和个人登录。
cloud 使用导出和平台删除，不支持本地解绑成为 standalone。

## 验证入口

本批直接测试：`tests/test_controller_managed.py`、`tests/test_controller_managed_paths.py`，
`tests/test_controller_managed_kernel.py` 以及原 workspace/observation/native 用量测试。涵盖真实 Web app、WS 撤权、headless/
stream/tmux/terminal/helper 入口、恢复、隔离、价格、导出、绑定与退出。

两仓库联合检查：

```bash
uv run --extra dev --extra web --extra controller --with /path/to/zaofu-controller \
  python scripts/check-controller-workspace.py --controller-root /path/to/zaofu-controller --managed --codex
```

使用临时 Compose PostgreSQL 17、Keycloak 26.8.2、真实 Controller HTTP，验证跨项目名额、
预算、真实 Codex 订阅回执、结算、额度耗尽和重传。owner/预算为隔离 fixture；不冒充
邮件注册或生产 Gateway/sandbox 资格。具体结果、测试命令和提交记录随 Z01–Z04 归档。

## 本批验证记录

- `uv run --extra dev --extra web --extra controller python scripts/dev-verify.py plan --base dev`
  判定覆盖 backend/config/Event/Store/orchestration/provider/Web API，自动扩大到 deterministic
  suite；新增直接测试文件也被规划器纳入。
- `... python scripts/dev-verify.py run --base dev --workers 4` 的 deterministic 层结果为
  **15,207 passed、20 skipped、7 failed**。其中 observed 移除项目后仍上传的问题已修复，
  后续直接回归通过；其余 6 个用例在干净分支基点上也失败，详见下表。
- 末轮 `pytest tests/test_controller_managed.py tests/test_controller_managed_paths.py
  tests/test_controller_managed_kernel.py -q --no-cov`：**37 passed**。相同 Workflow 在
  standalone/managed 下走同一 writer → verifier → `test.passed`，使用相同产物和 Gate
  证据；managed 的每次模拟 Provider 调用额外获得真实消费代码的授信与回执。
- 较广聚焦回归（managed、managed_paths、workspace、observation、native_usage、
  instruction_stack_contracts）：**109 passed**；新增 Kernel/Project/工作树归属与直接
  workspace/managed_paths 回归：**55 passed**。这些集合有重叠，不相加统计。
- serial：**10 passed**；`dev-verify run --base dev --workers 4 --skip-tier deterministic
  --skip-tier serial`：前端 typecheck/unit、sentinel **53 passed**、三流 mock E2E
  **10 passed**。另有变更 Python 文件 Ruff、`git diff --check` 通过。
- 两仓库实际 Compose 联调：零项目注册、两项目上报、共享名额、原子预算、额度耗尽、
  回执重传、轮换、排空、两份 IdP 凭据撤销及签名解绑均通过。真实人类登录为 fixture。
- 用户授权的本机 Codex 订阅调用经实际 headless backend/Controller HTTP 结算：
  **14,112 tokens** = input **14,104**（包含 cache **12,160**，fresh **1,944**）+ output **8**。
  原生模型为 `gpt-6-astra`；订阅实际金额未知，没有按 API 单价冒充账单。该测试还包含
  其他 fixture 消费，因此不能把整周期 14,352 tokens 当作这次原生调用。

| 基点遗留失败 | 复核结论 |
| --- | --- |
| `tests/test_export_public.py` 4 项 | 干净 checkout 缺少未跟踪的 `tools/export-public.sh` |
| `test_prompt_source_closure::test_five_workflow_prompt_is_standalone_in_fresh_worktree` | 干净 checkout 缺少该测试引用的本地 prompt 文件 |
| `test_feishu_channel_progress_card::test_confirm_click_is_wired_to_real_controlled_action` | 原 fixture 的简化 config 先触发缺少 observability；仅在内存补齐该 fixture 后，基点和当前代码均返回 `readiness_unassessed`，缺少 PRD readiness 证据 |

以上六项在当前分支单独复跑仍失败，未修改无关预期、跳过用例或声称全套全绿。
复现用临时基点 checkout 与隔离服务均清理；归档任务记录实现提交及分支。
此批交付代码与本地联合验证，Q01 的生产集群/TLS/网络/沙箱、邮件 MFA、真实 CPA、
备份恢复和容量验收仍待目标环境。未合并或推送。
