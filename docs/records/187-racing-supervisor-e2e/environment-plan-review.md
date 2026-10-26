# 环境与时长证据修订审阅

本版 Plan：`evt-498c4d468cc2`；正文 SHA-256：`93255908dd5c222141dd85b9c448829f2dde9a96fecdcf88d0ec0737d5095916`。

[完整 Plan](environment-plan.md)、[完整执行规格](environment-plan-execution_spec.json)、[Task Map](environment-plan-task_map.json)、[审批前快照](environment-plan-gate.json)。

## 授权与审查

用户已批准原 Plan，并明确要求真实端到端验证、发现问题持续修复。本轮属于该续修范围：产品仍是同一90秒赛车游戏，4任务、16项AC、原依赖及owner、3实现/3验证lane、模型、skills和zf.yaml均保持。独立Critic正式结果 `evt-096167993e32` 为approve、无阻断项；原先在取消fanout后生成的Critic意见未充当本轮审查。

监工复核：原产品规则、精确“重新比赛”文案与完整重置行为仍由AC-A02约束；原AC-A07入口/清理/失败传播场景保留，新增有限时间字段负例。PRD中的摘要精简不改变这些具体合同。历史Owner局部修改仍在原工件/审批账本可追溯，不将本版文档生成时间视为新的用户授权。

## 本次具体修复

- 各canonical命令自行完成冷依赖准备，不再要求操作者先运行npm安装。
- Docker内限定镜像浏览器组合须通过项目Playwright的实际协议/键盘/截图/trace探测；不兼容时回退到容器内900秒有界安装，缓存位于node_modules之外。保持Docker-only、0.0.0.0、隔离端口与失败非零。
- 实际交付校验逐字段拒绝缺失、错误类型和非有限时间，并独立检查两个时长均至少90000毫秒。8组fixture测试调用生产路径，不能替代正常真实90秒比赛。
- 每条命令期限显式包含冷准备成本，详见审批前快照的旧新值；未调整Provider预算或控制面配置。

## 执行边界

本次采用DEC-ENV-01的qualified_reuse_cache方案。待真实浏览器核对完整预览与摘要后，由监工依据既有“批准，继续”和持续修复授权，经token-gated plan-approve明确记录续修依据；不声称用户进行了新的点击，不开启自动批准策略，不伪造审批事件。

批准仅允许实施；原gap、候选14项检查、正常90秒比赛、独立多lane Verify、Discovery、Judge和Kernel终态均仍待真实执行证据。
