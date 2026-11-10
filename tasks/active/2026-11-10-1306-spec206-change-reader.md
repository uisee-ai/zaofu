> 状态: active

# 基于实测缺口决定规格变化阅读视图

来源：design 206 阶段 C；当前不因外部工具有 delta 就增加 runtime/schema。

触发：独立交接/变更实验反复发现消费者或保留行为难定位，并能在当前 HEAD 复现展示缺口。
先定位现有 Package/来源/修订工件能否给出同版信息，再做最小只读投影；若是作者语义缺失，优先修方法。

- 复现 -> verify: 具名原始要求、旧新版本、正确消费者和阅读失败记录。
- 形成方案并按批准范围实施 -> verify: 未知/未授权变化不被推断为已生效，不新建第二 Task 合同。

本轮用户已授权继续实施：固定内部基线/候选、六类样本及独立交接；已复现的问题直接修复，不等待外部工具比较。

## 本次结果

按本轮授权先复现已知机械展示缺口，不等待外部产品或多次语义实验：
- 相同条款仅证据位置变化，原比较签名漏掉变化 -> verify: 新单测复现并修复；浏览器读取两版矩阵后显示唯一变化及旧/新位置。
- 查看明确旧/新消费者 -> verify: 同一变化条目可定位 T-STOCK；读取不对称显示无法完整比较，旧结果清空。
- 复用原来源/覆盖入口 -> verify: 只改 Web 投影，无 runtime/schema 变更；不推断语义生效、间接消费者或自动使旧证据失效。

历史 R2 原文、迁移案例及具体展示证据见 [206 实施评估](../../docs/impl/206-spec-quality-evaluation.md)。原设想的完整语义变化/消费者效果资格未宣称完成。

验证闭包：`dev-verify.py plan/run --base dev`，显式补充 test_spec_handoff_eval、test_spec_handoff_verification、test_eval_provider_container、test_plan_execution_eval；101 passed、2 skipped，Web typecheck/unit 通过；生产 build 通过；Docker 浏览器原有 19 项及新增证据比较 1 项通过；manual-docs check、diff check、三份 Skill 字节及 36 份 R4/R5 轨迹摘要核对通过。未执行全量 pytest/真实 v5 E2E。

日志：`reports/spec206-next/`（忽略）；未 merge/push。
