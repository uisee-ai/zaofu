> 状态: done — 3755f6c8 fix: preserve recovery identity and reader outcomes across dispatch

# 校验四个通用恢复入口

8×8 工作树审计在本地 dev b7ef6567 复现了以下四个缺口。本任务用独立的状态、受控事件和通用工作流夹具验证。原始修复参考为 e009f669、1d39f418、d4357265；Web 定向回复依据 read-query-guard 的 2026-11-21-1924 提案。

- 从批次断点重发候选 -> verify: 同一冻结身份的可核对字段保持一致；有冲突时拒绝重发。受支持的历史断点和已经受控恢复的合法路径继续可用。
- Reader 提交依赖阻塞 -> verify: fanout 和汇总恢复路由能辨别依赖阻塞、弃判和拒绝，当前及可兼容的历史报告可重放。
- 在健康刷新期内和超期后运行标准 tick -> verify: watchdog 仅在真正超出阈值时提交源修复请求，Supervisor 的恢复责任人投影使用相同窗口。
- Web 回复当前或过期 worker operation -> verify: 排队与消费保留显式身份，过期消息拒绝，普通未绑定消息仍可投递。


## 验证记录

- 候选恢复 8 个模块 146 项通过，覆盖原生回执缺少冻结 ID、历史内核重发与同一冻结的字段冲突。
- Reader 的 16 个模块 470 项通过，另有 26 项准入及 manifest 测试通过。结果分别保留依赖阻塞、主动弃判、工件执行结果与被评对象裁决。
- Run Manager 和 Supervisor 95 项通过。Web 到 transport 的测试覆盖等待期间暂停、尝试轮换、原租约保留与普通消息，最终相关 6 个模块 133 项通过。
- 按锁文件安装 Python 开发、Web 与 Controller 依赖，以及 Web 的 npm 依赖。执行 `python scripts/dev-verify.py run --base dev --workers 12` 的自动层，812 个模块中 10,025 项通过、8 项跳过。Web 类型与单元检查通过，合并前哨兵 53 项通过，模拟工作流 10 项通过。
- 验证采用本地模拟 provider 与状态回放。真实 provider 的完整 8×8 运行属于独立验收层。
