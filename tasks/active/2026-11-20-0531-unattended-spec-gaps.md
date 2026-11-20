# 无人执行 Spec：当前 dev 已确认缺口修复

> 状态: active

来源：用户授权“执行下一步，基于 dev 补 Spec 缺口”；基线 d442a9bd。
对应本地规格 ZF-SPEC-004 r2（docs/spec/zaofu-unattended-execution.spec.md）。
此前只读对账已确认其他 worktree 没有需要合入的通用实现。

## 本批交付

1. Reader 纠错发送返回丢失，精确接收证据出现后正确对账。
   step -> verify: UE-04-A/B/C；真实 Orchestrator sweep + mock transport，重启、
   重复 sweep、暂停观察、过期身份与无回执都不重复发送或冒领业务完成。
2. RM 恢复建议接通现有受控动作，并支持精确定位失败 Reader。
   step -> verify: UE-05-A/B、UE-06-A；建议消费、准入、执行器和下游派发闭环，
   同候选健康兄弟保持不动；过期身份、暂停/取消和无授权不绕过。
3. 完成缺少 gate 的监督信号按任务及运行身份核对。
   step -> verify: UE-15；其他任务或旧运行的 gate 不消除当前告警，
   合法当前证据不会误报；此检测不改变核心业务结果准入。

## 实施约束

- 使用既有事件、operation、TaskAttempt 和受控动作，不建第二状态机。
- 先复现，再修复；执行 impact closure、premerge sentinel 和确定性 mock。
- 本批不启动历史产品 Run，不安装/部署宿主服务，不更改费用和权限。
- 记录所有 UE 条款的后续差距；本批通过不等于完整 Spec 或真实 0 监工资格通过。
- 完成后独立分支提交并归档，保留 worktree 供后续合并。

## 验证结果

初始 RED：Reader 回执 4 个场景失败、RM 动作 1 个场景失败、跨作用域告警
5 个场景失败，均在 d442a9bd 复现后修复。

最终验证（本 worktree，2026-11-20 UTC）：

- PATH 加入共享工具环境的 `.venv/bin`，运行
  `python scripts/dev-verify.py run --base dev --workers 8`：
  6834 passed / 9 skipped；premerge 53 passed；flow smoke 10 passed。
  选中 533 个测试文件，属于影响闭包，不是 full pytest 或真实 Provider 验收。
- 新增的回归验证同 attempt 纠错回执、重启和暂停观察、重复请求幂等、
  派发前再次检查 Run/身份、当前 Reader 完成后才关闭效果等待，以及其他
  Reader/旧 attempt 的结果不能冒领恢复。七个健康兄弟与已完成共享根任务保留。
- 初轮影响回归 11 项环境失败（子进程 PATH 缺少 zf/python），补齐验证命令的
  PATH 后按同一计划重跑全部通过；没有放宽断言或修改业务代码来跳过检查。
- `git diff --check` 通过；未引入凭据、运行时状态或 gitignored Spec。

实现和剩余条款说明见 [持续推进与无人资格](../../docs/impl/unattended-progress-recovery.md)。
本批覆盖 Q05/Q07/Q13 及 Q18 的相关 mock 路径，不宣称 Q01–Q23 整体完成。
UE-07 语义检查点、UE-09 额度恢复闭环、UE-03/10 复合故障域、
UE-11/12 真实自主修复部署及 UE-16 新版本零监工资格仍需后续工作。
