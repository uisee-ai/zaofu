# 受限授权的无人执行资格

对应 [实施任务](../../tasks/active/2026-11-16-1258-unattended-qualification.md)、本地 ZF-SPEC-003 和 [前序实现](continuity-handoff-spec-001.md)。基线 2137a567。

## 已接入的入口

- `zf recover host-unit --name zf-<project>` 生成用户服务；`--install` 安装并 enable，不自行启动未获授权的 Run。`ExecCondition` 调用现有 host-check；意外退出 restart，pause/stop/completed 拒绝启动。重启上限 5 次/300 秒，超限保留 systemd failure，不能宣称无限自愈。
- 用户服务保留明确的项目、state、Python、源码、PATH 和私有 tmux 位置，不持久化 token。节点启动依赖 user systemd；`loginctl show-user "$USER" -p Linger` 可检查是否无需登录启动。真实节点重启必须独立验收。
- 源码修复 actuator 在受服务托管时先核对 MainPID，只使对应控制器异常退出，由原服务接管启动。不会 exec 一个脱离原服务的新控制器。
- 活跃期的 verified checkpoint apply 同时要求 `source_repair.restart_policy: operator_approved`。
  `never_during_active_run` / `next_run` 在修复写入之前阻断并留下原检查点；验证成功不等于获准重启。
- Run Manager tick 在 `auto_execute` 且 `runtime.host_verification.enabled` 时消费已结束的不可用验证。使用现有合同/currentness/资源锁/manifest 和回执通道，不授予 Worker 宿主权限。

## 预授权测试边界

```yaml
runtime:
  host_verification:
    enabled: true
    approval_ref: owner:<具体批准记录>
    image: sha256:<本机已有镜像完整摘要>
    commands: ["npm test", "npm run build"]
    max_executions_per_run: 12
    timeout_seconds: 180
```

默认关闭。只执行上述精确命令与冻结合同的交集，使用固定镜像，不 pull。
默认执行在无网络、只读根文件系统、无 capabilities、受资源上限约束的容器内，非 root UID；
浏览器需要宿主网络时须在 zf.yaml 显式设置 `network: host`，依然不共享 Docker socket/宿主凭据。
`protected_files` 可固定验收脚本摘要，修改或删除会在启动容器前拒绝。
只拷贝审查过的源文件和声明依赖，不挂载 Git、运行状态、宿主 HOME 或 Docker socket。
目标和依赖摘要在调用前后复查；输出和证据保留目标、命令摘要、镜像、授权和来源。
图片/视频等原始 bytes 以带内容摘要的 base64 sidecar 保存，可无损提取；宿主回执不颁发产品 verdict。
一次 tick 至多一条有界命令；中断且无终态的副作用不可盲目重跑。

Candidate Reader 在原冻结目标的预授权采证完成后，使用现有受控 Reader infrastructure retry，
将回执反馈给独立 Reader。Task Pipeline 保留自身 rework 归属。临时宿主 `host-assist` 的 token gate 保持不变。
成功和失败的已完成采证都要交回 Reader，由它区分产品失败与基础设施失败；不能只消费 exit 0，
让失败证据停在原地。未决执行、目标不稳定或缺少退出码仍拒绝自动交接。

## 工作区和诊断生命周期

普通 Task、Candidate 和 OA 派发都明确：产品 checkout 受原 write scopes 限制；Reader 源码/证据只读；
只有 Kernel 提前创建的当前 operation scratch 可通过原生 Edit/apply_patch 原地更新。
scratch 在 checkout 外不是整个 runtime 目录的写授权。canonical state 仍只能经受控 CLI 提交。
原生 apply_patch 仅以 exec_command/Bash 适配提供时，说明明确指向已有守卫支持的单一、
引号 heredoc Update File patch；不放宽目标、添加/删除/移动或动态 shell 写入。
Task 派发消息显式请求合同要求的本地 writer commit，仅限分配分支与路径；
Reader 不因此获得写权限，不授权创建分支、merge/push 或绕过 Provider 拒绝。

