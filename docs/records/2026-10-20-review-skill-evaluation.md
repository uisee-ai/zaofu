# 2026-10-20 zf-cr 优化与对照验证

结论：首批三个实施项已完成。最终候选在固定样例中保持既定缺陷检出和负例行为，
三个 scoped 场景明显减少 input tokens；full 场景增加 8.43%，不能宣称所有模式都更省。
当前证据支持采用这次分层与正确性修复，不构成所有任务、所有 Provider 或生产零风险证明。

## 范围与交付

基线：`7bee177aabe46b4f94dc7cf7b74551f6b8d4c72b`。
分支：`wip/codex-20261020-skill-review`。
参考 `/tmp/gm/agent-skills@b8caa26` 的渐进披露组织，没有导入 React/Next.js 平台规则。

- `zf-cr` 入口从 445 行 / 19,218 bytes 缩至 95 行 / 6,174 bytes，入口字节减少 67.9%。
  完整交付包为 18,403 bytes：主要收益来自按需读取，不是删除检查项。
- scoped / architecture / full 分别定义范围和停止条件；局部审查不再强制扫描全历史。
  状态归属、完成 gate、rework cap、诊断、plan 判重、delta gate、pin commit、
  non_empty、candidate patch-id 等检查保留在直接链接的架构引用中。
- 私有设计文档不存在时，使用公开手册和当前代码；显式验证项目通过现有 loader
  解析 `project.state_dir`，拒绝空参数、缺失/非法配置，不继承其他项目的环境覆盖。
- canonical 与 Codex/Claude 两份完整包同步，既有 UI 元数据回归 canonical。
  新增完整引用、真实 materialization、briefing、Channel 和隔离评测回归。

本次没有修改 `src/`、`zf.yaml`、Workflow 注册或前端。主工作区保持原 HEAD 和干净状态，
真实 runtime state 没有参与评测；没有 merge、push 或重启服务。

## 对照方法

最终 28 个真实 Codex 会话：4 个 forced case × 2 臂 × 3 次，加 4 个自然触发 smoke。
同一 case 内交替臂的先后顺序；case 批次并发执行。固定模型 `gpt-6.1-sol`、
CLI `0.163.0`、镜像 ID、输入及完整技能包摘要；每次使用独立 ephemeral 会话。
预期答案和评分说明不进入被测项目。

容器根文件系统和 fixture 只读，仅会话缓存使用 tmpfs；不挂载主 checkout、
真实状态目录或 Docker socket。容器内 CLI 不再套用宿主机不可用的 Bubblewrap；
外层容器提供文件隔离。模型调用使用既有认证，凭据不写入报告。

按预先固定的缺陷、调用链、误报、覆盖声明和实际工具输出逐轮复核。
评分由本次实施 agent 完成，不是独立盲审或关键词打分。
原 `clean-paths` 实际含 `/var/run → /run` 导致的测试可移植性问题：保留该案例并增加
该缺陷的检出要求，另行冻结修正测试路径的 `clean-holdout`。后者是补充负例，
不是来自独立生产任务分布的泛化测试。最终候选冻结后没有再根据结果修改技能指令。

## 结果

每个 forced case 每臂 3 次，以下为 CLI 报告的 input tokens 中位数：

| 场景 | 旧版 | 新版 | 变化 | 质量结论 |
|---|---:|---:|---:|---|
| 重试预算局部审查 | 130,997 | 96,152 | -26.60% | 两臂均 3/3 检出多调用一次，并验证直接调用链 |
| 路径解析与测试可移植性 | 132,934 | 80,090 | -39.75% | 两臂均 3/3 检出测试期望错误，未误报业务路径实现 |
| full 事件归属审查 | 86,688 | 93,999 | +8.43% | 两臂均 3/3 检出覆盖历史、绕过 owner 和错误状态路径 |
| 补充干净负例 | 108,035 | 79,297 | -26.60% | 两臂均 3/3 正确报告无实现缺陷，未新增误报 |

两臂各 12/12 场景验收通过。这里的“通过”指上述既定目标，不代表发现了所有可能问题。
所有 28 轮均完成，输入文件摘要前后相同，没有 file_change 操作。

full 的成本增加必须保留在结论中。轨迹还显示，新版 3/3 提供纯内存动态核验，
旧版仅 1/3；不能把较少检查所得的低 token 当作质量提升。
这只是本轮观察，不能由小样本推定开销变化的单一因果关系。

