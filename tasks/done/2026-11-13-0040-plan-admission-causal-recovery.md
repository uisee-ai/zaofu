# 计划准入失败的因果恢复输入

> 状态: done — `494c0f3a` fix: bind plan admission recovery to its causal ready event

授权：用户持续授权监工中发现问题后修复并记录；根会话 2026-11-13 明确授权此唯一已复现缺陷，指定独立 worktree、仅提交不合并推送。基线 `8781a3d0`。

来源：LS-v1 `docs/records/2026-11-12-delivery-entry-resume/replan-failure-diagnosis.md`；`evt-d2c4fa22de0a` → `evt-c00e3ac01237` → `evt-501ce0a5d1a4`。新 DELIVERY 准入失败错误继承历史 FMS-03 / OA / incident / operator 授权。

范围：只修无 fanout 的 plan_admission 恢复源选择及当前 incident/授权保护；保留合法 fanout/OA 续跑。不得改 policy 限额、移除 authority 校验、重构其他失败分类或改 LS 运行态。

验收：

1. 旧 reader/OA fanout 与新的 task_map.ready 同 run/stage → verify: RED/GREEN 证明恢复仅保留明确当前源的任务、incident、候选及授权，不继承历史 OA/delta/generation；缺源/跨 run 源 fail closed。
2. 当前显式合法恢复源、有 fanout 的旧 reader 续跑 → verify: 相关 stage recovery、OA identity 与 caller 回归继续通过，不删除合法 authority。
3. 实际 caller 处理新 admission 失败 → verify: deterministic mock 测试产生正确恢复事件并可 replay，不启动真实 provider。
4. 影响闭包、sentinel 和相关 mock E2E → verify: dev-verify plan/run、dev-premerge-gate 与记录的独立测试通过；提交实现后记录 hash，归档任务并交还 clean worktree。

实现：`stage_failure_replan.py` 提前解析明确被拒 ready；无 fanout 的计划准入只消费该载荷，缺源/跨 Run/错误事件类型关闭恢复。当前 failure incident 覆盖旧 incident。既有 fanout/OA authority guard 不变；199 文档记录边界。

验证记录：

- RED：新增回归在基线 5 failed，实际为旧恢复 attempt 导致 cap_exhausted；失败源及授权污染与真实诊断一致。
- GREEN：新增 7 passed；早期新/旧 stage/OA 集合 71 passed。最终 focused impact closure 225 passed（14.96s），命令见下方，包含真实 Kernel handler→EventWriter→重放测试和两组既有 mock E2E。
- `PYTHONPATH=src ZF_PYTHON=/home/min/workspace/public/zaofu/.venv/bin/python bash scripts/dev-premerge-gate.sh` → manual docs currentness ok，53 passed（2.56s）。
- `dev-verify.py plan --base dev` 加 explicit tests 的自动闭包扩为 252 文件；去掉额外显式文件后 `run` 为 248 文件，均因超过 80 而选全 tests。该规模来自大型间接 caller，不代表本补丁跨多个核心域。根会话明确同意按 AGENTS 的实际影响闭包验证；未改 selector。`run --base dev --tests tests/test_plan_admission_replan_lineage.py --skip-tier deterministic --skip-tier serial --receipt /tmp/zf-replan-admission-verification-20261113.json` 保留 planner 事实及这两个宽泛 tier 的 NOT_RUN，不作为全量通过证明。
- 只读历史回放：使用配置解析 state_dir、EventLog 截止 `evt-d2c4fa22de0a` 的事件，在补丁纯函数上生成恢复 proposal。task_ids 为 DELIVERY、incident 为本次 bbeb…、旧 failed_task_ids/OA/delta/generation/operator 授权均不存在；未 append、启动 provider 或改 LS 运行态。当前 ready 自身的 rework_attempt=1 仍按既有逻辑推进为2，未重置 cap。

Focused command（从本 worktree，以共享已安装解释器和当前 src 执行）：

```bash
PYTHONPATH=src /home/min/workspace/public/zaofu/.venv/bin/python -m pytest \
  tests/test_plan_admission_replan_lineage.py tests/test_stage_failure_replan.py \
  tests/test_replan_authority_inputs.py tests/test_oa_replan_baseline.py \
  tests/test_reader_stage_recovery.py tests/test_reader_task_replan_scope.py \
  tests/test_reader_replan_index.py tests/test_reader_replan_ownership.py \
  tests/test_writer_fanout_admission.py tests/test_workflow_resume_event_first.py \
  tests/test_workflow_resume_batch.py tests/test_events_log.py tests/test_event_contracts.py \
  tests/e2e/test_replan_checkpoint_closure_mock_e2e.py \
  tests/e2e/test_design199_recovery_mock_e2e.py \
  -q --no-cov -m 'not host and not real_provider and not perf'
```

闭包依据：新 helper 行为、实际 Kernel event caller、reader retries/scope/index、writer admission、控制恢复入口、OA immutable identity、EventLog/Event schema，以及 checkpoint recovery / design199 的模拟端到端。本批不认证真 provider 或真实 LS 恢复；真实恢复由 merge owner 执行。
