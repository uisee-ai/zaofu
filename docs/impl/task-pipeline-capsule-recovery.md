# Task Pipeline 上下文发布与恢复

Task Pipeline 在准备新的 provider operation 前，通过 TaskStore 重新读取当前 Task，核对其计算出的合同版本与派发快照一致，再经 write_task_doc 原子写入任务说明投影并进行 freshness 检查。发布失败或版本不一致时停止派发，沿现有 dispatch deferred/cooldown 路径处理，不生成产品失败结果。

投影刷新不回写 TaskContract，不修改已接纳计划、验收、任务状态或工作树代码。新的 task.doc.published 事件记录 dispatch、来源事件和三种版本。

worker 发现任务说明、briefing、快照之间版本冲突时，以 implementation result 的 failure_class/blocker_kind=task_context_stale 报告具体版本和证据。该类型进入 harness 修复分支，不消耗产品重规划重试。真实 upstream_contract_gap 仍进入语义重规划；本修复不依赖自由文本猜测、不重写历史失败事件。

故障修复后，监工核验当前 operation 已停止、说明投影与快照一致，再通过现有受控恢复动作/CLI 恢复。已批准 Task Map 的修订仍须通过正式 Planner/Critic、Plan Preview 和新 package 接纳；重启进程不等于恢复 Run，也不等于批准新计划。

验证：tests/test_task_pipeline_capsule.py 覆盖派发入口与真实 briefing/transport 调用边界；tests/test_task_context_recovery.py 覆盖分类与 Run Manager 重规划排除；保留 pipeline、TaskDoc、result submit 和 flow mock E2E 回归。

真实恢复时还发现：候选 Task Map 保留了草稿状态，Planner 预检放行、Critic 通过后才被 Package 门禁拒绝。预检现在也检查必需 Task Map / Source Inventory 中显式声明的 enrichment contract：只有 status=ready 且 enrichment=fulfilled 才能提交；无该契约的历史输入保持兼容。复用已有矩阵检查，不自动把草稿改为 ready，也不替代语义审核。tests/test_plan_enriched_port_readiness.py 覆盖两个端口，以及同一次 result submit 被拒绝后修正并重新提交的实际入口。
