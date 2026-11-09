# 198 第三批离线检索与生产评测

范围：基于 `1a1989cc` 的第三批五项实现。这里记录确定性框架与宿主观测，不是实际业务项目的语义召回或真实 Provider 资格。

用户明确要求第三批、第四批均完成后再开始真实 Provider 验证；本次没有调用真实 Provider，没有启动 FMS/ls，也没有实际 delta 或 8+8 资格声明。

## 可复现入口

```bash
PYTHONPATH=src python scripts/eval-context-inputs.py retrieval
PYTHONPATH=src python scripts/eval-context-inputs.py benchmark
PYTHONPATH=src python scripts/eval-context-inputs.py prepare --output /tmp/zf-context-eval-<UTC>/
PYTHONPATH=src python -m zf.cli.main plan inspect-inputs --bundle /tmp/zf-context-eval-<UTC>/bundle.json --task T1
```

原始测量：[JSON 记录](2026-11-02-context-198-production-evaluation.json)。计时为本机单次运行，不作跨机器性能承诺。

## 检索结果

| 检查 | 结果 | 含义 |
| --- | --- | --- |
| 100 Task 材料中 Impl、Task Verify 的 Task/AC/来源/图片/shared 定位 | 10/10 命中 | 只在本角色授权 selector 内定位，没有通读其他 Task |
| Candidate 的完整材料显式定位 | 6/7 命中 | T99 位于 512 项导航上限之外，未命中；完整原始材料仍为必读 |
| 越权、未知 ID、任务范围外查询 | 8/8 无意外命中 | 相同内容不合并角色权限；Candidate 对 T2 的读取按完整范围计正例 |
| 原文变化且选择块删除后的固定版本恢复 | 2/2 正文一致 | Impl 与 Task Verify 分别重建自己的固定输入；历史记录不证明当前记忆 |
| 正文自然语言“重复请求” | 3/3 未命中 | 正文实际含该规则；当前不是全文/语义检索，不能因未命中断言没有需求 |

显式正例合计 16/17（94.1%），不包含“不支持全文检索”的探测。现有索引未达到任意规模完整关系召回；第三批已补全空结果的导航不完整提示，不隐藏这个限制。下一批应先解决大材料精确关系的完整定位，再用真实项目评测决定是否需要 FTS/中文召回。

## 读取与生产结果

- 宿主基准：100 Task 完整来源 1,069,665 B；当前任务 shared + owned 正文 10,910 B，准备输出含封装 13,655 B / 4 页，manifest 4,232 B。
- 快照与导航构建约 32.63 ms；分页准备约 1.81 ms。这里只测准备，不把它当作 worker 实际消费、模型理解或 token/cache 节约。
- 结构预检按 100 Task 分页及单 Task 运行，报告同版位置、依赖、AC、来源和图片；缺失/重复/未知引用不显示为通过。selection 声明与绑定是否可用分别展示。
- 生成 5 组匿名盲评材料：完整对照，以及保留引用 ID 却分别遗漏异常、共享规则、图片状态、依赖语义的四个反例。预检不会把这些语义反例错误升级为机械质量判定；需要独立 Reviewer。
- 评分器单元测试覆盖显式 findings、证据 quote 和 evaluation_digest，全部漏报也能得到明确结果。真实 Reviewer 尚未运行，因此语义资格仍是 not_run。

## 实施验证及发现

实现接入 CLI、共同 dispatch briefing、实际 recovery/handoff、Plan Preview 和 Task Inputs。覆盖当前 operation/TaskAttempt/lease/workdir 及输入绑定、工作记录并发/幂等/中断、固定证据分页和跨 Plan/项目隔离。

初轮影响闭包发现两类回归：手册不能依赖内部设计文档；重复新增 briefing 文本超过既有 12 KiB envelope。已修正手册路由，并将共同 briefing 收敛为角色范围与命令导航，完整方法留在技能/手册；保持原预算门槛。最终测试数和提交号见本批五项任务归档。

真实 Provider 的结构保留、语义漏检、cache/token 成本和 8+8 并行表现仍待第三、第四批完成后验证。离线来源范围缩小不能替代质量与费用实测。
