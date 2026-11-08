> 状态: done — 089bd583 fix: reuse terminal scope within retry projections

# 同一返工投影内复用终止运行快照

LS-v1实际Watcher采样：steady run_once的1280样本中442处于派发准备，operator retry分支反复做全量run归并。只读调用实际derive_impl_rework_requests三次，在174249事件和257当前操作上触发30次fold_terminal_run_scope，37.64秒，返工请求均为空。问题在确定性周期计算，OA/RM语义分诊不替代调用链性能测量。

- [x] 仅单次投影调用内按需归并一次 -> verify: 实际derive入口多个retry目标共用一次，下一次调用读新快照，终止/blocked→active/跨run状态不泄漏；新增2项测试，先复现1失败，再通过。
- [x] 保留CLI重试、权限/来源/代次/终态门 -> verify: 原CLI与重试/调度/投影/运行归并闭包148项通过，不缓存跨调用结果。
- [x] 冻结完整生产事件比较原/新输出 -> verify: 174514完整事件、257当前操作，3次实际derive调用结果精确相同，终止归并30→3次，无profiler耗时14.428052→1.788116秒。
- [x] 哨兵、mock E2E、提交合并与受控加载 -> verify: dev合并49958394，合后72项及53哨兵通过；23:02–23:03受控加载Watcher3643488/Web3643922运行该提交，health/UI200，FMS24、FMS12及OA三项operation/attempt/dispatch/workdir/pane/PID保持。

授权：用户持续监工及修复问题。改动只在operator retry方法及直接测试/说明，不改变任务合同或状态所有权；不声称解决所有Watcher热点。完成任务保留tasks/，不push。

验证：首轮40项、影响闭包148项、dev-verify自动7文件88项、合并哨兵53项、确定性mock E2E 6项通过；非全量pytest或真实provider E2E。生产回放的完整快照摘要为27f762afed12f641653ee40ac27757e4b319d97f4b1001de32288205479791a8。证据保存在LS-v1的docs/records/2026-11-08-operator-retry-fold-profile/。受控加载和在途身份校验完成；整体Watcher开销仍需观察。
