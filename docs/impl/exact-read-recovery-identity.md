# 精确来源读取与恢复审计身份一致性

来源任务：[精确读取恢复身份修复](../../tasks/2026-11-08-1248-exact-read-recovery-identity.md)。入口为 `zf artifact read` / `zf artifact recover`，实现位于 `src/zf/cli/artifact.py` 与 `src/zf/runtime/artifact_input_recovery.py`。

LS-v1 的一次 Verify 切换到既有精确来源分页后，完整内容已到达 provider，正式结果准入也成功，但恢复投影仍报缺失：批量读取记录 operation_id，精确读取没有记录，恢复按 operation 身份过滤掉了后者。OA/RM 接收的是未修正的投影；本次没有独立的投影与账本一致性诊断信号，因此不能指望两者自行识别该误报。

精确 CLI 在凭据授权之后、输出凭据落账之前记录授权所得 operation_id。旧的无 operation 行，仅在事件重放得到唯一 attempt 对应 operation，并匹配当前查询 operation、actor、run、task、dispatch 时进入历史统计；行本身仍需匹配 actor/role/provider/purpose/attempt 和 run/task/dispatch。明确错误的 operation、歧义或缺失事实保持 missing。

此兼容处理仅作用于历史投影，不重写旧账本，不授予执行权限，不提供当前会话记忆证明；`--resume` 的当前会话输出见证规则及正式结果准入保持原样。已结束 attempt 的生产回放仅调用只读投影，不使用已失效的读取凭据。

验证：新增15项行为回归先出现3项预期失败；修复后针对性39项通过。影响规划映射141文件，按精确文件闭包执行1505项通过，未执行全量pytest；planner 的按文件数扩展全套与仓库规则冲突，自动run仅执行串行10项和sentinel53项，另外执行design199及plan approve模拟E2E共3项通过。

真实历史 attempt `ta-e4fcd1087053e9fd3a54`：51行中28行无operation，9项必读的历史缺失6→0，current_memory仍unknown、authorizes_execution仍false。证据保存在LS项目 `docs/records/2026-11-08-exact-read-recovery/`。首次监工回放误将实际配置角色verify-lane-0写成verify，得出9→9；核对原始行后纠正测量身份，未修改生产数据。

## 两种分页的角色方法

关联[分页说明修复](../../tasks/2026-11-08-1922-read-page-method.md)。现场多个Verify尝试
`--required --page-bytes 30000/32768/65536`，被现有CLI正确拒绝。角色方法补充1024—8192
字节的实际范围，以及大段中文可采用的既有精确字符分页。两种模式的单位、续读位置和
游标不同：批量使用next_cursor；单项保持来源三元组，从完整page_end续接，并跟随
next_page_start。仍逐页原样消费，内容丢失时补读原文，不更改授权、范围、账本或门禁。
这只是方法补齐，不宣称减少了必读内容、证明当前理解或消除了全部读取/压缩耗时。


## 2026-11-22 完整历史交付后的按需恢复

后续实现见[8×8恢复闭环](eight-by-eight-recovery-closure.md)。同一operation、attempt、
来源版本和原生会话已完整observed，但compaction之后未保留完整页时，`--resume`
返回context_recovery。它区分历史交付完整和当前记忆未知，引导工作记录及精确缺口
读取；不默认重吐全部正文，不改变首次必读或正式结果准入。显式完整读取仍支持。
