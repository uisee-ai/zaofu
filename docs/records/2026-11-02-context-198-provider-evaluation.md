# 设计 198：第三、第四批完成后的真实 Provider 对照

关联 [设计 198](../design/198-context-index-write-read-resume.md)；
[原始输出、调用身份、usage 与评分 JSON](2026-11-02-context-198-provider-evaluation.json)。
第三批已归档于 `0c11ebd7`，第四批已归档于 `3043e3dd`；两次调用均在归档之后启动。

## 实测结果

| 输入方式 | 盲评完整通过 | 遗漏识别 | 误报/漏报 | input tokens | cached input | output tokens | 墙钟 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 全部 100 Task + shared | 5/5 | 4/4 | 0/0 | 71,353 | 0 | 397 | 8.593 s |
| T1 owned + 相同 shared | 5/5 | 4/4 | 0/0 | 15,915 | 14,080 | 393 | 7.485 s |

两次使用本机已有登录的 `codex-cli 0.163.0`，请求模型 `gpt-6.1-terra`、`high`，
各自新的 ephemeral session；忽略用户配置及 exec rules，关闭 shell/unified-exec/multi-agent，
read-only sandbox。全部材料通过 stdin 提供；原始事件无工具调用、文件写入或 grader 读取。
实际耗时以 JSON 为准，表格保留毫秒；模型名为请求参数，未另作服务端路由鉴定。

输入 token 实测减少约 **77.7%**，两个 prompt 的 UTF-8 字节分别 229,239 / 6,084。
这是该次完整上下文与切片上下文的对照；公共 provider 前缀和第二次的缓存仍计入实测。
不能把缓存、墙钟差或正文字节直接当作美元节省，账单费用未观测。

## 材料与评分

复用 `scripts/eval-context-inputs.py prepare` 的确定性 100 Task、五组匿名样例。
两份输入都保留每个 case 的 case_id、review_task、original_material，仅交付 plan 中的
`task_execution_input` 端口。full 保留所有 Task；scoped 保留原 shared 和精确 T1，
没有摘要、重写或删除 T1 的正文。Task Map/覆盖矩阵等其他端口均不属于此次输入对照。
两个调用的评分目标完全相同：只对账 T1 的原始材料、owned 和 shared。

四组反例分别遗漏共享审计规则、重复请求异常、版本依赖/409、空库存 UI 状态；来源/AC ID
继续存在。第五组完整。Provider 只见匿名材料和分类规则，不见 grader 或 case 答案。
`score()` 验证 evaluation digest、case 集合、原始引文和解释，再统计 TP/FP/FN。
监工逐项核对两份输出：四项解释均对应被删正文，完整对照无误报。
这不是独立人工审阅；评分器的 `requires_independent_review` 字段仍按原值保存。

复现时先 `prepare`，再按 JSON 中 projection_method 构造同一端口的全量/切片视图，
使用所存 prompt_prefix、output_schema、command_contract 和两个独立临时目录。
材料、prompt、原始事件均有 SHA-256；JSON 保存原始事件全文与 provider session ID。

## 资格边界

- 这是两次真实模型调用的合成语义盲评，不是 FMS/ls 的完整 PRD 评审，也不是生产 workflow E2E。
- 图片状态来自原始文字描述，没有传入效果图；不能据此宣称通过视觉/UI 还原验证。
- 未执行真实 artifact-read capability/required-read/result admission 的全链路或 8+8 并行。
- 五组样例、一次配对不能证明所有项目质量无回退；未测冷缓存重复实验、账单成本或实际 delta。
- 本次未启动/恢复 FMS/ls，临时输入与进程在证据归档后清理。
