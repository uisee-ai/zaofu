# Delivery / Loop / Trace / Agents 加载与指标对账

> 状态: active
> 授权: 用户要求分析并优化上述四个页面，完成后使用 8001/8002 对比速度及 metrics 偏差。继续 wip/codex-20261026-inbox-workflow，撤销 52e4a325 的冻结交付标记。

沿用 zf-backlog-batch-closeout：实现、验证、本地提交与任务归档，不 merge/push。docs/design 与 backlogs 保持 ignored。8001 不重启、不修改；8002 运行当前 worktree。事件、状态及 sidecar 权威来源不变。

- 分离项目解析、数据读取、派生计算、前端请求/渲染成本 -> verify: 两项目 cProfile 与真实 Docker Playwright 请求时序。
- 对已证实的重复扫描和不必要构建作最小修复 -> verify: 冷进程、已有 SQL、重复访问及来源变化的回归，不删除指标、不以无标识旧数据冒充实时。
- Metrics 对账 -> verify: 8001/8002 实际 API 与 UI 的业务字段结构深比较；年龄/时间、并发来源变化及既有基线故障单独记录，必要时固定输入与原实现对账。
- 数据量/分页/Run 归属 -> verify: Trace cursor、Loop stage/run/task/attempt、Delivery task/run/graph/ship、Agents 状态/费用/context/计数均保持语义。
- 检查闭包 -> verify: 相关 backend/API、frontend build/typecheck/unit、Docker Playwright、必要共享合同/sentinel/mock E2E；精确报告执行层及剩余限制。

证据目录 `/tmp/zf-monitoring-pages-20261026T1710/`。先取样再确定具体修改。

## 实现与验证记录

- EventLog 普通记录一次 JSON 解码；freshness 倒序按需匹配；graph health 一次提取最近事实，语义保持。
- Trace 当前水位使用事务维护的 SQLite 汇总，历史 cursor 保留快照回放；缺表/损坏时回退。
- Delivery features、Agents 角色/信号持久复用，精确校验事件分段、TaskStore、配置与相关文件；修复内存事件缓存归档/替换/decoder 校验，失败不持久化。
- Agents agents/live 独立更新，慢接口不阻塞已返回 worker。
- 真实 Docker 配对 32 页：重复访问 Cargo/loshu 的 Trace 1.698/1.938s、Delivery 2.361/1.778s、Loop 1.607/1.653s、Agents 完整 14.905/12.296s。8001 Agents 对照 55.981/56.653s。
- 最终代码重启 8002 后再测 8 页：Trace 1.988/1.963s、Loop 2.671/1.722s、Delivery 2.305/5.335s、Agents 完整 13.082/17.986s，均无 pageerror。首次汇总升级 Trace 仍需 12–15s、Delivery 入口未缓存约 11–13s，明确保留该限制。
- 52 对浏览器响应逐字段深比较，业务字段零差异；明确列出 age、heartbeat 秒数文本和缓存生成元数据差异。两项目两轮 Agents 卡与 Fleet 文本完全一致。
- 固定输入原代码对照：Cargo/loshu 4,188/12,550 事件；3/56 trace 分组、91/39 图节点、1/38 未终结任务 freshness、角色及信号完全一致。并非仅比较共享缓存结果。

已通过：`pytest` EventLog/Graph/freshness/事件缓存 76 项；Web Trace/Loop/Delivery/Workbench 90 项；SQLite/事件/metrics 67 项；最后缓存一致性 24 项、缺表回退及 Trace 路由 34 项（套件有重叠，不相加成唯一测试数）。frontend build/typecheck/unit 通过；Docker Playwright 原有 12 项通过，新增 Agents 延迟 API 测试修正缺失 per_role 的 fixture 后 1 项通过。51 sentinel + 10 flow mock E2E 通过。

`dev-verify plan/run --base dev --tests tests/test_monitoring_read_paths.py` 映射累计 647 文件；按 AGENTS 不因累计分支文件数触发 full pytest，使用 `--skip-tier deterministic --skip-tier serial`，以实际 focused 闭包替代，其他 automatic tier 均通过。receipt 在证据目录。无 full pytest / real provider E2E 声明。

设计更新：本地 ignored `docs/design/186-inbox-and-task-board-human-centered-design.md` 第 20 节、`188-dashboard-workflow-multi-run-sqlite-review.md` 第 9 节。docs/design 不提交。没有新增无授权候选或合并到 dev。

最终 API 对账：30 对请求全部成功，来源前后无变化；涵盖 Delivery 四视图、Loop、Trace 列表/两条详情及 spans、Agents/Live/Cockpit/Cost。175 个时钟派生差异、6 个 projection_cache 元数据差异逐路径解释，0 个未解释业务差异。报告 `api-parity-classified.json`，完整响应 `api-parity.json`。8002 PID 2099107 保持运行，8001 未重启。
