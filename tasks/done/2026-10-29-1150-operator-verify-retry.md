> 状态: done — `1b09fe0 fix: support bounded implementation retry after rejected verification`

# 独立 Verify 拒绝后的有界实施恢复

持续监工发现 P12A g4 的数值持久化反例，独立拒绝 evt-97bdc19193fe 和 orchestrator 精确返工建议 evt-9b639681c9d3 完整。CLI 当前只接受 blocked Impl，不能执行 Verify 拒绝后的 operator 恢复；旧式 advice apply 缺 role，且不应绕过 Task Pipeline。

- 扩展现有 task-impl 恢复入口接收当前已接纳的失败 Verify -> verify: 必须绑定同代次已通过 Impl 与同一提交；旧目标、新 operation、成功 Verify 均拒绝。
- 超出上限继续要求显式单次批准 -> verify: 只派下一代 Impl，原拒绝保留；新代次仍需独立 Verify，不继承批准。
- 不修改全局上限、不重开旧 operation、不伪造任务成功 -> verify: 回归、影响闭包、sentinel、确定性 E2E及真实预览。

验证：新增回归先复现入口拒绝；修复及补齐测试 generation schema 后 focused 68 passed。增量基线 f877bf5 的 dev-verify 影响闭包 71 passed。CLI 索引生成后 sentinel 50 passed / 1 skipped；八槽 Verify 与 OA 语义控制 mock E2E 8 passed。首次 E2E 命令路径拼写错误导致未收集，已更正为仓库真实路径；未运行全量或真实 provider E2E。

部署复验 36 passed。使用已有 source_repair_restart_actuator 仅重启控制进程，`evt-734ba4420f01` 确认 control_plane_only/workers_preserved；watcher 2363029 就绪，P02-CLEAN-R1 的 pane/dispatch/attempt 保留。该过程同时加载上一批 77f7b9e。

依据用户既有代批准/持续监工授权、独立拒绝与 orchestrator 精确返工建议，先预览后经 CLI apply 仅授予 P12A g5（evt-f552ee553065）。实际派发 evt-ccfe8addcfde，operation wop-impl-tp-P12A-745b3240199676ab-impl-re-68d68aae9cec，dispatch tpd-impl-re-68d68aae9cec-p8，worker 已开始读取恢复上下文。此处 done 只表示恢复入口修复完成；数值产品修复与独立验证继续监工，未宣称产品通过。
