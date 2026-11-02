# 设计 198 第四批：完整检索与输出成本评测

本报告绑定 [设计 198 §18](../design/198-context-index-write-read-resume.md)。
原始数据：[JSON](2026-11-02-context-198-retrieval-evaluation.json)。基线 `0c11ebd7`；候选实现见本批归档任务。

## 口径

使用同一新版评测脚本，分别加载第三批冻结 worktree 的旧 runtime 与本批 runtime；同规模 fixture digest 完全一致。
每个 Task 的 Impl、Task Verify 各 8 个定位/负例，Candidate 对全部 Task/AC 和共享引用查询。
独立按样例已知位置构造预期集合，逐页核对 source/selector/kind/relation/id/pointer；缺项、错项、重复或 incomplete 任一出现即失败。
不是以非空结果计成功。100 Task 与 1000 Task 分别执行 1,805 和 18,005 次查询。

| Task 数 | runtime | 通过查询 | 缺位置数 | Candidate 集合召回 | 最大响应字节 | 构建全部 fixture selectors |
| --- | --- | --- | --- | --- | --- | --- |
| 100 | baseline | 1601/1805 | 74 | 85.2295% | 7477 | 0.237 s |
| 100 | retrieval | 1805/1805 | 0 | 100.0000% | 8110 | 0.969 s |
| 1000 | baseline | 16001/18005 | 4574 | 8.5383% | 7489 | 5.310 s |
| 1000 | retrieval | 18005/18005 | 0 | 100.0000% | 8160 | 14.050 s |

候选三个角色的 precision/recall 均为 100%，缺项、错项、重复、不完整查询均为 0；ACL/unknown 负例无误命中。
8 KiB 是整页 UTF-8 JSON 加末尾换行预算；不是每个条目的预算。

## 共享引用成本

| Task 数 | 查询 | 旧返回/预期 | 新返回/预期 | 旧总字节 | 新总字节 | 新页数 |
| --- | --- | --- | --- | --- | --- | --- |
| 100 | source.md#exception | 85/100 | 100/100 | 106,621 | 20,765 | 3 |
| 100 | screen.png#empty | 170/200 | 200/200 | 211,685 | 38,894 | 5 |
| 1000 | source.md#exception | 85/1000 | 1000/1000 | 106,791 | 200,059 | 25 |
| 1000 | screen.png#empty | 170/2000 | 2000/2000 | 212,025 | 388,218 | 48 |

100 Task 来源原文 47,165 B，两类查询从 106,621/211,685 B 降至 20,765/38,894 B，且补全遗漏。
1000 Task 来源原文 471,965 B，新版完整结果分别 200,059/388,218 B；低于原文，但总字节高于只返回 85/170 项的旧版。
每个返回位置平均成本降低约 84%；不能用旧版漏项造成的低总成本宣称它更高效。

## 限制和代价

- 构建时间来自一次准备全体 Task selectors + shared + Candidate 的合成 fixture，不是一次生产 Task 派发，也没有在线逐次归因。完整分片构建较旧截断版更慢，以上单次结果不是延迟分位数。
- 每个角色的查询累计宿主耗时保存在 JSON；不等于 Provider 调用延迟、tokens、缓存命中或账单。
- Unicode/重复标题、超 20,000 个结构节点、旧版截断恢复、缺片/坏片、发布中断及同 digest ACL 隔离另有确定性测试。旧 manifest 字节不被重写。
- 明确 Task/AC/引用关系的完整命中不代表原始需求语义覆盖；自然语言“重复请求”正文搜索仍未支持，单独报告，不进入精确定位分母。
- 本报告未调用真实 Provider；视觉理解、真实 Plan 完整性、8+8 并发和实际 delta 仍不能据此宣称通过。

## 复现

```bash
PYTHONPATH=src python scripts/eval-context-inputs.py retrieval --tasks 100
PYTHONPATH=src python scripts/eval-context-inputs.py retrieval --tasks 1000
# 冻结第三批 runtime 对照（同一新评测脚本）
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=../context-198-production-20261102/src python -B scripts/eval-context-inputs.py retrieval --tasks 1000
```

评测只操作临时目录，不恢复 FMS/ls，不写运行中项目状态。

## 实现验证

- 同材料旧 runtime 对照，严格集合检索：100 Task 1805/1805、1000 Task 18005/18005；缺项/错项/重复为 0，三个 profile 精度/召回 100%，最大响应 8160 B。
- `dev-verify.py plan --base dev --json`：516 个影响范围测试文件，无未映射错误。按显式 selected_tests（xdist 8、排除 serial/perf/host/real_provider）执行，未按文件数自动扩大成全套。
- 广域首轮：7307 passed、9 skipped、2 failed、2 errors；磁盘耗尽导致末尾基础设施错误，且新增 CLI fixture 缺 required-read 声明。清理本会话 1.1 GB 临时测试数据及本 worktree 一个损坏 pyc，修正 fixture 的 manifest_metadata 与显式空必读政策。
- 对全部失败模块、索引/分页/查询/目录/评测、文档和 skill 直接回归：61 passed、1 skipped。广域排除 `test_channel_prd_evaluation_task_workflow_run_and_receipt_continuation`：此前干净 dev 已复现的本机 Claude 登录传感器失败，不算通过。
- `dev-verify.py run --base dev --skip-tier deterministic`：serial、Web typecheck/unit、53 项 sentinel、10 项 mock E2E 全过；`npm run build` 通过；Docker `mcp/playwright:latest` Task Inputs 浏览器测试 8/8。
- 聚焦/影响范围验证不是全套或真实 Provider E2E。
- 100 Task 公共引用累计字节较旧版下降约 81%；1000 Task 补全后总字节高于旧截断结果，平均每位置成本约降 84%，两种规模查询均低于原文。完整构建成本上升，详见报告。
