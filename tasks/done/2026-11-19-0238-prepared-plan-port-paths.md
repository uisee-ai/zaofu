> 状态: done — `1e3bb5d6 fix: validate prepared plan port paths before critic dispatch`

# Critic 前关闭 Plan port 引用与 Package 的路径差异

来源：混合真实验证 workflow-5d021bd50a5ec52b，Critic通过evt-a93e87db63f6后，Package在evt-5c7c9c0f923f以SidecarRefError拒绝。Planner的requirement_spec显式port使用workflow-requests下的绝对路径；预检可读但Package不可用，浪费完整审阅后才返工。用户已授权发现问题后修复、mock先行和继续真实验证。

范围：在已完成生产者文件归档、准备派发Critic的入口验证显式port的sidecar路径合同。保留生产者工作目录文件正常归档、inline body及已有合法sidecar；不放宽安全根、不替换摘要、不修改活动Run工件，不把语义判断放入Kernel。

- [x] 重现绝对及非sidecar根requirement port在实际Critic派发入口被错误放行 -> verify: 新回归在当前dev失败，并且复现绑定真实日志形状。
- [x] 将已归档端口路径检查接入Critic前置预检 -> verify: 无效引用在派发前得到端口/字段明确错误，合法归档、inline、受控sidecar通过；越界/符号链接拒绝。
- [x] 影响闭包、机械预检/调度mock与哨兵 -> verify: 记录实际层级、退出码、失败修复及代码提交；真实活动Run保持冻结源。
- [x] 完成源码提交和任务归档，准备交给已授权的dev合并流程 -> verify: 显式pathspec及清晰验证记录；合并/正常清理由主checkout收尾记录证明，保留无关修改、未push。

验证：当前dev上实际派发回归2项失败；修复后56项定向、1038项自动影响闭包（含Artifact Package mock E2E）、53项哨兵通过。真实失败报告只读重放准确返回requirement_spec路径错误；不是新的Provider验收。证据：reports/plan-port-admission-20261119/validation.json。
