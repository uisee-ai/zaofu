# 独立 External Issue triage 路线：真实执行链补测调研

> 文档定位（2026-09-30）：历史调研，正文“当前/建议/尚未实现”仅适用于各段所记时点，不是最新执行队列。现行方案见 [ZaoFu_TestKit.md](../03_plans_and_steps/ZaoFu_TestKit.md)，执行仍按既有暂停/跳过边界；本次不重跑或改写历史结果。
>
> 当前对齐入口：`tests/conformance/test_zaofu_testkit_s8_triage_route.py`。已有 triage route 检测；只读 triage 与写入 Fix Run 权限边界仍独立。

> 2026-09-14；主线程 zf-cr 定向补充。依据当前 `142a62ac` 的源码、根 YAML 和测试；以下是实现前的检查与有限实验设计，不是测试通过声明。不修改生产代码，不运行真实 Provider／Forge 或远端写入。

## 1. 路线与已有证据

- 原方案 `ZaoFu_TestKit.md` §8.3 要求官方工作流的成功、失败、缺依赖、重复、乱序；根 `zf.yaml:3–13` 的 `general:external-issue-triage` 是 `agent.read → agent.verify`，`artifact_delivery`、`independentVerify: true`，不是 IssueFlow 的 writer/candidate/judge。
- `tests/test_external_issue_ingress.py:120–170` 证明真实 poll→source/task/invoke 去重；`:767` 的终局例手写 admission 和 ready_to_fix，再执行 reconcile，没有实际两个 worker 阶段。
- `tests/e2e/generic_workflow_complex_support.py::_append_stage_result` 自建 operation/envelope/admitted 事实；`_prepare_reads` 新建 source manifest。可参考协议形状，不能用于本路线的正式准入或必读回执证明。

## 2. 真正入口与权威链

1. 用根 YAML 的完整 Workflow 文档，保留声明的两阶段、ports、criteria、独立 verifier。仅 provider/backend 和环境换成隔离 Mock；不取消阶段、降低 schema 或手写终态。
2. `ExternalIssueIngressService.poll_once`（external_issue_ingress.py:146）调用注入的只读远端镜像响应，生成 source revision、Task、run identity 和 invoke。fixture 可通过真实 IssueMirrorStore 提供固定外部数据；TaskStore 不由测试预造。
3. 锁定 Self-Issue 的 `resolve_self_issue_target_root` 返回运行包 checkout（self_issue_target.py:24）；测试只替换物理 checkout discovery `zaofu_v7_root` 指向临时 Git repo，不关闭 `target_locked`。断言实际 service root、source commit、state dir 均隔离。root-discovery 本身已有局部测试，本矩阵不将该替身称为真实宿主机定位证明。
4. `_workflow_identity` 读取启动时的 active run contract，再生成 immutable generic snapshot（external_issue_ingress.py:909–1012）。fixture 使用真实 `build_run_contract/write_run_contract` 建立启动前提，不能把缺契约误当合法成功输入。
5. 真实 `Orchestrator.run_once` 铸造 child/operation/manifest。按实际派发身份、签发事件和输入消费策略提交 worker 事实；由生产 `call_result_adapters` 与 `generic_workflow_outputs` 物化报告、写 envelope 并准入。
6. verifier 使用 `artifact_delivery_success_payload` 的可编辑模板形状，实际 artifact、claim-set、上游 admitted refs 来自 runtime；以实际字节／摘要核验后填写 claim coverage 和 verification evidence。不能把模板里的 placeholder 当证据。
7. `artifact_delivery_verified_event`（generic_workflow_fanout.py:208）要求 admitted/control refs；`external_issue_triage_outcome_event`（external_issue_clarification.py:197）只从当前 run 上游 admitted producer 报告提取 disposition；poll/reconcile 的 `_complete_triage_run` 释放 Run 名额。不得直接生成 `artifact.delivery.verified`、`external_issue.triage.*` 或 `run.completed`。

## 3. 有限矩阵与 oracle

| 场景 | 固定输入／扰动 | 必须观察 |
|---|---|---|
| 正常报告 | 合法 reader report/disposition、独立 verifier | 两个真实不同角色派发；一个 verified artifact；来源 revision、SHA、refs 和 outcome 绑定；Run admission 不再 active；无 Fix Run/源文件/远端变更 |
| verifier 阻断 | 当前目标的 rejected 或 blocked 结果 | 不伪造 verified/outcome/成功；实际失败或 attention 路径可定位，不把 attention 当 Run 终态 |
| 缺依赖 | 缺 report 输出或实际 required-read receipt | 真实准入拒绝，不能派发／完成后续阶段；保留具体错误码 |
| 重复 | 重复 poll、reader/verify 结果、终局 replay | source/invoke/admission/artifact/outcome/terminal 不重复；按 run/revision 计数 |
| 乱序／旧身份 | 已签发结果带旧 revision 或错误 child run | 不准入，不推进当前阶段；正确流程有独立控制 |
| 重建 | reader 事实已入账未消费、verifier pending、结果后重建 | 同目录重新 loader/Store/Orchestrator/ingress，不复制缓存；真实事实延续，不重复输出 |

