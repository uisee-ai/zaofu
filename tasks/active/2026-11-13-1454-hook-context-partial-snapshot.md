> 状态: active

# 补充修复：部分快照不能仅凭已消费前缀承认并发追加

来源：Root最终疑点及独立review确认第三个P1。旧候选 `63c36fbb` 未合dev/未部署；新worktree由dev快进包含旧候选，旧worktree保持冻结。用户持续修复授权已覆盖此精确followup。

- 复现真实 BufferedReader(8192)、40KiB settled被等长无newline覆盖，读满剩余边界后恢复并append -> verify: 实际 `_require_current_operation` 旧实现错误接受，新增回归先红后绿。
- 只修增长快照匹配：未消费到原snapshot边界时不得只验证consumed前缀 -> verify: 重试完整读取后拒绝settled；普通完整snapshot append仍高效、合法partial补齐和下一次terminal刷新不变。
- 交付补充纠正 -> verify: 更新原impl/旧task链接与本task，最新whole closure/sentinel、独立审核通过，再实现commit+archivecommit；新HEAD包含旧63c36fbb。

禁止改旧冻结worktree、主checkout、LS运行态；不重启、不merge/push。新验证完成前旧handoff不具备落地条件。

## 实施与验证

- 真实文件、`io.BufferedReader(8192)` 与40KiB settled回归先红：实际caller错误接受，`DID NOT RAISE`；只增加增长匹配时 `consumed < 原snapshot size` 必须重试后转绿，实际返回 `operation_terminal`。不是伪造readline输出。
- 模块/hook/CLI focused 55 passed；以dev为基线的26文件影响闭包263 passed/37.81s（含4个deterministic mock E2E），自动premerge sentinel53 passed/4.18s。未运行real-provider或全量pytest。
- 机器receipt `/tmp/zf-hook-context-partial-20261113-dev-verify-final.json` SHA `e537616a1a65bd074f83cf4ceec7aa3faf446aa4164b592cd67491863511399d`；运行后只补本文/impl测量记录，source/test未变。
- source SHA `06df77413d9776015b03475250f24da4e8f5cd92e83b9588fcfe4d8334429be7`；test SHA `0a8bfba343d5351ef70eceee87d0e768bc94336dc6b1767bd80efeaa93689509`。第三项P1原因、前两项回归保留、partial安全重试限制见 [实现说明](../../docs/impl/artifact-context-fold.md)。
- 独立review（2026-11-13 15:02 UTC）绑定上述source/test：真实BufferedReader纯内存大tail反例、正常完整append、稳定partial、并发补齐及旧两个P1路径七条独立断言通过，指定范围无剩余confirmed finding。Root另跑premerge sentinel53 passed/4.20s；源码/测试未改。
- 同10,000条fixture+同1条append与dev基线完整结果相同：旧cold0.105955s/new0.116722s，旧append0.103102s/new0.002401s。只证明完整snapshot追加热路径；冷启动仍全读。本轮未扫描活LS日志、未发事件、未改运行态；TemporaryDirectory夹具自动清理。

```sh
PYTHONPATH=src /home/min/workspace/public/zaofu/.venv/bin/python scripts/dev-verify.py plan --base dev --tests tests/test_artifact_context_fold.py --tests tests/test_artifact_context_events.py --tests tests/test_hook_context_events.py --tests tests/test_artifact_read_batch_cli.py --tests tests/e2e/test_stale_contract_handoff_mock_e2e.py --no-parallel
PYTHONPATH=src /home/min/workspace/public/zaofu/.venv/bin/python scripts/dev-verify.py run --base dev --tests tests/test_artifact_context_fold.py --tests tests/test_artifact_context_events.py --tests tests/test_hook_context_events.py --tests tests/test_artifact_read_batch_cli.py --tests tests/e2e/test_stale_contract_handoff_mock_e2e.py --no-parallel --receipt /tmp/zf-hook-context-partial-20261113-dev-verify-final.json
```
