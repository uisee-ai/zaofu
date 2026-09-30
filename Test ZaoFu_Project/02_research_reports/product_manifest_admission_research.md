# Product manifest admission 调研

> 文档定位（2026-09-30）：历史调研，正文“当前/建议/尚未实现”仅适用于各段所记时点，不是最新执行队列。现行方案见 [ZaoFu_Product_TestKit.md](../03_plans_and_steps/ZaoFu_Product_TestKit.md)，执行仍按既有暂停/跳过边界；本次不重跑或改写历史结果。
>
> 当前对齐入口：`tests/product_testkit/manifest.py`。manifest 顶层白名单、引用、有限超时和输入准入已存在；不再将这里建议的全部校验认作未实现。

> 范围：只审查当前产品 TestKit manifest/executor、ZaoFu 任务命令与矩阵准入实现；不执行产品命令，不修改生产代码。

## 事实基线

- `tests/product_testkit/manifest.py:58-73` 要求 JSON 顶层对象、固定 `schema_version`、项目类型、`source_root`、`target_commit`，并要求 `commands` 是非空列表。
- `manifest.py:74-97` 对命令检查稳定 ID/重复 ID、命令文本、tier、布尔标志、正 timeout 和非空 `acceptance_ids`；`manifest.py:98-102` 在已声明 acceptance 非空时检查悬空引用。
- `manifest.py:128-150` 检查 setup/acceptance/entrypoint 行结构和 acceptance 声明内 ID 碰撞；但 setup ID 未建立去重集合。
- `src/zf/runtime/verification_commands.py:18-91` 对任务契约命令做规范化：命令 ID 去重、命令文本非空、超时使用 `_positive_int`、并保留 command digest；选择命令来源时优先 `validation.commands`，再回退历史字段。
- `src/zf/runtime/plan_matrix_admission.py:18-34` 仅对调用方列入 `required_ports` 的矩阵检查 `status=ready` 与 enrichment fulfilled；随后在 `:36-81` 检查 task、acceptance、command 的 ID 和命令 root，` :83-130` 检查 acceptance 闭包，后续逻辑继续检查 test/real-E2E/capability/source closure。
- `tests/product_testkit/executor.py:44-68` 的 `run_product_plan` 接收已构造的 `ProductManifest`，只做可选 commit 检查并逐条执行 setup/commands；它本身不再次调用 `validate_manifest`，也不调用 `plan_matrix_admission_errors`。因此“绕过 load_manifest、直接构造 ProductManifest(raw=...)”是一个准入边界风险。

## 关键发现与建议

### 1. 空计划与 acceptance 闭包

manifest 层拒绝 `commands=[]`（`manifest.py:71-73`），因此空命令计划不会进入 executor。命令必须有非空 acceptance 引用（`:94-97`）。但悬空检查使用 `if declared_acceptances and ...`（`:100`）：当 `acceptances` 缺失或为空时，命令引用的 acceptance 不会被拒绝。这与方案要求“没有 acceptance 闭包不能通过”不一致。

建议：要求 `acceptances` 为非空（除非项目显式声明全量 `not_applicable`，且仍应有可审计理由），并无条件检查 `acceptance_ids - declared_acceptances`。补充缺失 acceptance、空 acceptance、悬空引用测试。

### 2. ID 碰撞范围

命令 ID 和 acceptance 声明内 ID 会去重；矩阵准入还跨 task 检查 acceptance/command producer 重复（`plan_matrix_admission.py:47-68`），并检查 test registry 与 task command 集合不一致。当前 manifest 校验没有检查 setup ID 重复，也没有检查命令 ID 与 setup ID 的跨集合碰撞。若 receipt 或索引以单一 ID 命名，跨集合碰撞可能造成覆盖或歧义。

建议：对 setup、commands、acceptances、entrypoints 分别去重，并对所有会进入同一 receipt/registry 命名空间的 ID 做全局碰撞测试；矩阵准入继续作为跨 artifact 的最终防线。

