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

实现提交：`8acb5eb8 fix: repair task pipeline admission and checkpoint recovery`。

运行态后续已出现 `operator` 审批 `evt-7dc7ad6df62a`（13:49 UTC），对应最新 Plan `evt-b3151b32f2c8`；本次修复会话没有再次批准。新 generation `d3a9329098cf63effdc674dfbafd8ffe5821320820f4d5b6082b17839f96ac4c` 已派发 FMS-02。13:57 UTC 使用仓库的 control-plane-only actuator 将 watcher 切换到本 worktree；PID `1172668`，`PYTHONPATH` 与 `ZF_CLI_CMD` 均指向本 worktree。保留当前 Attempt `ta-ed574070a8c45aec4308` 和已读页。

项目中两份 skill source 已备份后部署；materialize=copy，正在执行的 worker 保留原派发的 skill 副本和 CLI briefing，后续派发采用本 worktree CLI 与新 skill。不能将这次接管描述成整个当前 Attempt 都由新版本从零执行。

重启后只读投影为 Run active / FMS-02 in_progress / impl_active、单一活动 Attempt、无 blockers。配置、人工审批与原产品工作区保留；未修改 canonical store 或伪造状态事件。输入下发体积由 4144990 字符 / 514 页变为 538042 字符 / 68 页（8192 字符分页），约减少 87%；原包与完整矩阵仍保留。

## 真实断点恢复证据（2026-10-28 14:14 UTC）

- 必读输入的 68 页已经完成；规范读取查询返回 `missing_reads=[]`、`protocol_repair_required=false`。完整结果见外部证据目录 `fms-current-read-compliance.json`。
- 14:08 UTC，worker 在 Kernel 分配的 `FMS-02-61331d39782c/g1/project` worktree 首次写入导航测试，事件 `evt-c028a0543161`；该测试先因实现缺失失败（`evt-7d5b1f8933d0`），随后补充实现并得到 3 passed（`evt-15b25388c82b`）。14:10 再次测试仍为 3 passed（`evt-dd856629100f`）。
- `npm run build`（包含 `tsc --noEmit` 与 Vite）成功，事件 `evt-9780565b8a2d`；14:11 UTC worker 提交产品代码 `74e2582 feat: establish design sample shell and navigation foundations`，事件 `evt-a46b71689b95`。这是产品任务提交，和本修复分支的实现提交分开记录。
- 14:14 UTC 投影仍为 Run active / FMS-02 in_progress / impl_active；活动 Operation、Attempt、Session 各 1，blockers 为空。watcher PID `1172668` 心跳正常，代码与后续 CLI 来源仍为本修复 worktree。
- 事件、cwd、产品 HEAD、关键文件摘要、进程来源与投影合并存档于 `fms-real-write-and-build-evidence.json`；完整投影为 `post-implementation-pipeline-snapshot.json`。这证明已从控制面恢复推进到真实写入、测试、构建及提交。

修复实现与断点拉起已交付。验收项 1–4、6–7 的确定性回归及真实首次写入已核验；验收项 5 的早期反馈已发生，但三条完整合同命令及独立 Verify 尚未验收，因此任务保持 active、不归档 done。运行进程继续保留，不因本轮交接暂停。当前尝试跨越控制面版本切换，不能把这些结果描述成从零运行的新旧真实 provider A/B，或整个产品已经完成。
