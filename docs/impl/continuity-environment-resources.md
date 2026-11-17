# 连续执行：共享资源占用与必需环境准入

承接 [环境准备](continuity-environment-preparation.md)，对应 Spec F11 和 F13 的受控环境路径。
本次借鉴 LoopForge 的资源所有者、环境实例与准备回执思想，保留造父的任务源码隔离。
不将所有并行写任务改成共用一个 checkout；不增加角色私有的语言运行时安装。

## 配置与执行边界

```yaml
project:
  scripts:
    setup: ./scripts/prepare.sh
runtime:
  workdirs:
    provision_paths: [.venv, web/node_modules]
    required_paths: [.venv, .venv/bin/python, web/node_modules/.bin/tsc]
```

`provision_paths` 保持原来可选的准备语义。`required_paths` 是新增的显式必需输出清单，
默认空列表；只能声明非空工作区相对路径，拒绝绝对路径、父目录穿越和工作区根目录。
不要把本仓库语言依赖直接当成所有目标项目的要求。配置载入/渲染和 registered controller
生成均保留该字段；本批未修改正在运行的项目配置，也未全局启用示例中的依赖要求。

TaskWorkspaceManager、Role WorkdirManager 在 provision/setup 后检查清单；
实际 Task Pipeline dispatcher 在该检查成功前不会创建 Provider operation。
Candidate 在环境准备报告中明确返回 failed，沿用 harness_environment 修复责任，
不继续运行质量检查。坏链接与缺失文件不能满足清单。
Python 的自动本地 bootstrap 若没有显式成功 setup，`.venv` 及其子路径还要求当前的
成功 bootstrap 回执，安装失败残留目录不能成为 ready。
显式 setup 的内容和工具验证由项目决定；runtime 验证命令结果、输出存在性和身份，
不从目录名称猜测业务兼容性。真实 Node/Python/Go 版本、ABI、依赖内容资格仍需 setup/gate。

## 共享资源身份与锁

provision 会为目标工作区及存在的来源工作区注册声明的依赖根；required 路径也纳入声明。
资源声明保存在对应 Git 元数据区域的 `resources.json`，非 Git 目录使用既有准备元数据目录。
该文件用 sanctioned atomic writer 保存，是环境控制的必要元数据，不是 Task/Run 状态库。
坏 schema/JSON 失败阻断；声明扩展需要工作区独占锁；旧声明保守保留，不据此自动删资源。

每次受控执行/修改同时锁定：工作区身份，以及声明路径解析后的实际资源目标。
Git 资源锁放在 common Git 元数据的 `zf-resource-locks`，按实际目标绝对路径摘要定位；
非 Git 资源使用系统临时目录下当前 UID 的私有 `zf-environment-locks-<uid>`，权限校验为 0700；参与者需使用同一宿主临时目录命名空间。获取锁不会创建缺失的依赖父目录，避免制造“目录已安装”的假象。锁文件位于依赖目录之外，避免安装器
替换目录时换掉锁 inode。嵌套父目录创建不改变 Git 资源锁身份。

安装和清理持独占锁，执行持共享锁，兼容复用可只读检查；并行使用者不会彼此排斥。
所有锁采用非阻塞获取，冲突释放已取得的锁后返回等待/失败，不持部分锁无限等待。
原生 tmux Provider 的 exec 包装器继承全部锁描述符；stream-json 沿用回合生命周期锁。
发送到执行入口前的资源计划绑定声明摘要、实际目标、device/inode/mode；取得锁后重检。
软链接重定向、同路径目录被替换、声明变更时拒绝使用旧启动计划。

## 就绪回执与恢复

setup 成功后除原脚本/依赖输入 marker 外，再原子保存声明资源的目标与实例回执。
就绪复用必须同时匹配脚本/输入身份及资源实例。没有声明资源的旧项目保留原兼容行为。
目录被替换或删除时，旧回执不再证明就绪：仍有执行者时等待；无人使用时可重新运行
项目 setup。准备失败不发布新 ready，源码和未提交成果保持。测试覆盖缺目录后通过
idempotent setup 恢复，并证明 candidate 文件未被覆盖。

## 已验证与资格限制

- 实际 mock 原生 Provider 占用共享依赖时，兄弟工作区和来源工作区 setup 都不能修改。
- `zf stop` 不删除被其他工作区使用其依赖的源 worktree；已有脏成果保护保持。
- 两个消费者可并行；已有依赖可复用；重定向和替换实例会拒绝旧启动计划。
- Task、Role、Candidate 实际调用方和 Task Pipeline 派发入口均覆盖失败反例。
- 配置异常、注册工作流配置继承、失败 Python bootstrap 残留、就绪失效恢复均有测试。

限制：这是本机、受控入口的 advisory lock。任意 shell 命令直接修改依赖、未声明的外部
资源、hardlink/bind-mount 别名、脱离 Provider 的工具以及跨宿主资源尚未全覆盖。
父子资源根若声明不一致，不代表已实现完整目录层级冲突分析。目录身份不证明其内部所有
包内容未被改动。本轮不建立跨任务通用 venv 池，也不承诺独立新任务绝不需要本地安装。
控制器被强杀后的孤儿工具隔离、准备检查到业务使用的完整持久租约、环境蓝绿切换与回滚、
部署/清理全阶段事务仍待实施；不把本批当作 F11/F13 整项关闭。

验收记录：[环境 P0 报告](../../reports/continuity-environment-p0-20261115-review.md)。
