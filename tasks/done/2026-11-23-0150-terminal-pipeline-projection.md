> 状态: done — 33c4195a fix: preserve terminal pipeline history across policy changes

# 监工发现的已完成任务策略漂移投影故障

用户已授权持续监工、发现问题后修复并记录；当前按d5560824运行，沿用cb84bef6工作树。
原无人窗口closed_failed不重置；当前修复回归不冒充无人资格通过。

## 事实与范围

00:39:47起kernel.housekeeping.failed/task_pipeline_dispatch重复约600次，异常为
TASK-ASCENDING admitted profile_digest不匹配。16个原任务均done；当前Planner已经
自行修正9个preserved_task_changed错误并于01:46:45准入，plan-critic于01:47:08自动接续。
只读read_task_pipeline_projection可复现；派发先排除了terminal任务，但随后投影重新
把历史context全量送进严格policy匹配。此前candidate.verifyWidth从1改2改变了完整policy摘要。

- [x] P1 终态历史不按新策略重新准入 -> verify: 完成/取消任务在策略变化后仍可投影，原状态、generation和历史结果不改写；活动任务的摘要不一致仍拒绝。
- [x] P2 混合任务与实际入口 -> verify: 新活动任务与旧完成任务并存，done依赖保持有效、cancelled不自动满足；真实WRC派发入口重放不报错、不重开完成任务、不重复派发。
- [x] P3 验证与安全部署 -> verify: 聚焦模块、直接调用者、共享合同和mock通过；受控仅重启控制面，保留当前Reader会话；现场只读投影成功且原重复错误停止，提交修复和记录。

不删除原失败、不修改Task/operation/generation摘要，不以显示历史行授权新派发，不扩大
产品验收或审批权限。监工采样器每30秒只写reports，不调用恢复动作；主动干预另行记账。

## 验证

现场只读入口复现；新增回归先4 failed/1 passed，再完成修复。聚焦84 passed，增量
dev-verify基于cb84bef6（已检查base dev计划）344 passed；哨兵53 passed；三流mock
10 passed。未重跑此前已通过的全历史差量，不宣称全量pytest或产品最终通过。
修复代码只读读取现场成功：16 done、50 settled、24 archived bindings；canonical
Task/事件不被改写。当前plan-critic继续审核；部署前保持其会话不变。

## 另外记录，尚未修复

异常producer绕过通用housekeeping去重；RM先因其他恢复候选非空而不收集attention，
之后才按active_effect等抑制候选，存在独立诊断饥饿路径。该事件归RM，OA不拥有这条
基础设施恢复路由。Supervisor已告警但未找到对应诊断派发，不等于修复闭环。
本批只修投影根因，以上问题需独立验证同故障去重、恢复Case/作用域互斥与resident许可，
不能简单解除全部队列条件。本轮监工报告保留这些未决项，不标作已完成。

## 安全加载事实

真实部署前Critic已完成并reject，Run因stage_replan_cap_exhausted进入blocked。
首次run-pause被409拒绝，未执行stop；保留失败回执后，在blocked断点仅重启控制面，
未调用run-resume、未改上限或审核结论。运行基线7d1edef4包含本修复，加载证据
evt-29f26e77e997；源码树、配置和原closed_failed协议摘要核对通过。
原产品pane集合保留；额外AR pane为自动诊断启动。158秒观察无同类新报错，现场投影
保持16 done、50 settled。记录位于运行worktree reports/eight-unattended-20261121T132814Z/
supervision-20261123/projection-deploy.json，副本在本worktree reports/terminal-pipeline-projection/。
本条done不等于最终业务交付；Critic新旧版本混用反证和自动AR诊断继续由监工报告跟踪。
