> 状态: done

# V5 验证上下文、运行时可靠性与闭环修复

来源：用户 2026-10-30 批准在 `wip/codex-20261030-hello-prd-smoke` 先生成 backlog 再实施上轮审查问题。
基线：`7b5f8f7f`；[真实运行记录](../../docs/records/2026-10-30-hello-prd-v5-controlled-assembly.md)。
目标：维持 V5 验收与预算门禁，减少无效模型往返，修正误报与清理缺口。

## 实施范围与验收

1. P1 必读工件批量分页：超过 8192 字符自动返回有界页及续读信息，保留内容去重、逐来源鉴权和完整读取收据。
   - 多来源与大单来源分页 -> verify: 页面有界，跨页可完整还原，漏页拒绝、摘要变化拒绝、别名越权拒绝。
2. P1 Verify 取证与提交：技能统一索引优先、按阶段读取；成功预校验后直接受控提交，减少重复全文和模型往返。
   - 真实 CLI 调用 -> verify: 预校验失败不提交；通过后仍经过 canonical admission；当前任务/目标证据不可串用。
3. P1 V5 时间配置：operation、无进展阈值、on-demand reader/writer idle 默认 600 秒；常驻角色、心跳、启动和锁等待各自保留语义。
   - 加载完整配置 -> verify: 展开后的 reader 与 Task Pipeline lane 均为 600；活动操作不休眠，恢复保留身份。
4. P1/P2 用量可解释性：区分累计处理量、缓存与非缓存、输出、金额未知；保持当前 token 硬上限，不虚构定价。
   - 未定价预算记录 -> verify: 展示未知而非免费；累计增量与缓存无重复相加。
5. P1 消费者锁竞争：正常 contention 退避，不冒充运行故障；真正异常继续上报。
   - 并发消费者 -> verify: 不重复接纳，不产生假 tick.failed，解锁后继续推进。
6. P1 通知按渠道能力路由：Web-only 保留可处理通知，不隐式要求飞书；明确飞书请求缺配置仍失败。
   - 两种渠道请求 -> verify: 默认无假飞书失败，显式失败不吞掉，已有已送达/幂等行为保留。
7. P2 安全脱敏：数值 token 配置和普通测试名可读，真实密钥仍遮盖。
   - Raw/API/工具文本 -> verify: 数值与测试名保留，JWT/API key/环境 secret 回归通过。
8. P2 Task 关联：从核验过的输入 manifest 投影 Task，不从 URL 或 request 名猜测。
   - Web request 与页面 -> verify: 正确 Task 可见；无绑定、摘要不匹配及跨项目输入不冒领。
9. P1 派生资源回收：诊断退出、取消、失败时停止 owned runtime/tmux/sidecars，保留未归档候选文件；清理本轮 rmar-0cd86772ce2b 存活进程。
   - 异常与取消 mock -> verify: 退出路径执行 teardown；只清理所属资源；保留候选与原 dev 8001。

## 验证与交付

- 修改模块、直接调用方、共享合同定向回归；运行 dev-verify plan/run 和 premerge sentinel。
- 浏览器用 Docker mcp/playwright，临时服务使用 8002+、0.0.0.0；真实最小 Hello World 保留完整 V5 路线。
- 真实复测记录 Verify submit、后续验收和最终结果；未走通不能认定真实闭环成功。重复 token 用量只用实测比较。
- 不修改既有 Run 的预算、事件与完成状态；语义策略由 skill/profile 提供，Kernel 只校验确定性合同。
- 实现提交后记录短 hash，再归档已完成任务；不自动合并或 push。

## 实现提交

- `0e0321bd fix: bound V5 verification reads and harden runtime closeout`
- `d64c7d80 fix: project provider context window into workflow details`
- `9a44ad6f fix: reconcile provider usage before session retirement`

本任务关闭的是上述 9 项修复，不代表原 Hello World 全流程任务完成；原 smoke 任务保持 defer。
[完整实测、剩余阻塞与清理记录](../../docs/records/2026-10-30-v5-verification-efficiency-e2e.md)。

## 进度

- 9 项实现及直接调用方已完成；Task Verify 真实接纳，整轮最终因 Run 总预算而阻塞，不能认定完整交付闭环。
- B4 实测增补：Provider 尾部 211337 tokens 在 session 切换时漏采，补上退出后、身份轮换前的幂等对账；87 项 provider/生命周期/候选/计量回归通过。
- 单任务 Verify：3 次必读分页、20 个工具调用、95.454 秒，validate --submit 正式接纳；Provider 最终 453075 tokens，runtime 当时只采到 241738，不能混用这两个数。
- 固定 8-lane 候选验证期间触发 10000000 Run 总预算（观测 10035887）；Discovery/Judge 未执行。后续编排优化留在本地 proposed backlog，不擅自增预算或缩减门禁。
- 冻结候选 198eb416 已独立验证两条 CLI 与 3 项 unittest 全部通过；不替代最终交付门禁。
- 定向后端回归：384 passed、1 skipped；追加控制器 12 passed，预算/Graph 55 passed，最后一次受影响调用方 76 passed（集合有重叠，不累加）。
- 六组 deterministic mock E2E：17 passed、6 deselected；Web build/typecheck/unit 和 premerge 51 sentinels 通过。
- Docker Playwright 的 workflow-execution-graph 15 项通过，包含累计 tokens、缓存、金额未知及上下文占用分离断言。
- 实验遗留 rmar-0cd86772ce2b 已由其 worktree 内的 zf stop --fast --include-run-manager 停止；原 PID 111354/112615 已消失，候选文件保留。
- 真实测试项目 hello-v5-optimized，Task TASK-626329；创建及提交经 8002 浏览器受控动作完成。

- 尾部对账最终验证：87 passed；相关 mock E2E 11 passed；sentinel 51 passed；dev-verify flow-smoke 10 passed。新增对账修复未再跑真实 Provider 整轮，已明确记录。
- 临时服务、runtime 与浏览器已停止，状态私有归档后清理，8001 保持监听；worktree 保留待独立合并。
