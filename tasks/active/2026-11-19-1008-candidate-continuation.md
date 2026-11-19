> 状态: active

# 冻结候选的受控验收接续

来源：Owner于2026-11-19要求复用上一轮成果，随后明确批准“执行下一步，然后继续监工，评估，调研”。基线dev b93a1a07；证据为主工作区reports/eight-reader-real-20261119/incremental-continuation-review.md。

范围：已终止Run的有效冻结候选可经Owner受控创建独立后继验收Run，保留旧Run终态、旧Task和原始证据身份。不重做Planner及已通过Impl；不把旧控制结果伪装成新运行结果。原Candidate/Goal验收不降级，不新增第二套Task schema。

## 实施及验收

- 受控预览与创建后继 → verify: 精确旧Run/Candidate/目标/Plan/批准/Task独立证据绑定，拒绝缺失、篡改、未完成、漂移、无授权；重复请求幂等。
- 接入真实Runtime入口 → verify: 新Run正常admission后只进入既有Candidate验收链，不启动scan/plan/Impl；重启重放不重复发布入口，原Run仍取消。
- 保留有效证据来源 → verify: 新验证合同显式链接旧Task权威和候选；不更改旧Task所有权、不采纳错目标/错代际结果；Judge/Goal仍为新运行独立验收。
- mock先行 → verify: 新入口正反例、状态/事件/调用方影响闭包、sentinel及相关确定性E2E通过；不测试Go，不push。
- 真实接续 → verify: 从K5冻结候选开始，记录实际源码和人工操作边界；监工观察自动推进，问题留档并按影响范围修复。真实接续不是“最新源码从零8×8无人资格”的替代证据；P2保持独立结论。

实施文档沿docs/impl/unattended-progress-recovery.md更新；旧K5/暂停K6及无关工作区不清理。

## 已实施与阶段验证

已接入显式token Web action、精确预览/复核、原admission和候选Reader入口、原Task
证据继承、新Goal/PAS身份绑定及空闲重启补发。原Run取消、原Task权威和独立门禁保留。
Mock证明8Reader派发且无Plan/Impl、崩溃补发和暂停/终态拒绝。

影响闭包初跑5676 passed/9 skipped，3项因验证shell未配置python而失败；正确PATH下
这3项及最终接续16项全部通过（19 passed）。Web typecheck/unit、53项sentinel及flow
smoke通过。后续PAS身份修复及直接调用方144 passed；保留RED和环境失败原始记录。
source action/runtime由tests/test_candidate_continuation.py显式覆盖并在dev-verify传入，
未缩小其余自动影响闭包。实现提交bf8481bb feat: continue frozen candidate verification in a successor run。

真实K7已沿K5进入Candidate，原16 Task保持done、Plan/Impl未重复派发；随后发现
Goal/Plan事件漏必填字段且未检查写入拒绝，生产schema阻断Reader入口。已受控暂停
同一Run并停止Provider，计1次修复介入。修复包含v5 schema回归、缺失生命周期幂等
补发及真实canonical Reader入口继承原TaskRef校验；保留RED及原始失败账本。
K7最终Reader/Judge/Goal/交付未验收，任务保持active，P2无人资格不归档。

修复05ba2f05已通过完整dev-verify：5773 passed/9 skipped，Web及53项sentinel、
10项flow smoke通过。真实恢复证明空闲补发成功，但先处理积压Candidate产生一次
额外失败；第二次维护将补齐动作接入Reader实际派发前，回归验证补齐失败不启动fanout。
