# S7：不可信输入 → 危险副作用边界测试调研

> 文档定位（2026-09-30）：历史调研，正文“当前/建议/尚未实现”仅适用于各段所记时点，不是最新执行队列。现行方案见 [ZaoFu_TestKit.md](../03_plans_and_steps/ZaoFu_TestKit.md)，执行仍按既有暂停/跳过边界；本次不重跑或改写历史结果。
>
> 当前对齐入口：`tests/conformance/test_zaofu_testkit_s7_untrusted.py`。已有不可信输入与 TOCTOU 检测入口；真实外部副作用/远端写入不由安全用例授权。

调研日期：2026-09-10。范围：ZaoFu 自身 TestKit 的 S7 安全测试，不测试生成产品、不评价 Agent 能力。本文件依据 OWASP、Python、pytest 官方资料提出测试方法；仓库观察只代表本次读取的文件，不代表完成全仓安全审计或测试已经通过。

## 1. 结论与边界

采用现有 pytest 参数化 + 实际入口调用 + 末端副作用 spy + 隔离文件哨兵 + EventLog/Store 前后比较。暂不需要另加大型安全平台。S7 的核心判据是：**不可信文本不能自行升级为授权；拒绝之后没有未授权调用和状态变化；允许的普通输入仍可正常处理。** 这是本调研针对 ZaoFu 的方案推导，而不是 OWASP 给出的 ZaoFu 现成实现。

不能只断言 HTTP 403、异常或“恶意字符串没有出现在日志里”。必须观察真正的危险边界；否则可能出现“先执行了动作，再返回错误”的假安全。

本阶段禁止真实 Git push、远端引用修改和未授权网络访问。文件攻击载荷只指向测试临时目录中的 sibling sentinel；使用人工合成 token，不使用真实凭据。

## 2. 一手来源与方法依据

### 2.1 输入验证不是关键字过滤

OWASP 将输入检查区分为结构格式和上下文合法性，并要求覆盖外部数据源。它明确指出，仅靠拒绝危险字符不能代替允许值约束，也不能独自承担注入防护。合法文本可能包含引号、分号或 Unicode，不应仅凭这些字符判为非法。[OWASP Input Validation Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Input_Validation_Cheat_Sheet.html)

针对 ZaoFu 的推导：结构化 action、项目 ID、文件 ref 应检查类型和归属；Issue body、Channel 正文可以保留命令样式文本，但不得借此执行命令、获得 token 或直接写规范状态。

### 2.2 命令注入和参数注入要分开验证

