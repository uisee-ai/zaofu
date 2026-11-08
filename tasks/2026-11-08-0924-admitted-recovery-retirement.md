> 状态: active

# 已准入接替任务应撤销旧失败的即时重规划义务

LS-v1 已在 09:17 准入新生产者/消费者，旧 FMS14-02 被 task.superseded；09:19 Run Manager 仍为原失败生成 discovery。Kernel 的 reader replacement guard 在 fanout 创建后 0.137 秒取消，没有派发扫描 worker。下游兜底生效，但上游 pending/active immediate replan 仅识别旧式顶层 supersedes 字段，未消费真实 rework_summary + WRC admission 证据。

依据用户持续排障修复授权，复用既有 reader 精确接替证明，提前关闭 Run Manager 对同一失败的恢复义务。不得以任意较新计划或单纯 task cancelled 判定恢复已经接替。

- 实际事件形状重放 -> verify: 没有顶层 supersedes，仍在完整同源 WRC 准入后不再生成 pending/active immediate replan。
- 因果/权限边界 -> verify: 另一 run/失败/任务、部分接替、保留旧任务、外部伪造或未准入计划仍保留恢复。
- 乱序分诊 -> verify: 原失败已接替后才到达的 triage 不再打开旧义务。
- 真实调用链 -> verify: Run Manager projection/tick 不产生旧恢复动作；既有 reader 后置兜底、rework 和 mock recovery E2E 回归通过。

实现说明：docs/impl/admitted-task-recovery-retirement.md。

## 验证与边界

- 当前 dev `cae9d264` 的真实 LS 最小事件回放：pending=1、active包含已接替旧任务；修复候选同事件回放pending=[]、active=[]，证据位于 LS docs/records/2026-11-08-rm-recovery-retirement/。
- 新测试先3fail/7pass，修复后24pass。一次跨checkout基线重测被conftest导入路径防线拒绝（10 setup errors），不是产品失败；以真实事件回放作为当前HEAD复现证据。
- dev-verify计划列82个影响文件并因硬编码80门槛建议全量；遵照AGENTS不得仅按文件数启动全量，精确执行其82文件闭包：1583passed/7skipped；serial10pass，mock flow smoke10pass。
- 初次sentinel因triage模块1006行而红；将新共享证明封装留在其责任模块后triage恢复992行，未放宽结构门禁。最终调用链120pass、sentinel53pass。
- 本轮没有全量pytest或真实provider E2E。新增生产者仍由既有worker执行，源码修复不声称业务验收完成。
