> 状态: active

# Autoresearch resident 执行进程单实例保护

用户已授权监工发现无法自愈问题时修复。实际同一 datal state_dir 残留5个 resident，evt-71ea54ff069b 与 evt-465aea717e5f 对同一请求重复启动；其中一份准备worktree时读到另一份尚未完成的checkout，拒绝无关暂存，另一份仍运行。父watcher持有sidecar启动锁，watcher退出后旧resident存活，锁不再保护实际执行者。

范围：在resident CLI的实际执行进程持有state-dir级互斥锁，覆盖整段watch/单次execute生命周期；保留父侧启动协调、只读预览、不同state隔离与SIGTERM收尾。锁不unlink，进程退出由OS释放，不接管canonical状态。

- 并发进程请求同一state执行 -> verify: 第二个CLI在run_resident_once前退出且不执行副作用。
- 父launcher退出、执行进程仍在 -> verify: 执行锁仍由resident持有；强制结束owner后下一次可执行，无陈旧PID阻塞。
- 不同state和只读预览 -> verify: 可独立运行；原SIGTERM当前tick收尾测试保留。
- 现场清理 -> verify: 仅终止4个无子任务、命令与启动身份匹配的旧resident；登记3845151及当前3765376保留。新代码部署后在当前loop安全边界重载resident，不打断产品worker。

现场证据：datal/diagnostics/recovery-20261107-1220/autoresearch-duplicate-residents.json。源码隔离worktree实施，测试/实现/归档分别记录，不自动push/merge。

验证：真实子进程回归先2fail（重复执行实际发生），实现后CLI/sidecar聚焦18passed；dev-verify --base dev完整计划含历史815文件，不重复已完成批次，按本次明确paths及直接调用链执行7文件闭包。首轮81pass/3fail因子命令PATH找不到python，使用现有venv PATH重跑84passed；sentinel50passed/1skipped，ruff/diff通过。未跑full pytest或新增真实provider E2E。部署与现有resident安全轮换仍待完成。
