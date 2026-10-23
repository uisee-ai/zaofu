> 状态: done
> 实现 commit: `41f7a81f fix: preserve semantic ownership of automatic intake routing`

# 184 · P0：自动语义路由边界

来源：设计 184 第 8、10、11 节及本轮代码审查；基线 `d81797f5`。用户已授权先生成 backlog 再实施。

## 范围

Intake/CLI auto 保持未决，关键词降为提示；显式 kind/route 保持确定性路径；复用现有 Synthesis Operation，旧请求不隐式迁移。

## 验收

1. 否定句与混合输入 -> verify: auto 不产生 issue/refactor 等有效类型或默认交付 lane，未决输入不能直接 submit
2. 显式选择与 Synthesis -> verify: 显式路由无需模型调用，语义结果经版本与目录准入生成提案
3. 重放旧请求 -> verify: 保留原类型与版本；Provider 失败无关键词回退；新入口有直接调用方测试

## 验证与收尾

聚焦测试、共享契约、dev-verify 与适用 mock E2E；完成后本地实现 commit、记录 hash、归档。真实 Provider 评测为显式运行层，不用模拟结果代替语义能力结论。

实施说明：[184 Intake 语义路由与交接验收](../../docs/impl/184-intake-semantic-routing.md)。

## 验收结论

本项实现及确定性验收完成；精确命令、结果、已修复的首轮失败见上述实施说明的“本批验证记录”。
自动层 Web/typecheck/unit、51 项哨兵、10 项 smoke 通过；主链路、调用方和反例评测闭包通过。
真实 Provider 语义准确率与实际 Critic 评审未运行，不作为本项完成依据。