### 3. 非有限 timeout

manifest 使用 `isinstance(timeout, (int,float))` 和 `timeout <= 0`（`manifest.py:91-93`）；`float('nan')` 的比较结果为 false，`+inf` 也大于零，因此二者可能被接受，随后 executor 在 `:64` 转为 float 传入 runner。setup 使用相同逻辑（`:136-138`）。

建议：显式要求 `math.isfinite(timeout)`，并限定上界（例如项目契约允许的最大秒数）；对命令和 setup 都增加 NaN、正/负 Infinity、极大数测试。`verification_commands._positive_int` 的实现应同样确认有限性和上界，避免不同入口语义分裂。

### 4. executor 绕过 validator

`run_product_plan` 的类型标注不保证 `ProductManifest` 来自 `load_manifest`；调用者可以直接构造任意 `raw`，跳过 schema、ID、acceptance、cleanup 等检查。`verify_commit` 只验证 Git HEAD，不等于 manifest admission。该风险会使“executor 是安全入口”的假设失效。

建议：在统一 runner 入口接收 manifest 路径并调用 `load_manifest`；若保留对象 API，执行前调用 `validate_manifest(manifest.raw)`，并在产品运行前执行 capability/acceptance/test matrix admission。若需要性能原因不重复校验，应采用不可变、带 admission receipt 的对象，而不是裸 `ProductManifest`。

### 5. JSON 解析边界

Python 官方 `json` 文档说明：默认 decoder 遇到重复对象键时只保留最后一个值（可用 `object_pairs_hook` 观察原始键对）；默认允许解析 `NaN`、`Infinity`、`-Infinity`，`parse_constant` 可改为拒绝；编码器 `allow_nan=False` 才严格拒绝非标准数字。见 [Python JSON 文档](https://docs.python.org/3/library/json.html#standard-compliance-and-interoperability)（重复键、非标准数字）和 [loads 参数](https://docs.python.org/3/library/json.html#json.loads)。

当前 `load_manifest` 直接调用 `json.loads`（`manifest.py:45-53`），所以重复 JSON key 可能在进入 validator 前静默覆盖，且 JSON 数字 NaN/Infinity 可能进入 timeout 检查。建议使用 `object_pairs_hook` 检测重复键，并使用 `parse_constant` 抛出 `ManifestError`；同时限制输入大小，官方文档也警告不可信 JSON 可能消耗大量 CPU/内存。

## 建议新增的最小测试清单

- [ ] `commands=[]`、缺失 `commands`、空 acceptance 声明均 fail-closed。
- [ ] 命令引用未声明 acceptance 必须拒绝（包括 acceptance 字段缺失时）。
- [ ] setup 内重复 ID、setup 与 command 跨集合 ID 碰撞必须拒绝或明确命名空间隔离。
- [ ] timeout 为 NaN、Infinity、-Infinity、超大有限值时均按契约拒绝。
- [ ] 重复 JSON key（特别是 `target_commit`、`commands`）必须拒绝，不接受“最后值胜出”。
- [ ] JSON NaN/Infinity 常量必须拒绝。
- [ ] 直接构造非法 `ProductManifest` 调用 executor 必须不能执行任何产品命令。
- [ ] executor 运行前完成 matrix admission；required port 未 ready、闭包缺失、ID 碰撞均不执行。
- [ ] 保存 admission receipt，证明最终执行的 manifest、矩阵和 target commit 来自同一版本。

## 结论

当前实现已能阻止大多数普通空计划、命令/acceptance 重复和矩阵悬空引用，但仍存在四个重要边界：空 acceptance 时悬空引用绕过、非有限 timeout、JSON 重复键/特殊数字未拒绝、executor 可绕过 validator。它们不是产品业务失败，而是通用 TestKit 准入与 harness 风险；应先用严格负向测试锁定，再决定是否修改测试支撑代码或生产入口。
