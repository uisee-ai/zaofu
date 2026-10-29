> 状态: active

# 独立 Verify 拒绝后的有界实施恢复

持续监工发现 P12A g4 的数值持久化反例，独立拒绝 evt-97bdc19193fe 和 orchestrator 精确返工建议 evt-9b639681c9d3 完整。CLI 当前只接受 blocked Impl，不能执行 Verify 拒绝后的 operator 恢复；旧式 advice apply 缺 role，且不应绕过 Task Pipeline。

- 扩展现有 task-impl 恢复入口接收当前已接纳的失败 Verify -> verify: 必须绑定同代次已通过 Impl 与同一提交；旧目标、新 operation、成功 Verify 均拒绝。
- 超出上限继续要求显式单次批准 -> verify: 只派下一代 Impl，原拒绝保留；新代次仍需独立 Verify，不继承批准。
- 不修改全局上限、不重开旧 operation、不伪造任务成功 -> verify: 回归、影响闭包、sentinel、确定性 E2E及真实预览。

验证：新增回归先复现入口拒绝；修复及补齐测试 generation schema 后 focused 68 passed。增量基线 f877bf5 的 dev-verify 影响闭包 71 passed。CLI 索引生成后 sentinel 50 passed / 1 skipped；八槽 Verify 与 OA 语义控制 mock E2E 8 passed。首次 E2E 命令路径拼写错误导致未收集，已更正为仓库真实路径；未运行全量或真实 provider E2E。
