> 状态: done

# Spec 同条件生成与独立交接实验

历史来源：design 206 阶段 B；上一批仅完成公开能力分析与评估方法。

触发：冻结可比的工具版本、六类输入、模型/参数与预算，并配置未参与作者工作的评估者后执行。
先做正反例校准，再执行同条件生成与交接；所有失败/超时计入。模型不可统一时作为独立混杂组报告。

- 执行实验 -> verify: 记录每样本的输入/工件 digest、原始回答、误报/漏报、澄清、返工、时间和 token；不能仅展示最好一次。
- 汇总 -> verify: 能力、产物、效果分开，复评 206 时写明增加了什么证据。

本轮用户已授权继续实施：固定内部基线/候选、六类样本及独立交接；已复现的问题直接修复，不等待外部工具比较。

## 本次批准范围与实际结果

本次按用户继续实施指令，将执行范围落实为内部固定基线/候选六类试点，外部工具保持公开能力分析。
原计划“先校准”未满足：实际在生成后补做，已在报告披露，不宣称预先资格通过。

- 冻结六类输入及 oracle，实际生成/新会话交接 -> verify: R4 24 次、R5 12 次；全部原始轨迹/digest/usage 保留；同模型一次试点，不是统计优势。
- 发现方法来源不可交接后修订 -> verify: 候选四例出现 method.md 引用，R5 六例未再出现；三组仍有一个因命令未给出的 partial。
- 输入澄清及正反校准 -> verify: 相同补充命令后两组 R1–R4 covered；两类负例均识别、最终两类正例无冲突；两个不完整的预期正例版本全部保留，不伪称误报。
- 独立实现验证 -> verify: 两个小接口共六份通过冻结 oracle；来源判断/问题条数没有转成伪精确误报漏报率；真实产品返工未测。

详细结果与所有环境失败分类见 [206 实施评估](../../docs/impl/206-spec-quality-evaluation.md)。

验证闭包：`dev-verify.py plan/run --base dev`，显式补充 test_spec_handoff_eval、test_spec_handoff_verification、test_eval_provider_container、test_plan_execution_eval；101 passed、2 skipped，Web typecheck/unit 通过；生产 build 通过；Docker 浏览器原有 19 项及新增证据比较 1 项通过；manual-docs check、diff check、三份 Skill 字节及 36 份 R4/R5 轨迹摘要核对通过。未执行全量 pytest/真实 v5 E2E。

日志：`reports/spec206-next/`（忽略）；未 merge/push。

实现提交：`86a65e85 feat: improve spec reading and validate bounded handoffs`。本项按上述本次批准范围完成；扩大效果资格的未测项保留在 206，不冒充已通过。
