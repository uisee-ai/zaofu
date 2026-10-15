# Channel 协作 1–5 闭环修复与验证

> 状态: active

用户批准在 `zaofu-channel-collaboration` 修复并用 Playwright 验证贡献、续轮、同版评审、Owner 确认、Task/Workflow 交接。

当前 HEAD `978be97` 的真实证据：`/tmp/zf-channel-real-e2e-20261015/report.json`。四人配置重启仍冻结旧两人名单，`next_round_targets_not_in_current_roster` 拒绝续轮；已有 PRD 却显示 `synthesis_missing`。

1. 显式重新开始讨论冻结当前配置的有效成员 -> verify: 通过 controlled action 增减成员并重启；新轮次等待全部当前贡献，已有 Owner 决定不丢失，旧贡献不提前放行。
2. 定向续轮与状态提示 -> verify: 当前 roster 内续轮可派发，名单外仍拒绝；有 PRD 时展示真实续轮阻塞，旧轮次拒绝不污染新轮次。
3. PRD 自动生成与同版本评审 -> verify: 真实 provider + Docker Playwright 产生正文并启动独立评审，全部必审签名绑定相同 ref/digest；旧版签名不能放行新版。
4. Owner 确认 -> verify: Playwright 在评审结果和风险可见后操作；确认仅针对当前文档，执行审批边界保留。
5. Task / Workflow 交接 -> verify: Playwright 请求计划、审核测试 Task、选择和批准隔离 Workflow；证据绑定 PRD、Task 和 run，Channel 状态据实推进。只执行隔离测试，不实施生产 Loshu。

运行影响闭包、前端构建/相关测试、sentinel 和确定性 mock E2E；真实 provider 验证单独报告。更新 `docs/impl/channel-prd-file-publication.md`。完成后本分支提交，不合并、不 push。

## 实现与运行中证据

- 新讨论冻结当前有效 roster；定向续轮保留原始 review roster。
- 续轮拒绝纳入真实事件投影，已有 PRD 不再误报缺失。
- 原始需求标记、完整用户消息与上传附件正文进入重启及语义来源包；附件范围、摘要和预算均失败关闭。
- 最后回复后立即执行一次 relay 正常门禁，消除无后续事件时等待 300 秒 sweep 的延迟；保留 Owner / cross-review 门禁与幂等。
- 同线程、成员、PRD ref/digest 的完整重试 context 成功后，旧 context 拒绝不再阻塞 attention；历史保留。

真实运行使用 `/tmp/zf-channel-preview-20261015-080958`，仅 8002。
证据目录：`/tmp/zf-channel-close-loop-20261015`。
实际四角色贡献、DBA 单角色自动续轮、v2/v3 完整预览与下载均已执行。
v3 独立评审中 QA/DBA 通过，开发评审发现 canonical JSON 与 Markdown 摘要混用，
系统正确拒签并自动请求 v4 修订。这不是已通过的产品验收。
完整 review 来源 250780 字符超过默认 120000；隔离项目 zf.yaml 设置有界 350000 后，
通过 Playwright 的带 token 受控消息动作重试同版本评审。未修改 runtime canonical state，
未截断原始来源，也未把人工重试声称为零干预自动恢复。

测试分层：Docker Playwright 回归 4 passed；前端 build/typecheck/unit 通过；
serial 10 passed；sentinel 50 passed / 1 skipped；flow mock E2E 10 passed。
330 个选定影响测试文件初跑 4382 passed / 4 failed / 11 skipped：一个旧 phase2 断言按新
即时收敛行为修订，三个是 shell 缺少 python；更正解释器 PATH 后对应三个文件 100 passed。
新增 context 恢复和直接 projection 调用链 70 passed。最终 331 个选定影响文件重跑 4400 passed / 11 skipped（153.06 秒）。
最终 dev-verify 其余自动层全部 passed，receipt `/tmp/zf-channel-close-loop-verification-final.json`。
未把全量 pytest 或 mock E2E 声称为真实 provider 证明。


后续真实负例发现：v4 存在 QA 的 `revision_request` 时，直接 Owner 确认动作仍返回 202，
虽然 multi-lens 共识并未 reached。补上后端 409 门禁（含旧确认幂等重放），Playwright 原请求
复测返回 `review_revision_required`。原错误事件保留为历史证据，不手改状态。
对应控制动作调用链 36 文件：716 passed / 2 skipped；直接新回归与 PRD/模板 52 passed；
sentinel 50 passed / 1 skipped；flow smoke 10 passed。
v4 QA 发现验证命令可能复用历史成功文件，系统自动修订 v5；仍等待最终真实验收，任务不归档。
