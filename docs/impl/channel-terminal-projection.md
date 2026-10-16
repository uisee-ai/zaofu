# Channel 执行终态投影

Channel 的同一 `run_id` / `run_generation` 以 reply request 的终态为准。
Supervisor 已写入 `channel.agent.reply.failed` 时，即使 provider 没有写入
`agent.session.run.failed`，旧 `streaming` 也不能继续驱动 Working、Cancel
或成员的活跃状态。

`channel_projection` 在构造公开 session projection 时绑定当前 reply generation，
将已结束请求的 session 与活动 part 收敛到对应终态，保留历史工具结果，清除
`active_part_id`，并使用请求的结束时间。旧 generation 的 session 不参与新一轮
投影；新 generation 正常开始后仍可显示 streaming。这些操作不写事件或业务状态。
同一 generation 的 reply 一旦终结，迟到的 started/completed 不得重新激活它；
重试必须增加 generation。但保留既有 failed 判定路径：transport completed 后
结构化结果校验仍可写入 contract-invalid failure，并触发有界修复。
界面投影与 recovery fold 共用这条事件顺序规则，避免两者对是否结束产生分歧。
adapter 发布最终消息或处理迟到异常前重新检查请求，已结束请求不再发布答案、
语义 contract，也不被迟到异常覆盖成新的可重试失败。

Channel adapter 将 `run_generation` 传入统一 stream emitter，持久化事件与临时
SSE delta 均携带它。新代次重置旧 session 的 parts、结束时间和 provider 元数据；
旧代次事件不覆盖当前代次。历史无代次的 session/delta 按 generation 1 解释，
不会归入重试：升级前第 2 轮及后续重试的过程片段可能不再展示，最终回复正文
与事件账本仍保留。部署需同时更新流事件生产端与 Web 消费端，避免旧生产端持续
发出无法归属重试代次的 delta。Channel 的临时流文件按 run 和 generation 隔离，避免旧执行收尾
删除新执行的输出，或 SSE 字节游标在重用文件时漏读。

provider 执行抛异常时，adapter 先写入 session failed 并清理该代次的临时流，
再由既有 dispatch 失败路径写入 reply failed。不可用的 provider 在启动流前失败。

前端 `channelProjection.ts` 同样优先使用 `status` 中的终态，再为非终态请求读取
`live_status`，兼容尚未更新的服务端响应。live stream fold 拒绝向终态 run 追加
迟到内容，并校验 generation；缓冲压缩也按 generation 隔离。

验证：`tests/test_channel_terminal_projection.py` 覆盖缺少 provider 终态、迟到 delta、
新 generation、同成员的新请求和实际 API 注册路径；
`tests/test_channel_stream_lifecycle.py` 使用真实 adapter/emitter 和模拟 provider
覆盖重试、不可用、异常、执行中被终止及旧代次清理；
`web/tests/channelTerminalProjection.test.ts` 覆盖终态优先级及跨代次 SSE。

此修复不以 elapsed time 推断失败，也不负责停止 provider 进程。仍在运行的请求由
现有 provider deadline、取消动作和 recovery 路径负责终结。
