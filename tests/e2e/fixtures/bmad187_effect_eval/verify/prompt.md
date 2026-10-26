你是 Candidate Verify 的独立评审者。先读 `.agents/skills/zf-yoke-test-evaluator-role-context/SKILL.md`，按触发条件读取 verify-review、tdd-evidence 及其引用。
审查 source.md、handoff.md、src/ 与 tests/。这是只读评审，不是平台结果提交；目标身份以 handoff 的样例版本说明为准，不要求宿主仓库提交。
可以执行不写文件、不联网的纯内存探针来验证假设；禁止安装依赖和修改文件。不要重新执行已明确允许复用的相同确定性 unit 命令。
给出产品 verdict、可复现的具名回归和现有测试漏检原因、TDD 证据结论、返工 owner、Candidate/Thin Judge 的责任边界。不要为了证明测试先行破坏既有正确行为。
中文最多 1800 字。每个必改点给具体代码/输入/断言依据；无问题的部分明确保留。
