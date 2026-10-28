# FMS 暴露的通用 Task Pipeline P0/P1 修复与断点恢复

> 状态: active

用户批准：在新 worktree 执行复盘中 P0/P1 全量修复，完成后从该 worktree 断点拉起 FMS。基线 dev `237cd7e8`；导入主工作区 47 个相关候选文件的快照，原文件不动。快照/验证证据位于 `/home/min/workspace/public/zaofu-evidence/fms-pipeline-repair-20261028/`。

## 验收闭包

1. P0 执行准入：Plan owner、Flow pool、结果提交身份使用实际配置 -> verify: 多 Flow / 冷启动 / 非法 owner 回归，不能批准后才发现不可派发角色。
2. P0 工作区恢复：重派 epoch 与 Attempt/Operation/guard 一致，旧 watchdog 不抢恢复权 -> verify: 历史事件重放及合法/越界写入回归。
3. P0 输入消费：受控任务输入、分页和重复内容处理不丢语义/审计 -> verify: 原 Package 不变、选取与任务绑定、重复读取/截断/恢复测试及真实首次写入。
4. P0 故障路由：基础设施阻塞不得进入业务 replan 循环 -> verify: workspace/runner 阻塞保持执行诊断路径，真正 scope gap 保留语义重规划。
5. P1 首任务反馈：以现有 FMS-02 的早期反馈/continuation 合同运行，不削减 AC -> verify: 实际输入、首次写入、三条合同命令及独立 Verify 的证据；变更计划须新 Owner 批准。
6. P1 恢复有效性：诊断请求成功与工作进展分开，耗尽只升级一次 -> verify: Impl/Verify 耗尽、重启去重和进展证据回归，未证实推进不得称已恢复。
7. P1 可观测性与版本：Task/Attempt/Run 阻塞与恢复来源可解释 -> verify: 状态投影及 worker 启动 provenance，主项目运行态只经受控服务更新。

## 执行约束

- 通用修复，不按 FMS task ID/领域硬编码。
- 保留完整权威工件、摘要、Q01–Q15、人工审批、独立 Verify、预算和越界保护。
- 不修改 canonical 运行态 JSON/事件，不自动批准待审 Plan。
- 提交本分支，不 push/merge dev；进程从本 worktree 加载代码并继续原项目状态目录，产品源码留在 Kernel 分配的任务 worktree。
- 聚焦测试 + dev-verify impact closure + sentinel + mock E2E；真实恢复单独记录，不把测试通过视为产品完成。

## 复盘依据

FMS run `prepare-TASK-FCA9EC-a1de0a1bd6d60a50`；非法 owner `evt-c98530b694e1`；缺能力 `evt-61f16cba04f7`；缺结果协议 `evt-375cdcbec177`；输入截断 `evt-47492f69ba65`；错误工作区 `evt-8150d96a6b01`；错误语义重规划 `evt-f93b793584b7`；恢复语义缺失 `evt-6d552219c8c8`；耗尽 `evt-ed319a97b2f0`。

## 实现与验证进展

P0/P1 代码与方法修改已经实施：角色闭包预检、多 Flow pool、结果协议、重派 placement/guard、依赖变更 CAS、输入选择与完整分页、批量别名鉴权、基础设施故障隔离、Impl 耗尽/恢复所有权、Task/Run/Operation 分层投影，以及恢复工作区与原生压缩连续性。两份 skill 的 source / Codex / Claude 三份内容同步。

- 同一批 50 项定向回归 A/B：基线 dev `237cd7e8` 为 36 failed / 14 passed；修复版 50 passed。此为确定性故障回归，不代表真实产品吞吐 A/B。
- dev-verify 自动全量确定性层：13132 passed / 21 skipped / 5 failed；5 项均在原基线复现，原因是公开仓库缺少 `tools/export-public.sh` 与指定 prompt。未放宽测试或伪称全绿。
- serial：10 passed；sentinel：50 passed / 1 skipped；flow smoke：10 passed。
- 追加恢复连续性修复前 5 failed / 5 passed；修复后连同直接调用与上下文回收闭包 108 passed。该追加改变没有再次跑全套，而是跑对应闭包和最终 sentinel / flow smoke。
- bounded agent / Task Pipeline mock E2E、状态投影、升级调用链：41 passed / 1 skipped。Skill / 指令 / 结构检查：39 passed / 2 skipped。通用 skill quick validator 不认识本仓库既有扩展 frontmatter，未改写既有元数据；以仓库检查验证。
- 使用本 worktree 的代码对 FMS 最新候选执行机械预检：29 tasks / 0 errors；run contract digest 不变。

主工作区后续新增的恢复连续性候选经单独快照、复现及验证后纳入；未改写主工作区，未纳入它后续的其他并行工作。详见外部证据目录中的 `context-continuity-import.json`。

真实恢复尚未验收，因此任务保持 active。最新 Plan `evt-b3151b32f2c8` 仍需用户人工批准；不得用旧版审批替代。下一步是从已提交 worktree 启动原 FMS 控制面，核对代码/CLI provenance 和待审断点；获得该版本批准后才推进新 generation，并观察实际输入、首次写入、合同命令和独立 Verify。
