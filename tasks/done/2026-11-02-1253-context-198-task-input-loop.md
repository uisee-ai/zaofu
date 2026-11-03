# 198：任务范围上下文索引与读写恢复闭环

> 状态: done

来源：[设计 198](../../docs/design/198-context-index-write-read-resume.md)。
用户已授权“基于198生成backlog 在新的worktree 实施”。基线 `869f7aa0`，
分支 `wip/codex-20261102-context-198`。先生成本候选，再按本次授权移入 active。

## 范围与验收

- [x] C1 输入成本：按 source/selector 记录来源、选择、实际输出与重复消费字节。
  生成多任务输入 -> verify: 统计按实际字节，token 未观测则 unknown，索引不计为正文已读。
- [x] C2 任务与角色消费：延用批准的任务切片、独立 Verify 和 Judge 不同合同，补充方法与反例。
  100 Task/多角色 -> verify: 只交付本任务和必要共享正文，Candidate/Goal 必读不缩减。
- [x] C3 索引入口：当前 attempt 绑定的可分页输入索引、精确/字面定位、读取入口。
  CLI/Query/Web -> verify: 来源版本一致、权限先过滤、索引本身不写消费回执。
- [x] C4 分页快照：派发时固定选择与块摘要，续页只加载需要的块；旧 manifest 兼容。
  大正文多页 -> verify: 不重复整份原文解析，游标固定，损坏回退/拒绝、别名各自授权。
- [x] C5 写后准入：在现有 source pin/manifest 发布点生成导航和固定选择快照。
  发布/原文变更 -> verify: 下游仍读固定版本，不接受半写或篡改；不引入第二提交状态机。
- [x] C6 恢复：复用已实现 --resume，连接索引、缺读和当前会话恢复指导。
  丢页/换会话/重放 -> verify: 当前身份重验，不把历史回执当当前记忆。
- [x] C7 可视化：任务 Evidence 展示当前派发输入范围、大小、必读与回读入口。
  API/前端 -> verify: 加载、空态、换任务、无权限与分页准确，不泄露正文。
- [x] C8 shadow 验证：保持 context delivery full，验证索引/快照不改变准入和 shadow 回退。
  mock E2E -> verify: 完整性、独立角色证据、stale 输入拒绝，既有 gate 不弱化。

## 明确后续条件

- 全文搜索：按 198 原设计保留为可选；当精确/关系定位不足且有中文召回基准再评估 FTS。
- 实际差量交付 canary：当前先交付固定输入与成本证据；有 provider 保留/恢复资格验证后启用，
  本批不把 shadow 改为实际省略，也不更改现有运行项目配置。
- 工作记录沿用已有 handoff、checkpoint、result；不另建全局可写 MEMORY 或任意发布 API。

## 验证计划

先运行新行为定向测试和直接调用方；再运行 dev-verify plan/run、pre-merge sentinel，
相关 deterministic mock E2E；前端 build 与受影响组件/浏览器测试。
所有模拟使用独立临时目录，浏览器用 Docker，不调用真实 Provider。

## 完成记录

实施 commit：`8a31b087 feat: add task-scoped input indexes and pinned read snapshots`。
已完成 C1–C8 本批授权范围并归档。

实际验证：

- `PYTHONPATH=src .venv/bin/python scripts/dev-verify.py plan --base dev --tests tests/test_artifact_input_index.py --tests tests/test_artifact_read_snapshot.py --json`
  -> 识别 342 个影响闭包文件。base dev 当时为 `869f7aa0`。
- 对该 JSON 的 `selected_tests` 全部执行：`.venv/bin/python -m pytest <selected_tests> -q --no-cov -m 'not serial and not perf and not host and not real_provider' -n 8 --dist loadfile`
  -> 最终 **5111 passed, 8 skipped**，121.33 秒；只排除下述已在基线复现的环境失败 node。
  每轮外层有 `timeout 900`，未触发超时。计划器的 >80 文件阈值会扩大到全 tests，
  本批执行其完整 selected_tests 闭包替代该扩大，不缩减闭包，也不宣称 full pytest。
