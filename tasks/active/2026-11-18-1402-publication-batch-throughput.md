> 状态: active

# P2 补充：预授权候选发布的有界批处理

承接已批准的 P0→P1→P2 实施与收尾、[P2 真实资格](2026-11-18-0933-p2-real-qualification.md)。
真实32任务资格 workflow-8cacd612404a641a 已观察到 Impl 峰值8、Verify峰值6，但未出现8+8重叠。
源码 `reconcile_task_publication` 每次成功 commit 或 retry 后立即返回；Run Manager 每tick只调用一次。
已有候选排队而Impl槽位空闲，因此先用独立确定性用例复现发布吞吐限制，再修复机械调度。
当前真实Run继续使用冻结源码，不加载本补丁，不将这份修复计为该Run的无人恢复成功。

## 验收

- 8个独立、已授权且静止的候选 -> verify: 配置批量8后，一轮发布8个精确候选；下一轮请求8个既有Impl重试；重放无重复提交或重复请求。
- 默认配置与既有授权回执 -> verify: 默认仍一轮一动作，旧授权摘要及回执可继续使用；显式批量配置纳入授权摘要。
- 批内暂停、已用Run预算、未知效果、活跃worker -> verify: 每项重新核对当前授权/状态；暂停停止后续动作，预算不因旧事件快照超用，其他任务不继承错误授权。
- 实际 Run Manager caller -> verify: 真实入口执行批处理且保留 auto_execute=false 边界；不增加自动业务通过、第二状态机或并行Git效果。
- 影响闭包、哨兵、相关mock -> verify: 通过后提交/归档/合并；真实8×8仍由独立冻结运行证明，不以单测替代。

范围仅候选发布的有限批量配置及现有调用链；宿主容器命令执行仍保留原先每轮一个效果及超时，总发布预算、精确路径、权限和CAS集成不变。

## 验证发现的基线漂移

扩大闭包首次8033 passed、10 skipped、2 failed；两项在未修改、与dev树相同的cac88350重现为2 failed。
`workflow_synthesis_consumer` 已在6ebdb444提供可重试Busy异常，由 `tick_services` 捕获并保持队列，旧测试仍期待直接返回0。
生产profile在3fe3a300已将 bounded-direct-v1 的token预算设为0，旧断言仍为1500000。
按当前明确调用合同与既有配置校正两项测试，保留“不产生失败事件、操作仍requested”的断言；生产行为与限制不作额外修改。
首轮源码专项60 passed，哨兵53 passed、流程mock10 passed。校正后完整影响闭包8035 passed、10 skipped（175.64s），哨兵53 passed，流程mock10 passed；另两断言所在模块与caller70 passed。各集合重叠，不相加为独立测试总数；不是全库或真实8×8通过。
