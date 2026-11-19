> 状态: done

# 恢复 Reader 必须先消费当前恢复输入

来源：用户批准无人监工验证中发现问题后修复、mock 先行再真实验证；本轮不是新增 Provider 权限或额度重置能力。

## 问题与证据

32 项真实运行 workflow-503a4acc817a313d，在自动宿主采证完成后生成 evt-08174cd46eda。verify-lane-6 未读取 critical-reader-repair-context 就以 Docker socket 被拒再次提交 verifier_execution_failure（evt-4203dfe6ff16）。另外 7 个 Reader 已完成该恢复输入分页读取；说明输入存在，但默认 required 分页把恢复输入放在大产品输入之后，失败捷径也没有检查恢复输入。

## 范围及验收

- 新调度默认分页优先恢复上下文 -> verify: 实际 canonical 声明及 CLI 首个分页包含恢复输入；其余必读完整且旧声明不变。
- 对带当前恢复输入的 verifier_execution_failure 校验读取证据 -> verify: 未读/部分读拒绝，读取后允许真实 runner 失败，不要求先读完全部产品；reader_execution_failure 仍可报告读取故障。
- 预检与正式准入一致 -> verify: 预检不消耗凭据、不写状态，纠正读取后同一次操作可以提交。
- 保留全部身份/摘要/输入失败边界 -> verify: 定向回归、影响闭包、哨兵及相关调度 mock 通过。
- 方法归 skill，运行时只检查既有必读凭证；不评判回执语义、不授予 Docker 权限、不自动判产品通过。

当前两个真实 Run 的冻结源码不热更新；新补丁真实验证需独立记录来源与介入边界。

## 验证

- 旧源码9b70ae91不可变快照：纠正测试manifest定位后3 failed /30 passed，失败分别是恢复输入非首项与未读恢复输入的预检被错误放行。最初两次测试定位错误另存原始日志，不作产品RED。
- 定向55 passed；宿主交接/阶段恢复/8槽调度及进程mock45 passed、2 deselected（宿主级显式层未在该命令执行）。
- dev-verify影响闭包：2161 passed、1 skipped、8环境失败（PATH缺zf）；补正PATH后受影响CLI启动文件43 passed，原8项全部通过。未改代码迎合环境失败。
- premerge sentinel 53 passed，skill三份metadata及正文一致；不是完整pytest或真实无人闭环通过证明。
- 真实观察仍在冻结旧源码上；修复效果须后续同源真实轮验证。

实现提交：`e03ee209 fix: require recovery evidence before repeating runner failure`。已完成本补丁的定向、影响闭包、调度mock和哨兵验证；P2真实无人8×8资格仍未完成，独立保留。
