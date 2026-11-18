# 跨工件验收责任校准

对应 [208 第三批](../../../../docs/design/208-demand-critic-input-and-convergence.md)。
前三个精简候选来自真实历史漏检：同一人工交接 AC 被产品验收规格标为无生产者的 E2E。
另两个 outcome 候选来自真实 Channel 矩阵的成功/拒绝预期混写；合法参数分支共行
可以通过，不能只因未分行而拒绝。
`oracle.json` 只给评估者，不能放入被评模型的目录或提示。正例同时覆盖纯人工责任
和合法的自动/人工互补；不能按“标签不同”机械拒绝。fixture 不是生产合同样例。

真实 Provider 是显式测试层，先取得测试授权。逐例创建新的 `/tmp/zf-assignment-<UTC>/`
目录，只复制该例为 `input.md`，把以下三个当前方法文件串联为 `method.md`：

- `skills/zf-plan-execution-readiness/references/review-scope.md`
- `skills/zf-plan-execution-readiness/references/final-package-review.md`
- `skills/zf-plan-task-map-contract/references/scenario-economy.md`

冻结输入摘要，用本目录 `prompt.md` 运行独立 Critic，例如在临时目录中：

```sh
codex exec --ignore-user-config --skip-git-repo-check --sandbox read-only \
  --model gpt-6.1-sol -c 'model_reasoning_effort="medium"' \
  -c model_auto_compact_token_limit=84000 -c features.multi_agent=false \
  -c 'web_search="disabled"' --json - < /absolute/path/prompt.md
```

将 transcript/最终答复保存在输入目录之外，复核运行前后输入摘要相同。宿主若不支持
此沙箱，明确记录实际隔离边界；不能把提示要求只读当成 OS 强制只读。完成后归档并
清理自己创建的临时目录。

人工逐条比对 `oracle.json`：反例须指出错误端口、缺失生产者和最小修正；正例不得
因 NOT_RUN、版式或合法互补责任拒绝。记录真实 verdict、遗漏/误报、fresh/cache/output、
压缩次数和耗时；关键词命中、fixture 标签及 mock 答复都不能代替语义评测。
这是已知回归集，不是未见样本的召回率证明，也不是产品实现通过的证据。

第四批增加 `unicode-aligned/conflict`：未见的导出需求，检验省略重复字段后仍能接受
完整合法矩阵，并发现码点/UTF-8 与 normalization 的真实行为冲突。与既有阶段、身份、
人工/自动责任及成功/拒绝回归分开报告；少量成对样本不代表通用召回率。
