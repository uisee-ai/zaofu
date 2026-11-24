# 冻结候选的分工验收与断点接续

> 状态: done

本批按业主本轮批准实施 P0–P2。8×8 的原始冻结候选 c68235cb、Run workflow-7dbaae8a3501596d 和宿主测试保持独立身份。手工发布新控制面后另计接续窗口。

- P0 恢复时消费真实缺页与已绑定宿主事实，尽早交接已证实的反例 -> verify: 不重复已见证页，保留未见证、错版本和新的消费义务，不能提前签收。
- P1 在获批 Plan 中声明各 Reader 的验收条款和旅程，派发与提交绑定到同一冻结目标 -> verify: 漏覆盖、错角色、陈旧绑定或相互矛盾的结论不能通过，已有完整报告由整体验收入口承接，旧合同保持可恢复。
- P2 使用同候选匹配的输入进行三组影子比较 -> verify: 对已知溢出、跨任务缺陷和正常对照检查覆盖与检出，区分模型实测、确定性回放及运行时证据。
- 将已验证代码同步至该 8×8 控制面并原地重启 -> verify: 原 Run、Plan、16 个已接纳任务和日志前缀保留，核验实际后续派发或记录具体终止原因及下一责任人。

## 已完成

- `af7e245e feat: bind candidate reader assignments and resume approved repairs` 实现 P0、获批的 P1 分工和历史 OA 拒绝的受控重投。按原读取义务补缺页。分工的 Reader 仅对所分条款签收，独立的产品完整性角色汇总已准入的结果。
- `2c4ea859 fix: dispatch recovered candidate work through its approved plan package` 修复从旧账本到 Writer 的流程路由和新旧 Task Map 间的端口归属。真实隔离副本仅为唯一 successor 生成一次审批。
- `8c553b47 fix: deliver scoped counterexamples to successor implementation and verification` 将原 OA 认定的产品反例绑定首个后继实施与验证请求；`260e0872 test: exercise counterexample handoff through both task stage dispatchers` 验证两个正常调用入口。
- P2 真实模型配对测量覆盖原始溢出、修复对照、跨模块接线错误，三种读法分别命中 9/9。全量、进度摘要、分工加整合的中位秒数为 13.46、11.86、18.74。每臂 3 个候选，默认保留既有 Plan 职责。
- 实施批次确定性影响闭包 10823 项通过、12 项跳过；后续现场修复闭包 7694 项通过、12 项跳过。两次另含 53 项哨兵与 10 项流程 smoke。首批显式 5 个 mock E2E 文件 13 项通过。各层有重叠。
- 原运行内保留 16 份既有验收与集成，原账本字节前缀可核对；新任务仅 `NUM-AVERAGE-REPAIR-1`。Owner 审批后由真实 Worker 实施。造父宿主先发布 `439eff4c`，按冻结合同复测后，后继 Worker 更正边界行为并再次由宿主发布 `ac6fada6`。独立快照通过 5 项模块测试和 5 项冻结探针。
- `e6e659b4 fix: reject invalid implementation commits before result admission` 将成功 Impl 的 Git 对象绑定提前到预检和准入。以第三代实际宿主回执和第四代实际工作树分别重放，准确拒绝现场多抄一位的 SHA，正确引用可在同一 attempt 通过。最后一批影响闭包 4126 项通过，专项 97 项与哨兵 53 项均通过，计数有重叠。原控制面在 10:56 UTC 受控重启，原生 Worker PID 与启动时刻保持，同一 Run 的恢复事件为 `evt-ec6d5bc7db78`。
- 本轮有人工发布、断点重投及准确代 Owner 审批。最终交付按运行中的 Candidate / Judge 验收。线上证据见操作员留存的 `oa-candidate-recovery-proof-20261124/candidate-p0p2/`，含原始运行标识、P2 对照、两次影响闭包、发布与读取证据。

11:01 UTC，运行时在原第四次 Impl 尝试接受准确的产品提交，随后以同一个 SHA 自动派发独立 verify-lane-0。实际派发事件为 `evt-e2339d4adf0f`，验证请求 `evt-8337d7791fd2` 将原 OA 反例列为必读。任务归档覆盖控制面改动与断点恢复，8×8 的产品验收继续在原 Run 内进行。
