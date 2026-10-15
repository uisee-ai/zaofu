# Channel 文件预览与 PRD 草案发布

群聊中的本地 Markdown 文件是交付来源，不是已登记或已批准的 PRD。对应实现为
`channel_document_source.py`、`channel_prd_file.py` 和既有 `channel-synthesis-request`。

## 浏览器行为

群内 Markdown 的本地文档链接使用受限文件预览，不再导航到网站同名路径。
后端只接受当前项目、当前线程明确引用的文件；保留摘要、大小、UTF-8、敏感信息、
隐藏路径和 symlink 防护。普通外部 HTTP 链接保持原行为。

文件预览提供“生成正式 PRD 草案”。这是一项显式、token-gated 操作，不是打开预览
的副作用。请求携带 `source_file: {ref, sha256}`，摘要必须与刚预览的文件一致。
当前问题未解决、仍有回复在执行或有成员待批准时不接受发布。

## 同一条 PRD 管线

受控动作读取完整文件，原子保存到配置 state_dir 中的 `channels/prd-sources/`，
将 descriptor 绑定到 `channel.synthesis.requested` 及正式 synthesis 消息。
Agent 得到不可变来源和完整 `full.v1` 合同；可以用 `prd_file: {ref, sha256}` 引用
这个快照，或输出修订后的 `prd_markdown`。不能引用任意作者路径或其他线程的快照。

Runtime 展开正文后沿用来源覆盖、需求—验收映射、readiness、版本、同版 review、
Owner 确认和 Task/Workflow 权限门禁。没有新建另一套 PRD 状态机。read_only agent
可通过结构化回复交付；不需要为了 Runtime 保存文件而提升其文件写权限。

显式文件发布把已存在的讨论进入 `phase3_synthesis`，用于恢复“文件已生成，但旧的
盲答请求曾取消”的人工交付。保留原 roster/review_roster、问题台账及失败事件，不补造
盲答贡献或签名。后续按该讨论已有的 review 策略复审，仍不得自动批准或启动 Workflow。
不带 `source_file` 的自动 synthesis、普通聊天和原有 Synthesize 路径保持原语义。

只有成功通过原有合同的回复才会发布 PRD revision；源文件或链接出现不算完成。
草案登记后可预览，显示 Draft/In review；Ready 与可规划 Task 仍由当前版本签收、
Owner 确认和 readiness 决定。修复回合保留原来源快照，延迟/重复回复继续遵守
既有 currentness 和幂等规则。

文件快照减少重复输出长正文，但不降低语义来源上下文预算，也不替代独立内容评审。
如果历史讨论超出已配置来源预算，仍应报告具体 context 拒绝原因，不能忽略来源门禁。

## 验证

- `tests/test_channel_prd_file.py`：真实受控动作、快照、完整版本、同版评审与重放防护。
- `tests/test_channel_file_preview.py` / `tests/test_web_channel_delivery.py`：路径和 HTTP 读取边界。
- 原有 PRD composition、synthesis repair、discussion、consensus、Task/Workflow 合同测试。
- Docker Playwright：群内链接预览、明确提交草案、同版 PRD 预览及未批准的 Process 禁用。
