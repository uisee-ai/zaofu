> 状态: done — `25466e9 fix: enforce verification evidence checks on legacy results`；补充回归 `d92803a test: bind recovery fixtures to persisted verification evidence`

# P0 现场补充：禁止模板占位符绕过 Verify 证据门

来源：[设计 183](../../docs/design/183-runtime-result-authority-and-recovery-closure.md)，用户授权的恢复验证闭包。

事件 evt-37adee35a439 接受的 verification-result.v1 含未替换的 evidence_refs 和 probe target_commit。真实浏览器 receipt 通过不支持这些额外的非浏览器通过声明。根因：旧 emit transport 没有 semantic_submit，typed admission 整体跳过；仅检查字符串非空仍容许模板占位符。

## 验收

1. 带合同快照的 Verify，无论 transport 都执行原有命令/目标/证据门 -> verify: legacy 含错误 target 的结果拒绝，完整结果通过；实际 CallResultAdmissionService 调用回归。
2. evidence_refs 不得保留模板槽位 -> verify: report/probe/AC 各层正反例；已存在的 canonical command target/exit_code 等校验继续生效。
3. 保留旧准入历史但不能作为本轮通过证据 -> verify: 操作员审计记录、新受控 rescan、独立执行非浏览器命令及真实 receipt 后重新准入。Q01–Q03 外部决策仍不能假关闭。


## TDD 与调用闭包

先复现 legacy 绕门及 report/probe/AC 占位符两个 RED。增加真实 CallResultAdmissionService 测试：占位符进入 repair_pending、不产生 admitted；补齐有效合同/目标/命令/证据后才 admitted。原 live 控制结果只读检查现被拒绝。
原测试中仅用伪造 snapshot ref 或模板 probe 充当完整结果的 fixture 改用持久合同/目标及明确 mock 证据；reader recovery fixture 亦记录真实 trigger 因果链，避免把缺失的原始事件当作合法续接。

验证：261 passed（14 个直接调用方/合同/reader/lane/集成与三个 mock E2E 文件），sentinel 51 passed。现场新代独立结果尚待完成。

扩大回归后补齐 result-submit / role-lifecycle 两处历史成功 fixture；受影响两模块 49 passed，其余非基线失败精确复跑合计 24 passed。serial 10、sentinel 51、flow-smoke 10 通过。已知基线 20 项失败维持单独报告。
新 reader 已实际执行四条合同命令并形成完整 blocked 报告，typed validation 无 issues；三条 Vitest 缺合法 task Verify context，不能作产品通过。账号额度限制中断提交，用户更换账号后正在受控恢复，验收 3 仍待实际准入。

## 现场验收闭合

06:27:24 UTC 独立 reader 提交 `evt-f6039a7f34c1`；Kernel 以 `evt-1d05765bfd15` 准入，semantic_verdict=abstained，control result SHA256 `712f7918a2c5bcb549a19dd428e9d023c7ce2fb1386abaeebe075f31edf9dde9`。操作 `evt-b835e9bc115d` 正常结算，child 与阶段按真实验证环境失败结束，未产生产品通过声明。

已核对本轮四个原始命令、精确 target、全部 probe refs 实际存在；browser 在本代三视口通过，三条 Vitest 在断言前因 reporter context 缺失退出。29 份新证据完整归档为 required sidecar，事件 `evt-cf9ddb69eaee`，摘要 `b2e20e2d4a36fb4c174bb802d24fcb123f385563642ca38a0bddc5789ba2d3e7`。旧模板准入历史仍在审计记录中，未计为通过。

本任务关闭的是准入缺陷及其真实调用验收，不是产品、上下文供应或 Q01–Q03 的关闭。剩余事项归[现场恢复任务](../active/2026-10-21-0316-loshu-resume-q01-q03.md)。
