> 状态: active

# 必读工件的无损共享正文呈现

监工授权修复验证执行故障：Media Verify ta-2a593bc6c23a10713eba因大量嵌套重复正文与输出截断无法完成读取，不能误路由产品返工。只读测量244个已登记切片，原始正文1421665字符，经完全相同子树共享为658837字符，每条按原编码逐字节还原一致。此测量未生成读取收据、未删任何条款。

范围：新增显式CLI呈现选项，保持原单条/required/deduplicate输出兼容；逐源仍通过原权限/selector/digest读取与ledger。共享格式仅作用一次调用的展示，不改输入包、合同、canonical state或gate。独立定义与来源记录保留正文内容哈希、原始read receipt；遇到无效/truncated结果拒绝共享。不得用引用替代未消费的定义正文。

- 先写真实CLI回归 -> verify: 不同来源的相同嵌套大正文无损共享，每源分别鉴权和记录，未授权字段不泄漏。
- 反解比对 -> verify: Unicode、保留字、null、字符串JSON、数组、顺序、重复正文均逐字节还原；不同内容不混用，调用间不复用旧正文。
- 门禁与兼容 -> verify: 原CLI读取/receipt校验及direct callers通过；新格式是可选项，禁止与单条/截断参数混用。
- 现场恢复 -> verify: 先验证真实输入无损体积，再按原验证阶段受控恢复，不把执行失败当产品失败、不代发passed。

验证：TDD 8 RED→18新旧CLI/呈现GREEN；权限/ledger/CLI/direct-call/任务输入合同闭包116 passed；sentinel 50 passed/1 skipped；ruff与diff检查通过。dev-verify 的CLI传递调用映射扩张至118测试文件并选择全量deterministic，已中断该不必要全量轮次，使用上述明确116项闭包；不宣称全量通过。现场恢复尚待验证。

方法固化：Dev Worker/Test Evaluator canonical技能补充共享正文格式、完整连续分页及收据不等于消费的边界；同步两provider分发副本。现场Media Impl2已使用新格式，未改产品代码；独立Verify恢复尚待完成。

现场第二次交接：Impl2输入已完整消费并typed passed准入(evt-98d9fe09040f)，但两个未跟踪tmp文件触发task.ref.rejected(evt-9ac8986c98fb)，自动进入Impl3。这暴露新方法缺少临时文件clean闭环，补充skill要求授权且已忽略目录、交接前clean、保全移出不修改gitignore。继续保留原HEAD，不将环境问题冒充产品缺陷。
