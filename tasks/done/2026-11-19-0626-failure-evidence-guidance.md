> 状态: done

# 明确只读Verifier的实际工具失败证据引用

Owner已授权真实无人验证暴露问题后修复。K3 Reader verify-lane-7在Docker失败后，
两次自动续跑仍寻找额外持久化引用，最终provider_turn_closed_without_child_result_recovery_exhausted。
另7个Reader正常使用当前工具输出标识报告同类环境失败；现有CLI合同已支持该引用。
这是方法和渲染提示歧义，不伪称新增runtime行为或测试RED。

- 明确当前工具调用/输出标识可定位实际失败 -> verify: real CLI接收带已记录工具观察的
  failure body；不要求新建证据文件；无product passed或产品报告。
- 先修改canonical skill、同步两provider分发及派发提示 -> verify: 三份一致，metadata合法。
- 保持原身份、必读恢复材料、产品通过和写权限门禁 -> verify: failure admission、
  protected stdin、caller回归及sentinel/mock通过。
- 真实补丁载入及任何人工恢复单独记账，不覆盖历史无人失败资格。

验证：60聚焦通过/1skip；116影响闭包通过/1skip；53sentinel通过；三份skill metadata及内容一致。已有CLI能力先验证成立，不声称业务RED。

实现：`7f86dfaa fix: clarify native tool evidence for verifier failure reports`。done仅为方法修正条目，真实无人验收待完成。
