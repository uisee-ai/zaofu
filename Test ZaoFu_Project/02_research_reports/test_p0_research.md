# ZaoFu 与其产品项目测试：论文与开源仓库调研

> 文档定位（2026-09-30）：历史调研，正文“当前/建议/尚未实现”仅适用于各段所记时点，不是最新执行队列。现行方案见 [ZaoFu_TestKit.md](../03_plans_and_steps/ZaoFu_TestKit.md)，执行仍按既有暂停/跳过边界；本次不重跑或改写历史结果。
>
> 当前对齐入口：`tests/testkit/suites.py`。这是拆分前综合方法来源；当前 ZaoFu 自测与通用产品测试独立计划，模型评估不是主干验收条件。

> 调研日期：2026-09-04  
> 目标：寻找能够支持两条测试线的一手资料：一条测试 ZaoFu 自身的 harness / agent runtime，另一条测试 ZaoFu 生成的产品项目。  
> 证据原则：优先采用论文原文、作者/组织官方 GitHub 仓库和官方文档；以下链接均指向这些来源。

## 结论摘要

没有一个现成项目可以直接覆盖 ZaoFu 的全部需求。更可行的组合是“确定性测试为主、评估资料只作参考”：

1. 用 ZaoFu 自己的确定性 pytest、事件/状态断言作为底座；只借鉴 Inspect AI、OpenAI Evals、AgentBench 和 ToolSandbox 的可复现任务、轨迹、工具与状态断言结构，不把它们的分数当成测试结论。
2. 把产品项目的真实需求固化为 issue/任务 fixture，采用 SWE-bench 风格的“代码快照 + issue + 补丁 + 项目测试”闭环。
3. Web 产品使用 Playwright，再按产品复杂度借鉴 BrowserGym、WebArena 或 OSWorld；工具/API 型产品使用 ToolSandbox 或 τ-bench 的状态和里程碑断言。
4. 质量指标不能只看测试通过率：至少要记录终态成功率、必需证据完整性、错误/重复/过期调度、恢复成功率、时延与成本，以及浏览器/API 失败证据。

## 一、测试 ZaoFu 自身 harness/runtime

### 1. Inspect AI：可借鉴其任务与断言结构的 agent 测试框架

