# 连续执行：环境准备身份与安装互斥

对应 ZF-SPEC-001 F11，[总实现记录](continuity-handoff-spec-001.md)。本增量不宣称环境实际使用者的生命周期锁已完成。

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
