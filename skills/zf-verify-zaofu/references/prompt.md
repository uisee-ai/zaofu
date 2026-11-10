# 本轮验证 Prompt

将尖括号替换为本轮事实。执行层级沿用已经授权的范围；缺少关键目标时先查 briefing
和项目上下文。提示文本不是权限凭据，也不覆盖冻结的合同或任务身份。

```text
使用 $zf-verify-zaofu 和现有 zf-harness-verification-checklist。
项目：<ZaoFu checkout 绝对路径>
目标：<候选 commit 或明确标注的 dirty worktree>；base：<比较基线>
需求/合同：<原始来源和本轮负责的条款>
范围：<启动 / 恢复 / 多 lane / Dashboard 中适用的场景>
执行层级：<本轮授权的 deterministic/mock、浏览器或真实 Provider>
证据目录：<本轮独立输出路径或 briefing 提供的证据位置>

先把原始条款关联到场景、断言和命令，再运行所需验证。
恢复必须说明证明到 runtime 重建、进程重启或真实 Provider 的哪一层。
多 lane 检查同时在途、独立产物和同一候选版本的汇总。
新验收探针用错误产物验证其拒绝能力，失败须指向预期缺陷。
逐项报告通过、失败、阻塞、未执行；保留命令和产物证据。
按既有结果路径交接；不要改 canonical state 来制造通过。
```

验证其他产品时，把第一行替换成通用验证方法与该产品的现有项目 Skill，范围改为
业务链，并提供其真实启动、fixture、数据隔离与验收入口。不要套用 ZaoFu 的内部
测试文件或假设业务项目也使用 ZaoFu 的状态目录布局。
