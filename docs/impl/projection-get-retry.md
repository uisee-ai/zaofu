# Web 投影 GET 的短暂不可用重试

来源在构建过程中变化时，Delivery 等读取端点拒绝返回跨代拼装的数据，使用
`503 {"status":"unavailable"}` 和整数秒 `Retry-After`。Web GET client 按该
声明等待重试，沿用 202 building 的三分钟总等待期限和 AbortSignal 取消语义。

只有这组明确的响应信号会触发等待；普通 HTTP 错误、没有重试声明或非
unavailable 的 503 仍报错。等待到期同样报错，不把失败当成成功或空列表。
等待期间并发 GET 共享原请求链，只有最终有效数据写入缓存。后端的一致性规则、
任务状态及事件账本不变。

回归：`web/tests/queryClient.test.ts` 验证短暂失败恢复、并发共享、缓存、取消、
等待期限及普通错误；Docker Playwright 注入一次 503 后验证真实 Delivery 页面恢复。
