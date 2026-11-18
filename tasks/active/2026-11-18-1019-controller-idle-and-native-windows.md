> 状态: active

# 云端 Connector 空闲确认与 Codex 订阅窗口

依据 209、210，消费 Controller 新增的可选能力，保留旧协议兼容。实施见[云端 Runtime](../../docs/impl/208-cloud-runtime-delivery.md)。

1. 模拟达到阈值后新增终端、待补传、未知交付或未结执行 -> verify: 二次检查抑制确认，浏览器断开仍保留终端责任。
2. 测试稀疏及乱序的 Codex 限额窗口 -> verify: 最新独立窗口生效，非法数值、异项目/会话及未知字段不上传。
3. 运行实际 CLI 适配器调用路径并使用新旧能力组合 -> verify: 新 Controller 收到限定字段，旧 Controller 继续接收原用量合同。
