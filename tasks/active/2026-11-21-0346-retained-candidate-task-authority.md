# 局部代次冻结后的逐 Task 验收证据接续

> 状态: active

隔离 8×8 授权修复。9ba185da 上已用真实 Git 集成与原生 freezer 复现：TASK-A 在 map-g1、TASK-B 在 map-g2 均完成且两个 integration receipt 有效，完整 Candidate 冻结成功，但 Candidate authority 以 map-g2 检索 TASK-A，报 lacks current admitted Verify evidence。临时 fixture 提供独立 Verify 事件；不等于真实 Provider 验收。无真实 Run 写入、无模型调用。报告 retained-candidate-generation-reproduction.json。

- 接通原生保留证据 -> verify: authority 与结果 currentness 共用解析；从冻结的 kernel integration ledger、逐 Task generation context、原 Verify admission 与当前合同确定来源，不按同名 Task 或最新任意结果兜底。
- 保留既有复用链 -> verify: 未改 Task 的 reviewed completion proof 按原 Candidate 精确核验，旧 head/代次/收据不改写；发生修复的兄弟 Task 不再被要求满足旧整批 proof。
- 明确边界 -> verify: 新 Candidate 保留新 head/全局 generation/package，仍须独立执行最终验收；旧 Task Verify 只证明原 Task source commit，不授予新 Candidate passed。新 Plan 对保留 Task 的合同改动必须拒绝或正常重新验收。
- 负例 -> verify: 错 Task/context/代次/目标、损坏 integration/Verify/predecessor、当前合同变化、缺完整冻结来源与未完成 Task 均 fail closed。
- 实际入口 -> verify: prepare_candidate_verification_authority、candidate_currentness 及真实 result admission 路径测试；相关影响闭包、sentinel、flow mock 后提交。真实 Run 当前因阶段 cap 等待 Owner，不增加额度、不代写 worker 结果。

已实现，共用解析已接入 Candidate prepare/currentness 与真实 result submission 测试入口。LoopForge研究会话复审发现的 integration 原生校验遗漏、只读路径来源错误及 Git 异常漏捕已补齐。当前聚焦93项通过，另加多跳复用25项通过（集合重叠）；最终自动增量闭包1267 passed / 7 skipped、sentinel53 passed、flow mock10 passed；最后改动（只读来源、多跳链、Git错误分类）专项68 passed。保留--base dev全分支规划，执行相对已验证9ba185da的增量闭包；未运行全pytest或真实Provider。真实Run仍等待Owner阶段恢复决定；保持active直到新原生Candidate验证。

测试层级：真实Git集成与freezer、原生结果提交服务使用合成Verify输入；reviewed链测试仅mock Git宿主边界，sidecar/身份/准入校验真实执行。未触碰真实Run、未调用Provider。初始fixture的缺失Run绑定、集成凭据字段、错误done事件时序及一次不存在测试文件属于测试准备错误，不记为产品回归。
