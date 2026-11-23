# v5 正式派发与专属文件视图

> 状态: active

编译 v5 项目并派发 -> verify: 注册角色、命名空间与新 request 的 cgroup 一致，旧 request 重放回 CLI。

在独立 tmux / 用户 systemd 下启动 -> verify: 真实 PID 在独占 scope，重复启动被拒，其他角色保留原位置。
