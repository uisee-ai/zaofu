# Candidate独立Reader的宿主执行事实消费绑定

> 状态: active

隔离8×8授权修复。原候选19条宿主命令完成但仅有源operation绑定；当前reusable=false实际约束Impl自检复用，不能推出各Reader须重跑host命令。现有Candidate host owner去重和operator补充路径没有完整接通各consumer的不可变输入、原始输出/媒体与typed result准入。保持原合同、独立产品判断、执行权限及次数边界，修复机械证据通道。

- 冻结consumer输入 -> verify: 新合法Candidate Reader派发可绑定同Run/Task/目标/合同/世代的已授权宿主源事实；保留原source operation/attempt/receipt/digest/exit code，摘要及输出/媒体通过manifest和能力读取。已有operation重放保持原输入，不原地增补。
- typed admission -> verify: 独立有类型host引用只引用已冻结consumer授权列表，kernel解析原执行事实，精确command/target/结果覆盖；不使用Impl reused_command_receipt_ids，不把原收据包装成该Reader新执行的普通probe。
- 准入边界 -> verify: 错consumer、Run、target、contract、command，未完成/重复effect，坏digest、缺output/必需媒体，未授权source访问均拒绝；非零退出不能支持passed。合法多个Reader可消费同源事实但各自完成AC与产品报告。
- 无额外授权 -> verify: 不执行新命令、不改host policy/预算/镜像/网络/保护文件、不新增已授予范围。旧候选证据不能支持代码改变后的新目标。
- 生产链路 -> verify: prepare_call_operation、真实result validate/admission、briefing/schema兼容及Candidate恢复均接通；保留混合失败、median反例和未知项，不由host exit0代替产品判定。

实现前对照：bound_container_probe_receipt已有kernel因果/manifest/policy校验；typed_result_admission当前仅合并Impl复用和普通probe覆盖；verification_result passed基础shape也需接通新引用。Reader request_hash应覆盖消费绑定，避免将自身request_hash放进绑定而形成循环。

## 实施与验证

实际prepare_call_operation有效RED：新consumer缺candidate_host_evidence_ref，1 failed/13 passed。已接通只读source fact提取、generation 0、新call消费绑定/required reads、独立host_execution_receipt_ids、预检与正式准入、briefing。旧call不补事实；多个consumer能独立准入，无新增host动作。二进制媒体先经真实preflight复现错误文本解码，再改为既有verify_sidecar_ref按字节/SHA验证。拒绝错身份、错合同/目标/世代、未知/重复收据、缺输出、坏媒体、source ACL及partial grant；未经读取无法正式准入，无合同的legacy引用不能旁路。

验证层级：83项聚焦通过；补充新目标/合同与读取门禁后35项host专题通过；相对已验证14db969c的自动影响闭包6711 passed/7 skipped，sentinel53 passed，flow mock10 passed。另已生成相对dev的总分支验证计划，避免把增量闭包冒称整个分支重新验证。真实原候选副本81源事实→19条命令绑定，19可用、9媒体SHA完整；0新执行、0原运行态写入、原证据未变。日志和副本证明在隔离SOURCE reports/eight-qualification-20261120T122929Z/candidate-host-consumption-*。

保持active：尚待部署后新Candidate的原生消费及结果准入证明；median改变代码后必须使用新目标的新证据，副本证明不等于产品或8×8通过。未合ZaoFu dev、未push。
