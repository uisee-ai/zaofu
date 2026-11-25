# 206：规格精简、独立交接与阅读定位实施评估

日期：2026-11-10。对应 [206 设计方案](../design/206-spec-method-borrowings-and-quality-evaluation.md)。
本批完成紧凑方法、任务阅读入口、证据位置比较、六类真实 Provider 生成/交接试点与复评。
它补上了内部方法实验，尚未完成外部工具原生同条件实验、真人交接或完整 YAML v5 产品流程认证。

## 1. 实施顺序及边界

1. 固定基线 `fe39e0f85dfb42782f12864fc9c608880cec8b1a`，先复现重复与定位问题，再修复展示。
2. 冻结六类要求及独立 oracle，运行基线/候选生成、新会话交接、最小实现验证和语义校准。
3. 按实际结果修订方法，复测后核对输出及调用成本；只扩展已有阅读投影，更新能力评分。

实现继续复用 Task contract、Plan Package 与原有来源/覆盖矩阵。语义质量由方法、作者和评审负责；
没有新增 Kernel 语义评分门禁、自动批准、第二套任务 schema 或自动撤销旧证据的机制。

## 2. 真实问题与修复

| 问题及证据 | 实施 | 验证及限度 |
| --- | --- | --- |
| 历史 R2 原文 T-1/T-2 有两个完全重复章节，去除首尾空白共 843 字符 | Task Markdown 阅读时折叠跨任务相同章节；保留位置、全部原文及下载 | 固定真实摘录，单测重组原文逐字一致；双视口检查折叠/展开、下载；不做语义去重 |
| 阅读任务后要重新在全局条款中定位来源、AC、命令 | 增加“查看本任务来源、验收与证据”，精确 Task ID 过滤并可返回全部来源 | 浏览器从模块进入追溯并返回；不把相似任务名当相同任务 |
| 原 trace 比较签名没有 evidence 路径；只变路径时没有变化条目 | 比较证据位置，显示旧/新位置及两版明确消费者 | 证据单独变化单测；浏览器实际读取两版矩阵；读取不对称时报未知 |
| 生成规格把未交付的 method.md 列作产品依据 | 方法明确“写作指导不等于产品权威来源”；共享事实必须可交接 | R4 候选四例出现该问题，R5 六例均未再出现；有限样本不能证明永不复发 |
| 减少方法字数不保证产物更短 | 保留最小义务，允许简单任务合并组织，不重复整套来源/状态/验证说明 | 首次候选产物变长，修订后下降；完整流程成本尚未测 |

折叠目前用于精确 Task 标题解析的 Markdown 原文分区；已有 `execution_spec` 任务分区仍直接展示作者正文。
原始要求、决定、不同约束不会被语义合并。消费者来自已绑定条款关系，不自动推导全部间接依赖。
版本比较不是完整语义 diff；是否保留行为、重审任务或使证据失效，仍需评审和受控修订。

## 3. 可复现的实验设计

入口：[生成与交接脚本](../../scripts/eval-spec-handoff.py)、[实现验证脚本](../../scripts/verify-spec-handoff.py)、
[固定输入说明](../../tests/fixtures/spec_handoff_eval/README.md)。复用已有 Provider runner，没有部署评估服务。

- 模型 `gpt-6.1-sol`，CLI `0.165.0`；两组均 Provider 默认 reasoning effort，未固定采样 seed。
- 正式可用试点使用 `host-text`：冻结文件正文直接送入只读会话，禁止工具；检查轨迹未出现命令。
- 作者只取得要求及方法。接手者是同模型的新会话，只取得要求和作者最终 Spec，不取得方法、作者聊天、自评或 oracle。
- 每类每组一次，单次上限 240 秒、最多两个并发，不自动重试；这不是独立真人或跨模型盲评。
- 模型实现由独立、预先冻结且未提供给模型的测试执行。容器无网络、只读，限制 CPU/内存/进程和时间；不在宿主执行生成代码。
- 验证器核对 manifest 的 oracle 摘要与交接轨迹摘要，从最终原始回答读取实现，不相信可修改的 `parsed.json` 投影。
- `completed_ungraded` 只表示调用/格式完成；不能直接转为语义通过或正式任务终态。

| 六类输入 | 观察点 | 实现范围 |
| --- | --- | --- |
| save-failure | 持久化成功后才发布；失败保值、保输入、可重试 | 纯函数接口与异步 write 回调 |
| permissions | 所有者检查、拒绝不变、忽略所有权转移 | 纯函数更新接口 |
| shared-scenario | T-F/T-A 各自 AC、命令、owner/tier；共享规则一处维护 | 规格/交接，不实现应用 |
| migration | 保留字段、未知版本不写、原恢复义务、三消费者与旧证据重审 | 规格/交接，不补造原合同 |
| future-decision | 本期本地范围；未来决定有 owner/期限且不阻塞当前 | 规格/交接 |
| dependency-cycle | CORE 本片验证与 ENTRY 产品验证分离，不能等未来产物 | 规格/交接及单独输入澄清复测 |

冻结摘要：

| 对象 | SHA256 |
| --- | --- |
| 原六案例 cases.json | `697b75e5f70f8d9ee23d5a2a29cb118b50114036d13ce558abf032f5048d5521` |
| oracle.json | `83ac634da51ee4b98cc75862656051c158bc64e9861f4b1190de842ac40352c9` |
| 基线方法 | `136eb60c013ef02918d8303bcf413ec2d726c766d0df60a70b971b2c63c5b3df` |
| R4 初次精简方法 | `2000e2c3fa429c2d4afd8b86d555dd264916a2a2a09bb9f71a886c4a600e7eb2` |
| R5 最终方法 | `dc1c39a04c17fc12a1c01eb5c005ddbd881452089a3bfbe25ce60eb900fc7a00` |

