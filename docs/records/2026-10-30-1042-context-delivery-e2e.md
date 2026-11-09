# Context Delivery 1–3 实施与真实验证

> 结果：三项优化已实现；通过 Raw 审核为 lanes=1 的 v5 真实完整链路通过。原 lanes=8 配置仍会触发总预算上限，不能宣称已经解决。

worktree：`/home/min/workspace/public/zaofu-worktrees/context-delivery`；分支：
`wip/codex-20261030-context-delivery`；实施基线：`5a53145a`。

## 实施范围

1. CLI 完整写出并 flush 后才登记产出；封存时单独匹配 Codex transcript 的完整工具输出，
   保存不可变 witness。生成、产出、可观察交付分别记录；未知保持 unknown，不等同模型理解。
2. 在真实派发/恢复入口绑定原始用户消息、Ask User 回答、批准后的 Flow 配置、当前身份及
   合同/返工来源。新 attempt 不能借用旧 attempt 的已读状态。
3. 原文按摘要保存完整版本，恢复只接受同 SHA-256 正文；有效原文可绕过损坏副本，两者
   均不可用则明确失败。没有自动清空 Provider 历史或用摘要替代全文。

带入之前已批准的问答草稿保留、v5 profile 加载、批准配置 Git 基线依赖，共 19 个路径；
原 `kanban-workflow-input` worktree 未改动。读取与恢复约束见
[读取面](../impl/task-pipeline-capsule-read-path.md)和
[恢复面](../impl/task-pipeline-capsule-recovery.md)。

## 三轮真实运行

共同条件：独立 Python CLI 项目，源码来自当前 worktree，配置来源为 `zf-prd-v5.yaml`，
唯一控制面为 fixture 的 `zf.yaml`，状态目录 `.e2e-state`。使用真实 Codex 与 Docker
`mcp/playwright:latest`，API 监听 `0.0.0.0` 的临时端口；未占用 8001。
每轮都从干净项目重新走 CSV 问答、刷新保留、创建任务、Workflow Synthesis、Raw 审核和提交。

| 轮次 | Raw 配置 | 实际结果 |
| --- | --- | --- |
| 104246 | model=gpt-6.1-terra，lanes=8 | 11:04:04 UTC，Impl 在 1,503,976 / 1,500,000 token 阻断，任务未交付 |
| 111010 | model=gpt-6.1-terra，lanes=8 | Impl 810,109 token；子任务 EXP-1 完成，但 8 路候选验收使 Run 在 10,021,963 / 10,000,000 token 阻断 |
| 113650 | model=gpt-6.1-terra，Raw 明确将 lanes=8 改为 1 | 全部阶段及交付通过；12:05:16 UTC 出现 run.goal.completed |

第二轮 Run：`prepare-TASK-5EFD12-e363b91461b27972`。单任务实现、Task Verify 与集成已
通过，11:33:30 UTC 子任务由 runtime 标记 done；父 Run 仍为 blocked。它揭示的是固定
8 路候选验收的额外成本，不能用第三轮配置变体掩盖这一失败。

第三轮身份与结果：

- 父任务 `TASK-F156CC`，子任务 `TASK-EXPORT`；Run `prepare-TASK-F156CC-a64b94daeb40e095`。
- Raw diff 仅修改 model 与 lanes，逐字段比较其余 YAML 相同；保留 v5、blocking、
  on_demand、全部门禁、worker pool 及原 operation/Run 预算。候选验收实际编译为 verify-lane-0。
- 11:45:13 UTC Run 启动；11:58:30 子任务 done；12:00:24 候选验收通过；
  随后交付后复扫、judge-prd 和确定性完成门通过；12:05:15 ship.completed；
  12:05:16 run.goal.completed，Web 同样显示 completed。
- 批准配置基线提交 `98b61f4`；产品实现 `315029b`；交付 HEAD `d39661c`。
  受版本管理的交付文件与索引均干净；`.codex/`、`zf.yaml.lock` 是本轮未跟踪 runtime 产物，
  已随隔离环境清理，未将其伪装为产品提交。
- 在最终交付目录独立运行 9 个产品 unittest，并完成 14 项 CLI/CSV/错误处理检查，全部通过。
  覆盖 README 命令、空数组、Unicode/引号/逗号/换行、精确十进制字符串、缺文件、
  非法输出路径、非法 JSON、非数组、缺字段、布尔金额和非有限金额。
- 9 个实际 attempt 的 model 均为 gpt-6.1-terra；关键来源保留 CSV 回答、批准配置和完整版本 ref。
  239 条已封存产出收据中，202 条有完整 transcript 观察，37 条保持 unknown。

## 实弹发现及处理

