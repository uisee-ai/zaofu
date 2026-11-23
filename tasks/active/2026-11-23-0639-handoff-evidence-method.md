# 恢复验证按需定位前驱证据

> 状态: active

持续监工授权范围内修复已复现的证据导航与方法问题；不替业务 Worker 改代码、提交或重派。

## 现场事实

SUM Verify `evt-ea0ffdc342e0` / admission `evt-cd84eaaa86a5` 以缺RED拒绝；原Impl
`evt-74085021b54c` 的RED在合法前驱链中，时序为先改测试、RED、再改实现、GREEN。
最近publication复核仅GREEN，不代表全部实现历史。runtime已原生派发返工
`evt-6dabd0f55ff2`。研究报告 `/tmp/zf-eight-postapproval-research-20261123.md`。

此前handoff next_action要求读每页索引，SUM逐页遍历42项/31806字节；前驱导航字段
又位于索引正文末尾。已有按event查询能力，修复不新增语义状态机。

## 验收

- 首屏即可定位相关前驱 -> verify: 当前索引还有未读页时，经合法source-operation直接读到原RED。
- 方法按需而不丢信息 -> verify: 已有跨attempt已知event直读、输出完整分页、错误event/source/digest/token拒绝测试通过。
- Verify方法区分publication复核与原实施 -> verify: canonical与Codex/Claude分发一致；不降低当前目标独立验收义务。
- 当前Run自主返工继续 -> verify: 保留现有worker/当前冻结输入，后续新派发物化新版方法；不补造RED或人工通过。

基线9d360d9e；本次源码改动仅生成指令及受控CLI只读导航投影。事件、结果、存储schema保持。

## 定向验证

- 初轮32 passed；dev-verify显式本次路径及直接调用/Skill分发/旧合同handoff mock：354 passed、1 skipped；sentinel53 passed。
- 包含 authenticated CLI：索引还有未读页即可定位前驱；已知event直接读取；完整正文分页、错误source/event/token/digest拒绝及跨attempt绑定。
- ZaoFu Skill校验/dispatch treatment测试通过，canonical/Codex/Claude三份字节一致。通用skill-creator quick_validate不支持该Skill既有stages/tags/auto_inject/load_on_demand字段；前言与基线字节完全相同，未为通过工具删字段。
- 跨worktree运行新回归作为基线负例，被仓库import归属门拒绝（2 setup errors），不计作行为RED，不改此门。现场旧响应与指令已经复现缺失导航/全索引读取。
- 精确计划、日志与回执：`reports/handoff-evidence-method/`。本轮不运行全量pytest或新真实provider Run。
- 当前SUM已由runtime自主返工；尚待部署后下一Verify实际物化新版方法的证据，保持active。
