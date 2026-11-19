> 状态: active

# 恢复输入和宿主回执导航

Owner已授权对真实无人验收中出现的问题先mock复现、修复再续验。
旧轮`workflow-863563136fc24d34`的Average连续两代报告不存在的
`critical-reader-repair-context`；原提示把所有恢复都当作Reader修复。
同时Worker误将回执的创建事件作为handoff完成事件读取。
证据：`evt-4e429d9f78b6`、`evt-0194fed601df`，不是额度不足。

- 修正失败提示，只要求当前manifest实际绑定的恢复源 -> verify: 生成提示回归。
- 为已核验宿主事实提供精确operation及完成事件读取命令 -> verify: 实际派发产生的
  handoff可读原始非零事实，创建事件仍拒绝；无host事实不发明导航。
- 保留授权、摘要、当前身份、分页及独立验收 -> verify: 交接/输入/宿主影响闭包、
  mock E2E和sentinel通过；真实轮源码维护单独计数，不更改冻结Plan或结果。

RED：新增/增强回归3项在原HEAD失败。完整真实资格另由P2记录，本项不宣称Goal通过。

验证：62项聚焦、279项影响闭包、7项mock E2E、53项sentinel通过。
