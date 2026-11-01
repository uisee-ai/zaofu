> 状态: active

# Kanban → Judge 真实验证与阻塞修复

用户已批准按[测试 Prompt](../../docs/runbooks/2026-10-31-0040-kanban-judge-fidelity-e2e-prompt.md)执行两轮真实测试及必要修复。

- B01：聊天 Task 没有独立 source_ref，已有完整 Requirement 和绑定合同却卡在 PRD 源文件澄清。使用经身份/digest/覆盖校验的 Task 绑定，物化完整已确认 Requirement 快照；显式源仍优先且无效时拒绝。step -> verify: 先复现当前装配失败；新增真实合成/装配入口与损坏边界测试，浏览器重新准备成功。
- B02：执行基线 A 的双审批、Plan/Impl/Verify/Discovery/Judge 交付。step -> verify: 同 Run 的 Kernel 完成证据、冻结/交付 commit 独立 oracle、需求×阶段证据矩阵。
- B03：执行 B 的 YAML/Plan 局部修改、旧审批拒绝与受控恢复。step -> verify: 新 revision 生效，未改需求完整，旧动作不能重复运行，恢复不跳过审批。
- B04：收尾。step -> verify: 保存失败/修复证据与 unknown，simulation.done，审计并清理本次 runtime/Web/tmux/container，保留源码与必要产品证据。
- B05：Raw YAML 把 contextWindowTokens 的纯数字误识别为密钥，导致 Preview 丢失上下文窗口配置。step -> verify: 先复现 200000 被遮盖；只放行已知字段的纯数字，真实凭据和数字后接秘密仍遮盖；浏览器 Preview 恢复真实数值。
- B06：Preview 接受已安装但不适用于 Impl 阶段的技能，Apply 后 runtime 启动才失败。step -> verify: 复用启动技能校验于 preflight 与 Apply；无效阶段绑定提前拒绝且不写配置，合法绑定通过；保留失败 Run 并受控取消后在新项目重试。

记录：[运行报告](../../docs/records/2026-10-31-kanban-judge-fidelity-e2e.md)。

- B07：Judge 的 closure-fact 缺 Workflow 人工审批，真实产品行为通过仍被阻塞。step -> verify: 真实拒绝保留；按 invocation 的 Request/revision/digest 投影实际审批与提交证据，旧版本/错 Run/晚审批拒绝绑定；新 closure identity 受控重驱并由真实 Judge 再裁决，不制造审批或放宽判定。

## 本轮状态（2026-10-31 UTC）

- B01/B05/B06/B07：实现与聚焦回归通过，真实浏览器/Provider 复测证据见报告。
- B02：通过；A 最终提交 aa749e4f10cd7a8e45f807a8001b4c7b8a99dbe0，同 Run 三个终态事件与独立 oracle/8 项测试成立。
- B03：**未完成交付验收**。YAML 局部修改、旧审批拒绝、幂等、Plan 全文引用、Diff 局部性和安全检查点重启通过；world→driver 的正式 Requirement/mandatory claim 修订协议缺失，Critic 正式 rejected，受控取消负例。保留 active，不归档 done。后续触发条件：实施经 owner 授权且绑定具体 claim 的修订协议，再重跑 B2 至最终 driver oracle。
- B04：测试 runtime/Web/容器停止，四份 runtime 归档与三个产品 bundle 保留；dev/8001 未改。独立诊断 checkout 的候选资料保留，未强制删除脏 worktree。
- 仍不能证明所有阶段输入无损：OA、Discovery、重试 Judge 的 Provider witness 存在 unknown。不得将其改写成已观测。

本批测试已得到可复现结论，但原 Prompt 的“两轮交付和关键输入全证明”标准尚未全部达到。已修复项单独提交，未完成项保留待继续。
