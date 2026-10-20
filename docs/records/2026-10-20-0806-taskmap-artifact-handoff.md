# Task map 规格交接的补充修复

> 状态: active。属于 B17 的实际下游交接验收，承接[本轮授权与范围](2026-10-20-0725-taskmap-semantic-repair.md)。

## 复现与修复

第一份正式候选 `fanout-prd-plan-evt-0ad346bd` 修订了 PC-F1–PC-F5：
保留 54 任务、237 AC、171 命令、68 页面/表单、2,043 来源记录，
175 条权限来源拆为 411 个动作，新增完整作业修正及最终 G2 用例。
独立机械复核未发现本片 TDD 的未来命令错用、未知用例引用或 Q 快照差异。
这些结果不是独立语义通过，也不是产品测试执行证明。

该候选仍将具体规格交接为作者工作目录相对路径。路径列入 `artifact_refs`
可以使文件被归档，但不会自动成为 Impl/Verify 的受控输入；兼容解析还依赖
作者私有目录，不构成稳定、同版的正式交接。

第一次 Critic 两次遇到供应商 `Selected model is at capacity`，没有正式语义 verdict。
Runtime 将其记为 `provider_turn_closed_without_synth_result_recovery_exhausted`，
失败事件 `evt-f9d9dd6a3c66`，在同一受控 generation 内发起第二次规划
`evt-fbf8c23ffa7b`。未清零计数、改 verdict 或把该异常称为语义 reject。

第二份正式候选 `fanout-prd-plan-evt-fbf8c23f` 把 12 份完整正文及摘要嵌入
`source_index`，并修正早期 UI 范围和 immutable implementation baseline。
但文件达到 17,282,596 bytes，SHA-256 为
`625fe4fce4887c53d5193a310782d5c769af008a5a8ef6fffe4e1c41f77975b9`。
实际调用 `hydrate_sidecar_ref` 得到 `payload_too_large`（默认 4 MiB）；
`read_preview_artifact` 同样因 8 MiB 限制拒绝。低层 artifact read 的搬移实验
通过，不能替代完整 Package 和 Preview 入口验证。

本轮补齐现有引用式 `plan_ports` 的机械搬移：支持工作目录内
`{logical_name, ref, sha256}` 文件，沿正式 `prepare_fanout_synth_reports`
调用者定位、搬移并改写描述符，兼容 `plan_synthesis_result` 封套。
原摘要保持，路径守卫不变，不递归改写语义正文；不提高公共大小限制。
Planner 可把规格拆成具名受控端口，并在 `required_plan_ports` 声明消费。
方法已同步三份分发，要求检查真实下游入口、大小约束和版本绑定。

## 验证

- RED：正式汇总 caller 搬移后删除作者目录，两个引用式端口用例均以
  `sidecar ref missing` 失败；原有 3 项通过。
- GREEN：相同用例经端口物化、完整 Package hydration 可读取原正文；
  提交的 report 不被原地改写，篡改摘要和符号链接越界继续拒绝。
- 聚焦 22 passed；扩大影响闭包 619 passed、8 skipped；sentinel 51 passed；
  三流确定性 mock E2E 10 passed。未执行全量 pytest 或洛书产品命令。
- 新 Dashboard 的历史 rejected Plan 经 Docker 浏览器核验：
  222,432 bytes、6 页、12 附件，同版正文/下载/摘要，桌面及手机可读，
  0 非 GET API 写入、0 JS 错误。它只验证服务恢复，不替代最终新候选 Preview。

证据位于本 worktree `reports/taskmap-readiness/`，主要包括
`first-formal-audit.json`、`draft-handoff-gap.json`、
`second-formal-size-gap.json`、`port-ref-red.log`、
`port-ref-green.log`、`dev-verify-port-run.log`。
B17 及相关任务在正式候选重新打包、独立 Critic 与实际 Preview 验收前保持 active。

真实候选正文的 13 端口运输实验已通过，原正文保持相等，临时环境已删除。
第二次 Critic 仍因供应商容量错误退出；后续安排与具体返工证据见
[有界续跑记录](2026-10-20-0820-taskmap-bounded-continuation.md)。
