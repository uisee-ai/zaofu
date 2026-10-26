# 远端 dev 与本地 workflow graph 集成

本次同步基于本地 `39ba0d8` 和远端 `36c3bd3`，两侧独有提交分别为 23 和 93。
在独立工作区执行 `git pull --no-rebase --no-ff --no-commit origin dev`，解决冲突后再合入本机 dev。

## 冲突处理

- 保留 AI SDLC Graph 设计 187；远端 BMAD 设计改为 [188](../design/188-bmad-inspired-epic-task-map-and-tdd.md)，更新索引和引用。原任务编号及历史记录名称不改。
- `zf start` 保留 `make_watch_callbacks` 接线，委托 `process_watched_batch` 处理不可变事件批次。模拟终态仍先完成 Kernel 结算再停止。见 [批次唤醒](../impl/watcher-batch-wake.md)。
- Candidate 合并远端阶段/验证责任筛选与执行上下文证据，同时保留本地 contract 命令超时；回归同时检查非默认超时、上下文引用及下游验证延迟。
- Plan 审批统一身份计算，绑定完整任务 contract、Run、generation 和评审产物，保留相同候选去重、不同候选重新审批和显式 owner 审批。两侧同名测试分别保留，覆盖真实 writer 入口。
- Reader replan 同时保留远端因果 fanout 追踪和本地正在进行的 Candidate replan 保护；测试补齐真实 fanout 来源，不放宽重试上限。

## 验证范围

风险级别为 high：远端批次跨 Event、Config、Store、编排、恢复及 Web 边界；343 个路径含远端设计、技能、已提交任务记录和评估样本。
自动合并路径和人工处理路径已核对，没有混入主工作区的 `loshu-fms/`。新增行密钥模式及敏感路径检查未命中。

`dev-verify plan --base dev` 选择扩大确定性回归。Plan 指纹新增模块通过真实调用链测试
`test_plan_approval_candidate_identity.py`、`test_plan_approval_merge_identity.py` 显式映射；
BMAD 冻结样本通过 `test_plan_readiness_skills.py` 的方法合同及样本自身 3 项 unittest 复核。
样本带有供模型评估的已知缺陷，其自带测试通过不代表样本产品验收通过。

浏览器使用 `mcp/playwright:latest`、host network、隔离 workspace 和 `0.0.0.0:8024`。
首次失败源于隔离 workspace 尚未完成 onboarding，后通过受控 onboarding action 仅在该隔离目录跳过初始化，
使用仓库 Plan review 配置重跑 7 项全部通过（46.3 秒）。覆盖审批身份等待、审批/拒绝、完整正文、
附件、迟到响应隔离、长文分页及 Markdown 下载。没有调用真实 provider。

已通过的验证：冲突集中回归 129 项；pre-merge 哨兵 51 项；串行锁/会话 10 项；
三流及任务流水线 smoke 10 项；前端 typecheck、全部 unit、production build 和 bundle budget。
29 份变化的技能元数据通过 kernel 元数据读取器校验。

首次扩大确定性回归在 12 项失败后停止，4,569 passed、17 skipped：10 项由调用解释器时
未把 `python` 加入 PATH 引起，1 项旧 fallback 测试仍期望二元组而未包含既有 300 秒超时，
1 项为并发负载下模拟 transport 的 1 秒开始等待超时。补齐进程 PATH、将 fallback 断言
更新为完整三元组后，12 项全部复核通过；没有改动 transport 代码或放宽计时断言。
初次扩大回归并非全量通过。第二次在 6 项 controller 测试失败后停止，
6,046 passed、17 skipped；这 6 项在合并前 `39ba0d8` 独立复现。根因是 PRD/Refactor
测试复用仅适用于 Issue 的需求 fixture，缺少必需的 target_root/source_root，正确触发澄清。
补齐测试输入后 controller、proposal、synthesis、prepare 四个模块的 70 项测试通过。
没有改变缺失输入必须 ask user 的运行时行为。
最后一轮完整跑完：12,737 passed、20 skipped、9 failed，用时 908.59 秒。逐项回执保留于合并操作记录。
这些失败不能笼统报告为全量通过，处理如下：

- 启动等待用例在并发负载下再次超过 1 秒；并行回归结束后，transport 模块串行 24 项通过，计时断言保持不变。
- 远端原始 `36c3bd3` 上复现 Candidate 重试 fixture 缺少已准入的验证 sidecar。测试补齐与 TaskRef、contract、Run 绑定的证据后，重试、证据门及 Candidate 增量集成共 38 项通过，生产证据门不变。
- `runtime.start.failed` 缺少中文渲染映射，补充提示后渲染和直接调用方 49 项通过。
- 归档引用检查在尚未生成 merge commit 时看不到远端祖先提交；远端父提交确实包含这些引用，必须在合并提交生成后再复核。
- 4 项公开导出测试缺少 `tools/export-public.sh`，1 项 prompt 测试缺少 `prompt/2026-08-05-1559-kanban-agent-five-workflow-real-e2e.md`。这两个路径在两个父提交及原始主工作区都不存在，对应测试未被本次改动。保留为 5 项既有基线缺口，不跳过测试、不虚构历史文件，也不把它们报告为通过。

本记录在提交前形成；归档祖先及 dev 落地门禁在合并后另行保存回执。

主 dashboard 与赛车预览服务没有重启。
