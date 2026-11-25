# v5 正式派发与专属文件视图

> 状态: done · `bfbda3b3 feat: deliver scoped workflow inputs and recover blocked candidate reviews`

编译 v5 项目并派发 -> verify: 注册角色、命名空间与新 request 的 cgroup 一致，旧 request 重放回 CLI。

在独立 tmux / 用户 systemd 下启动 -> verify: 真实 PID 在独占 scope，重复启动被拒，其他角色保留原位置。

v5 配置编译、真实 tmux/systemd 绑定和 Codex/FUSE/正式 CLI 验证完成，40/40 必读、82 次实际文件读取，进程中断后复用原 SID。 详见 [实施记录](../../docs/impl/workflow-inputs-and-milestone-recovery.md)。