每份 Spec、提示、轨迹摘要及 usage 在原始 manifest。归档位置为忽略的
`reports/spec206-next/raw/`，子目录保留下表的原名。版本库只提交公开合成输入、复现工具和结果摘要；
完整本地报告不是发布给所有克隆者的可下载数据集。

## 4. 全部尝试及失败分类

| 原目录（均以 /tmp/ 开始） | 结果 | 纳入效果比较 |
| --- | --- | --- |
| zf-spec206-eval-20261110 | Docker HOME 与凭据挂载目录不一致，连接失败，停止本轮进程 | 否；保留失败轨迹 |
| zf-spec206-eval-r2-20261110 | 修正 CODEX_HOME 后仍遇 workspace routing discovery failed；12 个作者调用均失败，无交接 | 否；外部执行环境失败 |
| zf-spec206-eval-r3-20261110 | 宿主可连接，但文件工具遇 bwrap 网络命名空间错误；12 份启动轨迹、10 份结果记录；输出的“无法读取”可能满足 JSON 形状 | 否；格式完成不能充当有效交接 |
| zf-spec206-eval-r4-20261110 | 六类 × 基线/候选 × 作者/交接，24 次真实调用 | 是，内部成对试点 |
| zf-spec206-eval-r5-20261110 | 六类 × 修订候选 × 作者/交接，12 次真实调用 | 是，事后修订复测，不能伪装成另一轮成对实验 |
| zf-spec206-clarified-20261110 | 依赖案例补充相同明确命令，两组各生成/交接，共 4 次 | 单列；输入已变化 |
| zf-spec206-calibration-20261110 | 四个手写正反案例，4 次交接评审 | 单列；生成后补做，非预先资格通过 |
| zf-spec206-calibration-corrected-20261110 | 修正不完整的预期正例，1 次 | 单列；仍发现实际缺口 |
| zf-spec206-calibration-complete-20261110 | 再补齐正例义务，1 次 | 单列；最终各来源 covered，无 findings |

可用内容实验共 **46 次调用**：20 次生成、20 次交接及 6 次校准评审；基础设施失败和连接探针另记，
不混入此计数。所有组保留原始回答，不只保留最终最好的一次。没有放宽沙箱；改为送入有权使用的冻结文本，
因此只能验证文本方法交接，不能证明文件检索或容器读取隔离。

## 5. 生成、交接和实现结果

| 指标 | R4 基线 | R4 首次候选 | R5 最终候选 |
| --- | ---: | ---: | ---: |
| 原案例数 | 6 | 6 | 6 |
| 接手者来源判断 | 17 covered / 1 partial | 17 / 1 | 17 / 1 |
| 原始 questions 条数 | 12 | 14 | 14 |
| 原始 findings 条数 | 5 | 7 | 3 |
| 出现 method.md 来源问题的案例数 | 3 | 4 | 0 |
| 两个接口的独立实现验证 | 2/2 | 2/2 | 2/2 |

来源判断是模型对每例 R1–R3 的评审，不是对四项 oracle 预期逐条计算的独立准确率。
questions/findings 的组织粒度不同，也包含合理未知，不能直接把它们当缺陷率或澄清率。
本轮没有统一的人类标注全集，不能给出语义 precision/recall、零漏报或返工率。

共同的 partial 在依赖案例：要求明确验证责任却未给出实际命令。Spec 如实写待绑定并拆开阶段，没有重新制造循环；
不能为了提高通过率要求作者伪造命令。后续冻结 `clarification.json`，给两组同样的计划命令及责任，
两组 R1–R4 都 covered、无 findings。该结果是输入澄清后的新实验，不替换原始 17/18。

最终候选另外两条 finding 来自迁移：原版本识别、恢复/写保护等细节未给全。
文本保留未知和关闭义务，不能用这些合成要求冒充可直接交付的完整迁移项目。

实现验证共 **6 份通过**，覆盖保存失败/重试、成功状态、原对象不变、所有者授权及忽略 ownerId 修改。
范围仅两个小接口。接手者同时获得原始要求，因而通过不能独立归因于 Spec 的充分性；
跨任务装配、真实数据库迁移、Channel Group、YAML v5 编排和产品 E2E 不在本实验范围。

### 校准中的真实错误

两个负例分别把未来同步扩大为本期阻塞项、把 CORE 完成依赖于 ENTRY 的未来验证，均被识别。
依赖正例符合要求，无错误冲突报告。最初标为正例的未来决定文本实际遗漏“登记与 Task Spec 一致”，
首次修订又未明确决定内容及不得擅自选择方案，评审正确指出缺口；这不是评审误报。
补齐后的正例各条 covered、questions/findings 均空。保留这两个失败版本和最终修订。
因此只有两种最终合法正例、两种已知负例的校准证据，没有足够样本估算普遍误报率。

## 6. 精简与成本：观察值，不是收益承诺

| 指标（每组六例，含生成及交接） | 基线 | 首次候选 | 最终候选 |
| --- | ---: | ---: | ---: |
| 方法 UTF-8 字节 | 8646 | 6323 | 6763 |
| 作者产物字符总数 | 21936 | 23004 | 19420 |
| input_tokens | 202890 | 201335 | 199701 |
| 其中 cached_input_tokens | 147456 | 147456 | 147456 |
| output_tokens | 18873 | 20296 | 17719 |
| 每次调用 seconds 之和 | 599.609 | 587.399 | 569.678 |

首次压缩方法后产物字符和 output_tokens 反而增加。最终方法比基线少 21.8% 字节；本次产物字符少 11.5%，
output_tokens 少 6.1%，input+output 合计少 2.0%。每次调用耗时之和少 5.0%，不等于用户等待时间。
没有可靠计费价格，不报告美元成本；缓存命中、提示底座、网络及采样都影响结果。

