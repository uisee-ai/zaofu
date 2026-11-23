# 标准ZaoFu hook与已授权宿主发布协同

> 状态: done — `85fc8614` fix: recognize the built-in guard during candidate publication

来源：8×8首个无监工窗口完成8项审计，8项TDD实现保留未提交；内核自动宿主发布因标准init安装的pre-commit而升级。安装体与PRE_COMMIT_HOOK逐字相同，SHA256为610f5a5ef59d409754b805b5c8ac3a4453b9d59b7c2238419081d0cc5f3c6fc1。

- 真实init标准hook+预授权发布入口 -> verify: 提交成功、hook实际执行且未被删除/绕过；既有受控retry请求一次，不伪造Impl通过。
- hook身份绑定 -> verify: manifest冻结hook名称、字节摘要、模式，执行前漂移拒绝且未暂存。
- 未知/修改/重定向hook -> verify: 仍要求独立审核，不能根据文件名或注释信任。
- 聚焦/影响闭包/哨兵/流程通过 -> verify: 提交修复；保留8审计终态、8实现工作区，从宿主发布断点恢复。

授权依据：本轮用户要求发现问题修复、评估、从断点继续；仅改变机械内置hook识别，既有路径授权、Git锁、当前代际、闭合Provider turn及未知效果拒绝规则保留。首窗口并发227.597129秒；无人完整交付未通过，后续修复不能追认。

验证：新增回归先4 failed/5 passed，修复后92项聚焦通过；`dev-verify.py run --base dev --tests tests/test_builtin_hook_publication.py`通过9390项后端影响闭包（7 skipped）、Web typecheck/unit、53哨兵和10流程测试。提交修复时尚待真实部署；下方真实闭环完成后归档。

恢复预检：8项原operation均缺宿主commit收据，失败计数器因此保守拒绝普通retry，CLI统一显示budget exhausted；不是已发生多次业务返工。使用既有CLI、当前operation/source绑定及用户已授权的单代恢复路径；不放宽全局上限。

真实闭环：修复已由PID1484511加载；8个g2恢复全部保留原工作区和Provider session。17:57:56至18:05:20，内核按既有预授权完成8个独立commit，manifest绑定标准hook；8项均由原生服务请求g3复核。未代写Worker结果或扩大权限。18:05:53 MEDIAN已通过Impl、独立Verify启动。`reports/eight-qualification-20261120T122929Z/builtin-hook-native-publication-evidence.json`含8份摘要核验后的收据和后续retry事件；首窗口无人交付失败继续保留。该bug验收完成，产品最终交付属于后续验证。
