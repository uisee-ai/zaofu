# Hello World 控制链缺陷修复与断点验证

> 状态: active

用户已批准在独立 worktree 修复本轮问题并基于该代码验证。基线 fe6f3bd6；证据 `/tmp/zf-hello-world-20261026T015754Z/evidence/`。只修改可复现缺陷，不把缺失验证直接认定为缺少实现；不操作 loshu-fms、8001 或其他 worktree。

## Backlog 与验收

- [ ] HW-01 正式配置准入：开放实现已具备的 Product Flow pilot 检查点 -> verify: YAML loader 到 OA/WRC caller 回归通过；仍拒绝无 pilot、非 Product Flow、micro/light。
- [ ] HW-02 人工审批边界：OA adopt 与 Owner approval 分离 -> verify: 审批开启时 adopt 后只有精确版本审批请求且无 Impl；正式批准后派发；拒绝/重放不绕过。
- [ ] HW-03 Provider 传输准入：拦截 Codex + Claude-only stream-json -> verify: 配置/preflight 给出明确错误，Codex tmux 与 Claude stream-json 保持可用。
- [ ] HW-04 活动事件与阶段交接：复现注册表热路径并修复 -> verify: 噪声事件回归保证有界加载、保留活动/状态顺序；真实断点 Verify→集成继续且没有重复 Impl；新 Run 观察连续交接。
- [ ] HW-05 OA 单会话互斥：多个检查点排队、结算后继续 -> verify: 同角色不同时注入两个 operation；恢复与重复事件不会丢失请求。
- [ ] HW-06 恢复服务与交付闭环：核查 RM recommendation 消费和 AR 受控调用 -> verify: 实际入口回归覆盖建议→调用/明确拒绝；断点跑到 Judge/终态，有不足如实记录。
- [ ] HW-07 复测与收尾：focused closure + sentinel + deterministic E2E + 隔离真实 Codex 入口/断点验证 -> verify: 保存命令/计数/证据；实现与任务状态提交，未通过项不得标 done。

## 实施原则

配置灰度仍需显式 pilot。人工审批不能由 OA 代替。性能优化不能绕过 EventWriter、store 锁、新鲜状态或重放语义。断点已越过审批，不能用于证明修复后的审批入口，必须另起隔离新 Run。先复现再修复。实现结束提交本工作分支，不自行合并或 push。

## 验证中发现的根因补充

HW-06：断点恢复后 RM 建议已消费、AR invocation 与 loop 均启动；loop 在严格 skill 校验处失败。Autoresearch 配置重写把技能源指向 driver checkout，同时 experiment 中已有本地同名技能，造成重复来源。增加本地实验版本优先的修复和回归；保留严格碰撞校验及外部客户技能来源。

## 验证记录

- 新增首轮 7 个复现用例在 fe6f3bd6 上全部失败；修复后通过。
- OA 队列/恢复与人工审批追加回归 3 passed；核心 OA/活动事件回归 24 passed。
- 首轮影响闭包 155 passed、2 failed；两项为自恢复重试被自身锁挡住和新 fixture 缺 transport，已修复后复测。
- AR/配置闭包 192 passed、1 failed；旧 stream-json fixture 未声明 provider，已改为明确 Claude 并复测通过。
- AR 修复闭包 33 passed；实际 11 角色模板技能校验无错误。
- premerge sentinel 51 passed（34.27 秒）。自动 dev-verify 更大范围确定性层正在执行，真实 fresh/断点验证仍在运行。
- 证据目录 `/tmp/zf-hello-fixes-20261026/evidence/`；旧断点 8/8 集成、Candidate 已冻结；AR invocation 和 loop 已实际执行，首次 loop 的技能预检失败保留原始结果。