R5 是看过 R4 结果后的方法修订；启动时 R4 尾部调用仍在运行，没有随机多次重复或统计区间。
这些观察足以支持保留更紧凑的方法和继续收集证据，尚不能宣称整个造父流程节省 6% token，
也不能证明优于外部工具。没有删减必要角色或授权/验证步骤，轻量程度评分仍为 2/5。

## 7. 公开方案复评

外部来源、固定 SHA、七维分数理由和公式见 [206 第 3/5 节](../design/206-spec-method-borrowings-and-quality-evaluation.md)。
本轮重新核对官方资料，外部三个产品未原生运行；仍是 D/S 能力证据。

| 对象 | 多 Agent 交接 A /100 | 轻量迭代 B /100 | 本次解释 |
| --- | ---: | ---: | --- |
| Spec Kit | 64.4 | 67.0 | 保留跨产物一致性与故事切片优势；未测实际效果 |
| Kiro | 70.0 | 69.0 | 要求分析及性质验证支持；PBT 文档范围为 IDE |
| OpenSpec | 68.9 | 74.0 | 变更与当前规格组织集中；仍值得借鉴 |
| 造父阶段 A | 73.3 | 65.0 | 前一批记录，作为历史比较 |
| 造父本批 | **75.6** | **69.0** | 阅读支持 3→4，其余不因小样本实验加分 |

造父当前向量 `[4,4,4,4,3,4,2]`。A 权重 `[15,15,15,20,10,10,5]` 合计 90，
B `[15,10,10,10,15,20,20]` 合计 100；总分 `20 × Σ(分值×权重)/Σ权重`。
新证据增强了对行为/交接方法的信心，但同模型六类一次试点不足以升级到 5 分。

| 方法来源 | 适配分 /5 | 已借鉴内容 | 扣分及后续证据 |
| --- | ---: | --- | --- |
| NASA | 4 | 单一义务、清晰条件、来源及验证双向追溯；失败不变项 | 尚无多领域真实产品/独立读者的重复质量结果 |
| Shape Up | 4 | 按可观察成果组织集成片；避免当前完成等待未来任务 | 依赖案例有正反校准，仍缺真实复杂项目的周期及返工数据 |
| Cucumber | 4 | 规则、具体例子、未知问题分开；正反例可证伪 | 尚未证明所有业务规则都能被独立验证者充分覆盖 |

以上方法分只评各自专长，不能与工具总分混算。对造父最直接的下一步借鉴仍是 OpenSpec 的集中变化审阅、
Spec Kit 的最终跨产物检查、Kiro 的适用性质反例、NASA 的义务可追溯和 Shape Up 的真实装配反馈。

## 8. 验证、完成范围和后续资格

本批自动验证覆盖方法实际分发、评估输入隔离/失败分类、原始轨迹绑定、容器验证边界、阅读模型与浏览器入口。
Web 完成 typecheck、单测、生产 build 及 Docker Chromium 1280/375 回归。最终聚焦 Python 101 passed、2 skipped，Docker 浏览器已有 19 项及新增证据比较 1 项通过；manual-docs check 通过。命令记录在各归档任务及本地日志，
不把 focused verification 表述为全量 pytest、外部产品比较或真实 Provider v5 E2E。

完成的是本次授权的工程改进、内部六类试点和结果复评。以下尚未取得资格证据，不能写为已经解决：

- 外部工具原生同输入运行，且模型/预算/权限可比；当前保持 D/S 评分。
- 多领域真实需求、未参与写作的人类接手者、跨模型及多次重复，测实际理解时间和返工。
- 经完整 YAML v5/Channel 流程检验方法效果，统计角色、工具、缓存和审批成本。
- 复杂迁移与真实装配实现，验证间接消费者、保留行为和旧证据重审全链路。

这些是扩大结论范围所需的下一阶段实验，不阻塞本批已复现问题的修复，也不授权新增复杂运行机制。


## 紧凑中文交接续批（2026-11-11）

基线 dev@3ffacda5；此次批准目标是小产品从中文 Plan 到独立实施验收。
主规划方法沿用需求语言，把 PRD/SDD/TDD 作为语义覆盖，不再要求重复的固定章节。
共享条款集中一次；Task Spec 内写本片准备、恢复、失败不变项和验证交接，完整追溯
保留在绑定矩阵。Critic 核对执行身份与实际准备能力，文字说明不代替能力闭合。

完整 Plan Preview 默认审批概览；旧摘要仍默认原文。模块视图列出未按任务分区的
同版章节，可展开或跳转；不猜测相邻 TDD 属于某任务，不修改已接纳正文或下载。
真实新样本使用“权威、范围与当前状态 / 审批入口”和“PRD 用户结果”标题，首次
概览因别名未覆盖而缺少正文；补齐仅用于展示的映射。对同一 Plan ID 与正文摘要
核对修复前后桌面/手机预览，原文不变；最终 Docker 阅读回归 34 项通过。

验证复用 `tests/e2e/prd_ui/run.py`：`--requirement-file` 冻结两入口共同产品需求，
`skill-source.json` 记录候选 Skill 文件摘要；还须核对实际角色分发与工具读取。
`--delivery` 仅真实 Provider 可用：先保存预览证据并完成独立 preview audit，操作者
审阅后写入与 plan_id/正文 sha256 相同的 `execute-approved.json`，浏览器才点击
原有 Approve。该本地文件是测试协调信号，不能替代 Web 审批、Kernel 准入或产品验收。
产品原始样本见 `tests/fixtures/spec_handoff_eval/readme-cli-product.md`；流程身份、
角色调用、审批序列在测试协议验证，不加入业务 AC。旧样本的治理 AC 不作删除。

