> 状态: done

# 真实 Plan 验证缺口修复

用户已批准上一轮报告建议。基线 dev@1cd237bb；依据 reports/spec207-dev-validation-20261116-0501/evaluation.md（主 checkout 保留）。

- B01 来源索引与提前机械预检 -> verify: 自定义 state_dir 的 state-relative、project-relative、绝对 ref 正常准入；缺失索引在 Critic 前拒绝，不产生任务副作用。
- B02 任务阅读合同 -> verify: 声明分区 schema 与实际正文一致；Planner/Critic 合同明确完整模块正文、精确共享执行引用及 affected_task_ids；两种合法阅读入口的浏览器回归通过，旧产物不伪造新正文。
- B03 回归与真实验证 -> verify: 影响闭包、sentinel、fake 双路径、真实 Kanban/Channel 的最终模块正文及当前阅读证据；另记录符合增量前提样本的配对成本，未知不计零，不将回退全量宣称节约。

范围：修复与方法完善，报告 Markdown/HTML。保留未通过及返工证据，不审批真实样例 Plan，不实施样例产品。提交实现与任务状态，不 push、不擅自 merge。写入守卫不放宽权限，本批只改善明确合法的工件生成指引。

阶段验证：新增测试先复现旧误拒/格式漏检，修复后基础71项、影响闭包316项通过；Web build通过；dev-verify保守扩展为全库，按仓库层级用明确调用者及共享合同测试替代deterministic/serial全库选择，其余自动层（Web检查、53项sentinel、10项flow-smoke）通过。fake两路径及逐模块正文检查通过。真实 Provider 与成本复验已完成，最终结果及限制如下。


实现提交：
- c270c410 fix: align Plan source admission and module reading contracts
- f0491750 fix: bind explicit Task Spec headings across punctuation variants

最终验收：
- B01：旧路径误拒及 descriptor/body 不一致先复现；修复后通过。316 项影响闭包、130 项输入合同通过；53 项 sentinel、10 项 mock flow 和 Web 自动层通过。同一历史失败包只读复放通过。
- B02：真实 Kanban 首轮 Critic 接纳无冒号标题，新增浏览器断言正确失败。保留原退出1；仅阅读器兼容明确 Task ID 的标点形式后，同一工件2/2模块通过，未知/重复/伪标题防护保持。Channel 原始运行2/2通过。最终主工作区构建后4/4模块、四个Plan桌面视口及PRD两个视口通过；原文、下载和事件摘要不变。结构化入口与原文入口均由现有浏览器夹具覆盖。
- B03：fake两路径通过；真实Channel exit0/协议审计通过，Kanban原始exit1/模块失败保留，协议只读补审与修复后浏览器补验通过。两条均一轮Planner/Critic，无Owner实施审批。成本正式18次判断符合预期，53–65%选读减少，但配对延迟中位数约慢5.96%；30%提速、50%重复正文减少未证明，不标性能目标达成。最初18次不公平试跑完整保留。

增量命令：`npm run build`；`node scripts/run-unit-tests.mjs tests/planReviewModel.test.ts tests/planReviewDocument.test.ts tests/planMarkdownPages.test.ts`（实际分两次执行）；`scripts/dev-verify.py run --base c270c410` 自动层通过。Docker三个阅读器浏览器文件首轮38/39，流程图20秒超时未改代码/预算后单独复测6.6秒通过；保留两次记录。未执行full pytest。

报告：reports/plan-reading-fix-20261116-0611/evaluation.md 及 HTML，包含原始失败、同工件补验、方法投递、真实阶段耗时、独立阅读摘录和全部成本调用。后续边界：Critic阅读摘录执行不一致、正文去重/路径代码标记、局部AC来源显示仍需下一批优化；实际增量降本需真实批准基线及配对流量后再验证。本批修复和验证完成不等于206/207所有质量/成本目标完成。

未合并dev、未push；保留干净工作区供后续合并。
