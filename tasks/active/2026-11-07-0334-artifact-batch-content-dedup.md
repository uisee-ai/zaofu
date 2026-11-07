> 状态: active

# 必读输入的同源正文去重

监工发现 Align/Retention 的 fresh context 回收前真实上下文超过 93%；
恢复后需重新消费原始必读输入。task_map/planning_result 指向同一个文件，
相同 JSONPath 正文仍被重复返回。用户已授权修复卡顿，主会话批准本片实施。

## 范围和验收

- CLI 显式 `--required --deduplicate-content` -> verify: 同源选区输出一次正文、每个来源保留独立读取收据。
- 逐来源保留原鉴权/摘要/JSONPath 校验 -> verify: 第二个 alias 无权限、摘要错误或选区不允许时不得借用首个成功读取。
- 严格文件/完整摘要/JSONPath/limits/正文摘要分组 -> verify: 不同文件或选区不合并；截断不复用；新调用重新输出正文。
- runtime briefing 推荐新命令 -> verify: 真实 Task Pipeline transport 收到命令及 content_ref 消费说明。
- 原 CLI 单来源与未加新 flag 的批量格式保持 -> verify: 原批量/CLI/ledger 合同测试通过。
- 闭包、sentinel、相关 mock E2E -> verify: 日志记录实际结果；真实输入只读测量不生成 read receipts。

不修改 immutable manifest/required read policy，不跨 provider session 免读，
不删共享语义内容，不调整阈值；不修改运行态。提交/同步/重载由主会话负责。

实现说明：[必读正文去重](../../docs/impl/artifact-batch-content-dedup.md)。

## 验证记录

RED：新输出通路接线后，正文重复与缺少 alias 引用导致 2 failed、16 passed。
日志 `/tmp/zf-supervision-20261107/artifact-dedup-red.log`。
初步 GREEN：批量输出、纯输出 helper、读取 ledger 共 33 passed。
最终 focused 闭包：88 passed，命令如下（cwd 为隔离 worktree）：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src /home/min/workspace/public/zaofu/.venv/bin/python -m pytest \
  tests/test_artifact_read_batch_cli.py tests/test_artifact_read_output.py \
  tests/test_artifact_read_cli.py tests/test_artifact_read_ledger.py \
  tests/test_artifact_read_capability.py tests/test_task_pipeline_capsule.py \
  tests/test_task_pipeline_briefing.py tests/test_context_delivery.py \
  tests/test_fanout_briefing_scope.py tests/test_refactor_fanout_briefing.py \
  tests/e2e/test_doc152_artifact_package_mock_e2e.py -q --no-cov
```

日志 `/tmp/zf-supervision-20261107/artifact-dedup-closure.log`。
`dev-verify.py plan --base dev` 包含之前分支修复并选出775测试文件；以本片
起点 `--base 74de874d` 仍因公共 CLI 静态引用选出213文件并升级全套。
经主会话同意不执行整套：实际执行上述显式 CLI→ledger/capability→briefing
caller→context compatibility→mock package 闭包；`dev-verify.py run --base
74de874d --skip-tier deterministic --skip-tier serial` 执行自动 contract tier，
50 passed、1 skipped，不能把跳过的 broad tier 声称为通过。
最终源码另跑 `bash scripts/dev-premerge-gate.sh`，结果见
`/tmp/zf-supervision-20261107/artifact-dedup-sentinel.log`。

真实输入量化使用现有 manifest 选区和已经写入的读取收据；没有发出新读取
收据，也未调用业务 artifact CLI。完整 read 元数据版仅净省约7.2%，经审查
收敛为 `{attempt_id, occurrence_id, row_sha256}` 后：

| 输入 | 旧输出字节 | 新输出字节 | 净减少 |
| --- | ---: | ---: | ---: |
| Align，100选区/20别名 | 849455 | 720059 | 129396，15.23% |
| Retention，101选区/20别名 | 862574 | 731347 | 131227，15.21% |

脚本 `/tmp/zf-supervision-20261107/measure-artifact-dedup.py`；结果
`artifact-dedup-output-measure-compact.json`。选区数量不包括 briefing 中的
首选批量命令。该结果仅证明机械重复减少，未宣称线上context回收循环已完全消失。

本任务保持 active，待主会话审查、提交与记录 commit；本片不自行上线。
