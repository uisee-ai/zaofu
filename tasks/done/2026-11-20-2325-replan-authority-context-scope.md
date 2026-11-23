> 状态: done

# 新 replan 不继承上一轮 OA 执行权限

真实窗口7：4bb9c466转交语义规划后，触发器从旧scan复制OA决定，却绑定新Plan，两个Planner请求在preregistration均失败，stage cap正确升级。原始OA决定曾合法应用于e96c代次，不能当作87e7代次的当前授权。用户已授权修复恢复，限定隔离分支，不合dev、不push。

- 新候选replan只取当前请求携带的权限 -> verify: 实际resynth入口旧决定变为不可变历史观察，新请求有明确决定时完整保留；旧事件不变。
- 显式新代次恢复非OA请求 -> verify: 保留Owner批准和代次上限，只将错误继承的前轮权限降为历史；合法OA请求及普通重试不降级、不改其绑定。
- 现有输入身份门禁保持 -> verify: 当前决定跨Run/Plan、缺摘要、篡改仍拒绝，历史引用可读但没有执行权。
- 受控恢复 -> verify: 回归、sentinel及mock通过后，从确切stage升级事件恢复，证明Planner实际派发；不以准入成功声称整体无人通过。

回归：原HEAD新增测试2 failed/3 passed；修复后定向247 passed。扩大闭包4329 passed/7 skipped/2 failed，两项为Thin Judge简报超出既有12 KiB限制，基线4bb9c466同样2 failed；追溯为前轮a333f8fc WRC提示增长。将重复说明压缩为现有artifact recover与role skill入口，保留完整skill方法和原阈值，再验证。

最终广闭包4640 passed/7 skipped/1 failed，唯一失败为WRC简报提示文字断言；压缩提示时恢复原有Semantic progress checkpoint入口措辞，保留短提示及原12 KiB阈值。只重跑受该单行变更影响的Reader、结果卡、Thin Judge和恢复测试，不将有失败的广闭包称为全绿。sentinel53 passed、flow-smoke10 passed。真实账本预演证明当前Plan87e7全部基线port及历史OA观察可读取，当前权限不含旧决定；旧OA事件保留。

最后单行提示修正后的受影响复验132 passed（含两个Thin Judge原失败用例、WRC实际重启、当前OA权限否定、stage输入与提交入口），无已知未解决测试失败。未重跑整套4641项；完整日志保留于隔离报告目录。待真实部署签收。

实现提交：`559fecdf fix: scope replan authority to the current request`。真实签收：559fecdf部署后，stage-replan-new-generation原生动作evt-6b7923886633，Planner operation started evt-4562e4ae49e4，child dispatch evt-9055f2f77441；agent.usage确认gpt-6-luna并已发生实际读取。当前任务图16个业务Task通过TaskStore.get（含归档）仍done，没有新业务Task派发。报告replan-authority-scope-native-proof.json保留精确绑定。本项只签收恢复路由及权限修复，最终产品验收和完整无人资格仍未完成。