- `PYTHONPATH=src ZF_PYTHON=/home/min/workspace/public/zaofu/.venv/bin/python .venv/bin/python scripts/dev-verify.py run --base dev --tests tests/test_artifact_input_index.py --tests tests/test_artifact_read_snapshot.py --skip-tier deterministic --keep-going --receipt /tmp/zf-context-198-verification-receipt.json`
  -> 串行 tier、前端 typecheck/unit、sentinel、三流 mock smoke 全过；deterministic tier
  由上一条完整闭包执行替代，不能单凭此 receipt 宣称全套通过。
- `PYTHONPATH=src .venv/bin/python -m pytest tests/test_artifact_read_ledger.py tests/test_artifact_read_snapshot.py tests/test_call_result_runtime.py tests/e2e/test_agent_owned_verification_mock_e2e.py -q --no-cov`
  -> 回归修复后 50 passed；最终索引小修 `tests/test_artifact_input_index.py` -> 6 passed。
- `npm --prefix web run build` -> typecheck、构建与 bundle gate 通过，最大块 464047 B < 500000 B。
- Docker `mcp/playwright:latest`、host network，UI `0.0.0.0:8098`，
  `ZF_WEB_BASE_URL=http://127.0.0.1:8098`、容器 Chromium 路径
  `/ms-playwright/chromium-1226/chrome-linux64/chrome`；运行
  `node /work/web/node_modules/@playwright/test/cli.js test tests/task-inputs.spec.ts --reporter=line --output=/tmp/zf-context-198-browser`
  -> **3 passed**。覆盖分页/定位/跨项目、加载与拒绝、必读政策 unknown。
- `PYTHONPATH=src ZF_PYTHON=/home/min/workspace/public/zaofu/.venv/bin/python bash scripts/dev-premerge-gate.sh`
  -> 53 passed；`scripts/run-flow-smoke.sh` -> 10 passed；manual-docs currentness 通过。
- 技能 canonical 与两份分发副本逐字一致；设计 198 已登记索引、相对链接可定位；
  `git diff --check` 通过。没有真实 Provider 执行或 token 节约证明。

发现与修复：

1. 新快照曾把缺失 selector 的错误提前到 dispatch；改为不生成该选择缓存，原必读
   政策保留，受控读取/准入仍拒绝，回归覆盖该时点。
2. 新指导文字曾让 Thin Judge briefing 超过 12 KiB；压缩重复说明，保留全部读取、
   分页和独立验收义务，相关 mock E2E 通过，没有放宽预算。
3. 大于 4 MiB 的固定恢复副本被通用预览上限挡住；改为按已发布 descriptor 的
   byte_count 和 digest 恢复，新增中文大来源/原文与块缺失测试。
4. Web reader policy 可能仅存在 operation request sidecar；接通该来源，无法解析时
   显示 unknown，不误标全部可选。索引游标绑定 manifest、policy、identity、query。
5. Docker 使用任意 UID 时 Chromium crashpad 无可写默认目录；测试容器改用镜像默认
   用户，repo/node_modules 只读挂载，输出留容器 /tmp；未安装宿主浏览器。

基线环境例外：

`tests/e2e/test_channel_group_product_mock_e2e.py::test_channel_prd_evaluation_task_workflow_run_and_receipt_continuation`
触碰已安装 Claude CLI 的认证探针，报 `authMethod=none`，workflow-start 返回 409。
在临时 detached worktree、未修改的 `869f7aa0` 上单独复现同样失败（1 failed，3.28 秒）。
最终闭包用 `--deselect=<该完整 node id>` 排除此单项；本批不修改 Provider 登录、
不绕过运行门禁，也未伪报该项通过。后续测试基础设施修正该 mock 的宿主隔离时重跑。
临时基线 worktree 已移除。

功能及应用要求见设计 198 §15。真实 delta canary、FTS 和 Provider 成本资格按前述
触发条件保留后续阶段；本批任务完成不等于整篇研究设计的全部设想已实施。