本批是工程改进和真实验证实验，不把负例隐藏为成功。两入口均从冻结产品输入重新
生成，实际 Planner/Critic 分发的相关 Skill 文件均与冻结摘要一致（各 11/11），并有
真实工具读取 task-spec-slices.md 的事件；不是仅核对源码存在。

Kanban 首轮丢失独立 Plan 审批约束，恢复经过 Web 单字段配置审阅后唯一 invoke；
首版 Plan 被 Critic 指出 Python 3.11 准备及结构化决定不闭合，返工后经独立 preview
审计与 Web Approve（evt-30444d8ca5aa）进入实施。交付 commit 9251f840，独立 Task
Verify 通过；候选 2283f716 冻结后，Candidate Verify（evt-34467a7d6c0c）8/8 条款、
3/3 旅程通过；外部原始需求 oracle 5/5 通过，三文件摘要与审阅实现一致。独立 Verify
复跑 Python 3.11.14 下 6 项 unittest 和 6 项 CLI 探针。最终候选验证前曾遇浏览器总
时限中断，同一 Run 重启后由 Kernel 自动恢复；没有重新批准或借用旧候选证据。

Channel 初轮 Requirement 丢失审批时序导致引用不绑定，恢复通过 Web 补回原约束。
后续三轮语义 Critic 通过，但工件包依次因缺少 body/ref+sha、绝对路径、项目相对
路径被当作 state_dir sidecar 引用而拒绝。两次自动返工耗尽后，run.goal.blocked
（evt-686ca8e98e8c）收口，Web 实际显示阻塞、实施未开始；没有合格 Plan Preview。
因此不能宣称双入口成功、无人工协助或通用审批约束缺陷已经修复。

共享合同与修订补充试点为 2 案例 × 基线/候选 × 作者/新交接读者，共 8 次真实调用；
均 completed_ungraded，保留 R1–R3 与缺失信息，没有假造执行结果。候选作者正文
8996 字符对基线 8051，输出 token 7477 对 7127，未证明稳定减重。这不是完整 v5
A/B、复杂迁移实施或外部工具原生对照。

本轮维持既有文档能力评分 75.6/69.0、轻量维度 2/5；旧 dev 实跑 73.3/65.0 是不同
证据口径，不能通过切换口径宣称提升。Spec Kit/Kiro/OpenSpec 及 NASA/Shape Up
方法评分沿用第 7 节固定来源，不新增外部性能结论。工程回归：Python 聚焦
90 passed、1 skipped（可选配置样本缺失），Web 类型检查/单测、生产 build/bundle
门禁、Docker 双视口阅读 34 项通过；非全量 pytest。

后续优先级：保留审批约束到 Requirement；在耗时 Critic 前验证完整工件包的来源/
路径绑定；按消费者核对环境准备；在证明依赖后减少重复上下文。实测最终对账
23 段全量、22 必读，返工 Critic 17 段全量且 8 段已被 shadow 识别未变，说明仅改
可见 Markdown 不能解决全链路成本。详细协议、失败/恢复、截图及成本放在忽略的
reports/spec206-delivery-20261111/evaluation.md；原始隔离证据归档在 /tmp/zf-spec206-*。

最终 Judge `evt-09ab8f7a7079` 通过，Kanban `run.goal.completed evt-2a630c4f2678`
于 02:15:34 UTC 收口；独立终态审计同时核对候选、两级 Verify 和 oracle。invoke 到
终态墙钟 Kanban 85.56 分钟、Channel 52.67 分钟，包含返工/审批/恢复且不含前置
讨论；实际 artifact read 调用分别 288/227 次。成本按 Provider 序列最后累计值去重，
缓存、非缓存、输出及 Headless 分列，未换算账单。Judge 仍收到 38 段、30 必读，
后续不能仅凭“thin”命名推断上下文成本。三项后续候选保留在本地 backlogs/，未批准、
未实施；本批完成评估不代表 Channel 阻塞已解决。


## 第二批真实生成问题的修正（2026-11-11）

Task 唯一合同增加来源声明的 `constraints`，创建、更新、持久化及 Task→Workflow Requirement
均保留；没有条件时不补造审批。历史空输入摘要保持原形，来源直引/配置审批保护继续生效。
条件不是权限，最终 YAML 仍由来源绑定的语义选择与受控装配产生。

Preview 兼容真实决定中的 id/decision/source_ref/latest_use_point/closure_evidence，
保留作者的未知状态与待履行义务。Shared Spec 带说明标题仍可定位；源文不重写。
方法要求实际序列化后的模块正文分区、优先级/mandatory 对账、具体 RED/GREEN 和义务关闭点。
校准反例见 [最终包校准](../../tests/fixtures/spec_handoff_eval/calibration-final-package.md)，
多任务输入见 [共享只读产品](../../tests/fixtures/spec_handoff_eval/shared-readme-product.md)。

任意 Python shell 写入仍采用保守防护；生成工件改用明确路径的 Write/apply_patch，
不因引文包含项目路径而放宽范围或例外。长实测分开记录主体、退出、只读审计及录制完整性；
真实长旅程默认截图和事件取证，不持续录制。主体完成不覆盖测试退出失败，ZIP 截断算基础设施失败。

本批实际结果与成本数据记录在 gitignored `reports/spec206-optimization-20261111/`。
方法交付、真实生成质量和实施后产品验证分别报告，Plan Preview 验证不授权实施。

后续兼容补修：Channel 的扁平 `submit_payload.constraints` 映射到同一个 Task contract，
只接受字符串数组；来源 quote 校验不变。空 constraints 在 Task 权威、Workflow 绑定、
Capsule 和执行策略的身份摘要中按历史缺省表示省略，避免升级使未改变的请求失效；
非空条件仍参与摘要，修改条件必须使旧绑定失效。

### 真实候选阅读补齐（2026-11-11）

