> 状态: active

# 阻止普通 probe 绕过 host 事实来源准入

持续监工授权范围内修复。基线8af9912b。现场lane0 admission evt-84d1742a813d将19条
只引用已绑定host bundle的普通probe判为passed；host_execution_receipt_ids未填。
browser消费事实仍unavailable/invalid host producer path，实际没有该Reader重新执行。
lane1独立blocked，聚合evt-eff11aa22adf失败；Run已受控暂停evt-f9587ae6beea，保留历史。

范围：对普通passed probe中精确引用当前授权host bundle/fact/receipt/output/event/media
及受控来源alias的行为，要求走既有host_execution_receipt_ids入口。不得从文字关键词
推断语义，不把来源改名为本地执行，不改producer_paths或历史准入。真实本地probe及
blocked诊断保留原有合同。此次不宣称解决所有未提供可验证来源的报告可信度问题。

验收：

- 从旧代码实际预检/提交入口复现旁路 -> verify: 可用/不可用host事实均暴露普通probe入口漏洞。
- 对绑定来源和等价引用统一来源路由 -> verify: ref/absolute/fragment/file URI/source alias/event拒绝冒充。
- 保留正确host准入、本地probe与诊断 -> verify: 专用host IDs继续校验媒体；本地/blocked场景通过。
- 运行影响闭包、sentinel、恢复mock -> verify: 记录准确层级、反例和结果。
- 部署后通过已有失败聚合恢复 -> verify: 不改旧passed，不伪造失败，不放宽媒体合同，Judge前安排新代际独立验收，判词另行跟踪。


证据与实施边界：

- 现场真实已准入result只读重放：旧8af准入issues=[]；新入口返回
  verification_host_evidence_invalid / result_repair。未调用submit，未改历史。
  保存于reports/host-probe-admission/live-admission-{baseline,fixed}-readonly.json。
- 原基线20个来源反例均错误通过；另一个blocked诊断测试初版漏填blocked requirement，
  属测试fixture错误，已按既有合同补齐。新代码86项focused通过，含28个来源反例及正例。
- 来源判定只用当前冻结host图的精确ref/source alias/事件与词法路径等价；不扫描自由文本，
  不自动填写host IDs，不凭命令相同推断来源。既有prompt已经说明host事实不是fresh probes，
  本次补的是实际机械准入，不以再写一句prompt声称已防止旁路。
- 完整影响闭包按本项paths运行；前序live分支修复已有独立回执。不运行全量pytest。
- 真实lane1 blocked已形成evt-eff11aa22adf，后续使用受控新代际重新验收两Reader。
  旧lane0 passed留在历史；产物命名空间问题、最终语义判词和交付继续独立跟踪。

补充通用验证方法：host来源使用专用回执；媒体不可用先对照声明/脚本/采证根/索引，
再区分collector故障与需Plan port修订的合同阻塞。源码及Codex/Claude镜像同步；
此方法提供Agent判断步骤，不由runtime直接认定合同问题或授予修订权限。

代码最终闭包：1353 passed / 7 skipped，53 sentinel passed，10 flow mock passed。
共享Skill变更单独运行生成/物化/指令闭包，回执skill-verification-receipt.json。

方法验证：65 passed / 1 skipped；Skill源码与两镜像SHA256均为
7d637c926c7ddf257c612c061a95a7c1bc1b80802206afd704c0c4ff2c4471ec。
