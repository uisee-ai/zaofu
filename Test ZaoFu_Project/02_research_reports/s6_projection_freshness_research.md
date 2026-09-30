# ZaoFu Web read_model/events freshness 契约调研

> 文档定位（2026-09-30）：历史调研，正文“当前/建议/尚未实现”仅适用于各段所记时点，不是最新执行队列。现行方案见 [ZaoFu_TestKit.md](../03_plans_and_steps/ZaoFu_TestKit.md)，执行仍按既有暂停/跳过边界；本次不重跑或改写历史结果。
>
> 当前对齐入口：`tests/conformance/test_zaofu_testkit_s6_projection_contract.py`。已有 freshness 检测入口；投影缺陷、环境错误、报告缺证须分别归因。

调研范围：当前仓库 `src/zf/web`、`src/zf/web/projections/read_model.py` 及相关
`tests/`。源码和测试是本项目行为的第一方来源；本次没有发现需要补充的外部
规范事实，因此没有引用二手资料或运行时修改仓库。

## Confirmed（源码/测试直接证明）

1. **普通读取允许 active tail behind。** `projection_status()` 将 active
   segment 的普通追加视为布局仍然一致：状态保持 `ready`，同时
   `tail_behind=True`；只有 archive/segment rotation 改变布局时才标记
   `stale`（`src/zf/web/projections/read_model.py:278-298`）。
   `ensure_requested()` 对 `ready` 投影直接返回上一次的 projection，发现
   `tail_behind` 时仅调用 `request_catch_up()`，请求线程不等待后台 rebuild
   （`src/zf/web/projections/read_model.py:342-360`）。这就是普通读取允许
   tail behind 的明确契约，而不是隐含行为。

2. **普通 `events_page` 返回 last-known 水位并声明滞后。** 查询把结果限制在
   `selected_seq=projected_seq`（`seq <= selected_seq`），响应返回
   `current_seq`、`projection_state`、`projection_lag`、`tail_behind` 和
   `source=read_model.sqlite`（`src/zf/web/projections/read_model.py:598-626`）。
   因此返回体与选择的水位绑定：追加事件不能在同一响应中混入尚未索引的记录。
   `tests/test_event_read_model.py:289-312` 验证追加后普通读取只看到旧事件、
   `current_seq==1`，并调度 catch-up；`tests/test_event_read_model.py:328-346`
   进一步验证即便 catch-up 被替换成同步 rebuild，当前响应仍保持旧水位。

3. **`require_fresh=True` 是同步屏障。** `_ensure_for_read()` 在投影非
   `ready` 或 `tail_behind` 时调用 `_rebuild_single_flight()`，完成后重新读取
   status（`src/zf/web/projections/read_model.py:363-378`）。single-flight
   让一个 DB 同时最多一个 rebuild，其它调用等待完成（最长 15 秒），以保留
   read-your-writes（`src/zf/web/projections/read_model.py:311-339`）。
   `tests/test_event_read_model.py:315-325` 验证 fresh page 同时返回新事件和
   `current_seq==2`；rotation 场景也由
   `tests/test_read_model_freshness_gate.py:56-80` 覆盖。

4. **不同 read_model API 的默认值并不统一。** `events_page` 默认
   `require_fresh=False`（`src/zf/web/projections/read_model.py:555-568`）；
   `hydrate_events` 和 `hydrate_events_by_ref` 默认 `True`
   （`src/zf/web/projections/read_model.py:831-847, 912-937`）；
   `agent_session_history` 默认 `False`
   （`src/zf/web/projections/read_model.py:1002-1024`）；
   `channel_summary` 默认 `False`
   （`src/zf/web/projections/read_model.py:1200-1218`）。调用者必须显式选择
   语义，不能仅凭“read_model API”名称推断普通读取是否等待。

5. **Web events 路由也有两套契约。** 项目作用域
   `GET /api/projects/{project_id}/events` 暴露 `require_fresh` 查询参数，默认
   `False`，并原样传到 `_events_page()`（`src/zf/web/server.py:2008-2040`）。
   旧的根路由 `GET /api/events` 没有 `require_fresh` 参数，始终使用普通读取
   （`src/zf/web/server.py:3513-3535`；包装函数默认同样是 `False`，
   `src/zf/web/projections/events.py:217-247`）。

   S6 conformance 已把根路由普通读取与 fresh read_model 调用写成回归：根路由在
   tail behind 时返回空 bounded page 与 `tail_behind=True/current_seq`，随后 fresh
   函数调用收敛到新事件（`tests/conformance/test_zaofu_testkit_s6_projection_contract.py:16-57`）。

