# 自动源码修复闭环与无监工资格验证

> 状态: active

Owner 已批准按下一步执行：现有真实 Run 继续独立验收，同时核验并补齐源码修复链；条件满足后再开始新的零人工干预窗口。基线 a93fc04f，沿用隔离工作区。当前 120 次宿主采证授权保持，不合 dev、不 push。

| ID | 工作与验收 | 当前状态 |
|---|---|---|
| SR1 | 核对 RM 诊断、修复合同、隔离工作区、发布与独立验证实际调用链 -> verify: 覆盖真实入口的确定性测试，未知原因、越权、失效绑定保持拒绝。 | 核验中 |
| SR2 | 故障注入应用与重启边界，修复已复现问题 -> verify: 缺少重启前提不改源码；失败不假报已恢复；正确版本重绑后才恢复原检查点。 | 核验中 |
| SR3 | 配置受限的自动修复实验并验证 -> verify: 路径范围、合同/验证证据、授权和检查点均有效；模拟通过后才部署真实实验；完整保留失败与人工介入记录。 | 待 SR1/SR2 |
| LIVE | 既有 Run Reader → Judge → Goal → 本地交付 -> verify: 每阶段正式结果、同候选/目标/合同绑定，不将代码测试冒充真实交付。 | 8 路 Reader 运行中，19/19 原采证可用，恢复后无新采证 |
| QUAL | 新窗口验证 Worker 失败、控制器重启与 Reader 缺证恢复 -> verify: 同 Run 真实 8 Impl + 8 Task Verify 重叠，16 项独立验收、Candidate、Docker 浏览器、Judge、Goal、本地交付完整，零人工恢复。 | 前置条件未满足，不宣称通过 |

当前 Run 已有人工修复/部署，原无监工资格失败永久保留。新配置不允许 Agent 扩大授权，不将 `enabled` 或单条测试通过视为无监工完成。测试按影响闭包、sentinel、相关 mock E2E 执行；实现和实测记录提交，未满足验收保持 active。

12:32–12:33 UTC真实Reader新增两项执行失败（lane3/4，19/40必读）：lane3提交的cursor解码后摘要缺少`c9b`，与实际已交付bundle不符，现有拒绝正确；lane4遇到事件日志持续追加，前缀复核期间签名变化导致两次读取拒绝。两项均不是产品失败，也不是宿主媒体缺失。先补此运行前置：证明正常并发append不会饿死读取且仍拒绝改写/旋转漏终态；提供基于已交付页面的恢复方法，不允许猜游标或绕过必读。

## 已实施，待整体验证与真实签收

- RR：正常append争用独立进程RED复现；短时前缀锁修复后artifact闭包51 passed。
  游标报错有受控恢复指引，skill的两套既有分发副本同步；不接受损坏cursor。
- SR1：实际RM独立验证前后绑定Git候选；apply要求tested_candidate。补齐显式provider、
  结构化focused命令与可信解释器的生产/消费；必需manual review没有证据不通过。
  条件性governance scope不一致按原policy交集修复，未扩大默认授权。
- SR2：先检查重启目录、明确spawn失败的受限revert、正确boot证据才恢复、未知apply/
  restart超时产出RM诊断。初次新测试因fixture父目录未创建失败，不算RED；随后加载
  a93fc04f原三个RM函数，实际9失败/1通过，证实两个旧边界。最新直接调用闭包208通过。
- 主自动闭包6716 passed/8 skipped，后续增量检查另记。以上为确定性验证，不是
  真实OS故障、provider自动修复或8×8资格通过。代码仍未部署；LIVE/QUAL保持未完成。

12:40 UTC lane3由ZaoFu自动创建新尝试，无监工重派；其余Reader仍在执行。
原16业务任务done与当前候选签收分开；不能把8路最终Reader算成8路Task Verify重叠。

后续补齐resident→CLI accepted-only选择器，真实Git/CLI（仅provider mock）及调用闭包
68 passed；测试编写中曾使用错误fixture参数/过宽Popen替身，已更正，不计为产品缺陷。
生产者/provider/解释器闭包243 passed；新增纵向mock 1 passed，实际Git和pytest证明
失败fixture→RM接受→隔离候选发布→独立验证→应用→匹配boot后原checkpoint重新可执行。
该测试的诊断/provider/reboot为测试输入，不证明真实OS重启或最终业务工作流执行完成。

最终主自动闭包6780 passed/8 skipped；resident CLI最后补丁后相关闭包68 passed；
最终sentinel53、flow mock10通过。只读研究复核SR1/3/4/5/6代码缺口关闭，SR2撤回
必现判断；其余仅skill旧文案一致性提示已同步。12:56真实账本只读探针两次读取
37201条，冷1.468秒/复用0.002秒，无控制状态写入；没有观察到两次之间新增事件，
因此并发正确性以独立进程竞态回归为据，不把这次计时当作实时竞争负载证明。

Reader修复提交：`01acdbdb fix: keep artifact reads live during concurrent event appends`。
源码修复按独立提交保留，整批在真实签收前继续active。最后skill/CLI闭包65通过、
1跳过。范围审查：变更超过25文件，含调用者、回归、方法副本与记录；采用Reader/
source-repair两提交，前半链独立只读复核及影响闭包，没有runtime状态/秘密文件入库。
