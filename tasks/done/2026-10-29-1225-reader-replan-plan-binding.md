> 状态: done

实现：55e3039 fix: preserve current plan authority during rescan reader hydration

# 重规划 reader 保留当前 Plan 身份

真实 discovery evt-d154c01bd957 使用正确新 package，reader hydration 却从失败任务快照回填旧 package，注册拒绝并触发 Run blocked。上一修复的真实受控请求验证止于事件，未覆盖 reader 派发边界；本批补齐该边界。

- 全局重扫描保留 request 当前 Plan generation/package，任务旧 snapshot 仍可审计 -> verify: 实际 Orchestrator reader dispatch、immutable manifest 和必读输入可用。
- 原 task verify 继续继承 snapshot 身份 -> verify: 既有 writer/reader 回归。
- 测试、提交并部署后从失败断点恢复 -> verify: 不篡改 Run 状态或伪造 gate 通过。

TDD：实际 reader preregistration 先以旧 package/current package 不匹配失败；修复后实际 dispatch、source manifest、必读输入通过，原 task snapshot 继承回归仍通过。

验证规划器因超大 orchestrator 依赖闭包自动建议全量确定性套件，已停止该进程树；本次仅 reader 合同 hydration 边界，按 AGENTS.md 改为明确的 11 文件影响闭包，不把中断全量计为通过。首轮 436 passed；结构冻结门禁阻止向超大模块继续追加，因此将既有单一责任函数移入 reader_contract_target.py，原入口委托，再跑同一闭包。
最终同一影响闭包 436 passed；sentinel 50 passed / 1 skipped；stale-contract/恢复/语义控制 mock E2E 8 passed。

部署后入口回归 3 passed；保留 worker 重启 PID 2572055。受控新代际批准 evt-b21c610e2760 恢复 Run；实际 reader 派发 evt-d472d492cb7a 固定当前候选 1451f1b 与 Plan 6b9d432，provider 开始读取 briefing。产品扫描结论仍待独立产物，不计为业务完成。
