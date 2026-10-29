# 194 取消确认与容量释放

> 状态: active

依据：设计 194 §9.2；SHA256 `3c49d9c77af4cbe76d16410b756c77ba7a3e622d4645e28d0909b1263a5266cb`。
基线：`fe920497`。用户明确批准按七项顺序执行，包含隔离真实测试和评估。

## 范围

取消必须绑定精确 operation/attempt/dispatch；停止未确认不释放容量；重启核对不杀复用 worker。

## 验收

- 宿主假 Provider 延迟/拒绝退出、丢回执和迟到结果 -> verify: 不错误释放、不误杀、不推进过期终态；真实取消资格单列。
- 接线 -> verify: 真实入口调用，不能仅有孤立库；不新增 canonical Task/Store。
- 关闭 -> verify: 实现提交、测试结果与剩余限制明确；未满足项不得标记 done。

## 本批实施与验收

取消确认、持久化后释放、重启补偿与复用保护已实现；宿主假 Provider 测试通过，真实 transport 资格单列 HOLD。

报告：[194 后续闭合](../../docs/impl/194-followup-closure.md)；
精确命令、日志路径、源文件与实验哈希见 [机器验证记录](../../docs/impl/194-followup-verification.json)。
本任务完成的是上述实现／评估范围，不代表报告中 HOLD 条款已取得资格。
