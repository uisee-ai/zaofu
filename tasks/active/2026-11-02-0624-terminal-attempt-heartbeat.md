# 终态任务尝试不应产生虚假续租心跳

> 状态: active

## 证据

LS 的已失败 attempt `ta-405ffa44a2333c1a5d43` 与 `ta-b9bf7ab4f5f624450e78` 在重启后接收 disk-reader 历史用量，分别产生 `task.attempt.heartbeat` 事件 `evt-0fbe81fde318`、`evt-21a4adad4371`，其租约仍为上一日。已核对 canonical store 均保持 failed、租约未更新，但新事件会误导监控。历史记录必须保留，用量计量不能被删除。

## 验收

1. 重现已终结 attempt 收到 usage/heartbeat -> verify: 不续租、不发新的 attempt 心跳，原用量和终态保持可查询。
2. 活动 attempt 收到合法身份的心跳 -> verify: 租约更新且恰好生成关联来源的心跳；错误身份及竞态下终态不获续租。
3. 运行相关模块、调用方、store 及运行时事件契约验证 -> verify: 定向回归、影响闭包、sentinel 和 mock E2E 通过；部署后核对真实监控及在途 Planner。
