---
name: zf-evidence-synthesis
description: "在 ZaoFu evidence-synthesis-v1 的 scope、采集、综合或独立验证阶段执行资料核验；沿用当前阶段的产物合同，不负责启动流程或批准交付。"
stages: [research, verify]
tags: [evidence, research]
auto_inject: true
load_on_demand: false
---

# 资料核验方法

读取本阶段 briefing 中的 requirement、source manifest、required reads 与输出合同。
只承担被派发的阶段；采集者不能代替独立 verifier。复用注册的
`evidence-synthesis-v1` 和 `artifact_delivery` 完成合同，不建立第二个任务模型。

- **Scope**：将用户问题拆成需要覆盖的主张/问题，列出允许的资料、输入版本、
  比较口径与已知缺口。按互补视角分配采集任务；资料范围不明确时说明缺少什么。
- **Collect**：核对每项主张的直接证据、来源、定位和资料版本。区分来源所述、
  自己推断及无法证实；保留冲突，不能把多个转述视为独立来源。只读获准来源，
  引用原始 ref/digest，不在报告中泄露不需要的原文或机密。
- **Synthesize**：形成覆盖表，逐项关联主张、证据和不确定性。报告必须能回到
  具体资料；不能通过措辞把缺失、矛盾或过期信息合成为确定结论。
- **Verify**：独立读取报告及其绑定来源，检查必需覆盖、引用真实性、版本一致性
  和结论强度。产出当前 verifier 合同要求的结果；文件存在本身不等于核验通过。

独立验证时把固定身份与判断分开：原样保留 Run、版本、摘要和交付物引用，
但不能把 briefing 的成功示例当成预设结论；依据证据选择 passed/rejected/blocked。
来源独立不代表测量口径相同：核对对象、指标、时间和范围，不能仅因两个数量相等
便推断它们证明同一主张。正确指出证据不足的报告可以回答原问题；声称证据已经
证明一个事实的报告则必须真的有相应依据。

使用 artifact_delivery 合同时，goal_coverage.supporting_artifact_refs 指向合同中的
交付物；已经由本次准入输入绑定的上游资料引用放在 verification_evidence_refs，
并说明证据关系。仅把文件交给模型阅读，不等于已经具备当前结果的引用资格。
身份或结构不合法需修复协议，不通过修改摘要、改名报告或删掉负例来制造通过。

发现缺口时，用当前结果合同报告具体 finding、影响和最小补查范围。
只请求配置允许的有界补查；scope/来源变化由受控修订创建新 generation，保留仍有效
证据的引用。没有新证据或达到预算/次数上限时报告未决问题，不能无限重新采集。

报告及独立验证结果须经现有 admission，以 refs/digests 绑定。
由 artifact delivery 和 Goal Gate 判断本路线完成；不伪造代码 commit，
不把预研责任完成冒充交付父任务完成，不自行写 `task.done` 或 `run.goal.completed`。
