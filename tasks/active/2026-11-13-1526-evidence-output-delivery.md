> 状态: active

# 证据读取的聚合输出完整性

来源：LS-v1保险Verify监工，2026-11-13 15:20:54 UTC批量4命令，15:21:08.995外层结果报告12,573 tokens，wrapper预算10,000导致中间2,573被截；各inner cap足够不证明聚合交付完整。以上是Root给出的有界观测，不能泛化为所有工具的默认预算。当前dev `8c3b2ca7` 的证据skill无外层聚合预算与交付完整性方法，缺口仍存在。

已依据用户持续“记录、修复、完成记录”授权执行；Root唯一dev merge owner。

- 在既有canonical证据skill补短方法 -> verify: 覆盖并行总输出/外层与wait预算、分批/有界读、真实截断与源码引用区分、只补缺失片段、required-read校验与provider实际交付边界；不硬编码通用默认、不改限制、不冒充runtime gate。
- 只同步本skill两个分发副本 -> verify: metadata校验、全包parity及真实Impl/Verify materialization caller得到更新；不为文字写镜像测试。
- 记录真实例和OA/RM未自动诊断的观测边界 -> verify: 区分生命周期状态与输出交付可见性，不以缺少自动诊断声称其永不生效。
- 交付 -> verify: docs/skill相关dev-verify plan/run、sentinel，exact pathspec实现commit及task归档commit；干净冻结交Root。

仅改本skill canonical/distribution及本任务记录。禁止runtime/配置/当前LS或主checkout无关文件修改、安装工具、merge/push；不运行全pytest。

## 有界实际证据

只读核验原始native第309行：`LS/.zf-ls-v1/workdirs/verify-lane-0/codex-home/sessions/2026/11/13/rollout-2026-11-13T10-04-39-01a1d570-765f-7440-9a49-da1d1a02c2ae.jsonl`；LS为本次受监工项目根。该原JSON行含newline为41,591 bytes，SHA256 `8b73823cb181589c57d1a7a1a1bf8d58be53bc7a983cc0aea4530f7e770afde0`，timestamp `2026-11-13T15:21:08.995Z`，实际payload type为`custom_tool_call_output`，call_id `call_c624f8b46f7c4d688187fdeb5824492c`。

返回头部明确为`Script completed`、`Warning: truncated output (original token count: 12573)`及`Total output lines: 4`；中间真实省略标记为`…2573 tokens truncated…`。它是实际工具返回层标记，不是源码讨论截断的字符串。本任务只保留这些短片段、时间及摘要，不保存完整payload、不读取模型推理。此前15:20:54批次开始及外层10,000预算来自Root观测，不作为其他工具默认值。

Root另报告操作员反馈`evt-21bd3b113696`及送达`evt-6e629e895680`（15:23:57.821）；后续两批显式设置17,000/18,000聚合预算。这里只作为Root提供的修正经过，未独立检查后续全部正文，不能据更大预算宣称交付已完整。

## 为什么生命周期机制没有自动代替这次监工

本例证明外层交付缺失，未证明命令执行、attempt或任务生命周期失败。正常心跳/仍在Verify不证明每次工具正文完整，也不足以推出截断原因。`artifact_read_ledger.py`已要求required pages逐页消费、截断重读，并明确CLI回执仅证明产出；`artifact_output_delivery.py::observe_artifact_delivery`对可观察Codex工件输出提供精确正文的历史交付注记，未知保持unknown，注记不是新的结果准入状态机。

本轮没有证据证明该任意批量shell输出的完整性异常已作为具名诊断输入送入OA/RM。缺少这一观测时，不能期待仅凭正常生命周期识别遗漏；也不能由“本次未自动处理”推断OA/RM永不生效。本修复补Agent采集方法，不改监控、路由或runtime gate；不声称实现了所有工具输出的自动截断恢复。required-read原校验、授权及业务验收不变。

## 实施和验证

- canonical只新增“工具输出完整交付”16行。未发现仓库现成专用sync入口，经Root确认，从canonical读bytes精确复制到`.codex`/`.claude`对应SKILL.md；无独立编辑副本或新建同步器。
- 三份SKILL.md SHA256均为`916e298ff63793a1f01206d801e87021d4d46c8caea6aa94df1e6cda35d7439f`。全包两文件bytes一致；`references/work-record.md`未改，SHA `2c2f407b0f491cfed9ca43be0e5c249b2c8c9e0546a9f41d837a0a03171e518d`。
- skill-creator `quick_validate.py`通过。既有`test_plan_readiness_skills.py`实际调用`materialize_role_skills`验证Impl/Verify依赖加载后正文bytes与canonical一致，引用仍可回取；没有新增匹配规则文字的测试。
- dev-verify docs/skills影响闭包：4个测试文件，64 passed、1 skipped/1.60s。skip是现有示例依赖的`./skills/external`、`./skills/critic`缺失，未安装；不影响隔离构造的两个实际角色materialization测试。非全pytest、非real-provider验收。
- sentinel 53 passed/2.70s；`git diff --check`通过。receipt `/tmp/zf-output-delivery-20261113-dev-verify.json` SHA `0db6e03605521dc18908b31b207b9c623886ff48192eee21808916ef464ec86c`。验证后仅补本任务结果记录，三份skill未变。

验证命令（此独立worktree，主venv，`PYTHONPATH=src`）：

```sh
python /home/min/.codex/skills/.system/skill-creator/scripts/quick_validate.py skills/zf-harness-evidence-collection
python scripts/dev-verify.py plan --base dev --tests tests/test_plan_readiness_skills.py --no-parallel
python scripts/dev-verify.py run --base dev --tests tests/test_plan_readiness_skills.py --no-parallel --receipt /tmp/zf-output-delivery-20261113-dev-verify.json
bash scripts/dev-premerge-gate.sh
```
