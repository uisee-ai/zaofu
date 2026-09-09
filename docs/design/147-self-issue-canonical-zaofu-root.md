> 状态: accepted

# Self-Issue 统一使用 ZaoFu v7 源码根

## 决策

Self-Issue 的“当前项目”只负责保存该项目的运行状态、Issue 镜像和事件
账本；Issue 诊断、只读评估、Fix worktree 与 Verified Candidate 的目标根
统一解析为运行中 ZaoFu 包所属的 checkout 根（`/home/uisee/zaofu_v7`）。

普通项目工作流不受此规则影响。这样从任意注册项目点击 `/issue` 或
`Start Triage` 时，评估快照与后续 Fix 不会因为产品项目的 `src/` 为空而
误判为缺少 ZaoFu 源码。

## 实现约束

- 运行状态仍使用选中项目的 `project.state_dir`，不把不同项目的事件账本
  合并到 ZaoFu 根目录。
- 受锁定 Self-Issue policy 的服务和 Orchestrator 将 `project_root` 解析为
  当前运行时 checkout 根；未启用或未锁定的单元测试调用保留传入根目录。
- 新初始化项目写入 `self_issue.ingress.target_root: zaofu`。这是符号值，
  由运行时解析，不把机器绝对路径写入可移植配置；旧的 `.` 配置仍可加载，
  但工作区锁定 policy 会在继承时改写为 `zaofu`。

## 验证

- 锁定 Self-Issue policy 从产品项目路径构造服务时，解析根必须是运行中的
  ZaoFu checkout。
- 未启用 Self-Issue 的隔离调用仍使用测试传入的临时根目录。
- 工作区 policy 继承和新项目初始化都输出 `target_root: zaofu`。
