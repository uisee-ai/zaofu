# R38：OA 必读正文投递方法

> 状态: active

授权：继续监工并修复。LS车辆失败OA在evt-d6e1f05c0248使用后台Python循环capture_output所有必读页，只输出字符计数和complete；随后开始查源码，正文是否消费不明。旧生成briefing仅要求read every，通过CLI读取不能证明模型消费。监工已通过worker.reply.requested evt-777d5f633f6f要求补齐正文后再裁决。

- OA briefing 使用当前attempt的受控 required bundle 命令 -> verify: 真实OA operation生成命令可执行，完整分页包含每个required正文并匹配原始对象。
- context-hygiene 明确 required artifact 不适用构建日志tail/摘要法，需逐页消费，收据不等于理解 -> verify: 源技能元数据、角色物化及OA输入闭包检查。
- 只修复投递方法，不修改当前冻结输入/收据、不声称能机械证明理解 -> verify: 保留已有policy/身份与optional来源。

验证：RED缺少可执行required入口；修复后真实OA CLI分页回归通过。第一次闭包1失败220通过8跳过：既有compact briefing测试硬编码命令数量3，新增bundle后应为4，调整后dev-verify run（11文件）221通过8跳过，sentinel53通过，flow mock smoke10通过。技能元数据通过。非全仓pytest、非真实provider E2E。
