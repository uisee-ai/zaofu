# 199／200 逐项证据对账

本表更新 199 的后续证据，不修改其历史正文或原实施结论。200 的八个工作包按本次批准的验收合同签收；
“软件闭环”“实际跑过一次”“所有环境生产资格”分别记录。测试名称指仓库 `tests/` 下的实际调用方回归，
执行结果见 [test-receipts.json](test-receipts.json)，真实阶段结果见 [workflow-milestones.json](workflow-milestones.json)。

| 199 ID | 200 承接包 | 软件／方法及验证证据 | 本轮真实证据与未覆盖边界 |
| --- | --- | --- | --- |
| R01 部署生效 | 06 | `load_receipt.py` 接入 CLI start；`test_cli_start.py` | 四个进程加载快照区分源提交、内存消费者、配置和 skill；Web TestClient 是提交入口，未验证常驻 Web 热部署 |
| R02 候选／执行计划 | 01/03 | 保留 generation、approval、旧结果准入；`test_workflow_operation_resume.py`、`test_task_pipeline_reconciler.py` | 真实单 generation 成功；计划替换由确定性测试覆盖，未另做真实多代重规划 |
| R03 成功实现后交接 | 01/03 | 结果优先、TaskRef 交接和 attempt 互斥回归 | Impl 原成果经过控制面重载后交给独立 Verify 并集成；本次未在 TaskRef 写入点注入真实故障 |
| R04 消费者不可用 | 02/03 | `test_worker_assistance.py`、`test_run_manager_reflection.py` 的 unavailable、timeout、迟到出口 | 当前求助消费者可用并答复；关闭消费者的反例采用隔离测试 |
| R05 分阶段验收 | 05/08 | Plan 方法区分 fixture／真实责任；产品 acceptance 和结果 admission | 真实任务 Verify、8 路候选 Verify、外部冻结脚本分别验收同一产品；不把 host mock 证明充作模型证明 |
| R06 审核摘要 | 05 | `test_plan_digest.py` 的根文件真实冲突／政策区分 | 单任务计划经独立 Plan Critic；复杂共享文件拆分仍需专门样例 |
| R07 任务可执行性 | 05 | readiness skill、`test_plan_execution_review.py`、`test_plan_execution_eval.py` | 真实 Planner 选择一个完整纵向任务；复杂 DAG 的模型质量不是本轮资格 |
| R08 原稿语义矛盾 | 02/05 | 固定来源与语义评审方法；unknown 路由不代替 Agent 根因判断 | 正常业务需求经 Scan/Plan/Verify；未做“保留／删除历史”真实模型对照实验 |
| R09 Skill 消费 | 05/06 | skill 镜像一致、envelope/provenance、实际读取和结果绑定 | Worker 按方法请求 OA、读取答复、继续；已读回执不单独证明所有方法被遵循 |
| R10 停止／恢复 | 06/07 | `test_host_lifecycle.py`、pause lifecycle、CLI restart/stop、在途身份回归 | systemd SIGKILL 恢复与 pause 拒绝重启；真实 canary 收尾保留停止清理回执；不是五种意图的全部宿主组合 |
| R11 评审／权限 | 01/06 | `test_run_manager_action_admission.py`、OA execution policy、precondition | 正式受控 workflow-submit 和真实 OA 只读指导；shadow 没有升级为正式修改权限 |
| R12 进度对账 | 06 | `test_web_metric_facts.py`、`test_web_projection_hotpaths.py`、RM 请求投影 | 分别查询业务 Task、operation、Run 及交付事实；本次不启动浏览器做 UI 资格测试 |
| R13 共享故障 | 04 | `test_recovery_association.py`：一次实际 SQLite 修复、8 个独立探针、7 成功／1 schema 失败 | Agent 的关联决定与修复工具输入为 fixture；不冒充 8 个真实模型排障 |
| R14 容量隔离 | 03/04/07 | `test_task_pipeline_capacity_recovery.py`、1/1、3/2、8/8、12/4 和结果复用回归 | 真实 8 路候选 Verify、1 个 Impl；没有完成 8 Impl+8 Verify 的数据库／端口集中故障压测 |
| R15 可复现交付 | 08 | 独立 worktree、显式提交、冻结脚本、source/config 摘要 | 业务按冻结提交从 git archive 复验；未修改或清理 LS 的历史缓解措施 |
| A01 请求义务 | 01/03 | `test_resident_request_obligations.py`、request deadline、pause 时钟、固定请求 | 一次真实 OA 求助按 request_id 接收与关闭；超时／迟到负例为确定性测试 |
| A02 效果不能提前结案 | 01/04 | `test_recovery_handoff_effects.py`、step/effect 分离、显式 effect_bindings | 当前指导回答后原 attempt 继续；共享修复的效果由逐 Task 探针验证；未穷举所有业务根因 |
| A03 等待适用性 | 02/05 | RM/OA 方法按当前请求证据等待；`test_failure_diagnosis_routing.py` | 实际记录周期扫描造成的 180 秒首跳延迟并修复；不以历史成功解释等待 |
| A04 共同准入 | 01/06 | pending、Agent 建议、operator 共用 gate；9 个新增拒绝反例先红后绿 | 修复在实际控制面重新加载；本次无多操作员并发人工动作试验 |
| A05 独立监督 | 07 | 异步 reflect、固定结果与超时隔离；`test_design200_host_supervision.py` | 真实 systemd/tmux 与 SIGKILL；Worker 是 mock，测试结束移除临时监管 unit |
| B01 结果未知 | 01/04 | 稳定 operation、共享资源锁、重复／unknown 副作用拒绝、receipt 重放 | 一次实际 SQLite 工具效果，重复请求不重复修复；不承诺任意 shell 或远端服务 exactly-once |
| B02 通知与对账 | 03/07 | reactive + periodic 共用 reconcile；遗漏唤醒、竞争发布一次测试 | 真实首次延迟暴露旧路径缺口；修复后当前请求关闭及下游推进，新请求实时路径由测试验证 |
| B03 成果复用 | 01/03/07 | `test_reader_result_before_recovery.py`、有效结果优先、独立任务保留 | 控制面代码重载没有再次执行真实 Impl；多 Provider 同时重启尚无实机资格 |
| B04 处理器续作 | 01/04 | `test_recovery_step_admission.py`、跨版本 problem continuation 与 failure accounting | 当前请求身份跨控制面重载保留；所有未来 handler 的中断点仍须逐动作验证 |
| B05 独立任务记忆 | 02/03/05 | immutable question、tried actions、证据摘要、Store/Event/required sidecar | Worker/Verify/Discovery 的当前输入来自受控引用；模型上下文压缩后继续读取，未宣称检索开销最优 |
| B06 子角色方法 | 05/06 | canonical skill、materialization、实际 provider 能力说明 | 真实 Impl 自行纠正 scope guard 拒绝，OA 指导后按原合同交付；不等于所有角色／backend 已逐一评测 |
| C01 配置／简报一致 | 05/06 | budget briefing、配置快照、未支持硬限制明确声明 | 有效 v5 保留 8/8、16、8/2、独立 Verify、CAS 和原预算；未做活跃 Run 预算动态变更 |
| C02 恢复相互隔离 | 01/03/04 | `test_run_continuation_scope.py`、作用域锁、同请求去重、失效版本拒绝 | 当前求助没有第二写入者；多任务故障并发靠确定性测试覆盖 |
| C03 存储故障 | 07 | `test_recovery_storage_faults.py`、EventLog/signature/Store/sidecar 的 ENOSPC、中断和 unknown 拒绝 | 使用写入替身，不填满真实磁盘；不能把当前取证文件当作任意时间点的一致运行快照 |
| C04 性能与推进 | 07 | 相同 4,064 事件／16 Task、5 次预热+30 次测量，p95/RSS/read_all 成对记录 | p95 251.70→254.15ms，通过预冻结界限；真实模型阶段耗时另记，不代表集中故障吞吐 |
| C05 预演与 apply | 01/06 | CLI dry-run、apply 前 currentness/permission、共同准入回归 | 真实 workflow-submit 授权生效；没有修改已有运行的 shadow 权限来放行 |

本表未把尚无实机资格的场景删出范围，也不把本轮单一业务任务的通过推导为所有项目可无人工恢复。
后续部署涉及不同 provider、远端副作用或共享服务时，按对应行补适用性与效果证据，仍由原 runtime 权限门控制。
