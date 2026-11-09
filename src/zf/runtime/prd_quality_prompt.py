"""Versioned agent method for authoring and independently reviewing a PRD.

These are instructions to an evaluator, never keyword-based quality gates.
The runtime freezes this method alongside the input and checks ID coverage.
"""
from __future__ import annotations

from typing import Any


def prd_review_method() -> dict[str, Any]:
    return {
        "version": "prd-quality-review.v1",
        "criteria": [
            {"criterion_id": "PRD-Q1", "dimension": "来源与兼容基线",
             "expected": "区分来源已读、artifact 可读和事实已核验。逐项记录旧版本、接口/Schema、部署环境、"
                         "稳定 ref/digest、核验方法与缺口；目标设计、作者声明或一个路径不能证明 v1.3 等旧系统事实。"},
            {"criterion_id": "PRD-Q2", "dimension": "Owner 决策与阶段就绪",
             "expected": "质量阈值、SLO、容量、部署、数据驻留、灾备、留存等适用决定有授权来源；未知值保持 TBD，"
                         "记录决定者、影响行为、最迟决定时点、允许继续范围与重访条件。精确保留错误码、数值、"
                         "Unicode 长度/normalization/fallback，在正文、场景、AC、companion 中保持一致。"
                         "deferred 不是批准，不可同时写为已定 MUST。未来审批/测试结果不阻塞首次文档生产。"},
            {"criterion_id": "PRD-Q3", "dimension": "独立评审覆盖",
             "expected": "按来源风险与请求判断 PM、架构、资深开发、安全等视角的适用性；记录 reviewer/member、"
                         "职责、独立性、同版 ref/digest、检查范围、发现及处置。角色名、邀请、作者自评、签字数量"
                         "不等于已独立评审；缺所需 review 证据明确为未完成，不能由当前评估者冒充多位 reviewer。"},
            {"criterion_id": "PRD-Q4", "dimension": "真实数据与验收证据计划",
             "expected": "每条 AC 映射场景、验证方法/命令、负责人、环境、数据来源/版本、授权或脱敏要求、预期观察、"
                         "producer_paths 与执行阶段。区分真实数据、批准脱敏数据、mock fixture 和未知数据；"
                         "未执行不等于失败或通过。声称执行过必须提供对应版本、结果和证据引用，"
                         "不存在的测试文件标为后续生产，不伪造测量、脱敏批准或现成证据。"},
            {"criterion_id": "PRD-Q5", "dimension": "阶段交接与标准兼容",
             "expected": "给出来源/Requirement/AC/命令到 companion 和后续 plan/tasks 的索引、生产者与触发条件。"
                         "ZaoFu 的 canonical PRD、prd-plan.md、task_map.json/source_index.json 可映射 Spec-kit "
                         "spec/plan/tasks；不强制固定文件名，不创建第二份 Task Store。OpenSpec 变更区分 "
                         "ADDED/MODIFIED/REMOVED，沿用命名 Requirement/Scenario；无变更无需假造 delta。"
                         "Spec-kit analyze 在 tasks 生成后核对，assess 为可选扩展；首次 PRD 不要求未来工件已存在。"},
            {"criterion_id": "PRD-Q6", "dimension": "产品价值与阅读体验",
             "expected": "正文清楚表达 what/why、背景、目标/非目标、用户与场景、流程、业务状态/权限、依赖/风险。"
                         "PM 可判断价值和范围，架构师可判断边界与约束，开发可定位 AC。实现选型放 companion，"
                         "已有技术约束保留来源。不得给所有产品强加 ZaoFu 架构、某技术栈或字数。"},
            {"criterion_id": "PRD-Q7", "dimension": "可测试需求与异常路径",
             "expected": "优先级 User Story 有价值理由与独立测试；每项原子需求都有自己的命名 GIVEN/WHEN/THEN "
                         "场景和 AC，不能靠全篇 Scenario/MUST 总数冒充逐条绑定。覆盖关键实体、状态转换、"
                         "权限拒绝、边界/异常与恢复，按产品适用性检查而不杜撰功能。"},
            {"criterion_id": "PRD-Q8", "dimension": "可度量结果与明确范围",
             "expected": "成功指标是可观察的用户/业务结果，NFR 有测量方法、责任人和时点；基线/目标无来源时"
                         "不发明数值。定性约束有可审查观察方法，假设、依赖、关键澄清、风险和范围明确。"
                         "当前无法验收的关键 TBD 影响就绪度，已诚实处置的未来决定不自动导致文档质量 FAIL。"},
        ],
        "rules": (
            "质量清单与 SOURCE 提炼的标准分别冻结。inventory.criteria 只含来源标准，不能抄入 PRD-Q*。"
            "target.matrix 须同时覆盖冻结来源标准、有效决定 criterion_id 和全部 PRD-Q*，每项给实际定位、"
            "证据和反例；不适用项须解释适用性，不能空泛 PASS。focused 只声称实际检查范围，未查范围 "
            "INDETERMINATE。标准由评估者逐项语义判断，不使用关键词计数。"
            "文档质量、来源忠实度、规划就绪度、实施授权、产品验证分别报告。证据缺失不是反证；"
            "诚实记录缺口不代表外部事实已核实。质量 PASS 可以和 product_verification=未执行并存。"
            "checklist 标记只表示需求质量被审查，不能表示实现完成或批准执行。"
        ),
    }


PRD_AUTHOR_GUIDANCE = (
    "在 implementation_companion 中维护 source_baseline（旧版本/接口/环境、来源 ref/digest、"
    "核验方法/结果或缺口）、review_coverage（所需视角、实际 reviewer、同版证据、发现及处置）、"
    "acceptance_data_plan（AC、数据来源/版本与授权/脱敏、环境、观察、负责人、命令/producer_paths、"
    "执行阶段与 NOT_RUN/阻塞状态）和 handoff（spec/plan/tasks 对应 artifact、生产者、触发条件）。"
    "未知字段明确标未知，不为填表编造信息。decision_log 记录授权来源、影响范围、决定者、"
    "最迟决定阶段、暂行假设与重访条件；保留正文/场景/AC 的同一精确决定。"
    "不要复制已有 AC/命令目录，使用其稳定 ID。prd_quality_checklist 逐项记录方法中的质量维度、"
    "检查范围、证据/位置、状态与发现；作者自检明确标为自检，独立评审由真实 reviewer 出具。"
    "先前版本的签收不能作为当前版本的完成证据。尚未生成当前 PRD 时列评审计划即可，"
    "不能要求对当前输出的 review 先完成。实际产品测试与未来 plan/tasks 未产出不阻塞首份 PRD。"
    "兼容 Spec-kit 要检查价值/非技术表达、独立故事、可测试需求、可度量结果和边界；"
    "兼容 OpenSpec 保留 Requirement/Scenario，变更才做 delta。缺少固定 spec.md 文件名或可选 "
    "assess 扩展不等于质量失败。已选架构需要来源约束/决定证据，放 companion，不泛化为全产品门槛。"
)
