# Issue 上报源码与开发执行项目分离

> 状态: implemented

本决策替代 [147](147-self-issue-canonical-zaofu-root.md) 中强制统一源码/Fix 根目录的规则。

## 配置与界面

用户设置中的调查源码目录仍使用 `self_issue.source_mode` 与 `source_root`，
仅控制 `/issue` 上报评估。既有配置和会话源码绑定不变。

设置 → 开发者 → 诊断与修复项目使用项目独立字段：

```yaml
self_issue:
  source_mode: explicit_target
  source_root: /checkouts/report-source
  execution_project_root: /checkouts/developer-project
```

`execution_project_root` 省略或为空时，默认当前打开/上报项目的目录；
相对路径按该项目目录解析。目标必须是包含有效 `zf.yaml` 的本地项目，
运行状态使用目标配置的 `project.state_dir`。发布仓库仍由独立的 forge
配置控制。锁定发布策略的工作区继承不会覆盖这两个本地目录设置。

开发设置接口 `GET/PATCH /api/projects/{id}/settings/issue-execution` 要求
后端验证的开发者身份；PATCH 另检查同源、请求大小、配置摘要 CAS，
只更新当前项目 `zf.yaml` 的执行目录并记录 `settings.issue.updated`。
用户身份不能通过普通设置接口写入此字段。

## 执行归属

- 手动 Start Triage：尚未入队的 Issue 使用当前打开项目的执行目录配置。
- 自动接入：若 Issue 有本机已注册项目的发布回执，使用该上报项目的执行
  目录配置；GitHub 上独立创建、没有本地发布回执的 Issue 使用轮询项目配置。
- 本地发布尚未完成、只有正在发布的可信 intent 与远端 marker 时，暂缓自动
  入队，等待发布回执；远端文本本身不能指定或授权本地路径。
- 本地账本已记录 Triage 归属后，后续重试、诊断、Fix 提案、验证和交付沿用
  该项目。修改设置不迁移已有 Issue/Run，包含旧的排队请求。操作者先明确取消
  再手动 Start Triage 时属于新入队，使用当前打开项目的新设置；自动轮询不会
  复活已取消的任务。
- 同一个 forge 仓库的入队路由与写入通过工作区文件锁串行化，避免两个项目
  同时抢占。索引按账本 manifest、发布存储文件指纹缓存，可随时重建。
- 跨项目只复制受摘要校验的 Issue 镜像正文/评论，使用 `IssueMirrorStore`；
  新任务和事件由目标项目的既有 intake、TaskStore、EventWriter 创建。
- Triage 合同、源码 revision 和 Fix 的 source/target root 均绑定开发项目。
  Orchestrator 与 Candidate Delivery 使用所属项目根，不再读取上报源码配置。

新设置无需重启即可供新接入使用。首次部署新代码仍需重启旧 Python Web
进程；目标项目的实际执行仍由其正常运行器完成，设置/入队不会暗中启动 Agent。

## 验证

`tests/test_issue_execution.py` 覆盖配置默认值/继承/CAS/权限、跨项目自动和手动
接入、发布等待、任务与合同落点、Fix 提案以及设置变更后的归属保持。
`web/tests/settings-workspace.spec.ts` 覆盖开发者入口、错误反馈、保存、刷新和恢复默认。
相关 ingress、clarification、delivery、Web mirror 与源码设置回归继续适用。
