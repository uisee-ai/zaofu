# 198 按需读取：真实 Codex 分页断点验证

日期：2026-11-04。隔离临时项目、合成需求，ZaoFu 候选工作树；LS 控制面和开发
Run 未启动。这个测试调用真实 Codex CLI 和本轮 ZaoFu `artifact read` CLI，使用
受控 capability、EventWriter 和 RoleSessionRegistry 构造的操作环境；不是完整
Orchestrator 端到端重启。

1. 首次启动 Codex session `01a1a4f9-776d-7d72-846d-9b9f63dc2191`，运行一次
   必读，输入选中文本共 19,250 字符，第一页区间 `[0, 1101)`，随后结束进程；
   还存在未读页。
2. 把该 session 的真实 transcript 绑定到测试角色；重新启动同一 session，调用
   `--required --resume --page-bytes 2048`。第二页为 `[1101, 2199)`，两次命令
   均退出 0。源码身份、operation、bundle 和读取回执参与了检查。
3. 对两次输出独立执行 transcript 观察，均为 `observed_in_transcript`；
   `current_context_availability` 仍为 `unknown`。再要求模型不使用工具回答第一页
   内容，模型答出“确认订单前预留资金”。这只能说明一条合成事实被回忆，不证明
   完整内容的当前可用性、理解或业务语义覆盖。
4. 两次模型调用输出保留为 [首次](2026-11-04-context-198-resume-evidence/first.jsonl)、
   [恢复](2026-11-04-context-198-resume-evidence/resumed.jsonl)、
   [无工具回忆](2026-11-04-context-198-resume-evidence/recall.jsonl)。
   输入是合成数据；凭据仅存在于已清理的临时状态目录。provider 原始 session
   仍由本机 Codex 管理。CLI 统计分别为 input 30,635 / cache 27,008 / output 92，
   和 input 77,036 / cache 61,952 / output 179；不能据此推断完整流程节省或账单。
5. 首次使用操作系统沙箱的尝试报告 loopback 设置失败，未产生读取回执；有效演练
   在隔离临时目录使用本机允许的无沙箱 CLI 模式。测试运行完成后发出
   `simulation.done` 并清理临时状态。

此前真实业务近似场景完成过“所有页读完后的预算暂停和续跑”；本轮补充的是分页
之间的同会话恢复。真实 Ctrl/进程 kill 时机、整个必读集合消费、Critic 通过、
业务需求独立盲评、真实 Replan 和上下文压缩后的模型记忆均未由本记录证明。
