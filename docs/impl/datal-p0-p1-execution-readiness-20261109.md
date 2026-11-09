# datal P0/P1 执行就绪修复记录

关联[活动任务](../../tasks/active/2026-11-09-1225-datal-p0-p1-execution-readiness.md)。
本记录区分代码修复、环境实验和真实工作流准入；后三者不能互相替代。

## P0：提交前输入成本

真实失败 `evt-2b3000888026` 的 Critic 完整必读包为 4,194,524 bytes，
超过 4,194,304 bytes 上限 220 bytes。原 `input_cost` 明确只估算机器端口，
却容易被使用者误读为完整预算。`zf result validate` 现在另给
`input_cost.delivery_estimate`：统计作者报告、可解析附件/Markdown、执行审查、
与请求摘要绑定的冻结规划上下文；按实际渲染摘要去重，中文按字符计算分页。

该诊断始终 `authoritative=false`、`complete=false`；尚未生成的 Critic
合同、当前状态/局部选择、admitted envelope/prepared report 单列为未知。
已知估计超限为 `review_required`，未超限为 `incomplete_estimate`，不声称可派发。
最终冻结 manifest 的预算门禁保持不变。附件缺失和摘要损坏不静默计为零成本通过。
验证入口不产生事件、read receipt 或 sidecar。规划技能同步要求检查这些字段。

新增测试先失败 3 项，再通过；相关入口和影响闭包 571 项通过。
技能元数据 16 passed、1 skipped。此为机械诊断改进，不是独立 Critic 语义通过。

## P0：任务输入版本

原在途 P12B、P16B 仍绑定旧版输入，不改写其冻结 manifest。
对最新候选 `fanout-prd-plan-evt-21238a11` 的七端口 task-local 选择只读测量：
P12B 210,349 bytes/130 selections，P16B 160,127 bytes/83 selections。
此数不含最终全部上下文，不是派发预算或读取完成证据。
当前 Kernel 已支持声明式任务输入选择；下一步应正式审查和批准版本，不能再写
针对 datal 的字段过滤器或让重试偷偷切到新合同。

## P1：可拆范围及必须保留的集成

依据上述候选中的完整 task-map 条款；当前未完成整任务 DAG 宽度 3，
Impl/Verify 配置各 8，不能由容量推导实际可运行数。以下是供 Planner/Critic
裁决的具体切片，尚非批准任务：

| 原任务 | 可以提前实现并独立验证的行为 | 保留在原集成任务的责任 |
| --- | --- | --- |
| P11B | 比较条件判定与差异报告核心；不同 GT/评分器/板卡不出同条件提升，缺值不填 FPS，报告草稿缺证据标记 | 实际 Dataset/Quality、PC/INT8 运行结果接入，HTML/PDF 导出与权限拒绝不变集，实板性能验收 |
| P12C | 六态计分纯行为；空集不为100%，固定分母，20项14PASS/2FAIL/四态各1的80%/87.5%/70%，逐场景不回退判断 | P12B真实结果输入、P11B比较结果、冻结plan_version与重试历史、API/UI/数据库集成 |
| P13 | Case登记、候选组/发生记录/Case区分、三轴标签与历史记录模型 | 回流许可、冻结回归+复核关闭、P0下一版阻断、复发关联及撤权拒绝；不能移除P12C集成依赖 |

原 LOCAL-P11B/P12C/P13 均包含真实 API/UI/worker/数据库责任，不能直接删其依赖。
新增核心片必须提供独立 AC/命令，旧 AC 仍归完整集成片；fixture 不冒充真实数据。
优先让三个新增核心片分别独占 comparison/regression/cases 内的新模块及测试，
后续集成片依赖其精确版本。不要让新旧片同时写共享 api.py/manifest/migrations。
最终由独立 Critic 检查来源、接口、路径及 AC 映射，再通过受控 Plan 版本准入。

## P1：锁定依赖的冷环境实证

从 `1f82931345fa728fd627eb5e3818b4fee989f89e` 导出两个独立临时 checkout，
开始时均无 `.venv`。原 `tools/bootstrap.py --locked` 在离线缓存下均失败：
`av==16.0.1` 的远程 URL 对应缓存缺失。之前本地 wheel 安装不等于此 URL 缓存就绪。

对同一正式 bootstrap 设置 `UV_OVERRIDE=<verified-local-wheels.txt>`，
保留 `--require-hashes`、原 requirements.lock，并开启 UV/npm 离线模式后，
Impl/Verify 冷环境分别 6.23s、6.28s 返回0。两环境导入 av/PIL/pytest/psycopg/
fastapi 成功，venv 各自独立。原锁摘要均为
`00df069a28ffe43d2d7bbb3865751a7934dc298ce41c32d3973d9a68ecdf575a`。

这证明可重放准备方法可行，尚未证明后续 worker 自动采用。准备步骤需要正式纳入
新任务共享环境合同，提供 wheel 来源/哈希/持久路径、Python3.11及失败非零要求，
再以新 Impl 和独立 Verify 的真实派发验收。当前冻结运行配置不热改。
安装与导入成功不计为产品 RED/GREEN。

## 证据及未关闭项

真实项目 `diagnostics/reader-triage-20261109/` 保存 DAG、候选输入成本和问题记录；
`diagnostics/dependency-recovery-20261109/cold-environment-run.json` 保存冷环境
来源提交、命令结果、日志和导入证据。暂存于 `/tmp/zf-datal-coldenv-*` 的实验目录
只用于本次验证，不能当生产依赖缓存。

当前新 Planner `fanout-prd-plan-evt-8e7c080d` 负责保持九机器端口不变的表示修复。
其完整预算、独立 Critic、同版 approve、两个 gap 正式准入尚待验证。
新增并行切片与环境合同需后续受控差量，不能给当前冻结 attempt 临时扩范围。
历史账本 NUL 损坏仍阻挡自动恢复的全局 gate，未删除原始记录或绕过该 gate。
