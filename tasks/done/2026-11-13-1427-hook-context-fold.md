> 状态: done

实现提交：`8eaede97 fix: fold append-only artifact context reads safely`。

交付纠正：归档后Root与独立review确认第三个P1，旧候选 `63c36fbb` 单独不可合并，
此前未合dev/未部署。由 [部分快照补充任务](2026-11-13-1454-hook-context-partial-snapshot.md)
在新worktree完成修复（`f6f55655`）并重新验证；本记录保留原实现历史，仅以包含补充提交的新HEAD交付。

# Hook/reader 上下文追加读取复用

来源：LS-v1 C1 Verify 监工，`tool-roundtrip-observation.md`（2026-11-13）；Root 已依据用户持续修复授权指定此独立 worktree 实施。仅修复 ArtifactContextEvents 在 active 少量追加后重复扫描未变归档的缺口。

- 复现只增一条却重解码旧档 -> verify: 解码次数回归先红后绿，结果与独立完整过滤视图一致。
- 复用未变归档，对证明前缀不变的 active 仅解码新完整行 -> verify: partial tail、过滤/run aliases/preserve ids、同长改写/改写后增长、替换/截断/rotation/race、signed不缓存均保留。
- 保持实际hook/required-reader caller接线 -> verify: focused caller与deterministic mock E2E、dev-verify plan/run、premerge sentinel通过。
- 记录性能和边界 -> verify: 同fixture与安全只读真实ledger前后计量；冷启动仍全读，不声称解决全部30秒；更新 docs/impl/artifact-context-fold.md。

禁止改主checkout、LS状态、权限/过滤语义；不重启、不push、不merge。本任务完成实现commit后再记录hash并归档。

## 实施记录

- 基线回归3 failed/4 passed：追加重复解码，以及恢复mtime的同长active/archive改写。
- 独立审核发现两项P1并先复现再修复：无后验fallback轮转漏settled；snapshot边界前EOF/短partial被误当合法partial。均以实际 `_require_current_operation` 调用验证不能授权已settled attempt。
- 最终机制和性能/冷启动限制见 [实现说明](../../docs/impl/artifact-context-fold.md)。普通append可返回经完整前缀复验的冻结snapshot，下次只读取tail并及时吸收terminal。
- 模块/hook/CLI focused：54 passed；最终dev-verify 26文件影响闭包262 passed/35.41s（含4个deterministic mock E2E入口），premerge sentinel 53 passed/3.17s。
- 当时独立审核源码SHA `e3f7cf517c7dc8fcc66031026cc4d1b00c9b3220c302a201db9249518a397632`、测试SHA `36439eec1958fd5d0a86e7dd74b062144749b2a8e78426041a485173da98acd6`，其后发现第三项P1，原无剩余finding结论撤回，见顶部补充任务。此前额外6类纯内存caller断言通过；未执行real-provider tier或全量pytest。
- 最终机器receipt `/tmp/zf-hook-context-fold-20261113-dev-verify-final.json` SHA `496f8251270a4ba1ebb9196433f77f83f41ca40b0b9df96d7973ccf2edf26e84`；此前260项receipt在短读修订后不作为最终证据。

验证命令均在此worktree、主checkout `.venv/bin/python`、`PYTHONPATH=src` 下执行：

```sh
python scripts/dev-verify.py plan --base dev --tests tests/test_artifact_context_fold.py --tests tests/test_artifact_context_events.py --tests tests/test_hook_context_events.py --tests tests/test_artifact_read_batch_cli.py --tests tests/e2e/test_stale_contract_handoff_mock_e2e.py --no-parallel
python scripts/dev-verify.py run --base dev --tests tests/test_artifact_context_fold.py --tests tests/test_artifact_context_events.py --tests tests/test_hook_context_events.py --tests tests/test_artifact_read_batch_cli.py --tests tests/e2e/test_stale_contract_handoff_mock_e2e.py --no-parallel --receipt /tmp/zf-hook-context-fold-20261113-dev-verify-final.json
```

run自动调用 `bash scripts/dev-premerge-gate.sh`。临时fixture已随TemporaryDirectory清理；真实ledger仅只读，未生成新hook/read事件。旧归档约435MB保持过滤语义；局部性能收益和冷启动局限记录于实现说明，不作为整体30秒全部解决的声明。
