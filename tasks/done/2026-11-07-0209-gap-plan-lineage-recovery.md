# 修复 LS-v1 增量计划跨代际证据绑定

> 状态: done — `721cfcbd fix: bind gap recovery to its admitted plan generation`

授权：用户要求继续监工 LS-v1，分析并修复阻塞，恢复后持续观察并记录。

## 复现与范围

真实 Run `prepare-TASK-1B5607-c96302e2de2f19fd` 的 gap amendment 在较新计划存在时混用 task_map 路径与历史已准入代际，验收继承错误地选择全局最新包。`evt-8b1a5729832a` / `evt-8126e6cda4b5` 因 exact prior plan lineage 拒绝，后续到达 stage replan cap。

`09d7025d` 基线已复现新旧计划并存时 ProductAcceptanceError。仅修复机械证据关联；不更改业务验收，不增加重试上限，不伪造正式契约批准。业务修复及真实恢复结果另记 LS-v1 的 `docs/records/2026-11-07-three-blockers-recovery.md`。

## 验收

- 按 Run + 已准入代际解析 gap 基线 -> verify: bridge 回归证明 amendment 使用历史包 task_map，拒绝未准入代际及摘要不匹配。
- 按前置代际继承验收 -> verify: 新旧计划并存场景仍保留正确验收工件与 supersedes_package_ref。
- 调用闭包 -> verify: gap/plan package 回归、串行锁测试、premerge sentinel、mock flow smoke。

## 验证记录

- `dev-verify.py plan --base dev`：backend/docs/orchestration/tests，248 个受影响文件；此前自动广域执行日志 `/tmp/zf-ls-blockers-20261107-verify.log`：14,568 passed / 6 failed / 20 skipped。失败为已记录基线：导出脚本缺失四项、prompt 缺失一项、Feishu readiness fixture 一项；未宣称全绿。
- `bash scripts/dev-premerge-gate.sh`：53 passed；`bash scripts/run-flow-smoke.sh`：10 passed。
- `PYTHONPATH=src ... python -m pytest tests/test_gap_plan_baseline.py tests/test_plan_acceptance_rework.py tests/test_module_gap_plan_runtime.py tests/test_plan_artifact_package.py tests/test_memory_rotate_concurrency.py tests/test_session_mutex.py tests/test_state_locks.py -q --no-cov`：73 passed，覆盖上述调用闭包与剩余串行测试。
- 共享 Python 环境首次直接调用 pytest 未设 PYTHONPATH，收集时加载主 checkout 而缺少新模块；随后以 `PYTHONPATH=src` 明确验证本 worktree。该错误属于测试启动方式，未计为产品回归。
