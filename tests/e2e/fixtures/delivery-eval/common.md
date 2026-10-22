# 隔离小产品交付共同合同

从空产品目录实现可操作的本地 Web 产品，全部业务只使用人工构造的数据，不联网、不调用真实外部服务、不发送消息。这里是独立小产品，不代表完整 cargo 的交付。

技术边界：Node.js 24、内置 HTTP/文件 API、原生浏览器；不安装第三方依赖。app/package.json 与 package-lock.json 必须存在，`npm ci --prefix app`、`npm --prefix app test`、`npm --prefix app run build` 均可从干净 worktree 运行。build 生成可独立启动的 app/dist/server.mjs 及所需静态资产；启动 `node app/dist/server.mjs`，读取 HOST、PORT、DATA_DIR，默认 HOST=0.0.0.0。不要把编译或空脚本退出0当作业务验收。

所有端口由环境配置，测试不得占用8001。GET /healthz 返回200和JSON {"ok":true}。根路径提供真实中文操作页面。错误响应是JSON且有error字段；成功响应为JSON。GET /api/state 返回完整的当前业务对象（不返回秘密）。API读写使用 X-Role，测试身份 writer 可写、reader 只读；缺失/未知身份写入403。页面有 data-testid=role 的选择框，选项值writer/reader，并把选择传给API；只有前端禁用而服务端放行不合格。

所有写操作必须携带 Idempotency-Key。同key同语义请求返回原结果且不重复变更；同key不同语义请求409。权限应在幂等结果返回前检查，reader不能借重放writer的key写入或绕过拒绝。非法JSON/类型/缺字段返回400，不得崩溃。写入原子且重启后保持业务状态与幂等记录；失败不改变已提交状态。并发冲突按业务规定处理。

为可复现地检查持久化失败，仅当进程环境 EVAL_FAULTS=1 时允许 X-Eval-Fault: before-commit 注入一次请求的提交前失败，返回503；该请求不改变任何业务状态，也不得消耗幂等key，移除故障后可用相同key成功重试。默认未开启该环境时此头不能产生故障。不得据此改变其他业务语义。

提供本片行为测试、失败前后的真实状态断言与独立最终验收。TDD RED需为业务断言失败，不是缺依赖。使用 Docker mcp/playwright:latest、host networking 执行浏览器，不能安装host浏览器。外部评估器将从干净提交构建并用PRD中的公开接口检查产品；不修改仓库外评估器，不搜索其代码/答案。页面应有 data-testid=status 的状态提示，成功和失败后更新，不能显示假数据。HTML对用户数据安全转义。

只允许修改 app/**、scripts/**、docs/product/** 和规划所需 docs/plans/**、artifacts/**。业务需要的架构、切片和任务数量由Planner决定；不要修改PRD、zf.yaml、skills、AGENTS.md或外部状态。最终装配拥有共享入口；独立Critic、Verify和所有mandatory验收保持有效。
