# 项目验证入口

测试方法由 Skill 维护，本轮 Prompt 指定目标，现有测试 runner 执行断言。
本批没有安装 Midscene/e2e，也没有改变 ZaoFu 的任务 schema 或状态准入。

## ZaoFu 自身

调用 [zf-verify-zaofu](../../skills/zf-verify-zaofu/SKILL.md)，从其中的场景表选择
启动、恢复、多 lane、前端检查。现有 Verify 角色通过
[verification checklist](../../skills/zf-harness-verification-checklist/SKILL.md)
加载通用方法；项目专属 Skill 由该项目的 briefing/角色配置明确选择，不能塞入所有
产品共享的默认 workflow。配置仍只通过项目 `zf.yaml`，本批不改正在运行的配置。

普通交互会话可直接使用项目 Skill 的 Prompt；受控 worker 保留原 Active task、
required reads 和结果 profile。测试中使用的内部 fixture 不是 worker 可以伪造事实的权限。

使用 `scripts/dev-verify.py run` 做自动验证时，工作目录取自脚本所在的 Git checkout。测试、哨兵、Web 与 mock 子进程使用临时 HOME、tmux 目录和用户服务总线地址。每个测试项目按自己的 `zf.yaml` 确定状态目录。若运行中的项目与验证 checkout 重合，请从独立 checkout 执行。真实宿主或模型验证继续使用显式入口。

## 其他产品

按 [通用项目方法](../../skills/zf-harness-verification-checklist/references/project-verification.md)
维护产品自己的验证 Skill。以数据产品为例，方法应包含：

| 项目事实 | 需要落实的内容 |
| --- | --- |
| 原始需求 | 需求 ID → 导入/处理/导出场景 → 独立预期与断言 |
| 启动 | 实际命令、健康检查、Compose 服务及其就绪条件 |
| 数据 | dataset 路径、固定来源/摘要、种子数据和清理范围 |
| PostgreSQL（若使用） | 每个并发实例的数据隔离、约束与幂等性检查 |
| 证据 | UI 操作、API 响应、实际记录和输出文件，同时绑定候选版本 |

这里是适配方法，不代表已经读取或验收 dl。首次适配时从其代码、需求和启动入口取得
真实值；用户已有环境选择优先。无需为没有数据库的项目引入 PostgreSQL。

## 执行器选择

沿用 pytest/API/Playwright。需要视觉操作时可在现有 Playwright 中试接 Midscene；
新项目可评估 e2e runner。先用一个正常样本和一个明确错误样本比较结果与证据质量，
再决定是否扩大。模型不可用、工具失败、缓存回放分别记录，不能代替本轮业务验收。

参考方法来自 [e2e 项目验证 Skill](https://github.com/tester-army/e2e/tree/14dc946c40091496531451cf40e81eb407fe640e/skills/create-verification-skill)、
[Midscene Playwright 接入](https://midscenejs.com/zh/integrate-with-playwright) 和
[生命周期与并发](https://midscenejs.com/zh/midscene-test/execution)。

ZaoFu 的 PRD 规划 UI 闭环见 [两条 UI 旅程](../../skills/zf-verify-zaofu/references/prd-ui-journeys.md)：
Kanban 和 Channel 各自生成 PRD，再经 PRD v5 Workflow 生成完整 Plan；先 fake 后真实 Provider。
本轮实测结果、修复及真实层阻塞见 [验证记录](../records/2026-11-10-prd-ui-validation.md)。

完整 Plan 的 `plan_content_profile` 必须由已准入的综合审查结果显式声明并传到
`task_map.ready`、待审批预览。不得根据 Markdown 标题猜测完整性；缺失声明保持普通
Plan，`summary_only` 保持摘要。UI PRD 验证同时检查该交接、正文和下载一致性。

直接运行本仓库 pytest 时，收集前会分别检查当前目录、源码 checkout、继承的项目根和显式状态路径。任一路径的现有事件账本（含已轮转的分片）都可能被后续测试或换目录后的 CLI 选中。请在独立、干净的 checkout 运行 `scripts/dev-verify.py run`：入口按净化后的环境解析该 checkout 的 `project.state_dir`，并检查解析后的实际路径，包含符号链接和自定义名称。同一次验证的临时收据使后续 pytest、哨兵、mock 阶段可使用已确认干净的目录中新增的 fixture 数据；临时收据随验证退出而清理。
