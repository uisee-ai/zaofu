> 状态: active

# 恢复计划引用绑定已接纳包

用户授权持续监工并修复无法自愈的问题。合并修订请求 evt-e219a23e642f 绑定当前 package 36b2b573，但 task_map_ref/digest 仍为旧 package 的图。上轮3ee8bf72只读 source_event 顶层；当前 fanout.synth.completed 仅存 control_result_ref，正式输出位于包 produced/inherited 端口。此前回归只构造内联源事件，漏掉真实 sidecar 形态。

- 恢复时从已接纳包读取 task_map/source_index -> verify: reader retry 与 candidate replan 两入口使用相同代次/运行的实际端口及摘要，不取旧事件字段。
- 摘要/缺失/跨运行或代次校验 -> verify: 损坏包、缺摘要、缺文件、篡改端口与身份不符均 fail closed；包无可选索引时清除旧索引。
- 实际 runtime caller 接线 -> verify: controlled action、orchestrator、stage recovery、resume 和 replay 传入配置 state_dir；入口回归覆盖真实包，不以孤立 helper 宣称闭环。
- 保留在飞 Planner -> verify: 不改不可变请求，不重开19 done；后续批准必须复查新图使用候选 a94e4b4、五项续作及完整契约。

RED：增加state_dir传递后7个行为回归失败，证明旧引用残留且损坏包被忽略。测试初次环境错误及API参数错误不计行为RED。日志位于/tmp/zf-supervision-20261105-2332/ports-red-behavior.log。

验证：87 passed（包/源引用/retry/受控入口），134 passed（resume/candidate/agent控制闭包及mock），sentinel 50 passed / 1 skipped，flow smoke 10 passed，Ruff与diff-check通过。dev-verify plan选择722文件全量确定性层，按AGENTS改用上述明确影响闭包，run执行flow-smoke；未运行全量pytest。真实状态只读演算返回当前12d8b087任务图和29f9e86e索引，未写规范状态。