完整 read/profile/输出协议以实际 child manifest 为准；遇到阻碍先区分夹具缺项与生产缺陷。只对已复现、精确签名的生产问题登记 Issue；缺证据不当作通过。保留 JSON/JUnit、原始 state/ref/digest 和来源摘要；本批显式捕获不能代替 K4 的全普通用例自动取证。

## 4. 安全与联调闭包

复用本地 Git/禁止网络的 TestKit guard，禁止 Provider、shell、远端命令和根仓库写入；只有一次临时 baseline commit，无修复提交。失败后仍检查禁止调用列表和源树不变。注册 S8，联调 S0/S1、既有 Issue/Research，以及 ingress、clarification、generic output、call-result、artifact-delivery、run-admission 的直接契约；不称为全 S8/ALL 或发布层。PRD standard、真实硬进程恢复和 K3–K6 仍保留。

## 5. 真实派发后的协议核对

初始夹具试跑依次暴露 YAML 内部字段、publication mode、必读策略和 submit mode 假设不成立，均为测试准备问题，不登记生产 Issue。真实 reader 没有签发 `input_consumption_policy`；测试读取实际 source manifest 的输入并封存 ledger，但不宣称生产强制了必读门禁。

实际 `semantic_result_submit_mode=blocking`。依据 `result_submit.py:60–133,463–715`，在 dispatch 前调用真实 `provision_role_submit_credential`，由生产派发绑定 operation/attempt/lease capability。模拟 worker 只向实际签发的 scratch 写 profile semantic body，再调用真实 `SemanticResultSubmitService.submit(use_operation_scratch=True)`；不自建成功事件或 admitted envelope，不把 blocking 改成 off。

为避免 ModelEvidence 快照含秘密，仅在同步凭据签发调用内将随机 token 来源替换成 `testkit-public-fixture-token-<role>`，作用域立即退出；这些值只授权本测试临时 state。生产 hash、generation、role/capability、路径和一次性消费检查不替换；不声称验证凭据熵或真实 Provider 传输。先跑有效基线，再以错误凭据／重复提交等负向输入检验真实拒绝路径。

## 6. 有效基线与 oracle 修正

真实基线 `/tmp/zf-triage-terminal-baseline-dkfqxkl5`：1 passed。扩展首轮 `/tmp/zf-triage-route-matrix-qqwz82qs`：3 passed/8 failed；源码核对确认八项均源自夹具预期错误，保留失败报告，不登记生产 bug：

- 两个 worker 对应三个 operation 准入（reader、其父 fanout aggregate、verifier），不是两个。
- `run.completed` 由 ingress 释放名额；后续 `run.goal.completed` 是独立的机械 Goal gate，必须各一次且分别绑定 outcome/verification，不能把两种事件合计当重复。
- `CallResultAdapterRegistry.adapt_semantic_result` 明确用 protected request identity 覆盖 worker semantic 中同名字段。对自报 run/revision 的测试应证明不能改变签发身份；实际 artifact digest 或外来 evidence 则必须拒绝。
- `generic_workflow_outputs._output_body` 允许 summary 作为 report 正文。`outputs={}` 但保留有效 summary 并不是真正缺输出；改用生产明确拒绝的 placeholder body 验证缺输出拒绝。
- verifier `verification_evidence_refs` 只能引用已准入上游/声明产物等授权集合（artifact_delivery_evidence.py）；测试独立生成的 probe 是诊断附件，不冒充已准入引用。实际比对 artifact 字节／SHA 后引用对应声明产物。

随后 12 例 `/tmp/zf-triage-signed-matrix-2bzoceke` 通过。增加 pending 重复 poll 的依赖屏障、verifier 实际必读缺失和 blocked/rejected 结果后，`/tmp/zf-triage-complete-matrix-_98zsg_5` **15 passed**（pytest 12.39 秒），无 xfail；待注册后与既有阶段联调。reader 不签发必读策略，verifier 签发：两者在断言中分开。负向语义只证明不会被当作成功，不声称完整自动 replan/cap；重开是服务对象恢复，不是硬进程崩溃。

注册后的正式联调 `/tmp/zf-triage-route-integration-z4_a3ru6`：22 入口、328 passed/10 既有 strict xfailed，156.311452 秒；新 triage 15 例通过，既有 Issue036/037/044/045 继续检测。无普通失败、收集错误或超时，外层 fail；失败包及快照摘要核验见 steps.md K2。未增加生产 Issue、未修生产代码，完整目标仍未完成。
