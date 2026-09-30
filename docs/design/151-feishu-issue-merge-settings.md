# Feishu `/issue-merge` Settings 接入

> 状态: accepted

Settings 只管理 `integrations.feishu_issue_merge`。项目管理员在飞书开发者
后台完成应用、机器人、事件订阅、群和 Base 权限后，在 Web Settings 输入
App ID、App Secret、（可选）Bot Open ID、Chat ID、操作者 Open ID、Base
app_token/table_id/view_id。

非密钥参数写入项目 `zf.yaml`；App Secret 和自动生成的回调签名密钥写入
项目 `.env`，GET/API/UI 永不回显。保存使用配置摘要 CAS，并同步开启
`runtime.feishu_inbound`，由 `zf start` 的受控 sidecar 启动现有 bridge。

加载器把 Issue-merge 的操作者和精确群路由编译到现有 Feishu identity/routing
模型；消息入口只接受 Issue/Self-Issue 命令，其他 task、kanban 和 Run Manager
命令在该模式下拒绝。连接测试只调用 Feishu 只读接口，不创建或修改 Base 字段。

Issue 卡片沿用 Web Kanban 的受控动作合同：`awaiting_fix_approval` 展示并精确
绑定当前 Fix Proposal，支持批准并排队 Fix、填写原因拒绝和刷新 Proposal；运行中
的 Triage/Fix/Verify Run 支持暂停、恢复和输入确认文本后永久取消。Verified
Candidate 支持批准、要求修改和拒绝；PR 阶段支持登记 PR、打开 PR、打开 review
以及刷新远端状态。所有按钮都携带签名回调 token，并通过
`ControlledActionService`、proposal/revision 校验和 `EventWriter`，Feishu 不直接
写入 Issue 或 Run 的 canonical state。
