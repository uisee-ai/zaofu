# 连续执行：环境准备身份与安装互斥

对应 ZF-SPEC-001 F11，[总实现记录](continuity-handoff-spec-001.md)。安装资格与执行持有者分别验收；当前已接入本机 tmux 原生 Provider 和 stream-json 回合，完整资源图仍未完成。

## 配置与行为

```yaml
project:
  name: example
  scripts:
    setup: ./scripts/prepare.sh
  setup_inputs:
    - data/dataset-identity.json
    - tools/runtime-versions.txt
```

`setup_inputs` 是相对于每个实际工作区的文件路径。项目自行产生工具版本、数据版本或摘要文件；
runtime 只检查存在性、路径边界与内容摘要，不判断某数据集或工具版本是否适用于业务。
目录、绝对路径、父目录穿越、越过工作区的符号链接被拒绝。声明文件缺失时，旧就绪标记失效，
安装脚本不执行；准备中输入发生变化也不发布就绪。大文件按块计算摘要，不载入模型上下文。
仅声明该字段不代表已经准备环境，须与 `project.scripts.setup` 一起使用。

既有依赖清单摘要继续生效，并纳入 `.python-version`、`.node-version`、`.tool-versions`。
未通过文件声明的工具二进制或外部数据变化不在本轮自动发现范围。
Task workspace、Role workdir、Candidate 准备入口均消费配置，兼容输入不会重复执行已有 setup。
Candidate 的既有 `force=True` 验证行为保持。

## 锁与 bootstrap

安装 mutex 按物理工作区定位，角色 marker 路径和目录别名不能绕过它。
Git 工作区的准备记录置于对应 Git 元数据区域；非 Git 目录使用本地 `.zf-setup-state`。
这是安装回执与锁，不是新的 Task/Run 状态库。setup 与 uv bootstrap 共用该 mutex；
遇到冲突返回失败/等待，不运行第二个安装器。不同 Git worktree 不因共用仓库而串行安装。

uv bootstrap 仅在命令成功、输入仍匹配、确有本地 `.venv` 时记录 ready；回执绑定目录 path/device/inode。
失败后残留 `.venv` 不代表成功，失败/中断回执支持下次准备重试。
本地 bootstrap 失败不会改用另一个 checkout 的 `.venv` 符号链接。
既有外部环境、被替换的环境或损坏回执被保留，不发布虚假的复用成功，也不自动原地重建。
bootstrap 仍是 best-effort；强制环境资格应使用项目声明的 setup 与实际验证 gate。

## 验证与未覆盖项

- RED 复现：不同角色 marker 同物理目录并发安装；失败安装留下目录却返回已 provision。
- 定向回归：818 passed、7 skipped；跳过项是既有 loader 字段别名 allowlist，不是新增验收。
- 直接调用方：281 passed，覆盖 Candidate、SpawnCoordinator 与 Task Pipeline generation。
- sentinel 53 passed，flow smoke 10 passed。
- 实际 TaskWorkspaceManager 回归：身份改变重做必要准备；不变不重装；缺文件拒绝；未提交候选原样保留。
- 只证明安装互斥，不证明活跃 worker 或脱离父进程的工具已释放环境；共享依赖目标持有者和安全重建事务仍待实现。
- 未执行真实 Provider、生产环境重建或 Datal 恢复。没有把 F11/B05 标为完成。

原始日志与检查清单保存在本地 `reports/continuity-environment-20261115/`。

## 2026-11-15：执行持有者与清理互斥

`TmuxTransport.spawn` 对 Codex/Claude Code 的实际启动命令加入 stdlib exec 包装器；
包装器取得物理工作区共享锁，以可继承文件描述符执行 Provider。多个 worker 可同时持锁。
`StreamJsonTransport` 在 SDK drain 期间持有同一共享锁；退出上下文后释放。
安装、bootstrap、Role/Task cleanup 和 `zf stop` 的工作区删除路径取得独占锁。
锁冲突时拒绝启动、安装或删除，不把 canonical session 计数为零当成资源无人使用的证明。

兼容 setup 可在共享锁内只读验证 marker 与输入摘要后直接复用；强制重装或身份变化必须
取得独占锁。provision 遇到使用者占锁时，只允许复用现成目标；Python bootstrap 还须验证 ready 回执、输入摘要及本地环境 inode。缺链接或资格变化返回等待，不执行安装。不同物理 worktree 相互独立。锁不可用时失败上浮，不跳过保护。

资格限制：这是一台宿主上的 advisory lock，不是分布式资源租约或完整写入隔离。
原生进程必须保留继承描述符；主动关闭描述符、未经过该启动入口的进程、脱离 Provider 的
后台工具和指向其他工作区的共享依赖目标不在完整保护范围内。SDK 锁由控制器持有，
控制器被强杀后孤儿子进程的回收仍需独立故障域验收。准备中资源保留、正常持有者退出
后的重试已测，不宣称上述未知情形已安全隔离。

定向用例含真实 mock Provider 子进程、长 stream query、多个共享持有者、安装优先占锁、
兼容复用、身份变化、独立 worktree 和清理拒绝。详见
[本增量报告](../../reports/continuity-runtime-20261115-review.md)。

后续 P0：[共享资源占用、必需输出准入与实例回执](continuity-environment-resources.md)。