多任务真实候选没有独立人读概览，且开篇标题为“权威、来源和授权”。Preview 在已声明概览与已识别章节均缺失时，明确标注后引用原文首个二级章节及其子节；不把任务规格或共享合同当成概览，不推断批准结论。决定卡兼容 `proposed_resolution` 展示，保留原始状态；“共享 Task Spec”作为作者明确共享章节展示。真实候选的语义缺陷与 Critic 拒绝仍保留，不因阅读视图完整而转成验收通过。

真实 Kanban 另暴露审计器把 Critic 逻辑输出路径当成最终物理文件的问题。审计现校验同 Run/fanout 的明确来源引用、Critic 合同摘要和文档摘要；不扫描目录猜文件，也不只信任 Preview 返回的文本。首轮审计失败保留，补充只读审计单列。

### 本批真实复验结论

`2362995c` → `f872d575` 的真实复验确认两入口的来源条件进入 Task/Requirement，首次 Workflow YAML 保留 plan_approval=true；Channel首轮遗漏flat入口的失败保留。Kanban一次额外PRD终点UI协助、一次Critic返工后到达pending Preview，浏览器主体通过；原审计路径错误经 `d032afa5` 补充摘要绑定审计通过，不覆盖原failed状态。

多任务候选被独立Critic指出首片矩阵混入后继输出及README时点冲突，停在Owner决策。修复后Channel则由Planner以10项来源AC/每Task上限8为由abstain，运行归为reader_execution_failure并升级人工；没有实际Critic派发和最终Preview。配置上限确实存在，但不能未经独立审查就认定保留来源映射无法合理归组。

因此本批交付已完成且实测有效暴露新问题，完整双入口不是全绿。后续优先处理规划粒度与策略的语义协调、失败分类/恢复、稳定的人读产物，以及实际必读上下文的重复成本。单次PRD长度变化不足以证明性能提升；本轮没有覆盖表单/数据库/外部副作用产品。

## 规划阻塞草案审查与复验（2026-11-11）

上一轮 Channel 的 10 项来源 AC 被误认为必须对应 10 项 Task AC，与单任务上限 8 冲突。
方法修正位于 `zf-plan-task-map-contract`：同类行为可归组，全部来源编号与原子矩阵仍分别保留；
不提升上限、不丢 mandatory/P1。多任务验收只消费本片和前置片，文档时点以原文为准，
全图文件独占继续生效。

`plan_candidate_owner_review.py` 为 `candidate_kind: owner_review` 提供机械预检，经实际
`validate_plan_candidate_semantic` 提交入口和 `PlanSynthRuntimeMixin` 派发入口调用。
要求 execution_status=completed、artifact_production、abstain/blocked、具名阻塞问题，
以及 Markdown 草案/规范来源索引的实际引用与 SHA-256。缺失、篡改及声明执行失败均拒绝。
这里的 passed 只表示草案可交独立 Critic，不表示 execution_ready；完整 Task Map/工件准入、
受控 Owner 检查点和人工 Plan 审批没有被绕过。Reader 的 artifact_production 成功表示工件
生产完成；subject_verdict 保留 abstained/blocked，独立 Critic 决定返工或提出 Owner 问题。
真实执行失败仍按 reader_execution_failure 处理。

当 OA 签发的 `candidate_diagnosis_only` 选择零个 Writer、现有答复足以完成诊断时，
Planner 在同一提交入口交付 `candidate_kind=diagnosis_review`。内核核对请求身份、冻结目标、
OA 决定、每个原 finding 的 Reader 工件摘要、原样保留的 `operator_feedback` 及诊断正文和索引。
每项 finding 由 Planner 提交独立判断、理由和对应原证据，证据不足可以记为 `unknown`。
成功预检返回 `execution_ready=false`，形成供独立 Critic 阅读的子结果。

对于已准入的单份冻结诊断，如果独立 Critic 在构造交接输入时发生机械异常，Kernel 核对原
Planner 的签发、结算、结果工件、冻结身份，并确认该 fanout 尚未签发 Critic。阶段失败标记为
`failure_scope=diagnosis_critic_handoff`，保留 Planner admission、结果引用与原请求摘要。
Run Manager 的待办以该次失败为来源，要求修复交接后恢复独立评审。该故障的语义返工消耗为零，
通用阶段重试不会重新派发 Planner。

测试脚本按可见 PRD 保存选项识别终点，并保留选择证据；Other 后等待新回复以处理重新生成
的问题。只对预先授权的单项 OWNER-GIT-01 答复，未知决定留证停止。当前 Run 的 human.escalate
等明确终止事件使等待及时失败，普通 prd.plan.failed 可继续既有返工；历史/其他 Run 不混入。
本轮原始验证和逐条质量结论记录在 `reports/spec206-recovery-20261111/`，完成前不宣称实测通过。

### 恢复批次的首轮真实发现与方法补接

55af3551 的真实 Channel 已做到 11 个来源 AC → 单 Task 8 项验收，SC-01～11 各自保留，
实际配置上限仍为8、plan_approval仍启用；两个Scan和真实独立Critic已调用。首轮Critic发现
独立Verify证据指向Impl自检文档的真实冲突，Planner返工后通过未来派发受控报告绑定解决。

但Critic连续两轮声称V-03的JSON转义导致SyntaxError。对两轮dispatch的输入清单所绑定
工件核验摘要后，实际JSON解码值经shlex和Python compile均通过，前后命令字面串也未变。
因此该finding属于显示转义误读，Planner“已修复”声明也不成立。测试在第二次误判后由操作者
停止（browser exit 137），保留失败及两版候选，不能记作链路通过或Provider崩溃。

后续补接：Kanban独立PRD提示采用同样的来源粒度/文档时点指导；owner_review对非列表的
options/evidence_refs返回结构化诊断；执行就绪Skill要求对语法问题验证绑定工件实际值，
返工允许带证据反驳不复现finding，并明确独立证据生产者与入口。201项聚焦测试、53项sentinel
通过，随后以新冻结版本重新执行真实验证；原轮证据不覆盖。详见本批reports目录。