OA 的 periodic liveness sweep 同时检查“进程仍活着但 Provider 回合已经结束”。
只有当前 operation 开始后的同一 session/turn 有明确关闭事件、没有更新的活动回合、
没有已准入结果且 Run 未暂停/终止，才保留原输入/operation 并重投一次检查点。
重投复用读取回执与游标，不能跳过尚未读取的正文；再次缺结果则 operation blocked，
释放 OA 队列供后续诊断，不伪造语义 verdict。仍活跃或身份不明的 Provider 不受干扰。

AR 启动前保存包含工作区、命令、阶段、PID/start ticks/boot ID 的 checkpoint sidecar，账本绑定 digest。
resident 重启后只在证实原 owner 已退出时，将悬空诊断明确关闭为 interrupted，保留候选和检查点并升级；
不将 `loop.started` 当作完成，不重放未经新决定授权的命令。已有 self-repair launch/result、验证及合并回执
继续提供后续阶段检查点，未新建另一套任务状态。

`runtime.autoresearch_resident.on_parent_terminal` 支持 preserve（默认）、cancel、continue。
preserve/cancel 终止进程且保留磁盘成果；继续运行需要新的受控决定，不能自动把不完整报告判成功。
continue 仅在主目标完成后移交已运行的 resident，保留其 singleton lock，队列排空后退出；显式 stop/pause 优先。

## 资格记录

证据写入本 worktree `reports/unattended-qualification-20261116/`。必须区分确定性回归、真实宿主能力和完整真实 PRD。
当前宿主、容器测试不能替代新 PRD 到 `run.goal.completed` 的验收。每轮临时干预记录为该轮无人资格失败；
修复后再启动新轮，不用旧轮结果冒充新源码全流程通过。

## 自动修复的额外隔离修正

旧 self-repair 启动器曾使用 Provider sandbox bypass 参数；本批移除，Codex 固定 workspace-write。
`runtime.run_manager.source_repair.model/model_reasoning_effort` 显式选择模型，真实验收配置 Terra High。
`publish_candidate: true` 为独立 opt-in：只有已退出进程、冻结合同、原 base/修复分支、精确 scope/allow/deny、
空 index、无活动 Git hooks 都成立，才由 runtime 提交候选。Worker 不需要获得共享 Git 元数据写权限。
briefing 移到 required sidecar，避免污染修复 worktree；创建修复分支不再使用会重置已有分支的 `-B`。
提交只是候选发布；独立 verification、受控 apply、源码加载及续跑保持原有门禁。
RecoveryCase 对 validate/apply 的成功保持 open，不能把中间步骤标为原目标 resolved。

自动 repair 使用的 `dev-verify` 保留完整影响闭包，不再因为超过 80 个测试文件而扩大为全仓测试。
仍包含直接调用方、共享契约和串行测试；未映射源码仍拒绝。全量发布验证应显式执行，
不能让文件数量触发无关失败并阻断修复。该修改经 81 文件闭包回归先失败后通过。

修复请求 continuation 与合并后的 `workflow-batch-resume` 必须携带同一 `recovery_case_id`
及原 Run/Task/代际/合同版本/目标提交/attempt 绑定。否则实际恢复后会关闭一个新生成的 case，
留下原 case 悬空；跨修复、合并、续跑及 effect 投影的回归覆盖此问题。

真实 PRD 第一轮暴露了原文交接缺失：验收驱动应传 `source_ref`（不是 CLI 风格的 `from`），
Plan/Critic 输入编译也必须从正式 `source_refs.source_ref` 物化原文，而不是只传带路径的
Requirement 摘要。原文与需求快照同时绑定为 required 输入，保留各自摘要；后续源文件变化
不改变已签发正文。受控需求修订继续按既有 amendment 规则替代旧权威输入。

真实续跑还暴露了 OA 示例歧义：`work_unit_id` 是单元标识，不能用裸 `impl` / `verify`
冒充阶段选择器。pre_impl 的结果模板现在展示 `task_ids` 与 `stage_ids`，要求引用
已批准 Plan 的任务。仅改善生成方法；未知任务仍由原准入规则拒绝，不放宽权限。
