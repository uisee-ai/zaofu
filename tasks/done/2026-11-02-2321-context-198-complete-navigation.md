# 完整索引与尾部精确定位

> 状态: done
> 实现提交: 87b49487 fix: complete task input navigation and compact retrieval pages

来源：[设计 198](../../docs/design/198-context-index-write-read-resume.md) 及第三批评测。基线 `0c11ebd7`。

- [x] 分块建立完整显式关系/章节索引，固定版本及可恢复引用 -> verify: 100/1000 Task 的头/中/尾、所有 Task/AC 与共享来源完整命中，单页仍受字节预算约束。
- [x] 角色/occurrence/selector 授权及旧 manifest -> verify: ACL 先于索引读取，相同 digest 不合并权限；旧版截断索引可以从固定原文恢复，不静默换版本或伪报完整。
- [x] 派发/目录/恢复生产调用链 -> verify: CLI/API 实际调用新索引，缺块/坏块/中断显式处理；索引不生成正文回执，不改变 required reads。

本批不启用全文/向量检索或实际 delta，不修改 FMS/ls 运行状态。真实 Provider 资格另行按第三、第四批完成后的约定执行；本批只做确定性和离线检索。

用户已批准“按照建议，先生成 backlog，然后实施”。

## 验证与交付

- 同材料旧 runtime 对照，严格集合检索：100 Task 1805/1805、1000 Task 18005/18005；缺项/错项/重复为 0，三个 profile 精度/召回 100%，最大响应 8160 B。
- `dev-verify.py plan --base dev --json`：516 个影响范围测试文件，无未映射错误。按显式 selected_tests（xdist 8、排除 serial/perf/host/real_provider）执行，未按文件数自动扩大成全套。
- 广域首轮：7307 passed、9 skipped、2 failed、2 errors；磁盘耗尽导致末尾基础设施错误，且新增 CLI fixture 缺 required-read 声明。清理本会话 1.1 GB 临时测试数据及本 worktree 一个损坏 pyc，修正 fixture 的 manifest_metadata 与显式空必读政策。
- 对全部失败模块、索引/分页/查询/目录/评测、文档和 skill 直接回归：61 passed、1 skipped。广域排除 `test_channel_prd_evaluation_task_workflow_run_and_receipt_continuation`：此前干净 dev 已复现的本机 Claude 登录传感器失败，不算通过。
- `dev-verify.py run --base dev --skip-tier deterministic`：serial、Web typecheck/unit、53 项 sentinel、10 项 mock E2E 全过；`npm run build` 通过；Docker `mcp/playwright:latest` Task Inputs 浏览器测试 8/8。
- 报告：[严格检索评测](../../docs/records/2026-11-02-context-198-retrieval-evaluation.md)。聚焦/影响范围验证不是全套或真实 Provider E2E。
- 100 Task 公共引用累计字节较旧版下降约 81%；1000 Task 补全后总字节高于旧截断结果，平均每位置成本约降 84%，两种规模查询均低于原文。完整构建成本上升，详见报告。
