> 状态: done；529afee2 `fix: scope task evidence gates to dependent recovery`

# 外部人工门禁只约束对应任务恢复链

LS-v1首环部分交付56c84537后，恢复reader明确分离设备选择UI与Q06 Owner决策。监工核对现有external_gate入口发现：生成human.escalate未声明blocking_scope，Run Manager按默认run门禁解释，可能阻止独立UI故障恢复。已有恢复消费者支持明确task门禁及TaskStore依赖闭包；不新增业务规则或放宽真正run门禁。

- [x] 通过真实Task Pipeline入口生成未满足人工门禁 -> verify: RM action admission拒绝门禁本身及直接/间接依赖，允许无依赖的独立任务；先确认当前HEAD失败。
- [x] 最小修复入口的明确作用域并更新设计/实现说明 -> verify: 保持原证据摘要、目标绑定、真正人工resolution及幂等要求，旧或不明确作用域继续fail-closed。
- [x] 影响闭包、哨兵与相关mock通过后提交、合dev、控制面加载和归档 -> verify: 保存准确测试层级、commit和在途reader身份；未push。

授权：用户要求持续监工、分析修复阻塞、记录问题；此前授权本会话修复合dev与受控控制面重载。

验证：当前dev基线751800ba的真实入口回归RED（独立任务得到human_decision_pending）；补一个明确blocking_scope字段后29项入口/共同准入/作用域通过。追加RM选路/continuation/199恢复mock14通过；dev-verify自动影响闭包37文件411通过（含3类dispatch mock），哨兵53通过，diff检查通过。未跑全量或真实Provider验证。源码只改1行；既有不明确与run级门禁仍按原规则拒绝。

落地：603f1bbf合dev；合并后入口29与哨兵53通过，Watcher/Web均验证加载603f1bbf。诊断Watcher包装器不含start导致首次actuator未识别，锁正确拒绝重复启动；核对PID/cwd/argv后受控替换为正常入口，未杀worker。详见LS记录2026-11-09-external-gate-scope；未push。
