# Request / Run 工作台读投影与刷新

> 状态: active
> 用户已批准：针对新 Request / Run 进行读投影与刷新优化。基线 dev fec215b3。

## 范围

复用 read_model.sqlite 存储项目、Request / Run、Proposal 修订、历史游标隔离的完整图投影。
保留 canonical EventLog / TaskStore / artifact 与既有归属算法。事件、任务、配置、引用附件变化
必须失效；构建过程中变化不得发布混合代次。新工作台接入已有页面事件流，合并刷新，
保留低频校准、隐藏页暂停、历史冻结、请求取消与失败时保留上次成功结果。

补充范围（用户反馈 Workflow / Monitoring 刷新慢）：准备器复用现有增量索引定位 canonical operation 事件，
避免空闲项目全量重放争抢 API CPU；保持自动准备、恢复、配置 state_dir 与原 consumer 准入语义。

Monitoring 补充：移除 source fingerprint 对展开配置的重复深拷贝，保持相同 JSON 编码与失效语义。

## 验收

- 稳定源重复读取 -> verify: 第二次不读取详情、不遍历事件/任务、不重建图；重建进程后复用 SQLite。
- 多 Request / Run / 游标 -> verify: 结构、统计、Plan、状态与未缓存算法一致，隔离、回放语义不变。
- 事件、Task、引用附件变化与并发读取 -> verify: 缓存失效；读取竞态重试或 503；SQLite 故障回退 canonical。
- 浏览器突发事件与慢响应 -> verify: 同类请求不重叠、迟到响应不覆盖当前图；304 不重置图位置。
- 后台准备 -> verify: 索引与完整 reducer 一致、故障回退、并发源不消费；已结束 Request 不全量读取，新 Request 自动准备。
- 对比 8001 -> verify: 稳定源业务字段无差异，记录冷/热读取耗时、响应大小和校准请求数，不声称小样本 P95。

## 验证

定向 Python API / SDLC / SQLite 测试、frontend build/typecheck、Docker Playwright、
dev-verify 计划与对应自动层。文档：docs/impl/workflow-request-run-projections.md（本地忽略）。

## 已执行验证

- API/SDLC/SQLite/操作投影/mock E2E 闭包：112 passed（focused-tests.log）。
- Synthesis consumer / preparation / tick caller / intake mock E2E：76 passed；最终重复 startup 与自动准备闭包 10 passed。
- 最终 source-cache / Agents API / metric facts / SQLite / graph cache / preparation reader：50 passed；既有 Monitoring read-path + source 编码验证：16 passed。
- frontend build/typecheck 与全部 unit tests 通过；dev-verify receipt passed。最终 plan 基于固定 fec215b3，无 error/warning；本轮 dev 被其他会话推进，未覆盖其改动。
- planner 因影响测试数量提出 broad Python；按 AGENTS 的领域闭包规则，以以上 focused + mock E2E 替代全量 deterministic/serial tiers，未跑 full pytest。
- 最终 pre-merge sentinel：51 passed（premerge-last.log）。
- Docker Playwright：既有 Inbox/Workflow 13 passed；刷新竞态 4 passed；图/Plan 5 项最终均通过（干净 fixture 4 passed + 最后一项按 30 s 无事件校准更新后重跑 1 passed）。
- 曾暴露 Plan 应用后仅等待校准的问题，已接回 onApplied 主动刷新；曾有 fixture 无容器高度和重复使用可变 fixture 的失败，已修复验证环境。没有把失败／skip 当通过。

## 实测与限制

证据目录：/tmp/zf-workflow-projection-20261027/。8001 基线最初为 fec215b3 进程；其后其他会话推进 dev 并重启服务，因此后期数据仅标为候选测量，不伪称同一基线 A/B。

Cargo / loshu-fms live 与 as_of=1 图，稳定源完整 JSON 比较一致（api-final.json）。
Monitoring summary / delivery / loop / trace 稳定源比较一致；Agents 差异为实时生成时间、age、含 age 的 reason 和源版本，未隐藏该差异，也未用页面可见项代替完整指标。

候选 API 热读样本（存在同机服务与后台重建竞争，不是 P95）：Cargo/loshu Delivery 28/51 ms、Loop 41/98 ms；Agents 2.28/1.63 s。
配置深拷贝剖析修复：Cargo warm 3.81 -> 1.95 s，fingerprint 2.59 -> 0.40 s（两次均含 profiler 开销）。
图条件请求仅检验小型 source receipt；响应体为 0，不再读取多 MB 图 JSON。

**首屏尚未全面提速**：真实 Docker 浏览器在安静轮次，Cargo Workflow 约 15.1 s、Agents 约 10.5 s；存在负载的轮次更慢。
Workflow 首屏仍经过项目目录／Request 列表与详情、完整图传输与布局；Agents 冷读仍可能全量解码窗口事件。
本批完成的是读投影和刷新开销优化，不把缓存命中 API 时间宣称为完整页面首屏时间，也不宣称所有 Monitoring 冷路径已解决。
下一轮应独立优化 Request 详情与列表读投影、workspace lifecycle 批量探测以及 Agents 冷窗口读取，并以完整页面 ready 而非单接口作为验收。