### 真实双入口通过与多任务来源修正

98073bd6 的真实 Channel、Kanban 小 PRD 均通过独立 Planner/Critic 和 Web Preview：
Channel 为13来源AC→7任务AC，Kanban为8来源AC/9场景；1280/375阅读、刷新、来源与下载
检查以及运行审计通过，均停在pending人工审批，未实施产品。Kanban Critic曾怀疑命令转义，
但实际解码和语法复现排除误报后通过，没有再次要求无效返工。

同版多任务的PRD阶段仍将“最终README及每片用法”强化为“每片在README交付用法”，
并提前手写Plan，错误地用串行顺序解释共享文件写入。保留该轮输入后由操作者停止，未把
手写预览当作v5 Plan通过。补充逐项反查“具名文件×交付时点”的来源方法，未指定的用法
载体和文件分配留给Plan；E2E明确当前只写PRD，原始产品中的Plan目标由后续Workflow生成。
这不修改原始产品输入，也不放宽全图文件独占。83项聚焦、53项sentinel、TypeScript及
journey policy检查通过，继续单独重跑受影响的多任务样本；前两轮保留其实际98073bd6版本。

### 多任务返工与 Provider 弹窗阻塞（66d194a1）

多任务第三轮已修正 PRD 的文件时点：每片用法载体由 Plan 分配，README 只要求最终交付，
没有提前生成手工 Plan。真实 Planner 产出 T-READ、T-SIZE 两个任务，各4项AC；首片写
`docs/usage/read-file.md`，后片独占 `README.md`，全图写入路径不重叠。首片验证不消费
后续 SIZE 产物；后片复验两入口。独立 Critic 首轮正确发现正文四项决定与 Task Map
空登记不一致及一处来源ID笔误，自动返工后两任务内容不变、四项登记和来源ID已修正。

第二轮 Critic 未实际启动：14:14:01 UTC 投递时，Provider 的“Approaching rate limits”
选择器接收了 Enter；transcript 在同一时刻记录模型改为 `gpt-6-luna`，没有新 task_started，
而账本仍记录 task.attempt.sent。Planner 窗口保留同型换模型弹窗，Provider 使用记录为95%。
操作者保留证据后停止失效样本：browser137、body incomplete、audit not_run。
这是投递与交互状态问题，不能算模型推理慢，也不是已触发硬配额拒绝的证据。

针对已复现形态，在实际 TmuxTransport 发送入口检查当前可见的 Codex 换模型选择器，
发现时不发送任何输入、不选择模型；TaskAttempt 记录 transport_interaction、不可自动重试、
recovery_owner=human。普通低用量提示和历史选择器文本不触发。此保护是窄范围修正，
不承诺识别所有 Provider 弹窗，也未解决检查后才出现弹窗的竞态。

完成边界：Channel/Kanban 小例的真实通过来自98073bd6；多任务候选来自66d194a1，
只完成静态/语义取证及首轮评审返工，最终审批与两模块 Web Preview 未验证。需要在
Provider 用量恢复或操作者明确处理选择器、保持约定模型后，用最新提交重新执行多任务
真实样本。不得以重放候选、静态解析或 fake E2E 代替这项真实通过。任务继续 active。

最终弹窗补修验证：modal-tests.log 103 passed；modal-closure-tests.log 41 passed/1 deselected；
modal-sentinel.log 53 passed；modal-flow-smoke.log 10 passed。计数存在覆盖交叉，不相加。
dev-verify 对该补丁选择277测试文件并提升全量；未执行全量pytest/serial，
modal-verify-receipt.json明确记录skip，以上述传输、实际dispatch、TaskAttempt、恢复、
fanout、v5启动及mock验证闭包替代。没有在新增补丁后重新运行真实Provider。
多任务临时project/workspace已归档、摘要验证并清理；8003已释放，8001未操作。

### 剩余多任务真实复验通过（2026-11-13 UTC）

本节更新前述“任务继续active”的历史结论。b1f79651 上的真实 Kanban 多任务 v5链路
已完成：两Scan→Planner→独立Critic首轮approve→pending Plan，body/browser/audit均passed。
T-READ为7AC、T-SIZE为8AC，16原子场景全部保留，另列双入口复验；写入路径无重叠，
首片独立用法、后片最终README，P0/P1 mandatory保留。8554字符完整正文与评审候选摘要
一致；来源、刷新、下载通过，额外只读Docker检查两模块在1280/375下均无正文横向溢出。
没有批准Plan或实施产品，补验没有API写入。

本次先复现测试措辞误拦，b1f79651将首轮回复检查改为有效状态、同turn绑定及非空，
保留后续真实PRD/Plan检查，随后独立重跑通过。源码保护仍含a531f0fe。报告及截图见
`reports/spec206-retest-20261112/evaluation.md`；原始失败和后续成功分别留存。
本轮约29.5分钟，Planner约10.5、Critic约11.1分钟，未解决性能偏重。

真实候选的READ AC同时引用最终命令，但实际verification_commands阶段筛选和
verification_result_template将其从首片验收排除；已保存绑定候选函数重放证明。
这不代表产品命令已执行。正文仍有未包代码的tests/__init__.py被Markdown解释为强调
的可读性瑕疵，原始工件及结构化路径正确；决策状态文案也可更直接，作为非阻塞改进保留。
本轮Critic一次通过，没有真实返工或配额弹窗，不能宣称覆盖了故障注入场景。
Channel/Kanban小例保持98073bd6历史通过归属，不伪称三条路径同HEAD全验证。


## 阅读、修订与反例续批（2026-11-13）

