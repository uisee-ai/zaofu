# 实施 211 的本地账号与首次使用流程

> 状态: done

实现提交为 `334e20ec feat: authenticate standalone dashboard access with a local account`。Controller 配套实现为 `ced66a8 feat: support protected logout from the personal workbench`。验收依据为 [设计 211](../../docs/design/211-standalone-login-and-cloud-workbench.md)。

| 验收项 | 已执行的检查和结果 |
| --- | --- |
| AC-01、06 安装与访问 | 用独立 workspace home 运行 `install.sh --mode standalone --skip-deps`，探测和普通地址登录成功。CLI 显式监听、回环 HTTP 和本机 HTTPS 代理均通过。HTTPS 会话带 Secure、HttpOnly。实际 Chromium 从已存在项目的 Traces 深链接登录、跳过向导后仍进入原项目和 Traces，期间项目创建请求为零。 |
| AC-02 写权限 | 已登录浏览器权限对应的受控 Kanban 操作把测试任务从 backlog 移到 in_progress 再移到 done。真实 Herdr 0.9.5 的 control WebSocket 接收输入、写出预期文件、返回终端帧，退出后连接关闭。模型端使用确定性夹具。只读服务拒绝项目与向导写入。 |
| AC-03、04 会话 | 两个浏览器验证退出与改密的撤销范围。重复安装后旧密码被拒、新密码可用。匿名探针只返回就绪标志；未授权读取、跨站写入与错误终端票据均被拒。外部接管绑定出现后，原本已启动的本地服务拒绝浏览器及 API 凭据。 |
| AC-05 旧安装 | 独立仿旧安装验证 Token 文件、显式 passcode/trusted/token、显式账号迁移和独立 Bearer API Token。另以真实 Chromium 跑原有 Token 向导测试，保留 Access 步且不创建项目。 |
| AC-07 仅上报 | 使用临时 PostgreSQL 17、Keycloak 26.8.2 和真实 Connector 注册节点。注册前改的密码继续有效。停掉 Controller 后用该密码登录并成功保存向导；恢复服务后观察到两个项目、两个 Session、两个操作。确定性用量重放无重复，轮换和撤销机器凭据的检查通过。 |
| AC-08 准入、归属与撤销 | 两个合成邮箱经真实 Keycloak 公开注册、接收验证邮件、设置密码，登录 Controller 后仅有申请权限。管理员审批，两名用户分别升级会话并看见自己排队中的空间；跨用户读取返回 404。此审批测试环境的管理员 ACR 为 1。常规 ACR 2 的准入条件另由 Controller 回归覆盖。另一组就绪实例经真实 Gateway SSO 访问；原工作台会话在准入撤销后返回 401，直连受管 Pod 提交本地凭据返回 403。 |
| AC-09 登录页 | 同一 Docker Chromium 在 1280 和 360 px 视口下分别打开本地和真实 Keycloak 登录页。两端卡片宽度分别为 432 和 328 px，输入框均高 44 px。四张无凭据截图保存于工作树的 `reports/spec-211-login/`。 |
| AC-10 本地向导 | Docker Playwright 覆盖初次加载失败后重试、向导读取失败后重试、中途刷新、写入时失去会话、重登恢复、旧第 3 步映射为 Ready、换浏览器和改密。另检查已跳过状态、只读不能改变进度及重复安装持久化。 |
| AC-11 云端向导 | 两个专用用户通过 Gateway 进入 claw02 隔离命名空间的两个真实 Pod，各用单独的 PVC。A 在官方 Dashboard 选择 Codex 并完成向导；缩容到零再拉起，Pod UID 变化、PVC UID 相同，完成标记和选择仍在。B 继续显示首次向导。A 从工作台退出后原 Gateway 会话失效。 |

验收使用两组独立数据。公开注册与审批检查到空间排队；已获准工作台使用真实 OIDC、Gateway 和预置路由的 Kubernetes 实例。没有把两项结果合并为从新注册到生产自动调度的一次端到端运行。临时命名空间和容器已清理。

可复核命令包括 `pytest -q --no-cov tests/test_cli_web_access.py tests/test_start_webkanban_proxy.py tests/test_install_script.py tests/test_onboarding_state.py`（53 项），`npm --prefix web test`、`npm --prefix web run build` 和 `bash scripts/dev-premerge-gate.sh`。Docker Chromium 运行 `web/tests/standalone-login.spec.ts` 时需指定隔离的新安装、`ZF_WEB_BASE_URL` 和 `ZF_E2E_FRESH_ACCOUNT=1`。Controller 的认证、注册、Gateway、工作台和申请测试在真实 PostgreSQL 上通过 47 项。构建的 Python wheel 包含离线登录页。


最终的 `dev-verify` 确定性并行阶段为 16,077 项通过、20 项跳过、8 项失败。单独的串行阶段 10 项通过。8 个失败节点均在改动前的干净 `8fff1bdd` checkout 中复现，涉及公开导出脚本、计划建议、设计文本、飞书卡片和提示词来源检查。完整自动验证状态为有基线失败。登录专项、Docker 浏览器和前端检查分别记录。
