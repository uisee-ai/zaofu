# 保存恢复诊断的不可变上下文

> 状态: done
> 实现: `ef6330ac fix: preserve recovery context snapshots across semantic handoffs`

依据：用户持续监工、发现修复并记录授权；[199](../../docs/design/199-worker-failure-recovery-and-role-handoff.md)。

## 真实复现

LS-v1 FMS-03-GAP-IMPLEMENT 同一 failure-diagnosis 请求在 RM 诊断和 OA 交接分别写入 diagnostics/recovery/<request>/context.json。evt-d56d180e491d 引用 17,752 字节，evt-58aae56926f0 后变成 17,265 字节；旧 required sidecar hydrate 返回 byte_count_mismatch。新 OA 输入有效，但历史请求正文被覆盖，重放不能满足证据合同。记录：LS docs/records/2026-11-07-recovery-context-overwrite.json。

原测试只验证首次写入可读；RM/OA 检查最新上下文，没有同时验证先前 descriptor，故未发现历史引用失效。

## 有界修复

1. 复用既有不可变 JSON sidecar writer，按内容保存每次快照；保留 request/task/run 语义与访问元数据 -> verify: 同请求连续不同事实写入后，新旧引用均能 hydrate，旧内容不被覆盖。
2. 走实际 RM tick 的 OA 交接 -> verify: 原诊断上下文仍可核验，新交接上下文准确；不改变 Task 状态或故障关闭规则。
3. 聚焦、影响闭包、sentinel、mock、合并 dev 并受控重载 -> verify: worker 保持，后续真实引用采用不可变路径；已丢失历史正文不伪造补写。

## 验证与交付

- 修改前：两项回归失败（旧 descriptor 被覆盖），11 passed。修复后聚焦与直接调用链 83 passed；期间发现测试夹具误用 TaskStore.update，改用 task_id/title 参数。
- dev-verify planner 选择 57 文件；精确影响闭包 974 passed（60.10 秒），不是 full pytest。额外 serial 10、sentinel 53、mock flow 10 通过；ruff 与 diff check 通过。
- 日志 /tmp/zf-context-snapshot-{focused,closure,serial,gate,mock}-20261107.log。待合并与控制面加载；业务 worker 保持。历史丢失正文不能由新快照冒充恢复。

## 实机验收与归档

合入 dev becef7e9，合并后 83 聚焦及 53 sentinel 通过；06:02:14 evt-f03f14dade0a 控制面重载、worker 保持，PID 1889643，runtime-load/4192f2919f494b16a23e3e8baf9e72b7.json 绑定版本。全部 tmux pane/PID 前后一致。06:03:42 FMS-13 新恢复请求 evt-99bf5f79a97e 已写入 artifacts/recovery-context/semantic-replan-f8212c79595d8efa/f15a4be0c58a638798d84a2c4a22dc3ba9b995bb47378a93a506c835ad4560a4.json，required descriptor hydrate 通过；现场记录 2026-11-07-immutable-recovery-context-live.json。不同快照旧新均可读由实际 RM tick 回归证明；已损坏历史引用仍如实保留，不补造正文。源 worktree/branch 清理完成，未 push。
