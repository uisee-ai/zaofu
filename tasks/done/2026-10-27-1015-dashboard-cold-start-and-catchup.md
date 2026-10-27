# Dashboard 冷首屏与指标追赶

> 状态: done
> 实现提交: `81cc73c3 fix: accelerate dashboard cold views and metric catch-up`

用户批准上一轮 Loshu 评估后的下一步实施。基线为 `3977bb57`，独立分支 `wip/codex-20261027-dashboard-cold-start`。
采用 zf-backlog-batch-closeout 完成验证、实现提交、记录 hash 后归档；不合并 dev、不 push。
设计参考 `docs/design/191-dashboard-projection-process-and-refresh.md`；补充设计同目录且保持 Git 忽略。

## 范围与验收

1. Board 先显示 canonical TaskStore 的真实任务 -> verify: 空投影首屏不等待 EventLog 索引/完整派生摘要；明确基础任务与完整派生状态的差别，后台完成后替换；项目切换/请求失败不混入别项目数据、不把未知指标变成零。
2. Graph 冷构建消除重复工作 -> verify: 完整返回值与旧算法一致；digest、脱敏、外部 artifact 变更、历史游标隔离保留；Loshu 首次构建相对基线改善。
3. Agents 来源变化时尽早停止过时计算，避免重复追赶 -> verify: 不发布混合 generation，不丢完整旧值；控制 queue 合并，事件停止后最新水位更快达到 ready；store-only/config/归档轮转变更仍正确失效。
4. 同一份 Loshu 输入对比 -> verify: 记录 Board 基础任务首屏/完整数据、Graph 冷/热、两轮 burst 新鲜度与健康延迟、业务指标逐字段差异；不能只以旧值响应快作为验收。
5. 影响范围测试、Docker Playwright、临时环境清理 -> verify: 写明确切命令、版本、源数据哈希；实现提交后记录 hash 并归档；干净 worktree 交接。

## 边界

复用既有单 Web + 单投影进程和 SQLite。不得改变 canonical Store/EventLog，不放宽审批/摘要校验，不降低完整指标精度。
可提供明确标记的基础任务首屏，但不能伪装成完整投影。其他页面继续使用完整数据。

## 结果

已完成实现与 Loshu 实测。完整本地报告：`/tmp/zf-dashboard-cold-20261027T1015/report.md`；
设计更新：`docs/design/192-dashboard-cold-start-and-catchup.md`（Git ignored，不提交）。

- Board 新增 TaskStore 只读 API / 独立前端 bootstrap state；取消旧项目请求，完整 Snapshot 到来后替换。
  项目列表可在基础任务展示后加载。空投影基础首屏 75.74s → 2.85s；完整派生数据 76.06s → 71.64s。
- Graph 内部复用 mapping，脱敏仅在单次调用内 memoize；依赖/digest/history 校验保留。
  完整冷 API 33.55s → 28.87s；7,548,768 字节、408 nodes / 879 edges 逐字段一致。
- Agents 及时退出过期计算，worker 单轮尝试，外层完整 generation 校验不变；OA 复用共享事件缓存，
  freshness 类别查找减少重复分配。两轮密集更新（40 events / 0.1s）停止写入后最新 ready：
  1.63s → 1.03s，1.04s → 0.76s。两轮 1Hz 为 0.30s → 0.31s、0.61s → 0.31s，
  本轮没有复现前次 7–11s，不能用跨轮差值宣称提升倍数。
- 7 个 API 完整字段对账无业务指标差异；仅时间、年龄、来源身份与刷新元数据不同。
  18 次真实页面访问无错误；热 Workflow 中位数 1.59s → 1.04s，热 Board/Agents 基本持平。
- 同一份 Loshu 输入 6885 files / 403809117 bytes / 12596 events；逐文件 SHA256 两侧一致。
  事件聚合 SHA256：`56a548221140982b79e12ab46db7995b0598da6895bc65137851512ac8192c71`。
  隔离 8048/8049，不修改真实 8001/8002。共享主机小样本，不代表 p95/SLA。

## 验证记录

- `uv run python scripts/dev-verify.py plan --base dev`：323 个测试文件；
  `workflow_workbench_routes.py` 由显式 `--tests tests/test_workflow_workbench_cache.py` 的实际路由 oracle/失效测试覆盖。
- `uv run python scripts/dev-verify.py run --base dev --tests tests/test_workflow_workbench_cache.py --skip-tier deterministic --skip-tier serial --receipt /tmp/zf-dashboard-cold-20261027T1015/verify-receipt.json --json`：自动 Web typecheck/unit 通过。
  planner 因 >80 文件建议全套；依 AGENTS.md 改为执行 receipt 中全部 323 个 selected_tests，
  `pytest <全部选中路径> -q --no-cov -m 'not serial and not perf and not host and not real_provider' -n 8 --dist loadfile`：
  **4087 passed / 2 skipped**。命令列表与退出码在 `impact-command.json` / `impact-exit.json`。
  首次输出缓冲时手动中断，随后完整重跑通过；未将中断结果充当完整验证。
- `uv run pytest tests/test_memory_rotate_concurrency.py tests/test_session_mutex.py tests/test_state_locks.py -q --no-cov -m 'serial and not perf and not host and not real_provider'`：10 passed。
- `bash scripts/dev-premerge-gate.sh`：51 passed。`npm run build`：typecheck/build/bundle budget 通过。
- Docker `mcp/playwright:latest`，生产构建 `8049`，`board-first-paint.spec.ts`：16 passed。
  真正 API 的 Board 冷加载 2/2、Workflow/Agents/Board 热页面 18/18 成功；7 API parity 通过。
- 早期新夹具缺初始化文件、错误 dataclass API、项目列表等待完整投影均已修正并复测。

## 限制与交接

完整 Board 冷派生约 72s、Graph 冷构建约 29s 仍需后续优化。本批快速 Board 是明确标记的基础任务，
不代表完整执行证据已经到齐；热 API 返回完整旧值也不代表新水位 ready。持续无限高频写入仍无固定新鲜度上界。
真实样本成本为 0，非零金额/重复 usage 精度由 focused regression 覆盖。未执行全 pytest、真实 provider 或 host E2E。
清理结果见 `cleanup.json`；保留证据报告、当前 worktree 与本地分支，不 merge、不 push。
