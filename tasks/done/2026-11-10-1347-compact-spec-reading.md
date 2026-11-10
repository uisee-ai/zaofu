> 状态: done

# 紧凑规格与任务定位

来源：用户批准继续实施 design 206，按基线/修复、多样本、成本评估顺序推进。

- 实施上述范围 -> verify: 真实夹具复现重复；本片默认阅读与共享上下文可达；完整原文、缺失提示、只读及移动端回归通过。

## 本次结果

- 历史 R2 T-1/T-2 原文重复两章、843 字符 -> verify: 原摘录与摘要保存在固定夹具；相同章节折叠可展开，重组/下载原文字节不变。
- Task 直达来源/AC/命令/证据并可返回全局 -> verify: 精确 ID 单测和浏览器导航通过；双视口、原文及只读回归通过。
- 精简写作方法，保留职责与失败不变项 -> verify: 8646→6763 字节；三技能目录一致且实际物化测试通过；不改变 Kernel 权威。

折叠限于 Markdown 精确 Task 分区，execution_spec 分区仍直接展示。方法文字缩短不是理解速度证明。

验证闭包：`dev-verify.py plan/run --base dev`，显式补充 test_spec_handoff_eval、test_spec_handoff_verification、test_eval_provider_container、test_plan_execution_eval；101 passed、2 skipped，Web typecheck/unit 通过；生产 build 通过；Docker 浏览器原有 19 项及新增证据比较 1 项通过；manual-docs check、diff check、三份 Skill 字节及 36 份 R4/R5 轨迹摘要核对通过。未执行全量 pytest/真实 v5 E2E。

日志：`reports/spec206-next/`（忽略）；未 merge/push。

实现提交：`86a65e85 feat: improve spec reading and validate bounded handoffs`。本项按上述本次批准范围完成；扩大效果资格的未测项保留在 206，不冒充已通过。
