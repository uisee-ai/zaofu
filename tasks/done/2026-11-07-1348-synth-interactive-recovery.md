> 状态: done

# Critic 交互阻塞不得变成计划语义返工

授权：用户要求持续监工直到全部完成，遇到卡点记录、修复并从断点继续。真实 Planner 成功事件evt-1110803f8950后，Critic的额度模型弹窗被c61aac0a守卫正确拒绝，但Plan synth的宽异常捕获生成reject终局，继而触发prd.plan.failed和新Planner evt-14547e9b。这是派发失败，不是计划内容被Critic否决。

- 交互弹窗阻止synth派发 -> verify: 保留已完成reader，不产生fanout.synth.completed失败或业务failure event；复用现有dispatch_deferred且60秒窗口内不重复刷事件。
- 弹窗解除后恢复 -> verify: 正常incomplete aggregate恢复扫描派发同一synth operation，无重复reader或语义返工。
- 真实语义/机械失败不豁免 -> verify: 原有schema/preflight失败仍按原路径拒绝，paused dispatch兼容；仅识别transport异常的typed interactive_prompt_marker，不用文本猜业务语义。
- 交付 -> verify: 运行直接reader/synth/recovery闭包、sentinel与相关mock E2E；精确同步main并保留运行中Verify/Planner重载；记录实现commit与真实证据。

TDD：测试入口import错误先修正（不计行为RED）；随后quota_model_switch/login_required两项均因错误产生synth失败而RED。修复后两项及原有paused/schema回归共4passed。未提高cap或取消正在运行的合法返工。实现归档后补充最终验证。

验证：dev-verify.py plan/run --base dev --paths（本次四文件）--tests tests/test_synth_interactive_deferral.py --tests tests/test_reader_fanout_runtime.py --no-parallel -> 自动12文件闭包428passed；额外reader/synth/recovery/preflight/stage-replan与channel PRD恢复mock E2E闭包244passed；sentinel50passed/1skipped，ruff及diff检查通过。均为隔离/确定性测试，未宣称真实provider E2E。

真实因果：evt-9c14bbeb21c9错误synth终局→evt-5a9d96a556ac prd.plan.failed→evt-14547e9bc437 stage replan 2/2。已仅通过Escape解除Critic、Orchestrator、Discovery旧额度模型弹窗，保留Astra high；未修改retry cap、历史事件或已完成Planner工件。Core于evt-bef0232e5ad8完成Impl，evt-cc534a058055派给独立prd-verify-lane-0，源f61ac152a07e30c07aaa29529253f69746b2a970。

实现提交：`90f76bd2 fix: defer synthesis while provider interaction blocks dispatch`。main仅同步经preimage逐字节核对的source/test，组合17passed。控制面保留worker重载evt-6be0bb5de2a6；新watcher3235695于13:52:27 UTC启动，Core Verify原operation/attempt仍运行，Planner单次提交evt-6a0ab0625319后，新Critic派发evt-c8ff7cf517e4，operation wop-prd-plan-synth-trig-evt-14547e9b-5b16a1fb1bf3，pane已收到正确briefing且保持Astra high。独立评审结论尚未产生，不把恢复派发当Plan批准。

历史旧synth operation wop-prd-plan-synth-trig-evt-64319b1a-994e3c657bfe仍requested（原失败路径遗留，未实际执行）；新synth已能正常派发。保留诊断，后续核对最终quiescence，不伪造旧op完成或强制恢复旧fanout。
