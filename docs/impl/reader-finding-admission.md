# Reader findings 提交边界

`workflow-read` 的 typed adapter 和 Plan candidate preflight 使用 fanout 的同一
finding validator。`findings` 必须是数组，条目必须能形成有效正文、严重度及定位字段；
非法条目在提交或 Critic 派发前返回具体诊断，不能先被接纳为成功再在报告投影中失败。

首选 `message` 保存正文；既有 `summary` / `description` / `reason`、结构化
observed/expected 等表达继续由原 validator 处理。未支持的 `detail` 不被静默猜测为正文。
预检不解释 finding 的业务结论，不修改源工件，也不把有高风险发现的成功扫描改判为执行失败。
生产者应修正同一 scratch 后重新验证和提交。已接纳历史工件保持不变，通过新受控代际恢复。

回归测试 `tests/test_reader_finding_admission.py` 覆盖 producer 预检、真实 submit
拒绝及修正重试，以及 canonical report 投影，保留 artifact_production 与语义结论的区分。