Python `subprocess` 默认不会隐式使用 shell；显式 `shell=True` 则需要调用方负责 shell 元字符处理。官方还说明 Windows 批处理文件存在特殊解析边界，因此 Linux 测试通过不能冒充跨平台证明。[Python 3.11 subprocess 安全说明](https://docs.python.org/3.11/library/subprocess.html#security-considerations)

OWASP 强调：即便阻止附加 shell 命令，攻击者仍可能把输入变成被调用程序的选项；参数化必须结合命令及参数的允许范围验证。[OWASP OS Command Injection Defense](https://cheatsheetseries.owasp.org/cheatsheets/OS_Command_Injection_Defense_Cheat_Sheet.html)

针对 ZaoFu 的推导：分别测试 `;`、换行、命令替换样式等文本，以及 `--option` 样式参数。观察最终 argv、允许的 executable、cwd 和环境，不把“参数是 list”当成完整安全证明。不需要实际执行攻击命令，spy 在真实调用边界记录并阻断危险执行即可。

### 2.3 路径验证必须看解析后的目标

WSTG 建议枚举所有接收文件相关输入的入口，再验证目录穿越和绕过情形。[OWASP WSTG v4.2 目录穿越测试](https://wstg.owasp.org/v4.2/4-Web_Application_Security_Testing/05-Authorization_Testing/01-Testing_Directory_Traversal_File_Include/)

Python 文档明确：`PurePath.is_relative_to()` 是字符串层判断，不访问文件系统，也不特别处理 `..`；`Path.resolve()` 才解析 symlink 并消除 `..`。[Python 3.11 pathlib](https://docs.python.org/3.11/library/pathlib.html#pathlib.PurePath.is_relative_to)

针对 ZaoFu 的推导：需要相对穿越、绝对路径、相似前缀 sibling、目录 symlink、文件 symlink，以及正常项目内路径正控。仅调用 `resolve()` 也不能自动证明检查和打开之间没有竞态；TOCTOU 必须单独用可复现同步点测试，未测不能声称已覆盖。

### 2.4 授权必须覆盖每条请求

OWASP 推荐默认拒绝，并逐请求检查权限，不能因为多数接口已检查就认为所有接口安全。[OWASP Authorization Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html)

针对 ZaoFu 的推导：分别覆盖缺 token、错 token、合法 token 但非法 action、合法 token 但跨项目目标；有效 token 只表明身份条件，不意味着所有 action、path 或项目都已授权。

### 2.5 脱敏检查必须覆盖最终输出边界

OWASP 日志指南列出 token、密码、连接串和密钥等不应直接记录的数据，并建议验证日志机制及敏感数据处理。[OWASP Logging Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html)

针对 ZaoFu 的推导：注入唯一、合成 secret 标记，分别检查持久化事件、API、SSE、错误输出和待发布的报告。正向同时保留普通文本及定位 ID，避免“删掉整份报告”也算脱敏成功。只测试一类敏感 key，不能证明任意自然语言中的秘密都能被发现；每个 case 必须说明已声明的脱敏契约。

### 2.6 测试替身应放在边缘，而不是替换安全逻辑

pytest `monkeypatch` 可在用例结束后恢复属性及环境修改；官方提示全局修改标准库可能破坏 pytest，自身代码使用的引用是更合适的 patch 位置。[pytest monkeypatch](https://docs.pytest.org/en/stable/how-to/monkeypatch.html)

针对 ZaoFu 的推导：保留真实路由、授权、schema、path guard 和事件写入；仅替换远端写、shell、危险文件写等最终 sink。不得把被测 validator 直接 mock 成拒绝，再宣称验证了安全。

## 3. 可执行测试清单

以下是建议清单，不表示都已实现。每项使用固定输入、隔离 state_dir、固定初态；expected status/exception 必须从具体入口契约确定，不能统一假设全部接口返回相同错误码。

| 编号 | 输入与调用路径 | 方法/工具 | 通过判据 | 必留证据 |
|---|---|---|---|---|
| S7-IV-01 | action 字段缺失、错类型、未知 action，调用实际 Web action 入口 | pytest 参数化、TestClient、sink spy | 返回约定拒绝；危险调用 0 次；规范状态不变；无成功事件 | 输入 ID、响应、事件差量、Store 快照 |
| S7-AU-01 | 缺/错 token 请求合法 mutation | 同上 | 401/403（按路由约定）；handler/sink 未执行；无业务写入 | 响应、spy 计数、文件或 Store digest |
| S7-AU-02 | 合法身份请求错误项目/路径/资源 | 两个隔离项目 fixture、真实 scope 解析 | 明确拒绝；另一个项目可读写状态不变；不泄露其秘密 | 双项目快照、拒绝证据 |
| S7-CI-01 | Channel/Issue/provider 正文含命令样式文本，调用已有真实 ingress | pytest、末端 shell/git/remote spy | 文本作为数据或按契约拒绝；额外 executable/远端写 0 次；无伪终态 | 输入 digest、实际 argv/calls、事件与状态差量 |
| S7-CI-02 | 本应为单值的字段含 `--option`、空格、换行 | 参数化、目标模块 subprocess 引用 spy | 不改变允许命令语义；或在 sink 前拒绝；允许 executable、argv、cwd 精确匹配 | argv 快照、拒绝记录 |
| S7-PT-01 | ref/附件文件名含 `../`、绝对路径、相似前缀 sibling | tmp_path、PathGuard/真实文件入口、sibling sentinel | 拒绝越界目标；sentinel bytes/digest 不变；拒绝响应不含哨兵内容 | 路径类别、响应、前后 digest |
| S7-PT-02 | 项目内 symlink 指向隔离项目外文件/目录 | 本地 symlink fixture、真实解析/读写入口 | 不可通过链接读写外部哨兵；正常项目内路径仍可用 | symlink 目标、前后快照 |
| S7-RD-01 | 嵌套 token/password/auth、Bearer、受支持密钥格式 | pytest 参数化、真实事件/action 路径 | 原始合成 secret 不出现在规定输出；脱敏标记存在；普通内容和因果 ID 保留 | 脱敏后输出、输出 ref/digest |
| S7-RD-02 | 上述秘密经过异常/失败响应、API、SSE、报告导出 | TestClient、结构化 SSE 读取、输出捕获 | 每个已声明输出 sink 原始 secret 出现 0 次；拒绝信息仍可诊断 | 各输出边界独立断言和证据 |
| S7-PC-01 | 与负向 case 同路径的合法 action、合法路径、普通文本 | 同一 fixture 的正控 | 正常执行一次；预期事件与副作用恰好发生；证明 spy 接线真实有效 | 正控 calls、Store/event 变化 |

审计事件可能是合法拒绝副作用，因此不能对所有入口机械要求 EventLog 完全不变。若接口有拒绝审计契约，应断言只出现允许审计事件、没有业务状态推进；没有审计写入的入口可精确要求 ledger bytes 不变。

## 4. 统一 oracle 与错误分类

单个负向用例通过应满足：

```text
拒绝状态/异常符合具体契约（或文本被安全作为数据接受）
+ 未授权危险 sink 调用次数 = 0
+ 规范状态与外部哨兵保持预期
+ 禁止成功/终态事件未出现
+ 必须保留的安全事件及定位字段存在
+ 输出脱敏契约成立
+ 测试夹具正控成立、无环境错误
```

需要保存 `case_id/source/sink/expected/actual`，以及事件差量和状态 digest。敏感测试输入用非敏感 case ID 引用，发布报告只收录脱敏版本；合成 token 也应清晰标注为测试值。

分类规则：

- 实际生产入口绕过授权、越界访问或泄漏受契约保护的秘密：测试失败并记录 `ZaoFu_issue.md`，不修生产代码。
- spy patch 错对象、fixture 未启动、依赖缺失、输出未采到：`harness_failure`，不能归为产品安全 bug，也不能通过。
- 尚无规范规定某字段必须拒绝，或端口只存在于设想：先确认实际契约，不虚构失败标准。
- 只有 mock 方法的返回值断言、没有实际调用链：证据不足；不能算端到端覆盖。

## 5. 本次有限仓库观察

读取的当前文件包括：

- `tests/conformance/test_zaofu_testkit_s7.py`：已有缺 token、Channel 命令样式文本、未知危险 action、ControlledAction 嵌套 token/password 脱敏用例。其“文本仅作为数据”断言尚不能独自证明所有 shell/git/remote sink 都未调用。
- `tests/test_path_guard.py`：已有项目外路径、symlink 逃逸、truth file、workdir ownership、source/target disjoint 测试。可复用为路径防线的底层证据，但不代表所有外部输入入口都连接到了这层防线。
- `src/zf/core/security/redaction.py`：当前包含递归对象脱敏及若干文本模式；实施矩阵时应据此区分“明确受支持的脱敏格式”和“仍需讨论的新格式”，不能把正则等同于任意秘密识别器。
- `tests/test_external_issue_ingress.py`：存在 Issue body/comments 作为受控输入、revision 去重及 approval intent 测试入口，可作为后续真实 ingress → sink 联调的接入候选；本次未执行这些测试。

建议先把 S7-IV/AU/CI/PT/RD 做成明确 source × sink 的参数化矩阵并保留实际入口和正控，再扩展更多协议或竞态。不要仅把上述现有测试文件数加总后宣称 S7 完整；对未接通或未观察的 sink 继续标记欠缺。
