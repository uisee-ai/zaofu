# R31 补正：恢复方法使用实际 Task Map 字段

> 状态: done — d0f72074 docs: use canonical plan fields in recovery guidance

监工在复核 LS 已准入提案时发现刚补充的方法混用了落地 Task 合同与 Plan 字段；源 e815efde 中 required_artifacts 并非此处证据合同字段，验证对象应从 validation.commands[] 继承。修正方法用词，避免再次教错生产者；不变更 schema 或运行时。

- 对照实际 LS Task Map 与 zf-plan-task-map-contract -> verify: 使用 evidence_contract.base_path + required、validation.commands[]，说明落地 contract.verification_commands。
- 校验与部署 -> verify: metadata、dev-verify docs/skills档、sentinel通过；LS技能源摘要与dev相同，不修改在途技能快照。

验证：dev-verify 41 passed / 1 skipped（既有缺外部示例的条件跳过）；metadata无警告；sentinel53通过。LS补正提交39937fa，gap-task-synth源摘要5564f557；在途快照保持不变。原R31部署记录保留历史摘要，新记录以本补正为准。
