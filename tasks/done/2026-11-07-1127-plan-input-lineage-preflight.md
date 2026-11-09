# 计划预检复用受控后继输入来源

> 状态: done — `64c7d194 fix: validate inherited task partitions against pinned plan lineage`

LS-v1 Q04候选保留原始task_execution_input摘要；实际Handoff已能从显式前任继承分区，预检却只查新TaskID并拒绝TODO后继，造成有效历史输入无法继续进入新计划。只修复来源证明与预检不一致，不消除旧矩阵/GOAL04/路径冲突错误。

1. 固定包来源与真实预检入口复现 -> verify: 新回归先红。
2. 原始输入保持相同body，基线包同Run/摘要正确；新后继须当前显式替换，保留后继须历史合同逐字段不变并找到原准入替换声明 -> verify: 追溯仅选原shared及原任务分区；跨Run、缺来源、改合同、改输入、歧义、循环、损坏摘要拒绝。
3. 实际预检与Handoff消费语义一致 -> verify: 输入/包/结果提交/来源边界闭包、sentinel、mock；LS只读回放计数与剩余错误分别记录。
4. 合并dev并重载控制面 -> verify: 加载证据与在途worker保留，不能声称整个计划或产品已通过。

关联：[199实现](../../docs/impl/199-worker-recovery-implementation.md)。


## 验证

真实preflight入口2正例先红/7负例通过；增加代次及Run合同负例后，新测试11项，相关73项初次通过，11文件闭包171 passed，dev-verify自动45文件857 passed，sentinel53与flow smoke10通过。没有全量pytest/真实Provider。来源检查在独立小模块，复用Handoff同一替换边和分区算法；旧Task必须逐字段相同才能沿历史包查找原替换声明。

真实LS只读回放确认task_execution_input的所有后继分区诊断消失。Planner在期间重试并调整文档路径，故另冻结同一候选到/tmp/zf-q04-preflight-comparison-20261107：修复前52、修复后51，差集仅task_scoped_input_invalid；其他矩阵、GOAL04、路径、文档缺失诊断保留，不宣称计划获准入。结果记录在LS docs/records及该独立比较目录，未改写任何运行时包。

部署：dev c7402991，合并后75+53通过；11:33控制面watcher2830160加载新源，runtime-load f8c1c9a05be2489d8673b58c5967d36e。前后所有worker pane/PID保留；主线实际preflight对冻结候选仅移除原始分区误报，其他51条保持。产品计划仍未准入。
