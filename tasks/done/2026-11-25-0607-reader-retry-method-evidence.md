# Reader 重试方法变化与绑定证据

> 状态: done
> 实现提交 `e4991e9ac fix: close duplicate reader diagnosis and recheck recovery authority`

原冻结单路技术弃权时，保留签发时真实可读入口并比较现行方法，给 RM 可核验的动作条件。

## 验收

- 在同一冻结和权限上构造 unchanged、changed、unknown 和错配 -> verify: 仅有具体改善且授权有效时给出单路恢复依据，状态变化按内核受控动作执行，已成立的人闸继续受保护。

依据为 8×8 原 Run 的七票保留、技术恢复和最终交付复盘，以及 [199 恢复职责](../../docs/design/199-worker-failure-recovery-and-role-handoff.md)。

## 验证结果

签发快照和比较器见 `candidate_reader_method.py`。重放一个旧 wait、更换证据、重复建议和过期媒体均经正常 Run Manager 入口验证。同一候选七份已通过的 operation 与票据保持不变，第八路产生一个替代 operation 并令整批通过。6+2 反例保留独立分诊。覆盖 `tests/test_candidate_reader_method.py`、`tests/test_candidate_reader_recheck.py` 和 `tests/test_candidate_reader_seven_plus_one.py`。
