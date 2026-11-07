# Task Pipeline 失败恢复保留已提交部分交付

> 状态: done
> 实现: `140b1944 fix: preserve task pipeline partial commits during recovery`；dev 合并 `bb9aa90a fix: retain committed task work across semantic recovery`。

来源：用户授权持续监工、修复阻塞并记录；LS FMS-04 dev.blocked evt-6953b57083e2 明确 source_commit=1b3ffa8，semantic request evt-d24c06f6a0cd 却选择开工前 d14768b。当前 dev 1deb6b42 的 semantic_replan 仅消费 legacy fanout.child.failed continuation。

范围：只补 Task Pipeline 已准入失败交付到 semantic recovery 的可信提交继承；不改业务判断、任务完成标准、失败事实或其他在飞任务。

## 验收

- 当前已准入 blocked Impl、身份/派发/request 绑定及真实 Git 提交核验 -> verify: Run Manager 实际入口生成 partial_task_continuation，target 为已提交部分交付。
- 缺准入、跨 task/run/attempt/generation、篡改信封/工件摘要、提交不匹配或非当前操作 -> verify: 拒绝采用 worker 声称的提交，不读取任意目录/不携带未提交文件。
- 原 legacy continuation、task-base 和候选分支行为保持 -> verify: 直接调用者、共享 Operation/Envelope/Store、恢复 mock E2E 与 sentinel 通过。
- 合并 dev、控制面重载后受控重发 FMS-04 分析 -> verify: 新请求和实际读取锁定正确部分提交，原失败保留，其他 worker 不中断。

## 现场约束

FMS-04 的 local 误将真实授权集成作为前置，成员能力也尚未补全；恢复指导必须引用已批准隔离开发要求并保留候选层责任，不能只删脚本 exit 1。此业务纠偏交由语义分析/实施，当前软件修复只保留可信代码基线。

## 已取得证据

- 新测试首轮夹具误用 EnsureOperationResult.operation，13 个错误均为夹具；改用 request_hash 后当前 dev 基线 2 failed/11 passed，证实 partial 恢复丢失。
- 初版修复 69 passed/1 failed：损坏 sidecar 应安全拒绝而非抛异常；修正后 70 passed。
- 真实现场回放发现准入事件 origin 可为空、规范化结果使用 target_commit，初始夹具与真实生产者边界不符。改为调用实际 ControlResultAdapterRegistry，保留拒绝 worker 来源及其他身份/摘要证明；当前 71 passed。
- 只读实机回放已输出 partial_task_continuation → 1b3ffa8，原 task base d14768b 与规划 provenance 保留；尚待合并、部署和受控新请求。

- 自动影响闭包 58 个测试文件 1,001 passed；共享 Operation/Envelope/Result/Capsule 51 passed；sentinel 53 passed。所有验证使用当前 worktree 的 src，未运行 full pytest/真实 provider E2E。

## 实机验收与边界

- dev 合并后聚焦 71、sentinel 53；控制面 09:03:22 重载，PID 2460647，runtime-load 8dc28c2b1e64420492ad81dd42242c1d / 4c599f4bf06345d2ab5c20cb881beeeb 证明 source_revision=bb9aa90a。活动 TODO Verify 保持；治理 Verify 在重载后自然 passed/done 并退休。
- 09:07:36 受控恢复 evt-137fd8746da5，fanout evt-807d69b42b69 / operation evt-c944d676b932；request、briefing、实际工作目录均锁定 1b3ffa8。semantic-replan-request 与两个关键输入全部 read-ledger 页覆盖完整，读入指导明确保留已有实现、隔离开发和最终集成分层。
- LS 证据：docs/records/2026-11-07-projects-partial-continuation-live-proof.json、projects-controlled-redrive.json、partial-continuation-reload-before/after.json；完整事件、测试与操作纠偏见 2026-11-07-three-blockers-recovery.md。旧取消 scan/失败保留；未改 canonical 文件，未 push。
- 操作脚本初次重复获取 runtime 的恢复锁导致等待、随后当日窗口与全量历史的 progress_digest 不一致导致 blocked；均在临时脚本修正，复用同一 external 请求并通过原有准入检查。与产品源码缺陷区分，不声称 RM 自主完成修复。
- 本 task 的完成只代表可信部分提交进入真实恢复分析。FMS-04 成员业务补齐、精确验收、集成以及最终产品认证/授权联调仍未完成，继续监工。
- 归档文档检查 16 passed，门禁 53 passed。

09:10 后续阻塞（另开修复，不归本提交解决）：新 attempt 的 artifact index/recover 被旧 fanout.cancelled evt-305bb2ba41af 错误标记 superseded，source_watermark 指向旧取消事件；已确认新工作目录及 worker git rev-parse 为 1b3ffa8。部分代码传递修复有效，但真实恢复尚需修复取消作用域。