6. **后台追赶是节流且可合并的。** `request_catch_up()` 按 DB 单飞线程和
   最小间隔调度（`src/zf/web/projections/read_model.py:381-400`）。已有并发
   测试使用 `threading.Barrier`、`Event` 和显式 join，验证 6 个普通请求只触发
   一个 rebuild、全部先拿到 `tail_behind=True`，最终状态收敛
   （`tests/test_read_model_single_flight.py:25-69`）。

7. **部分投影明确选择 stale-with-lag。** Workflow graph 读取缓存水位；小范围
   lag 时直接返回缓存并标记 `stale=True`，后台刷新
   （`src/zf/web/projections/workflow_graph.py:444-470`）。Loop view 的测试
   则明确要求 stale read model 不应阻塞事件真相读取
   （`tests/test_web_measure_loop_routes.py:287-311`）。这说明“普通读取允许
   tail behind”是按投影契约设计的，而非所有页面都必须 fresh。

## Inferred（由实现组合推断，尚未成为统一显式规范）

1. **同步屏障的可验证定义**应是：调用 fresh API 返回后，响应的
   `tail_behind=False`、`projection_state=ready`，且 `current_seq` 至少覆盖
   调用前观测到的事件序号；随后以该水位查询事件，不能要求响应覆盖屏障之后
   新追加的事件。这个定义直接对应 `_ensure_for_read` 的“rebuild 后重新取
   status”和 `events_page` 的 `seq <= selected_seq` 绑定，但仓库没有单独的
   API-level barrier 规范文件。

2. **普通读取的客户端应把水位当作一致性 token。** 响应的 `current_seq` /
   `projected_seq` 是可用于下一次 cursor 或 fresh follow-up 的水位；
   `tail_behind` 是“可能漏掉 active tail”的显式信号。该推断来自
   `events_page` 响应字段和 channel/agent 测试，而非客户端类型定义中的统一
   接口声明。

3. **`hydrate_events*` 的默认 fresh 可能是性能敏感路径上的隐性阻塞。** 两个
   hydrate 函数默认 `True`，但 workflow graph 等调用点显式传 `False` 并以
   `max_seq` 固定水位（`src/zf/web/projections/workflow_graph.py:502-511,
   733-739`）。因此新调用点应同时审查默认值、是否需要 bounded `max_seq`，以及
   是否能接受 stale-with-lag。

## Uncovered / 缺口

1. **项目路由的 freshness 断言仍不完整。**
   `tests/conformance/test_zaofu_testkit_s6_projection_contract.py:16-57` 已覆盖根
   `/api/events` 的 bounded-stale 字段，并直接覆盖 `read_model.events_page` 的
   fresh 收敛；`tests/test_web_server.py:1635-1644` 也会请求项目作用域
   `require_fresh=true`，但只断言 `items` 非空，没有断言 `current_seq`、
   `tail_behind=False`、`projection_lag=0` 等完整响应契约。因此项目路由参数
   转发和 freshness 元数据仍缺强断言。

2. **根 `/api/events` 无 fresh follow-up 入口。** 该路由签名没有
   `require_fresh`（`src/zf/web/server.py:3513-3523`），所以客户端只能依赖
   后台追赶或转用项目作用域路由；是否保留这一兼容差异没有测试/文档契约说明。

3. **已有 Web 测试使用 sleep 轮询后台追赶。**
   `tests/test_web_server.py:2595-2619` 为找到新事件反复请求并
   `time.sleep(0.05)`。这验证了最终可见性，却没有验证无等待的同步屏障，也会把
   调度延迟误认为产品契约。应增加 fresh 参数测试，保留后台异步行为测试时用
   明确的线程/事件信号替代 sleep。

4. **`projection_status()` 的默认 `count_source=False` 不报告真实 source lag。**
   此时 `source_seq` 取 projected watermark，`projection_lag` 在 tail behind
   时为 `None`（`src/zf/web/projections/read_model.py:262-288`）。调用
   `projection_status(count_source=True)` 才会扫描真实 source cursor；但 Web
   `_event_projection_status()` 未把 `tail_behind` 字段转发到响应
   （`src/zf/web/server.py:4544-4558`）。这使某些健康/状态 API 只能看到
   `projection_lag=null`，不能直接表达 active-tail 的数值滞后；S6 conformance
   已明确接受 `projection_lag` 为 `None` 或 `1`，把 `tail_behind + current_seq`
   作为 bounded read 的稳定信号（`tests/conformance/test_zaofu_testkit_s6_projection_contract.py:37-45`）。

5. **并发 fresh 超时后的语义未被测试固定。** single-flight joiner 最多等待
   15 秒后继续，但 `_ensure_for_read` 不返回“等待超时”标志；若 owner rebuild
   失败，调用方可能继续读旧投影或 fallback。现有并发测试只验证正常释放和最终
   收敛（`tests/test_read_model_single_flight.py:31-69`），未覆盖超时/失败。

