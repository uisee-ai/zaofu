> 状态: done

实现：`9ee0ba74 fix: bind approved plan and OA authority into replan inputs`

# 返修 Plan 的权威输入接线

监工确认并由 Planner 独立报告：新返修输入虽然可完整读取，但已批准 Package 的 ref/digest 只在身份元数据中，正文和端口未登记为受控来源；OA return_to_plan 的原裁决及 delta 也未从 marker 传递到实际触发。无法安全复用现有 93 任务和完整矩阵。用户已授权修复、合并 dev、恢复并继续监工。

- 接入既有 Package 与完整端口 -> verify: header 必读，端口可按 JSON path 受控读取，保留摘要且篡改拒绝，不把所有大端口变为强制全文。
- 转发 OA 原裁决和 delta -> verify: 实际 OA apply → resynth → Plan handoff 入口读取同摘要正文；旧事件只按相同 Run/Package/generation/失败指纹/任务范围精确匹配 marker，歧义拒绝。
- 接入裁决所引用证据 -> verify: 仅复用原 OA immutable manifest 已登记并绑定摘要的来源，不猜测路径、不拓宽访问权限。
- 保留门禁及预算 -> verify: focused closure、sentinel、相关 mock E2E；不改写历史输入和 verdict，不提高60调用上限。

验证记录：原实现 5 项用例失败；修复后 24 个直接调用者/共享契约/mock E2E 文件共 282 项通过，自动层 10 serial + 51 sentinel + 10 flow smoke 通过。dev-verify 的226文件保守闭包触发全套回退，本次以明确24文件影响闭包替代 deterministic 全套层；未运行全量 pytest 或真实 Provider E2E。Ruff 与 diff check 通过。

对真实 dl 不可变输入执行无读取收据的只读预览：补齐 Package、20端口、OA裁决/delta及7项原证据；10项必读共30页（241105字节，含输出元数据），60工具往返上限不变。预览临时目录已删除。测试日志 `/tmp/zf-replan-authority-{closure,auto,live-preview}-20261031.log`。
