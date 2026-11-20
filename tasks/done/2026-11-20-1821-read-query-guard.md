# 只读 Python 事件筛选误触发写保护

> 状态: done — `10498930` fix: recognize bounded Python event read queries

来源：8×8真实接续中的事件evt-d947fd67361c。Run Manager读取events.jsonl并用ts>=时间字符串筛选，合法Python AST无写入目标，却被shell重定向词法检测拒绝。Agent自行换用其他只读查询继续；不为此中断验证。

- 原始只读查询经过真实PreToolUse入口 -> verify: dev与run-manager均放行，不写业务状态或生成拒绝事件。
- 仅可证明无文件副作用的有限Python语法获得豁免 -> verify: 写入方法、写模式open、符号反射、危险import、动态执行、shell尾随命令及未引用heredoc均不能获豁免，既有写保护保持。
- 绑定部署 -> verify: 隔离worktree测试通过再提交；原验证窗口结束后接入源码，不热改运行中的守卫。

用户授权：发现问题修复、评估后从断点继续。只修机械工具误判，不改变任务合同、权限、业务结果或读证据准入。

验证：原回归4 failed/12 passed；修复后111项Hook/作用域聚焦测试通过。`dev-verify.py run --base dev --tests tests/test_hook_python_read_query.py`通过9494项后端影响闭包（7 skipped）、Web typecheck/unit、53哨兵、10流程。包含循环目标属性重绑定、写模式open、print文件目标、shell重定向和尾随命令反例。当前仅在隔离worktree，真实8×8窗口仍加载旧守卫。

闭环：实际PreToolUse聚焦入口与原始查询回归通过；已在观察窗口外部署并随4d1564df运行。没有宣称新的真实RM恰好重发原查询。
