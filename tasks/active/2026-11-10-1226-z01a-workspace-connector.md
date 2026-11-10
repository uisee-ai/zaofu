# Z01a 部署级观测 Connector 与安全注册

> 状态: active

来源：用户批准从 `dev` 新 worktree 执行 203/204 的下一步；父任务 [Z01](2026-11-09-1224-z01-cloud-controller.md)，关联 [Z03](2026-11-09-1224-z03-cloud-controller.md)。
基线 ZaoFu `489d92b3`，Controller `bb06981`。本项交付第一批可独立运行的部署接入，
不将注册成功等同于完整 managed；Z02/Z04 与父任务其余合同继续保留。

## 范围与验收

1. 部署级 `zf.yaml` 与真实 `zf controller` CLI -> verify: 零 Project 可生成安装公钥、注册、确认观测策略并持续心跳，不创建虚构 Project。
2. 公钥绑定交付、私有落盘及轮换 -> verify: 错误 issuer/Controller/Workspace/部署被拒绝，网络响应丢失重试复用身份；私钥缺失不重建；轮换不更改部署、流或用量基线，旧凭据不能回滚。
3. 多 Project 只读采集 -> verify: 本地显式同意与远端项目范围同时满足，每个 Project 重读自身 `zf.yaml` 并尊重 `project.state_dir`；原配置、事件和业务状态不修改，凭据不上报/不下放项目。
4. 可恢复的传输与故障隔离 -> verify: 原账本重放、ACK 丢失和 503 后不丢未确认消息、不重复计量；项目采集失败不阻塞其余项目；策略回退、未知范围和 managed 升级拒绝生效。
5. 实际联合验证 -> verify: 临时 Compose PostgreSQL/Keycloak 与 Controller HTTP 完成注册、零项目心跳、多项目采集、轮换和撤销；一次本机 ChatGPT 登录的 Codex 调用提供真实原生计数，费用未知不伪造账单。

实现、命令与边界见 [实施说明](../../docs/impl/203-controller-workspace-connector.md)。

## 验证记录

- `uv run --extra dev --extra web --extra controller pytest tests/test_controller_workspace.py tests/test_controller_observation.py tests/test_controller_native_usage.py -q --no-cov --maxfail=1` -> 68 passed。最终协议类型与外部 schema 引用拒绝也包含在此轮。
- `dev-verify.py plan --base dev` 选出 136 个受影响测试文件。CLI 注册导入导致超过工具的 80 文件阈值；遵循 AGENTS 的按影响闭包验证规则，实际对 `plan.selected_tests` 运行 `python -m pytest <selected_tests> -q --no-cov -m 'not serial and not perf and not host and not real_provider' -n 4 --dist loadfile`，预算 300 秒 -> 2,165 passed、8 skipped，64.03 秒。没有扩成全仓 pytest；此后协议边界加固仅重跑上述 68 项直接闭包。
- `uv run --extra dev --extra web --extra controller python scripts/dev-verify.py run --base dev --tests tests/test_controller_workspace.py --tests tests/test_controller_observation.py --tests tests/test_controller_native_usage.py --workers 4 --skip-tier deterministic` -> 自动 deterministic 全仓扩展由上述精确闭包替代；serial 10 passed、sentinel 53 passed、flow mock E2E 10 passed。
- `uv run --extra dev --extra web --extra controller --with /home/min/workspace/public/zaofu-controller-203-204 python scripts/check-controller-workspace.py --controller-root /home/min/workspace/public/zaofu-controller-203-204 --codex` -> 独立 Compose PostgreSQL 17、Keycloak 26.8.2、真实 Controller HTTP 全链通过。Controller HEAD `bb0698175b649a35b7520a80463fe472db5e8627`。
- 真实 Codex 原生会话 `01a1c56f-0161-7222-a8ee-b01f84887bdd`，`accounting_mode=subscription`，模型 `gpt-6-astra`；input 14,936（含 cached 12,160）、output 8，total 14,944。Controller 对账 fresh input 2,776 + cache read 12,160 + output 8，不重复加缓存，实际费用保持 null。此真实用量使用原生 reader/factory 写入受控 EventWriter；另外一个项目的 Session/Operation 是确定性生命周期 fixture，不宣称真实完整 Workflow/Web 旅程已通过。
- 显式文件 `ruff check`、`git diff --check` 通过。临时 Compose 容器/卷、凭据与模拟项目已清理；本次 Provider 自己生成的原生日志保留，没有修改登录或 Provider 配置。

Controller 人类登录在本次检查中由隔离 owner/session fixture 提供。真实云端、CPA、邮箱及灾备资格按用户要求后续验证；额度耗尽阻止新执行仍属 Z02/Z04，本项不替它们关闭验收。
