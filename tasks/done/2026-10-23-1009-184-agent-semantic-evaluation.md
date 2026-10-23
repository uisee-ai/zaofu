> 状态: done
> 实现 commit: `41f7a81f fix: preserve semantic ownership of automatic intake routing`

# 184 · P1：入口语义评测

来源：设计 184 第 8、10、11 节及本轮代码审查；基线 `d81797f5`。用户已授权先生成 backlog 再实施。

## 范围

建立固定语义正反例和离线可验证的评测入口；可接实际主 Agent，逐例记录原始输出、模型与失败，禁止将预制答案称为模型能力证明。

## 验收

1. 普通问答、否定、混合、明确交付与澄清 -> verify: 固定样例分别声明允许的动作与路线，评分不靠关键词推断用户意图
2. 评分器反例 -> verify: 错误路由、越权建单、非法输出均不能通过；未执行 provider 标为未测
3. 真实执行入口 -> verify: 使用隔离 read_only 主 Agent 和有效目录，不 apply 业务动作；模拟测试验证调用及结果记录

## 验证与收尾

聚焦测试、共享契约、dev-verify 与适用 mock E2E；完成后本地实现 commit、记录 hash、归档。真实 Provider 评测为显式运行层，不用模拟结果代替语义能力结论。

实施说明：[184 Intake 语义路由与交接验收](../../docs/impl/184-intake-semantic-routing.md)。

## 验收结论

本项实现及确定性验收完成；精确命令、结果、已修复的首轮失败见上述实施说明的“本批验证记录”。
自动层 Web/typecheck/unit、51 项哨兵、10 项 smoke 通过；主链路、调用方和反例评测闭包通过。
真实 Provider 语义准确率与实际 Critic 评审未运行，不作为本项完成依据。
