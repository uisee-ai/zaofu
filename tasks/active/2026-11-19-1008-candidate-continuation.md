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

74a02915真实恢复后8个Reader均进入running并产生受控读取。发现带continuation_key
的Candidate输入恢复先解析空锚点Task合同而返回unknown/rebind_required，实际候选
权威检查为true。已以同形活动锚点复现RED并改为优先校验不可变Candidate权威；
58项回归通过，真实只读回放由unknown变为current。Docker沙箱失败已由Run Manager
自动进入预授权宿主采证，监工未放开沙箱权限；最终验收继续等待真实结果。

9cc5b624保存输入恢复修复，最终94项影响闭包及53项sentinel通过。在线进一步发现
首Reader19条宿主采证完成、仍等待fanout聚合时，为第二Reader重新执行相同命令。
修复按精确Run/fanout/target/contract协调已采证owner，不额外改写reusable或产品
verdict；同组重复及不同组独立推进回归RED→GREEN，268 passed/2 host deselected。

Reader 0/5随后读完材料并确认浏览器回执通过，但图片只有base64，无法完成视觉
审查。既有媒体投影只在失败恢复入口调用，首次接续未接入。复用该投影器补齐入口，
以真实Orchestrator消费者验证首次即可读图片、损坏摘要不得发布Candidate；两项
RED复现后，99项接续/媒体/恢复/输入回归通过。旧K7的现有失败仍等待原恢复机制交回，
不把源码修复或图片存在当作产品验收通过。

第三次源码维护后Reader7停在环境等待：Provider已退出，受管空bash仍以cwd持有
工作区；请求从未投递但因暂停标成suspended，原空闲释放条件遗漏这两种形态。
两项RED复现，补齐严格身份/历史/受管空进程树验证，保留未知和曾派发请求的阻断。
106项Reader/角色生命周期/transport/CLI/环境影响闭包通过，真实恢复另行记录。

第四次维护只替换控制器，保留活Provider。Reader7在补丁加载前已等待超时，未
宣称此次自动释放成功。Reader3停顿进一步定位为遥测漏返回：原生会话有精确
成功工具返回，账本只有use。增加当前绑定会话的不可变完成观察，未观测到的
工具仍阻断；Reader2将已修正的分页参数错误及未读完作为终态弃权，补齐方法
指引。校正测试夹具后旧HEAD两项RED，修复后32项针对性回归通过；真实推进、
最终Judge/Goal及无人资格仍需独立观察，不将单项修复计作全流程通过。

本次增量最终验证：241项影响闭包、53项sentinel、7项确定性flow smoke通过；
真实只读预检精确找到Reader3漏采返回，不写运行状态。完整基线5773项验证沿用
此前记录，本次不冒称重新全量验证。三份Evaluator skill逐字一致。

ee2f137a为工具返回观察/分页方法修复。加载前Reader3已经idle超时，并触发整批
timeout，导致其余五个活Reader的原生续跑也停止。新增wait_for_all局部超时结算、
父Run身份保留，以及严格绑定的历史部分超时对账；不更改已失败子项或终态Run。
原实现两项RED，新入口与反例10项通过；最终影响闭包与真实恢复另行记录。

部分超时修复最终影响闭包303 passed；新增全局暂停反例后入口11 passed；
53项sentinel、7项flow smoke通过。真实只读预检确认当前K7符合精确对账条件，
仅将健康Reader聚合恢复为等待，不恢复Reader3/7失败attempt。

定向重试0/2/3经原repair.action入口请求；0因环境占用拒绝，2/3实际派发但恢复
材料不可读而abstained。根因是新来源事件改变材料摘要，旧必读引用仍被继承。
Candidate入口一项RED复现；保留父调用已固定且语义未变的恢复材料，原读取义务
不转移。91项影响闭包通过，最终sentinel/mock及真实重试结果另记；未标全流程完成。

本次输入修复最终91项影响闭包、53项sentinel、3项确定性flow smoke通过。

Reader7首次未送达却等待超时，原请求因早前暂停仍suspended，原重试入口必须
有历史dispatch而拒绝修复。2项RED复现；补齐未送达请求结算和原受控重试入口，
不新增动作或伪造派发。240项影响闭包通过，新增实际action幂等及再次重试身份
反例后21项入口/flow mock通过；53项sentinel通过。真实运行待加载验证。

Reader7已真实首次派发（evt-c45ef0b6a798），同批保持4 passed+4 running。
0/2/3/7重试中重复观察到空闲持有者释放后仍返回拒绝，需要第二意图。修复为确认
释放后同请求内仅一次真实repin；仍忙/超时/错目标保持阻断。入口RED复现后
258项Reader、角色生命周期、直接调用方和flow mock通过，53项sentinel通过。
此补丁暂留工作分支，避免为非当前卡点继续重启在途Provider控制器；现场结果不
归因于尚未加载代码。

只读检查还发现Reader7已真实派发，pending_reader_wait_diagnoses仍列出旧等待。
修复仅在绑定的kernel后继dispatch后移除旧诊断待办，保留事件和产品验收义务。
一项RED；223项RunManager/Reader/flow mock及53项sentinel通过，真实历史只读
投影旧待办由1变0。尚未加载，不能据此宣称线上RunManager闭环通过。
