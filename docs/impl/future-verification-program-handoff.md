# 后续验收程序交付的语义检查

任务：[补齐交付和验证方法](../../tasks/2026-11-08-1255-future-probe-handoff.md)。修改现有 `zf-harness-done-contract` 与 `zf-yoke-test-evaluator-role-context`，源及Codex/Claude分发同步。

监工在LS-v1冻结候选中发现8条未来E2E命令无条件失败；原Task local检查通过，未来阶段未运行，所以Run Manager没有失败输入。OA/Verify未识别程序本身应由当前owner交付，这是语义交接漏检，采用agent方法修复，不增加runtime关键字扫描。

方法要求先核对program生产者和已批准交付范围，然后区分当前程序是否可消费、最终真实环境是否通过。前者用实现审阅及有意义的预检/自测验证，后者保留原Candidate gate。后续owner或未明确责任不能转成当前Task的隐含拒绝条件。

本次仅有方法对账、分发一致性和既有角色物化/指令生成测试，不是独立盲测或真实Provider收益对照。真实8项程序交付及后续Verify持续观察；不能声称方法改动已经消除所有漏检。在途会话保留原技能快照，新派发由现有resolver重新物化，禁止为刷新文本重启当前worker。

验证：角色激活/解析/必需技能派发33项通过，自动docs/skills闭包41项通过、1项跳过；源与两处分发逐字一致。通用quick_validate不认识仓库既有stages/tags/auto_inject等扩展，两个技能均按既有格式被拒；frontmatter与基线完全一致，使用本仓库解析及物化测试验证兼容，未删项目元数据迎合通用校验器。
