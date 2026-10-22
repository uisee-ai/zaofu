# 草稿审批发布小产品

用户创建草稿、编辑并经不同职责审批后发布，防止发布未审版本。该产品用于跨业务迁移检查，不涉及真实外发。

- P01 POST /api/documents，body={id,title,body}，三项非空字符串；writer可创建，201返回{id,title,body,revision:1,approved_revision:null,published_revision:null}。同id409。GET /api/state 返回{documents:[...]}。
- P02 POST /api/documents/:id/edit，body={expected_revision,title,body}；writer匹配当前revision才可编辑，200返回revision+1、approved_revision:null；published_revision保留历史已发布版本，公开页面仍为原已发布内容。
- P03 POST /api/documents/:id/approve，body={expected_revision}；仅X-Role:reviewer可审批当前版本。writer/reader不能自批；不存在404，旧版本409。成功记录approved_revision=revision。
- P04 POST /api/documents/:id/publish，body={expected_revision}；仅writer且当前revision已审批才能发布，成功保存不可变发布快照和published_revision。未审批或陈旧版本409；失败不改变公开内容。
- P05 GET /api/public/:id，未发布404；发布后返回当时的{id,title,body,revision}。后续编辑不能修改已经发布的快照，重新审批/发布后才切换。
- P06 幂等、事务故障、非法输入、并发和重启遵循共同合同；两次相同版本编辑至多一次成功。reader重放writer的key仍403。
- P07 浏览器role选择框另提供reviewer；document-id、title、body输入和create/edit/approve/publish按钮，revision和public-body展示实际值。所有名称均为data-testid。先writer创建、reviewer审批、writer发布可见；未批发布明确失败。正文如<script>必须作为文本展示，不得执行。

以上7项全部mandatory。发布只更新本地公开端点，不发送网络消息。
