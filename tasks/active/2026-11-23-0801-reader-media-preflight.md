> 状态: active

# 首次 Reader 恢复前核验必需媒体

用户持续授权监工中发现问题后修复并记录。本项仅修复 ZaoFu 的机械交接遗漏。
基线 f9aafa82；真实 Run workflow-ddd320aa0dc7c2ef、Candidate 88992ce7。

现场19条命令成功后自动派发两个新Reader，但浏览器事实被标记 unavailable /
invalid host producer path。合同声明 artifacts/evidence/browser/，采证索引却为
相对容器证据根的desktop.png等文件。没有该目录到证据根的显式映射。
该合同差异需由Reader/RM/OA/合同Owner判断修正，不得通过basename或剥目录伪造满足。

另已确认runtime首次交接无candidate_host_evidence_ref时直接返回inputs improved，
未检查已有媒体是否满足声明；因此明知可机械检查的无效输入仍触发一轮Reader。

验收：

- 以原始代码运行首轮恢复反例 -> verify: 无绑定/有请求无host绑定两种情况均暴露错误重派。
- 在首次和后续恢复共同验证命令产物 -> verify: 已绑定文件可重派，非法或缺失路径不可重派。
- 保留Agent合同修正职责 -> verify: 不改producer_paths、不改收据、不新增执行、不给产品判词。
- 覆盖真实resume_reader_after_probes入口与影响闭包 -> verify: focused、dev-verify、sentinel及mock通过。
- 记录源代码修复与现场缺口的不同状态 -> verify: 不把预检修复称为当前产品路径契约已解决。


验证记录：

- baseline-red-confirmed.log：4 failed / 2 passed，确证无绑定两路径上的错误重派；首次测试
  还误将stage-replan本身的action-started计为host命令，已收窄到kind=verify并重跑原基线。
- focused.log：101 passed / 2 deselected；影响闭包由dev-verify显式paths仅选择本项差量，
  因为同祖先上的前序三批修复已独立验证，避免将整条live分支差量误计为本项范围。
- 最终闭包与sentinel以reports/reader-media-preflight/verification-receipt.json、
  verification.log、sentinel.log为准；包含candidate-recovery-closeout确定性mock。
- 当前真实Reader在补丁前已经冻结输入，修复不原地变更它们。实际产物声明缺口仍需原生
  语义恢复；本项不能据此报告产品验收通过。

最终验证：影响闭包188 passed / 2 deselected（包含mock），sentinel53 passed。
