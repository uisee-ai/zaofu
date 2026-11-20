# Plan 作者工件与运行态工件的预检边界

> 状态: active

来源：8×8真实验证中，Planner通过result validate得到11项plan_port_unreadable。
已生成且有摘要的作者工作区工件，被按state_dir中的已搬移sidecar解析；
接口已有ports_materialized标记，却没有传给正文读取。另32项任务字段错误属于作者
产物，不由本修复放行。

- 作者提交前预检ref/sha256 -> verify: 受限作者目录工件可读取，validate不写账本、不搬移文件。
- 实际submit→Kernel搬移→下游预检 -> verify: 相同摘要及完整正文保留，最终仍要求sidecar。
- 错误摘要/目录逃逸/符号链接/缺文件 -> verify: fail closed，不搜索其他worker文件或重建缺失证据。
- 现有合法sidecar与已有错误绑定 -> verify: 不由作者同名文件替代已有绑定。

验证：实际Service入口TDD先1 failed；修复后116项聚焦回归通过。
原有Package路径测试明确按搬移后阶段执行，其严格路径断言保留。
`dev-verify.py plan/run --base dev`：8,766 passed、7 skipped、12 warnings；
Web typecheck/unit通过，53 sentinel、10 flow smoke通过。
真实Planner自身的schema修正仍在进行；监工通过受控worker-reply说明引用缺陷
修复与原合同，未代写或代提交结果。Plan后资格窗口尚未开始。
参见[实施记录](../../docs/impl/eight-by-eight-unattended-qualification.md)，
日志`reports/eight-qualification-20261120T122929Z/author-port-preflight-*`。
