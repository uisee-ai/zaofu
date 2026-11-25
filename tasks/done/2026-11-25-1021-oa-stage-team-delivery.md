> 状态: done
> 实施提交 `d8bf60df feat: dispatch OA-selected stage teams and trace their handoffs`。

# OA 阶段组队和执行工作台

本批次验收工程实施和可复现的确定性测试。按 [215](../../docs/design/specs/215-oa-stage-teams-and-execution-workbench.md) 实施并核对第 7、8 节。开发基线为 `9451996a`。

- 合成并准入缺省、空、单、多专家的方案 -> verify: 检查实际阶段派发、唯一计划作者、越权拒绝和回放。
- 从已准入分工派发独立验证 -> verify: 核对并发 Task Verify、冻结 Reader、Judge 必需意见和唯一判词。
- 打开阶段工作台并回放 -> verify: 在同一快照对账已选未启动、Task/Agent 关系、消息、等待和实际回执。
- 重放失联、超时、两种诊断模式及重启 -> verify: 查看下游实际领取、失败责任、复用和合法等待。
- 比较常驻与按需 RM -> verify: 用配对工具核验确定性样本、人工干预和运行身份，记录真实样本的缺口。真实小需求、跨域 Plan 和两组 8×8 的新批准窗口以及默认策略放行，单列在[实证验收记录](../../docs/impl/spec215-qualification.md)。

复评复现并修复了四条原本会停住或误报的路径：

1. Plan 准入返工缺少专家交接时，检查原 Plan 与同 Run 的专家证据，复用有效报告或有界重派原团队。重放后仅有一个正式后继。[正式后继包验收](../../tests/test_plan_team_replan_package.py)
2. WRC 缺少任务状态或专项选择了不存在的合同字段时，会停在派发前。现已向调度投影提供任务状态；字段缺失绑定原 Task 和摘要交回 OA，其他任务照常推进。[派发回归](../../tests/e2e/test_oa_stage_team_dispatch_mock_e2e.py) · [局部纠正](../../tests/test_oa_verification_teams.py)
3. 诊断空回执曾让已耗尽重试的 Reader 失去接手人。现可从原案卷进入按需诊断，重启后只执行一次获准的恢复，保留兄弟任务结果。[诊断交接](../../tests/test_diagnosis_timeout_handoff.py)
4. 仅更新一项任务时，工作台曾漏掉其他代次仍有效的派发或完成回执，新代次也可能误用上次冻结的判词。现按 Task 合同和候选冻结各自核对来源。[工作台](../../tests/test_stage_team_workbench.py) · [任务回执](../../tests/test_sdlc_graph.py)

## 验证结果

- `python scripts/dev-verify.py run --base dev --workers 16 --keep-going` 自动层全部通过。Python 确定性并行 15,118 项通过、19 项跳过，串行 4 项通过；Web 类型与单元检查通过，预合并哨兵 53 项、流程 smoke 10 项通过。
- Docker Chromium 工作台 24/24 通过，最终 API 上的三个相关路径复验 3/3。覆盖真实只读 API，以及按义务、任务和冻结版本模拟的业务快照。锁定的浏览器镜像、trace 和截图在工作树的 `reports/spec215/browser/validation.md`。
- Synthesis skill 结构校验和两端分发副本一致性通过。真实 provider 试点与默认配置放行见[资格验证记录](../../docs/impl/spec215-qualification.md)。