6. **projection 异常 fallback 会丢失 freshness 元数据。** `_events_page()` 捕获
   read_model 异常后扫描 EventLog；fallback 响应只有 items、cursor、current_seq、
   limit，没有 `projection_state`、`projection_lag`、`tail_behind` 或 source
   字段（`src/zf/web/projections/events.py:217-279`）。因此同一事件列表 API 在
   正常索引与异常 fallback 两条路径的响应契约不同，且没有发现对应 API 回归测试。

7. **fresh 检查存在移动水位窗口，未定义“追加恰在屏障期间”的结果。**
   `_ensure_for_read(require_fresh=True)` 只在一次 status 检查后 rebuild，然后
   进入 SQL 查询；若事件在这次检查/重建之后、查询之前追加，响应仍可能只到旧的
   `projected_seq`。现有 watermark 测试通过 monkeypatch 模拟 catch-up race，
   没有真实并发追加覆盖（`tests/test_event_read_model.py:328-347`）。Loop
   projection 有 before/after fingerprint 重试逻辑可作对照，但 events/read_model
   API 没有等价的稳定水位重试契约。

8. **事件详情不是 fresh/read-model 屏障。** `hydrate_event_by_id()` 直接查索引，
   不调用 `_ensure_for_read`（`src/zf/web/projections/read_model.py:756-790`）；
   Web 详情路由索引未命中才扫描 EventLog fallback
   （`src/zf/web/server.py:4568-4603`）。因此详情响应没有水位或
   `tail_behind` 字段，也没有 tail/rotation 期间的测试；不能把详情 200 响应当作
   投影已 fresh 的证明。

## 无 sleep 的同步屏障测试设计

推荐新增一个 API-level deterministic test（可放在现有 Web server 测试附近）：

1. 写入事件 `evt-a`，显式 `read_model.rebuild(state_dir)` 建立基线；再追加
   `evt-b`。这一步只操作 append-only fixture，不需要时间等待。
2. 调用普通 `GET /api/projects/{id}/events?limit=10`，断言响应只含 `evt-a`、
   `current_seq==1`、`tail_behind is True`，并 monkeypatch
   `request_catch_up` 为记录函数，验证请求没有同步等待。
3. 立即调用
   `GET /api/projects/{id}/events?limit=10&require_fresh=true`，把它作为同步
   barrier：断言响应含 `evt-a,evt-b`、`current_seq==2`、
   `tail_behind is False`、`projection_state=="ready"`。这是现有函数级
   `tests/test_event_read_model.py:315-325` 的路由闭环版本。
4. 为并发屏障再加一个纯函数级测试：用 `threading.Barrier(N)` 同时进入
   `events_page(require_fresh=True)`，monkeypatch `rebuild` 以 `Event` 通知开始、
   用另一个 `Event` 显式释放；线程全部 `join(timeout=...)` 后断言 rebuild 次数为
   1、所有响应 `current_seq==2`。整个测试依赖事件信号和 join，不使用
   `sleep` 或“重试直到出现”。
5. 若要验证“响应体绑定水位”，在 fresh rebuild 期间安排第二个追加只在
   `rebuild_started` 信号之后执行；断言该响应最多到 barrier 前水位，下一次
   fresh 请求才看到第二个追加。这样可区分“屏障完成前的 source”与“响应查询时
   的 selected watermark”。

该设计同时覆盖普通读取允许滞后、fresh 的 read-your-writes 屏障、single-flight
   并发以及事件体与水位绑定，且没有依赖调度时间。

## 现有契约与建议测试矩阵

| 接口/调用 | 默认 | tail behind 时行为 | 已有直接测试 | 主要缺口 |
|---|---:|---|---|---|
| `read_model.events_page` | `False` | 返回 last-known，调度 catch-up | `test_event_read_model.py:289-346` | Web project route 未覆盖 |
| `GET /api/projects/{id}/events` | `False` | 同上；可 query `require_fresh` | `test_web_server.py:1635-1644`（仅 items） | 增加完整无 sleep API barrier |
| `GET /api/events` | 无参数 | 永远普通读取 | `tests/conformance/test_zaofu_testkit_s6_projection_contract.py:16-45`、`test_web_server.py:2586-2593` | 是否提供 fresh 能力未定 |
| `hydrate_events*` | `True` | 调用前同步 rebuild | `test_event_read_model.py:273-286`、ref 测试 | 超时/失败和默认性能契约 |
| `agent_session_history` | `False` | 返回旧历史并给 tail 字段 | `test_event_read_model.py:349-411` | 路由层 fresh 参数 |
| `channel_summary` | `False` | 返回缓存摘要并给 tail 字段 | `test_channel_projection.py:1499-1550` | API 层 barrier |
| `_rebuild_single_flight` | N/A | owner rebuild，joiner 等待 | `test_read_model_single_flight.py:25-69` | timeout/failure |