自然触发 smoke：两臂都在审查请求中读取技能，在翻译请求中只返回“早上好”，不调用工具。
每个提示每臂仅 1 次，且未加入其他项目技能竞争，不足以估计真实技能目录下的选路准确率。

原始 usage、缓存 token、output tokens、工具命令和耗时均保留在逐轮记录。
环境出现模型目录刷新超时、`python` 缺失后回退 `python3` 等现象，故不宣称稳定延迟改善，
也不将 input tokens 直接等同于账单金额。

## 被拒绝的初版及环境失败

初版禁止了本可安全执行的纯内存验证，导致新版漏掉测试可移植性问题。
虽然 token 下降更大，该候选未获接受。修正为允许无副作用的只读核验后，重新冻结并完成
上面的正式重复实验。空项目参数守卫完善前的中间包也未进入最终效果统计。

宿主机 Bubblewrap 报 `Failed RTM_NEWADDR` 时，模型无法读代码；这些 exit 0 会话仍属于
评测失败。首次容器认证挂载的权限错误也单列为环境失败。全部保留在原始证据目录，
没有拿它们的 token 差异证明效果。早期未完成的批次与正式轮次分别保存，没有覆盖旧证据。

## ZaoFu 兼容性验证

最终 `dev-verify.py run` 自动影响闭包：**173 passed、2 skipped、1 failed**。
唯一失败是 `test_ci_uses_current_controller_and_skips_provider_auth` 读取不存在的
`.gitlab-ci.yml`。已从固定基线导出同一测试和脚本，在隔离目录重现；基线 Git tree
也不含该文件。没有修改该测试或伪称全绿。

两项跳过来自公开导出缺少 design 文档和外部 dev backend 样例。
新增技能包专项 **19 passed**，技能 validator 和 manual currentness 检查通过。
覆盖 Codex/Claude copy/symlink 的完整资源、metadata 按需加载、Channel 自定义状态目录、
injection 与 self-repair 直接调用。未运行全量 pytest、Web E2E 或真实 Claude 模型评测。

关键命令（本次使用主仓库已有 venv 的 Python，PYTHONPATH 指向该 worktree 的 src）：

```bash
python scripts/dev-verify.py plan --base dev \
  --tests tests/test_review_skill_package.py \
  --tests tests/test_channel_skill_materialization.py \
  --tests tests/test_runtime_injection.py \
  --tests tests/test_channel_templates.py \
  --tests tests/test_autoresearch_self_repair.py \
  --tests tests/test_manual_docs.py --workers 2
# 相同参数执行 run；原始失败回执保留在元数据中。
```

当前 production skill manifest 的 sha256 仍只标识 SKILL.md（既有语义），不能据此
推断整个包不变。本次评测另外固定并验证整个包摘要，没有改变 runtime 协议。
未来仅修改 references 时，回放或效果比较仍须携带完整包摘要。

## 证据与复现

- [冻结身份、逐场景统计与自动验证原始回执](2026-10-20-review-skill-evaluation.json)
- [28 轮答案、命令、动态证据、usage 和 trace SHA-256](2026-10-20-review-skill-runs.jsonl)
- [运行方法与验收边界](../manual/operations/review-skill-evaluation.md)
- 原始 manifest、stdout/stderr、输入快照和失败探索记录：`/tmp/zf-skill-review-20261020-0525/`。
  `final-scoped`、`final-clean`、`final-full`、`final-holdout`、`final-natural` 为正式结果。

完整包摘要（有序相对路径 → 文件 SHA-256 的 canonical JSON 再取 SHA-256）：

- baseline：`d29e32a36986a1c7dee71b230eed1df2792842e1a201073f4cffe94574a9031c`
- evaluated candidate：`0a41e13ecd31bfb74b5087ee58d30436757da6e50f599b2e3befed53ac424779`
- delivered candidate：`fee4fe7ecf2b5ced4d8d515596ca6c7be6b42100a3d056a4c18d9078fec074a7`

暂存检查发现架构引用多一个 EOF 空行，交付时已去除。机器比对证明除此以外所有字节
与被测包一致；模型未因该 Markdown 空行规范化重跑。两种摘要均保留，避免混淆被测包和交付包。

本次结论限固定 Codex 样例及已测调用边界。大型真实仓库、长期会话、Claude 语义行为和
多技能竞争仍需后续独立样例；不能将本次结果外推为这些范围已通过。