首轮发现 Codex 工具结果采用 custom_tool_call_output + input_text blocks，初版只识别
function_call_output 字符串。已补充两种格式及截断/旧时间/错 session/工具输入假回显测试；
首轮运行中完成适配，旧收据没有手工重写，不能把首轮当作冻结源码的成功验收。

首轮还暴露出每个短正文单独占一页的浪费。v2 分页在同一 UTF-8 字节上限内装入多个正文，
末项允许部分片段，每个别名仍独立授权、记账。对首轮同一个实际 Impl 输入离线重放：
16 次输出降为 9 页，全部 16 项 requirement 逐字节一致且范围完整，未写 runtime 回执。
第二、三轮实际 Impl 都使用 7 个输出页；第三轮 21 条收据全部观察到交付，使用 922,463 token。
模型生成的计划不同，因此不把不同轮次的 token 差异当作严格的性能 A/B 结论。

补充测试复现了非对象 transcript 记录造成封存异常；修复后忽略异常记录并保持 unknown。
该修复在第三轮 workflow 启动前完成。第三轮首次复扫提交缺少内联报告正文，被 validator
拒绝；agent 根据诊断补齐后正常接纳，未跳过门禁或手工写入完成事实。

第一、三轮浏览器曾在异步提案尚未准备完时过早检查 Raw，保留等待失败证据，提案 ready 后
重新检查通过。源码快照曾被浏览器目录中较早副本覆盖；已保留旧副本、记录修正原因和
文件修改时间，最终快照逐文件匹配，执行代码在第三轮启动后没有改动。

## 自动验证

- 初始后端 24 个文件影响闭包：284 passed；重放/调用入口补充：24 passed。
- 最终读取、账本、真实调用方及提交准入闭包：103 passed。
- dev-verify 的 serial：10 passed；最终 sentinel：51 passed。
- Web typecheck、全部 Web unit、production build/bundle budget 通过。
- Docker 问答恢复、草稿及防重复提交：3 passed；此项使用 fixture API，不冒充真实 Provider。
- deterministic flow smoke：10 passed；真实 tmux + 隔离 mock Provider 的 v5 启动：1 passed。
- git diff --check 通过，skill 三份副本逐字一致。

`dev-verify plan --base dev` 包含此前分支与 dev 的无关差异，因此另用本批次明确的 41 个路径
生成计划，无映射错误。选择器仍扩大到 421 个测试文件并回退 full pytest；遵循 AGENTS
影响闭包规则，执行其他自动 tier，加上述直接调用/共享契约闭包，没有声称全量 pytest 通过。
最终复现命令、计划和日志见第三轮归档的 `evidence/verification/` 与
`evidence/final-verification-plan.json`。例如：

```bash
PYTHONPATH=src /home/min/workspace/public/zaofu/.venv/bin/python -m pytest \
  tests/test_context_delivery_integrity.py tests/test_artifact_read_batch_cli.py \
  tests/test_artifact_read_cli.py tests/test_artifact_read_ledger.py \
  tests/test_call_result_runtime.py tests/test_call_result_admission.py \
  tests/test_task_pipeline_briefing.py -q --no-cov
bash scripts/dev-premerge-gate.sh
npm run build --prefix web
```

## 未解决及未覆盖边界

- 默认 8-lane 配置仍可能超过总预算；本批次没有实现按任务规模自动调整 lanes。
- orchestrator 的部分输出被工具转义包装，另有大配置仅返回提取结果，现有适配无法证明
  全文交付，因此保持 unknown。当前 admission 验证生产与完整范围，并未强制每条都有交付观察。
- 字节完整、可恢复和 transcript 可见，都不能保证模型注意力或语义精度。本轮未实测
  Provider 原生 compaction/切换会话恢复，也未将 Codex 结果推广到 Claude 原生问答桥接。
- fixture 没有 Feishu 通知路由，发生通知投影失败；它与产品 Run 完成分开记录，外部通知未验证。
- 预算包括 cache-read tokens；未定价不能理解为美元花费为零。

## 证据与清理

三轮归档分别为 `/tmp/zf-context-evidence-20261030-104246`、
`/tmp/zf-context-evidence-20261030-111010`、`/tmp/zf-context-evidence-20261030-113650`。
包含 runtime ledger/sidecars、Git bundle、产品快照、截图、浏览器 trace、配置 diff、
源码快照和验证脚本。第三轮重点读取 `evidence/final-runtime-proof.json`、
`evidence/independent-acceptance.json`、`evidence/context-evidence.json` 与 `cleanup.json`。
归档已脱敏，不包含 private-env 或 Provider home。

每轮真实终态后均发出 simulation.done，停止临时 runtime、Web、tmux 与 Provider 进程，
删除对应临时状态目录；第三轮 stop 与 simulation.done 均返回 0。未修改真实 dev runtime。
