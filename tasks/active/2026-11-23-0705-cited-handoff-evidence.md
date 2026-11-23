# 将已接纳结果引用的前驱执行证据显式交付

> 状态: active

授权来自持续监工中发现问题、修复、评估后续恢复的既定范围。上一轮导航方法已实际物化，
但SUM第二次Verify `evt-d56859ca339b` 仍只判断事后重演，未取原始RED。当前runtime原生
g4返工已派发，已到maxReworkAttempts=2的第二次语义返工。不得重置计数或代发通过。

原始RED `evt-74085021b54c` 存在于合法前驱冻结索引，早期blocked结果和g2 self-check
都已引用；g3只重新引用事后重演。资料没有丢失，交接缺少直接可审查的历史引用清单。

## 范围与验收

- 仅沿当前handoff的不可变前驱链，收集已admitted结果的结构化event evidence_refs -> verify: 不扫描全局日志、不跨Task/Run，不按RED等文本推断语义。
- 引用与冻结执行索引按精确event/operation/ref摘要关联，形成当前consumer的required输入 -> verify: 真正dispatch/request/source manifest携带清单且重放不重写。
- 明确来源operation、事件、时间、摘要和受控读取命令 -> verify: 多跳原始观察可直接读取；错误身份、篡改、歧义拒绝；非执行事件引用保留为未匹配事实。
- 原始输出按需加载，当前权限和完整分页不变 -> verify: 不将历史事实当当前通过，不复用旧凭据。
- 完成闭包测试、部署并观察原生恢复 -> verify: 当前worker冻结输入保留，新派发入口测试证明required输入携带清单；真实业务判词及后续激活独立观察，不能改写已在途Verify输入。

基线3d0c78ec；研究证据 `/tmp/zf-eight-postapproval-research-20261123.md`。

## 实施与验证

- 新producer只读取不可变前驱链、admitted结构化event引用和索引；输出摘要/来源/当前读取命令，不分类RED或通过。默认进入下一新request的required读取集合。
- 初轮入口测试发现清单未进入canonical required_reads（38 pass/1 fail），补充source登记后39 passed。保留首次失败日志，证明测试检查真实request接线。
- dev-verify显式本次8路径，139测试文件调用闭包：2054 passed；包含stale-contract-handoff mock；sentinel53 passed。未运行全量pytest或新真实provider场景。
- 隔离基线3d0c78ec运行相同真实dispatch回归，native/host均缺cited_execution_evidence_ref而失败（2 failed）；新版通过。临时worktree恢复干净后清理。
- 原Run当前g4 handoff只读重放生成11条引用观察/约12KB，原RED740850精确关联原Impl、输出digest及两个admitted引用。没有向业务Worker注入该清单。
- SUM g4已于07:11:59自主提交原RED+当前GREEN且无代码差量；下一Verify已07:12:28派发，早于本补丁部署。保留其冻结输入，不为制造live激活证据而重新派发。
- 报告与精确命令：`reports/cited-handoff-evidence/`。下一符合条件的新Task派发才会使用新清单；当前语义结果继续监工。
