# Workflow 列表只构建一次准入投影

> 状态: done — 29e325cb feat: simplify inbox and task views with isolated workflow reads
> 来源: docs/design/186-inbox-and-task-board-human-centered-design.md；docs/design/188-dashboard-workflow-multi-run-sqlite-review.md（188 第 3.2 节）。
> 基线: dev c769680b；worktree zaofu-inbox-workflow。

## 范围

复用同一批事件的 admission projection，在列表内按 request/run 查找；保持列表与详情准入状态／排队位置一致。不改变准入状态机与并发策略。

## 验收

- 多 Request 与多事件 -> verify: 单次列表只调用一次 admission builder。
- running/queued/terminal Request -> verify: 列表与详情相同，未知 Request 保持既有空准入语义。

## 验证与依赖

tests/test_web_workflow_proposals.py、tests/test_run_admission.py 及验证计划选出的直接调用方。

本批按用户“生成 backlog 实施”的授权推进；只本地提交，不自动合并或 push。


## 实现结果

列表路由预先构建一次 admission projection，既有 helper 支持复用该投影；默认调用者行为不变。
新增真实路由测试先在旧实现复现 20 次 reducer 调用，再验证修改后 1 次。
running、queued、cancelled 与未知 Request 的列表／详情准入结果一致，最终单独回归 1 passed；
既有列表／详情一致性与整个 `test_run_admission.py` 在影响闭包中验证。


## 实施与验证记录（2026-10-26）

- 实现分支：`wip/codex-20261026-inbox-workflow`，基于 `dev c769680b`。实施期间 dev 被其他会话推进到 `50eb6ba1`；本分支没有合并或修改该 checkout。
- 验证计划：`PYTHONPATH=src <repo-venv>/bin/python scripts/dev-verify.py plan --base dev --tests tests/test_web_workflow_request_reads.py tests/test_web_workflow_proposals.py tests/test_run_admission.py`。按保守影响闭包选中 119 个 Python 文件；原始计划与各组 receipt 保存在 `/tmp/zf-inbox-workflow-20261026T1011/`。
- 为遵守不因文件数量自动运行全量 pytest 的规则，保持这 119 个文件不变，分成 30/30/30/29 四组，分别执行 `scripts/dev-verify.py run --base dev --paths <组内全部文件> --workers 4 --receipt <文件>`。没有修改 selector 或删除已选测试。
- 四组结果：252 passed / 14 failed；338 passed；586 passed / 1 failed / 1 skipped；582 passed。共 1,758 passed、15 failed、1 skipped；不是全量验证通过。
- 失败分类：14 项 `test_cli_start.py` 都在环境 preflight 的 `zf events --help` 20 秒超时；其中 `TestZfStart::test_start_dry_run_succeeds` 在未修改的 `c769680b` detached worktree 同样失败。`test_setup_repair_retries_real_candidate_integration_without_provider` 也在当前分支和该基线单独复现同一 IndexError。它们未作为本批 UI／读投影修复的一部分改写；合并前由维护者结合当前 dev 再处理基线或环境。
- 三个 serial 文件最初被验证脚本的非 serial 空集合提前终止（pytest exit 5，无测试失败）；随后按计划中原 serial 命令独立执行：`pytest tests/test_memory_rotate_concurrency.py tests/test_session_mutex.py tests/test_state_locks.py -q --no-cov -m 'serial and not perf and not host and not real_provider'`，10 passed。
- `bash scripts/dev-premerge-gate.sh`：51 passed，manual docs currentness 通过。所选确定性 mock E2E 在上述第一组通过；未运行真实 provider、生产数据迁移或多 Run 压测。
- 本批测试均使用临时 fixture；Docker Playwright 运行在 `mcp/playwright:latest`、host network、隔离端口 8004+。未安装宿主浏览器、未操作真实审批。
- 设计补充在 gitignored `docs/design/186-*.md` 与 `188-*.md`；五项下一阶段候选保留 `backlogs/`。不纳入提交。

- 前端最终验证：`npm run typecheck`、`npm run test:unit`、`npm run build` 全部通过；bundle budget 为 39 chunks、最大 432,696 bytes（上限 500,000）。`tests/test_instruction_stack_contracts.py` 8 passed。
- 浏览器命令：`docker run --rm --network host --ipc=host --user 1000:1000 -v <worktree>:/work -v <evidence>:/evidence -w /work/web -e ZF_WEB_BASE_URL=http://127.0.0.1:8004 --entrypoint /bin/bash mcp/playwright:latest -lc 'export ZF_E2E_CHROMIUM_EXECUTABLE_PATH=$(find /ms-playwright -type f -path "*/chrome-linux64/chrome" | sort | tail -1); ./node_modules/.bin/playwright test tests/inbox-workflow.spec.ts --workers=1 --reporter=line --output=/evidence/browser-closeout'`：11 passed。后续 attention／换行微调后单独重跑 `--grep 'Board and List'`：1 passed。
- 本项验收通过，广域回归仍有上述已分类的基线／环境失败；本地提交不代表当前 dev 合并验证完成。真实页面 p50/p95 和数据库迁移不在本批验收结论内。