- 来源：[Inspect AI 官方文档](https://inspect.aisi.org.uk/)；[官方仓库](https://github.com/UKGovernmentBEIS/inspect_ai)。
- 解决的问题：把一次运行建模为 `Task`，由带标签的数据集、产生答案的 solver 和判定结果的 scorer 组成；solver 可以是单次模型调用，也可以是多轮、带工具的完整 agent。官方文档还提供 sandbox、并行/限额、日志分析和 checkpointing。原项目定位是通用 evaluation，因此这里只借鉴其测试组织方式。
- 测试对象：工具型 agent、多轮 agent、外部 agent framework 的桥接，以及长时间运行的测试任务。
- 关键方法：`dataset -> solver/agent -> scorer`；把运行日志和 transcript 保留下来；对长测试周期性 checkpoint，以便在进程终止或资源耗尽后恢复。
- 对 ZaoFu 的适配价值：可把 `WorkflowRuntimeCoordinator` 的每个最小场景写成测试 case，把事件序列/终态/sidecar 证据写成断言；把 restart/resume 场景映射到 checkpoint/recovery 测试；通过固定 fixture 和 mock provider 形成可重复 CI 测试。
- 局限：它是通用 LLM 评估框架，不知道 ZaoFu 的 EventLog、TaskStore、generation 或受控动作约束；这些必须由 ZaoFu 自己的测试断言、sandbox 和 fixture 实现。

### 2. OpenAI Evals：可选的回归测试组织方式（原项目是 eval registry）

- 来源：[官方 GitHub 仓库](https://github.com/openai/evals)；[构建自定义 eval 指南](https://github.com/openai/evals/blob/main/docs/build-eval.md)。
- 解决的问题：为模型或包含模型的系统建立可重复运行，并允许使用自己的数据创建私有测试集；官方 registry 同时提供可复用模板。原项目仍以 evaluation 为主。
- 测试对象：LLM 输出、prompt/工具链、包含 LLM 的应用系统。
- 关键方法：用数据集描述输入和目标，选择已有 eval 模板或注册自定义 eval；将结果和指标纳入持续回归。
- 对 ZaoFu 的适配价值：可以为事件 schema、rework/recovery、错误注入、scope/权限拒绝建立小型 deterministic 测试；不同 provider、模型或 prompt 版本的横向比较属于后续评估，不是 P0 测试门禁。
- 局限：框架重点是 LLM/system eval，不会自动验证 ZaoFu 的 canonical state、EventWriter 因果链或浏览器投影；复杂状态机仍需自行编写适配器和断言。

### 3. AgentBench：多环境、多轮 agent 测试结构参考

- 来源：[论文《AgentBench: Evaluating LLMs as Agents》](https://arxiv.org/abs/2308.03688)；[作者官方 GitHub](https://github.com/THUDM/AgentBench)。
- 解决的问题：传统静态问答或短代码题不能覆盖 agent 在交互环境中持续推理、决策和执行动作的路径。论文提供 8 类环境和统一运行包，覆盖多轮开放式任务；其汇总分数属于能力评估。
- 测试对象：LLM-as-Agent 的长期推理、决策、指令遵循和环境交互。
- 关键方法：为每类环境定义任务适配器、交互接口和可计算的任务结果；比较不同 agent/model 的成功率和失败类型。
- 对 ZaoFu 的适配价值：可参考“统一环境接口 + 多轮 episode + 任务结果”的测试结构，将 kernel/runtime、provider transport、Web/API projection 作为不同测试族，分别断言调度、工具使用和端到端终态。
- 局限：任务环境与 ZaoFu 领域不一致；论文的通用分数不能替代事件不变量、证据 gate 或人工审查，且完整基础设施成本较高。

### 4. ToolSandbox：有状态工具调用与中间里程碑验证

- 来源：[Apple 论文页面](https://machinelearning.apple.com/research/toolsandbox-stateful-conversational-llm-benchmark)；[论文](https://arxiv.org/abs/2408.04682)；[官方 GitHub](https://github.com/apple/ToolSandbox)。原项目是 benchmark，但其中的状态快照和里程碑 checker 可直接借鉴为测试组件。
- 解决的问题：单轮、无状态 API 测试无法发现工具之间的隐式依赖、对话中的状态变化和安全拒绝问题。
- 测试对象：多轮用户/agent/tool 对话，工具参数与调用顺序，数据库/世界状态变化，以及信息不足时是否拒绝臆测。
- 关键方法：执行上下文保存完整世界状态、对话历史和每轮 snapshot；用 milestone DAG 表达必须按顺序发生的中间状态；同时比较中间和最终状态。
- 对 ZaoFu 的适配价值：把 EventLog、TaskStore、ControlledActionService 或产品业务 API 映射成有状态工具；用 milestone DAG 断言 `task.created -> dispatch -> evidence -> verify -> terminal`，并测试非法动作、重复动作、依赖缺失和 replay。
- 局限：示例主要是 Python 工具和模拟数据库；接入 ZaoFu 要自行实现工具 schema、状态快照和领域相似度；它不覆盖 Web UI 像素或完整部署拓扑。

### 5. τ-bench：用户、agent、工具和策略的真实交互可靠性

- 来源：[论文](https://arxiv.org/abs/2406.12045)；[Sierra 官方研究页面](https://sierra.ai/resources/research/tau-bench)；[论文代码入口](https://github.com/sierra-research/tau-bench)。
- 解决的问题：很多 agent 测试没有覆盖 agent 与用户的多轮沟通，也没有覆盖领域策略遵循。τ-bench 让模拟用户与 agent 对话，agent 使用领域 API 和策略文档完成任务；其原始定位仍是 benchmark。
- 测试对象：多轮协商、工具调用、业务规则遵循和最终数据库状态。
- 关键方法：将最终数据库状态与标注的目标状态比较。`pass^k` 是多次运行的评估指标，P0 可先采用一次运行的确定性状态断言。
- 对 ZaoFu 的适配价值：适合产品项目中的客服、订单、审批或其它工具流程；可把“用户澄清—受控动作—状态变更—确认”做成可重复 scenario，并测量一次成功率与多次一致性。
- 局限：官方场景偏零售/航空客服；ZaoFu 需要自建业务 API、策略和目标状态，`pass^k` 仍受 scenario 设计影响。

## 二、测试 ZaoFu 生成的产品项目

### 6. SWE-bench：可借鉴的 issue 到补丁再到项目测试闭环

- 来源：[论文《SWE-bench: Can Language Models Resolve Real-World GitHub Issues?》](https://arxiv.org/abs/2310.06770)；[论文全文](https://proceedings.iclr.cc/paper_files/paper/2024/file/edac78c3e300629acfe6cbe9ca88fb84-Paper-Conference.pdf)；[官方 GitHub](https://github.com/SWE-bench/SWE-bench)。
- 解决的问题：短小、自包含的代码题不能代表真实软件工程；真实 issue 往往要理解多个文件、运行环境和已有测试。
- 测试对象：给定代码库快照和 issue，产生补丁；补丁在原项目测试框架中执行。SWE-bench 的整体用途是评估 coding agent，但这个执行闭环适合改造成产品回归测试。
- 关键方法：固定 issue、base commit、环境和测试命令；容器化准备环境；以项目测试和状态断言判定 patch 是否解决问题。官方仓库明确使用 Docker 以获得更可重复的运行。
- 对 ZaoFu 的适配价值：把每个生成产品的 bug/feature 变成固定的 `issue + base commit + expected tests` fixture；在独立 worktree/容器中运行 Fix Run，执行产品 unit/integration/E2E，再由独立 verifier/judge 生成 candidate。
- 局限：原始数据偏 Python 仓库，测试质量和任务可解性决定上限；它不验证 ZaoFu 自身的调度/事件语义，也不能把“补丁通过测试”当成完整产品验收。

### 7. OpenHands Benchmarks：可借鉴的隔离测试运行器

- 来源：[官方 GitHub 仓库](https://github.com/OpenHands/benchmarks)；[官方 Evaluation Harness 文档](https://docs.openhands.dev/openhands/usage/developers/evaluation-harness)。
- 解决的问题：为 coding agent 提供标准化、可复现的真实任务运行管线。仓库目前列出 SWE-Bench、SWE-Bench Pro、GAIA、Commit0、OpenAgentSafety、ProgramBench 等任务，并支持 Docker 或远程 workspace；其排行榜和汇总属于评估层。
- 测试对象：编码 agent 在 issue 修复、长时工程任务、函数实现、程序重建和安全场景中的能力。
- 关键方法：固定 Agent SDK submodule commit；构建 agent/server 镜像；在隔离 workspace 中执行测试 case；保存结果和日志。
- 对 ZaoFu 的适配价值：可以借用其“固定依赖/镜像/测试 runner/结果汇总”结构，为每个 ZaoFu 产品项目定义测试 case；将产品测试命令、事件证据和日志作为结果字段，成本只作辅助记录。
- 局限：默认关注 coding agent；必须自行增加 ZaoFu 事件终态、必需 sidecar、scope 隔离和人工 review gate 的断言。

### 8. Playwright：生成 Web 产品的黑盒 E2E 与失败证据

- 来源：[Microsoft 官方 GitHub](https://github.com/microsoft/playwright)；[官方 Trace Viewer 文档](https://playwright.dev/docs/trace-viewer)。
- 解决的问题：用一个测试 API 对 Chromium、Firefox、WebKit 做隔离的浏览器端到端测试，并在 CI 失败时保存可回放证据。
- 测试对象：产品页面、路由、表单、权限、跨页面流程、API 请求和浏览器可观察行为。
- 关键方法：每个测试独立 BrowserContext；web-first assertions 自动等待；失败或重试时记录 DOM snapshot、网络、console、截图和动作 trace。
- 对 ZaoFu 的适配价值：把产品 PRD 的关键用户旅程编成少量稳定 workflow；将 trace、截图、网络错误和后端请求 ID 与 ZaoFu Run/Task 关联，避免把“页面出现 toast”当成业务成功。
- 局限：只证明浏览器可观察行为；仍需后端状态/API 断言、数据清理和稳定测试账号，不能替代 kernel/EventLog 测试。

### 9. BrowserGym / AgentLab：统一 Web agent 测试任务和追踪分析

- 来源：[BrowserGym 官方 GitHub](https://github.com/ServiceNow/BrowserGym)；[论文《The BrowserGym Ecosystem for Web Agent Research》](https://arxiv.org/abs/2412.05467)；[AgentLab 官方仓库](https://github.com/ServiceNow/AgentLab)。
- 解决的问题：不同 Web 测试环境的观察/动作接口不一致，难以复用 agent 和断言。BrowserGym 用 Gymnasium 风格环境统一 MiniWoB、WebArena、WorkArena 等任务，并允许继承 `AbstractBrowserTask` 定义新任务；AgentLab 提供运行和轨迹分析。原项目的横向排名属于评估用途。
- 测试对象：Web agent 在真实或自托管网站上的长流程操作、视觉观察和动作选择。
- 关键方法：统一 observation/action API；每个 task 自带目标和 evaluator；收集轨迹以分析失败步骤。
- 对 ZaoFu 的适配价值：如果生成的产品有复杂 Web UI，可将注册、CRUD、权限、跨页面等场景包装成自定义 task，和 Playwright 的确定性用例并行运行；将 evaluator 直接检查产品后端状态。
- 局限：浏览器层框架，不覆盖 ZaoFu kernel、Store 或事件 schema；需要编写环境封装，部署浏览器和站点的成本也较高。

### 10. WebArena：自托管真实网站和程序化状态测试

- 来源：[官方 GitHub](https://github.com/web-arena-x/webarena)；[项目页面/论文资料](https://webarena.dev/)；[WebArena-Verified 说明](https://servicenow.github.io/webarena-verified/dev/)。
- 解决的问题：在可复现、自托管的多网站环境中测试长时自然语言任务，而不是只在静态网页或单 API 上测试。
- 测试对象：论坛、CMS、电商、代码托管等网站上的多页面、多标签用户流程。
- 关键方法：Docker 部署站点；任务由自然语言指令驱动；使用答案比较和中间/最终数据库状态 checker 测试功能正确性。WebArena-Verified 进一步复核任务与 checker，减少脆弱字符串匹配和含糊成功标准。
- 对 ZaoFu 的适配价值：可借用“自托管环境 + 可编程状态 checker + 任务 fixture”的模式，为生成的产品建立隔离测试环境和可审计验收器；适合验证权限、数据持久化和跨页面副作用。
- 局限：环境资源和初始化成本大；checker 如果依赖脆弱文本仍会误报；自有产品必须重写站点、数据和 checker。

### 11. OSWorld：真实操作系统/桌面/浏览器的跨应用 E2E 测试参考

- 来源：[论文《OSWorld: Benchmarking Multimodal Agents for Open-Ended Tasks in Real Computer Environments》](https://arxiv.org/abs/2404.07972)；[官方 GitHub](https://github.com/xlang-ai/OSWorld)；[项目页面](https://os-world.github.io/)。
- 解决的问题：只测单个 Web/API 无法覆盖桌面文件、多个应用和跨应用工作流。OSWorld 提供可扩展的真实计算机环境和跨操作系统任务，并支持任务初始化、agent 交互、后处理、信息提取和测试函数。
- 测试对象：需要浏览器、桌面应用、文件系统和多个应用协同的产品工作流。
- 关键方法：通过配置文件初始化测试环境；记录 agent 动作；在完成后提取文件/状态并执行断言函数。
- 对 ZaoFu 的适配价值：当生成产品包含本地客户端、文件导入导出或跨应用流程时，可借鉴其“环境初始化—动作—后处理—断言”的分层，作为高成本 E2E 测试层。
- 局限：资源消耗和环境维护成本高；不适合作为每次提交的默认测试；网络依赖、桌面状态和视觉模型会带来波动。

### 12. Hypothesis：产品和 ZaoFu 状态机的性质测试

- 来源：[官方 GitHub](https://github.com/HypothesisWorks/hypothesis/)；[官方文档](https://hypothesis.readthedocs.io/en/latest/)。
- 解决的问题：手写少量例子容易漏掉边界输入。Hypothesis 根据声明的策略生成大量输入，发现失败后自动缩减为最小反例；还支持 stateful rule-based tests。
- 测试对象：Python 业务逻辑、解析器、配置/事件 schema、状态机和 API 边界。
- 关键方法：声明输入域和永真性质；使用随机生成、失败样例数据库、shrink 和 state machine 模型。
- 对 ZaoFu 的适配价值：验证事件序列幂等性、generation 单调性、非法状态拒绝、Store round-trip、配置边界和 rework cap；也适合验证生成产品的领域规则，而不是只测固定 happy path。
- 局限：性质必须由开发者正确声明；高质量性质测试仍不能替代集成/E2E；随机测试需固定 seed/独立 state 以保证 CI 可复现。

### 13. EvoSuite：自动生成单元测试的研究和工具

- 来源：[EvoSuite 官方网站](https://www.evosuite.org/)；[官方 GitHub](https://github.com/EvoSuite/evosuite)；[自动测试生成论文资料](https://www.evosuite.org/experimental-data/maintenance/)。
- 解决的问题：对 Java 项目自动生成可执行的 JUnit 测试，以降低手写成本并提升覆盖率；官方资料说明其在 SF110（110 个开源项目、约 2.3 万个类）上进行验证。
- 测试对象：Java 类和方法级行为，尤其是难以手写大量输入序列的代码。
- 关键方法：搜索式/遗传算法探索输入和调用序列，生成并执行测试；可与已有测试一起运行。
- 对 ZaoFu 的适配价值：如果生成产品包含 Java 服务，可将 EvoSuite 作为补充的探索层，寻找固定验收用例未覆盖的分支，再把确认后的回归样例人工整理进产品项目。
- 局限：语言和运行时主要面向 Java；高覆盖不等于业务正确；自动生成测试可能难读、难维护，必须经过人工筛选，不能直接作为产品验收标准。

### 14. AppWorld：可控多应用世界和副作用检查

- 来源：[论文《AppWorld: A Controllable World of Apps and People for Benchmarking Interactive Coding Agents》](https://arxiv.org/abs/2407.18901)；[ACL 论文页面](https://aclanthology.org/2024.acl-long.850/)；[官方 GitHub](https://github.com/StonyBrookNLP/appworld)。
- 解决的问题：单个 API 或静态代码题不能测试 agent 在多个应用、用户和依赖状态之间完成真实多步任务的能力。
- 测试对象：跨应用 API 调用、用户/权限、持久化状态和长流程任务。
- 关键方法：提供可控的模拟世界和应用 API；用程序化 state-based tests 同时检查目标状态、未发生意外的 collateral changes，并支持任务 reset/replay。
- 对 ZaoFu 的适配价值：非常适合借鉴为“产品 fixture”：为生成产品准备可重置数据集和跨服务任务，除检查目标记录外，还检查不应被修改的数据，防止 agent/修复引入隐性副作用。
- 局限：模拟世界与真实产品数据库、鉴权和外部服务有差异；迁移时需要为产品 API 编写 adapter 和状态 checker。

### 15. SWT-Bench：自动生成能复现缺陷的回归测试

- 来源：[论文《SWT-Bench》](https://arxiv.org/abs/2406.12952)；[官方 GitHub](https://github.com/logic-star-ai/swt-bench)。
- 解决的问题：修复 agent 可能“看似修好”但没有测试证明缺陷已被复现和回归保护；SWT-Bench 的方法可用于检查生成测试是否真正复现缺陷。
- 测试对象：真实 GitHub issue、修复 patch、golden tests，以及 agent 生成并集成到原测试套件的单元测试。
- 关键方法：要求生成的测试能复现已知 bug、通过测试执行，并衡量 bug reproduction、test pass 和覆盖率。
- 对 ZaoFu 的适配价值：产品项目每发现一个 bug，先要求生成一个最小回归测试，再进入 Fix Run；该测试可作为 `/issue` 的 evidence anchor 和 merge gate，避免修复后问题再次出现。
- 局限：主要面向 Python 单元测试，UI、跨服务和非确定性外部依赖覆盖较弱；生成测试仍要人工审核是否测试了正确行为。

### 16. AgentDojo：不可信输入和 prompt-injection 红队测试

- 来源：[论文《AgentDojo: A Dynamic Environment to Evaluate Prompt Injection Attacks and Defenses for LLM Agents》](https://arxiv.org/abs/2406.13352)；[ETH Zurich 官方 GitHub](https://github.com/ethz-spylab/agentdojo)。
- 解决的问题：工具型 agent 可能被网页、文档或用户内容中的 prompt injection 诱导越权；仅测试正常路径无法发现此类安全缺陷。
- 测试对象：动态、有状态工具环境中的攻击任务、防御策略和正常任务回归。
- 关键方法：在任务和工具环境中注入攻击，分别报告任务成功、攻击成功和防御误报等结果，检查防御是否阻断恶意动作且不破坏正常功能。
- 对 ZaoFu 的适配价值：可把外部 GitHub issue、网页、文档和 provider 输出视为不可信输入，测试 ControlledAction 是否拒绝越权、是否阻止远端写入/伪造终态，同时保证正常 triage/fix 流程可用。
- 局限：现有攻击集合和工具 schema 有限；需要按 ZaoFu 的角色、权限和受控动作扩展场景，不能替代普通功能、性能和恢复测试。

### 14. promptfoo / DeepEval：LLM 产品的质量与安全回归（可选）

- 来源：[promptfoo 官方 GitHub](https://github.com/promptfoo/promptfoo)；[DeepEval 官方 GitHub](https://github.com/confident-ai/deepeval)。
- 解决的问题：对 LLM 应用做声明式 prompt/agent/RAG 回归、模型版本比较和安全红队测试；DeepEval 提供 pytest 风格的 LLM app 测试和指标。
- 测试对象：生成产品中的聊天、RAG、工具调用和 agent API；提示注入、越权、事实性和相关性等质量/安全属性。
- 关键方法：用 YAML/代码声明测试样例和断言，批量运行多个模型/版本；可把结果放入 CI，必要时使用 LLM judge 或规则型 scorer。
- 对 ZaoFu 的适配价值：产品项目如果包含 LLM 功能，可将 PRD acceptance prompts 和攻击样例纳入独立测试层，并把每次模型/提示变更与产品版本关联。
- 局限：judge 型指标存在波动，需人工校准和固定模型；这些工具不能证明 EventLog、TaskStore 或产品数据库事务正确，必须与确定性测试并用。

## 三、建议落地的组合

| 测试层 | 推荐组合 | 主要断言 |
|---|---|---|
| ZaoFu kernel/runtime | pytest + Hypothesis + ToolSandbox 的状态断言思路 | schema、事件因果链、Store、幂等、非法动作拒绝、replay/resume |
| ZaoFu workflow/provider | pytest + mock provider + 自定义状态断言 | 多轮 dispatch、工具/策略遵循、rework/recovery、终态；Provider 横向比较另列为评估 |
| 产品后端/修复闭环 | SWE-bench 风格 fixture + OpenHands Benchmarks 的隔离运行器 | issue、base commit、补丁、项目测试、独立 verify/judge、candidate |
| 产品 Web UI | Playwright；复杂场景借鉴 BrowserGym/WebArena | 用户旅程、权限、后端状态、trace/console/network 证据 |
| 产品桌面/跨应用 | OSWorld 思路 | 环境初始化、跨应用动作、文件/状态后处理和 evaluator |
| LLM 产品功能 | promptfoo/DeepEval（可选） | 质量、安全、工具调用和模型版本回归 |
| 产品跨应用/回归安全 | AppWorld、SWT-Bench、AgentDojo | 目标状态与 collateral change、可复现回归测试、prompt injection/越权 |

建议每个测试 case 都拥有固定的 `case_id`、产品/Zaofu commit、独立 state dir、环境镜像、输入、预期终态、允许的副作用、证据路径和 scorer 版本。测试报告至少分离：

- 任务结果：terminal success / resolution rate、必需 evidence 是否齐全、功能/业务状态是否正确；
- 可靠性：重复运行是否得到同一预期状态、重复 dispatch、stale generation、错误终态和恢复成功率；`pass^k` 作为后续评估指标，不是 P0 测试门禁；
- 运行成本：时延、provider turn 数、token/费用、资源超限和重启恢复时间；
- 可诊断性：事件 ID/Task ID/Run ID、transcript、trace、截图、console/network、日志和数据库 diff。

## 四、与 ZaoFu 当前 `/issue -> merge` 流程的衔接

这些项目的共同启示是：测试失败必须保留“可重放输入 + 固定环境 + 可计算 checker + 完整证据”，而不是只记录一句“测试没通过”。因此：

1. ZaoFu 自身失败：用 `/issue` 记录症状、复现步骤、期望/实际、commit、Run/Task/Event ID 和证据；走 Self-Issue -> 外部 issue mirror -> 只读 triage -> proposal approval -> Fix Run -> independent verify/judge -> candidate -> owner review -> 人工 push/PR/merge。
2. 产品项目失败：在产品项目自己的 source root 和 IssueFlow 中创建 issue/task，使用 SWE-bench 风格 fixture 复现，执行产品 unit/integration/E2E，再进入产品项目的 verify/judge 和人工交付流程；不要把产品源代码错误地映射到 ZaoFu 的 Self-Issue target。
3. 合并前：必须检查测试 case 的 base commit、配置、state dir、镜像和报告是否一致；“页面成功提示”“agent 自称完成”或“单个 Task done”都不能替代后端状态和证据 gate。

## 五、优先级建议

第一阶段先实现 ZaoFu 自身的确定性 L0/L1/L2：pytest/Hypothesis、mock provider、事件/状态 scorer 和独立 state dir；同时为一个最小产品建立 SWE-bench 风格 issue fixture + Playwright E2E。第二阶段再加入 ToolSandbox/τ-bench 式多轮工具状态测试和 Inspect/OpenAI Evals 的回归矩阵。只有确实需要桌面/跨应用能力时，才引入 OSWorld 这类高成本环境。

上述资料中有些项目原本是 benchmark/evaluation，但这里只提取可复用的测试方法：固定环境、可重放输入、状态 checker、隔离 workspace、测试报告和失败轨迹。任何 benchmark 分数都不能直接作为 ZaoFu 发布门槛；发布门槛应由 ZaoFu 的状态不变量、产品测试结果和人工审查规则共同定义。
