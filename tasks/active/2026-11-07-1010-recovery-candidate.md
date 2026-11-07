# Task Pipeline 恢复选择当前增量 Candidate

> 状态: active

用户授权继续修复LS-v1阻塞。10:08只读复现：FMS13恢复anchor指向另一个任务FMS04的部分分支1b3ffa8，而当前已准入Candidate为4d562d9，可能把已集成依赖误判为缺失。现有semantic_replan只识别candidate.ready，遗漏v4 candidate.updated。

- [x] 固定复现跨任务遗留分支抢占当前Candidate -> verify: 真实Run Manager调用发出的恢复请求使用已准入增量head，不改任务原合同代次。
- [x] 同Run/kernel增量Candidate绑定不可变集成回执，检查摘要/身份/branch祖先；仍优先同任务已验证部分提交，Candidate落后于任务base则用base -> verify: 跨Run/worker来源/篡改/缺回执/分叉拒绝或安全退回任务base。
- [ ] 跑影响闭包、sentinel、mock E2E，合并dev后只读回放及受控加载验证 -> verify: FMS13新恢复目标正确；既有活跃worker保持。

验证：新回归初始9 failed/2 passed，修复后RM入口/既有部分续接82 passed；dev-verify影响闭包1012 passed。实际LS-v1只读回放 target=4d562d9，candidate_event_id=evt-3a84ee410f50，原base/合同代次不变。补充Candidate/Sidecar/199 mock/部分续接及sentinel、flow-smoke见本轮验证日志；未运行全量pytest或真实provider E2E。