用户批准两批共八项优化，实施任务见 tasks/active/2026-11-13-0139-spec206-review-batch1.md
及 tasks/active/2026-11-13-0139-spec206-validation-batch2.md。Plan 审批概览增加原始任务依赖与
验收入口；命令按已有 owner/producer 声明标注本片、其他任务、候选及人工责任，不替代
运行时阶段选择。决定显示原始 status 及中文解释，pending_validation 不推断授权；
blocking 继续只读取显式布尔值。变更视图引用同版作者章节，缺失不推断无变更。

最终包对账集中在 readiness 的 final-package-review.md，PRD Skill 引用同一方法；
返工复用必须保留候选身份、受影响消费者和强制读取义务。没有新增自动业务质量 gate。
只读 CLI 双入口独立 oracle 采用冻结需求及随机 seed 206，覆盖字节/大小/错误/只读，
以正确实现与单缺陷变体校准。真实 Kanban/Channel 结果、失败与成本记录将放在
忽略目录 reports/spec206-batches-20261113/，未运行不记通过；真人理解时间另列未测。

### 两批实施、真实交付评测与补修

本轮首轮实现为 `ea894e5d`，两个真实入口冻结相同提交、同一共享 CLI 需求、
生产 v5 配置及 `gpt-6.1-sol`。Kanban 经 Plan UI 审批、T-READ/T-SIZE 实施、Task Verify、
候选 Verify、交付后复核和最终 Judge 后，于 2026-11-13 03:59:29 UTC 达到 Kernel
`run.goal.completed`。最终候选 `bd3c392e7fe7b3c0233fd7c95b6e67b304b937e2` 在干净环境
通过30项生成测试及22项独立黑盒场景；补强后的用法判据也在同提交归档上通过Python3.11复验。

Channel 首轮未一次通过：环境准备返工后仍有三任务/空决定登记；经真实 Web Reject，
改为两任务和完整6条登记。后续 Critic 将JSON显示转义误认为实际反斜线；精确字段、
参数解析和静态语法反证表明误报，前后实际命令相同。独立接手读者又发现唯一文档生产者
可选、后继强制消费的矛盾，再次经 Web Reject。第五版将首片文档生产与后继实际执行
分开、撤回误报，并获独立Critic批准；Web批准为 `evt-1826e81bdcfc`，Plan为
`evt-db09269fe9cf`。原浏览器脚本只支持一次审批，停在被拒版本；原run保留137/incomplete，
在无活跃Workflow操作时停止，再从同状态、同冻结版本恢复操作员续测，单独记录结果。

Channel 最终候选 `450d62274c73d51e5b6607d429be71d5d59c74ab` 在干净克隆、Python3.11
通过22项独立黑盒、21项生成测试及README两条实际调用，Git及所有受控文件摘要保持不变。
候选Verify通过14条AC且无收据复用；Discovery无阻塞缺口；Judge闭合全部28项强制目标，
Kernel于06:00:47 UTC发出 `run.goal.completed`。操作员续测状态为completed，原始run仍为failed；
不能称为首次全自动通过。T-SIZE任务Verify曾复用三条声明命令收据，另有新12调用独立探针，
这与最终候选Verify及外部干净克隆检查分列。

这些通过不抹掉质量缺口：Kanban正文4条决定却空机器登记，READ AC包含后继/最终义务，
原Verify仍签出通过；干净候选README另有未交付来源文档链接。Channel曾强化原文档时点、
改变明确任务数、漏写修订导读并产生误报返工。自由中文决定状态在Web按未知原文展示，
没有推断授权，但状态词汇一致性仍未达到。不能把产品行为通过等同完整Spec质量通过。

补修继续沿用已有边界：Web即使遇到空机器登记仍可查看同版决定原文；Skill先比较完整
决定ID集合，再对账状态，单独检查原始范围/任务数/交付时点及实际AC。唯一生产者必须
承担后继必需内容；可提前生产带阶段说明的用法，不能提前运行未来入口。Verify要求
逐条对照AC原句与实际证据，并检查干净候选可交付资源。语法问题必须还原实际字段，
不能从工具content层的转义外观制造finding。所有重提交都需同版变更导读。

引用化只使用现有六类 `<name>_ref` 与 artifact_refs，不省略完整语义和强制输入。
六项测试覆盖作者目录移除后的预检读取、Critic交接、最终包摘要和缺失文件拒绝。
校准包含14种正反规格片段；产品oracle包含中文/英文正例及五种单缺陷反例，共7项测试。
短文本校准、真实工件重评、Web回放和完整真实Workflow属于不同证据层级，分别记录版本；
不把补修方法的局部通过称为最终补修提交已重跑完整双入口Workflow。

性能仍未解决。Channel五轮Planner合计约45分钟、Critic约66分钟，包含人工返工和误报；
方法新增检查使总字节增长。既有引用的离线体积下降约37%，不是实际token/时延下降37%。
尚无真人理解时间、多领域真实产品样本或稳定成本A/B。完整事件、截图、绑定摘要、失败与
恢复证据见忽略目录 `reports/spec206-batches-20261113/evaluation.md`。

最终补修验证：`dev-verify` 按两批明确路径与6个直接测试模块执行，Python **77 passed、1 skipped**；Web typecheck、完整Web unit、`npm run build` 通过；Docker `plan-human-review.spec.ts` **14/14**通过。未运行全量后端pytest。

## 2026-11-16：真实模块阅读与机械准入修复

`dev@1cd237bb` 的真实 Kanban/Channel 评测发现：两条流程均抵达 Plan Preview，
但四个模块都退回任务描述；Kanban 的状态目录相对 source_index 被按项目根读取，
在 Critic 批准后误拒，又消耗一轮 Planner/Critic。固定回归和原文渲染通过不能替代
最终模块正文消费验证。

