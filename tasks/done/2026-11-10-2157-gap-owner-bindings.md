> 状态: done

# 增量 TaskMap 保留既有 owner 实例声明（LS 监工 #204）

用户持续授权监工中发现问题即修复；根监工明确批准本项在独立 worktree 实施、验证及本地 commit，保留 worktree 交根监工合并。基线 `dd88c3ac`。本任务不修改 LS 的源码、任务合同、状态、配置或活动 worker，不合并 dev、不 push。

当前 `build_gap_task_map_amend` 丢失已有合法的 `owner_instance` / `preferred_impl_role`，使 `owner_role: dev` 的 FINAL 后继在 product delivery materialization 后失去注册实例绑定。原 map 的同 lane assembly/bundle 拒绝是既有 AVBS 保护，不应删除；任意未注册逻辑 owner 也不因此合法。

只在 `preserve_gap_task_declarations` 保留上述两个显式字段，沿现有规范化和角色校验，不新增默认值、不改优先级、不扩大 role 语义。来源证据：LS-v1 `docs/records/2026-11-10-post-core-final-dependencies/issue204-owner-field-proof.json`（当前 HEAD 实际 helper 的正/负例）；本次生成可独立运行、不依赖 LS 状态的回归。

## 验收

- [x] 声明 generic `dev` 加合法 owner_instance / preferred_impl_role -> verify: 经真实 gap artifact builder、product delivery materialization、严格合同校验保留实际实例，原依赖/AC/命令不变。
- [x] 两字段同时声明及未声明 -> verify: 原 owner_instance 优先级保持；没有声明不注入实例。
- [x] 未注册实例或任意未注册逻辑 owner -> verify: 仍由原严格合同校验拒绝；原 Legacy assembly 同 owner 自锁回归保持。
- [x] 实施影响闭包 -> verify: 新回归先红后绿；dev-verify plan/run、相关 mock E2E 和 pre-merge sentinel 通过，说明精确测试层级。
- [x] 更新已有相关实现说明并完成本地提交/任务归档 -> verify: 实现 hash 写入 done 记录；实现提交后无残留，本记录归档后交根监工唯一合并。

## 验证记录

以下 `python` 为 `/home/min/workspace/public/zaofu/.venv/bin/python`，测试使用 `PYTHONPATH=src` 从独立 worktree 加载受测源，不启动真实 provider 或 LS 服务。

- `python -m pytest tests/test_module_gap_task_declarations.py -q --no-cov --tb=short`：当前基线的有效夹具先得到 5 failed / 2 passed；三种合法实例声明在转换后丢失并遭合同拒绝，两个未知实例声明被吞。仅增加两字段保留后 7 passed。前置夹具的 gap_category、scope 和 verification 格式校正不计产品缺陷。
- `python scripts/dev-verify.py plan --base dev --json`：backend/docs/tests，12 个测试文件，无 warning/error，未选择全套。
- `python scripts/dev-verify.py run --base dev --receipt /tmp/zf-gap-owner-verify-receipt-20261110.json`：自动 deterministic tier passed。
- `python -m pytest tests/test_module_gap_plan_runtime.py tests/test_product_delivery.py tests/test_writer_fanout_admission.py tests/test_task_map.py tests/test_contract_validation.py tests/e2e/test_replan_checkpoint_closure_mock_e2e.py -q --no-cov -m 'not serial and not perf and not host and not real_provider'`：193 passed，涵盖实际调用者、合同校验、原 AVBS gate 和隔离状态 mock replan 闭包。
- `ZF_PYTHON=/home/min/workspace/public/zaofu/.venv/bin/python bash scripts/dev-premerge-gate.sh`：manual docs currentness OK，53 passed。
- `python -m ruff check src/zf/runtime/module_gap_task_declarations.py tests/test_module_gap_task_declarations.py` 与 `git diff --check`：通过。

实现提交：`2d714d17` — `fix: preserve task owner bindings in gap amendments`。归档阶段仅更新本任务状态与路径；最终 plan 的测试集合及步骤与通过的 receipt 完全一致。基于 `dd88c3ac` 的独立分支 `wip/codex-20261110-gap-owner-fields` 保留供根监工合并，未 merge/push，未变更 LS 运行态。关联：[Plan 候选与增量输入](../../docs/impl/plan-candidate-input-selection.md)。