修复边界：来源索引 IO 复用 `resolve_artifact_file`，遵守配置 state_dir 及既有别名；
Planner 候选预检复用 strict/release 的来源锚点门，在 Critic 前返回缺口，baseline
保留既有降级语义。声明 `task-scoped-plan-input.v1` 的端口描述符必须与实际正文
schema 一致，避免缺 schema 被静默按旧整份输入消费；未声明的旧格式仍兼容。
这是机械一致性检查，不判断业务覆盖或引入另一套 Task 合同。

新完整 Plan 的逐任务阅读入口由主 task-map skill 明确：可选 execution_spec 的
`tasks[].body.human_review.module_spec_markdown`，或同版 Markdown 的精确二级
`## Task Spec: <task_id>`。共享行为及执行规则要有可解析的同版入口；决定关联使用
`affected_task_ids` 或任务 decision_ids。Planner/Critic 对最终文件独立核对入口、
实际开头与场景摘录；摘要、方法加载、预检通过均不等同语义批准。

真实 UI 旅程现在逐个检查接纳 Task Map 中的模块，保留正文和截图，并拒绝描述回退。
fixture 用例仍只是 mock；真实生成结果单列。历史已接纳产物不直接改写，修复须经
新候选和正常评审。成本目标仍需合格增量样本、同条件对照及重复测量，不能把合法
回退全量或机械重试减少直接报成已达 30%/50%。

真实 Kanban 首轮仍产生无冒号的 `## Task Spec T-SAVE`，并被 Critic 接纳；
浏览器新增断言如实拒绝其描述回退。阅读器因此兼容英文冒号、中文冒号及空格
分隔的显式 Task Spec 标题，仍要求完整 Task ID、二级 AST 标题和唯一匹配。
跨别名重复、未知 ID、带附加标题文字和代码块中的伪标题不获绑定。只引用作者
原文，不改写已接纳工件、推断正文归属或补造规格；推荐生产格式仍为英文冒号。
同一失败工件在修复后补做浏览器验证，原失败记录与生成版本独立保留。


## 2026-11-17 按需 Critic：证据闭环与显式选择

本批从 dev 54e4ecf8 开始。两批任务：先保留评审证据与 AC 来源，再复用现有受控
提案实现按需调用。职责选择属于 Agent/Owner；Kernel 不根据业务关键词、任务数
或自评分判定风险，也不增加一个选审查者的 Agent。

### 可用入口

PrdFlow.spec 支持显式 `planReview`（别名 `plan_review`）：

```yaml
planReview:
  mode: targeted  # none / targeted / full
  reason: 本次只调整共享失败规则，需要核对两个入口的影响
  focus:
    - 保存失败后状态和重试是否在两个入口保持一致
    - 本片与后继的验收时点是否正确
```

默认与既有 planCriticEnabled 行为兼容。none 不生成专门 Critic；targeted 保留独立
Critic 并限定关注问题及依赖；显式 full 不启用局部选读。targeted 没有已批准基线和
消费证据时仍读取必要完整上下文，不能宣称必然减少 token 或延迟。现有角色的
summary/findings/evidence_refs 记录当前版本、实际检查范围、摘录与未审范围。

Workflow Synthesis 使用 parameters.plan_review：普通 Flow 保留当前 lanes，注册 v5
控制器保留其固定 lanes/pipeline。两条路径均经配置候选、Proposal
批准路径生效，不直接改当前配置；保留 Plan approval、工件准入与下游 Verify。
不能用旧布尔开关静默覆盖已有 planReview。PrdFlow light 无 Plan 阶段，显式拒绝此参数。
Plan preview 从该 Plan 绑定的 run contract 读取方式，不读取实时配置，也不把配置
为 full 或 targeted 显示成评审已通过；历史无记录显示未知。

Channel 显式选择 `prd-clarification-on-demand` 模板，产品与架构成员仍参与，固定
critic 不入初始 roster。默认模板不变。问题优先走现有定向 cross-review；专业能力
缺口通过主席 member_invitations + Owner 受控批准补审，新增必审角色保留在 review_roster
直至同版签收；未邀请角色不会成为幽灵必签成员。

### 证据与来源

Channel 共识评审接受 evidence_refs 的字符串及带 ref 的对象。完整评审原体及绑定
通过原子、不可变 sidecar 保存，事件保留字符串导航和 review_ref/review_digest，
投影暴露评审证据。非法引用显式拒绝，不再过滤后签字；历史签字标 legacy_unrecorded，
不回填旧账本。证据保存不是语义真实性认证。

Web 局部 AC 解析明确 source_acceptance_ids/source_ids，以及 coverage ledger 的
任务 + AC 联合映射；source_id 不唯一、跨任务错配或缺失仍提示未解析，不根据名称猜测。

### 验证边界

确定性检查比较 none / targeted / full 的实际派发、受控提案、审批保持、版本绑定、
选读降级和 Channel 名单收敛；归档真实样本仅用于原故障回放。mock 不证明模型审查
质量、漏检率或端到端提速，真实多样本成本实验必须单独记录，不复用上一轮评分宣称提升。


按需邀请使用项目已有 `channel.agent_profiles` 公共目录，不从角色名编造 provider 或
权限。未配置可用专业 profile 时，主席记录能力缺口并走现有 Owner 决策；不能伪称已
加载 Critic。可在项目 ZfConfig.spec 中配置只读审查 profile，例如：

```yaml
channel:
  agent_profiles:
    prd-critic:
      channel_role: critic
      persona: 核对原始需求、异常反例和交付时点的独立审查者
      provider: codex
      backend: codex
      permission_ceiling: read_only
      skill_refs:
        - skills/zf-channel-discussion-participant/SKILL.md
```

模型和 provider 沿项目已有配置选择；本批不修改真实 dev 的 zf.yaml，不自动重启或
改变正在进行的讨论。
