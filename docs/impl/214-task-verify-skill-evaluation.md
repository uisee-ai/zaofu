# Task Verify 的 Skill 评分卡

使用 `task-verify` 输出协议的工作流已接入按次评分和独立复核。首个加载策略试点已完成，结论为保留默认必读。卡片保留原任务裁决和独立的机械评分。Operator 可以先看关键项，再看分数的分母和覆盖率。即使可判定项全部通过，独立业务裁决仍可能待证。[Spec 214](../spec/214-workflow-skill-evaluation-and-optimization.md)

## 查询和回填

在归属项目目录执行。项目的 `zf.yaml` 决定状态目录与事件签名策略；回放历史配置时需要兼容该配置的加载器。目标可选一个 attempt、task 或 run，还可以缩小到 operation 和 dispatch。

```sh
zf attempt skill-evaluation <attempt-id>
zf attempt skill-evaluation --task <task-id> --history
zf attempt skill-evaluation --run <workflow-run-id> --replay
zf attempt skill-evaluation <attempt-id> --operation <operation-id> --dispatch <dispatch-id>
```

`--replay` 读取归档、活动事件段与冻结的证据文件，在标准输出生成当前证据的 JSON。正常模式读取已发布版本。任务详情的 Evidence 标签使用同一查询，可展开 Skill、逐项证据和历史修订。项目级 JSON 入口为 `GET /api/projects/{project_id}/tasks/{task_id}/skill-evaluations?history=true`。

## 证据和修订

- 新的 Verify 派发保存物化事件 ID、简报摘要、后端及配置指定的模型。旧事件依据派发前同一角色、任务和工作目录的最近物化记录恢复冻结包，并标记关联方式。共享同一个包的重派仍各有一张卡。没有冻结版本时只列已知名称。
- 受控 Skill 工具调用和文件返回各有证据 ID。文件返回需同时命中物化路径和 Skill 名。旧 Hook 缺少派发 ID 时要求已绑定的 Provider 会话、相同角色和派发时间窗。未观测到的使用、适用性、单个 Skill 的增益和成本保留未知或未测量。
- 核对目标与合同绑定、必需验收项是否有结果、非通过结果的理由，以及已归档探针与声明是否一致。已准入的判词来自原业务角色。独立审核项在取得有效复核回执前保持待证。
- 默认 watcher 在独立线程中增量消费事件，在配置的状态目录下维护 `projections/skill-evaluations.sqlite3`。消费者停机期间工作流可以继续。下次启动从事件重建索引状态并补卡。同一证据摘要重复发布时沿用版本，迟到的匹配工具返回或补齐的文件可产生后续修订，并保留前版摘要。
- 页面呈现事件 ID、可见证据引用和汇总，原始工具输出留在原证据中。任务的履职分单独计算；加载策略采纳属于阶段 4 的同案配对实验。

## 按需独立复核

Operator 可以选择一个 attempt，也可以在 run 中分层抽样。模型参数使用本机 Ollama 已安装的完整标签。基础评分卡与工作流在评审服务停机时仍可使用。

```sh
zf attempt skill-review <attempt-id> --model qwen2.5:latest
zf attempt skill-review --run <workflow-run-id> --model qwen2.5:latest --sample-percent 10 --max-calls 3
zf attempt skill-evaluation <attempt-id> --history
```

单次请求固定策略版本、抽样比例、调用次数预算和每次超时。机械检查发现的关键证据冲突优先入样；其余有业务判词的 attempt 按放行与未放行两组，以冻结身份确定顺序，每组按比例向上取整。显式指定 attempt 可单独复核。预算不足的入样卡记录 `budget_exhausted`，可在下一批继续。冻结输入、模型摘要、提示摘要及推理设置均相同的已完成复核会复用。已有有效结论时，后续失败的请求会报告原因，同时保留原评分修订。

每个 attempt 启动一轮独立的无工具推理。评审器冻结原判词及声明供回放。模型只收到脱敏的验收义务和本次派发已引用、能从 Kernel 事件及宿主回执重新验证的同目标输出节选。原角色、判词和声明在模型生成观察结果时均隐藏。输入包和复核回执按内容摘要归档，回执保留评审会话、模型摘要、服务版本、规则、模型实际可见输入的摘要及 token 计数。当前适配器连接单机回环地址的 Ollama 服务；托管项目的模型准入需要另行接入。[Ollama Chat 接口](https://docs.ollama.com/api/chat)

模型按合同验收项回答 `met`、`violated` 或 `unresolved`，代码与原判词比较后生成 `supports`、`contradicts` 或 `insufficient`。若冻结包没有独立宿主输出，响应格式只接受 `unresolved`。给出确定判断须引用已提供的独立宿主证据。原任务通过且有义务被反证时，卡片标记关键误放行；原任务阻塞且证据支持一项阻塞义务时，可以确认阻塞有据。服务不可用、输出无效、缺完整冻结义务或预算耗尽均显示待评。有完整答复但证据不足时显示弃评，业务项继续计入未知。任务页可展开每条义务的理由、引用、评审局限和建议对象。历史修订保留旧判断，新证据使不匹配的评审失效。

## 验收记录

| 冻结的 8×8 批次（UTC） | Task Verify 卡 | 通过 | 拒绝 | 弃评 |
|---|---:|---:|---:|---:|
| 2026-11-21 13:28:14 | 26 | 23 | 3 | 0 |
| 2026-11-23 15:16:37 | 18 | 16 | 1 | 1 |
| 2026-11-23 17:11:20 | 16 | 16 | 0 | 0 |

三批的派发身份、冻结 Skill 版本与五项评分均逐卡对上历史回填。60 张卡都有合同和验收项覆盖证据。其中两张卡有能核对的归档探针。三批原始卡片的独立业务项均为待证；下述研究抽查使用冻结副本。后两批使用了工作流分支较新的 PrdFlow 参数，验收时用兼容的分支加载原项目配置。

隔离测试覆盖同一 lane 重派、目标错误的构造负例、缺证据、失败、阻塞、取消、并发发布、消费者重启、迟到证据以及项目间同名任务。另一个非 8×8 的 mock 项目经标准派发与结果准入完成 Impl 和 Verify，评分卡可从真实派发事件追到冻结 Skill 包。

### 独立复核校准（2026-11-24）

在单机 Ollama 0.6.2 上使用 `qwen2.5:latest`，模型摘要为 `845dbda0ea48ed749caafd9e6037047aa19acfcfd82e704d7ca97d631a0b697e`。仓库内的样本事先标注了预期判定。评分针对原 Verify 判词是否有据，分别记录判词有据（`pass`）、判词被反证（`fail`）、信息不足（`unknown`）和无有效评审（`pending`）。复现各组可运行以下命令。

```sh
uv run python scripts/calibrate-skill-review.py --suite calibration --model qwen2.5:latest --timeout 240
uv run python scripts/calibrate-skill-review.py --suite holdout --model qwen2.5:latest --timeout 240
uv run python scripts/calibrate-skill-review.py --suite challenge --model qwen2.5:latest --timeout 240
```

第一版在五个校准案例中与参考一致 4 项。空购物车的宿主实测总价为零，评审却支持原来的阻塞。第二版让模型只报告义务的满足情况，仍受原验证者声明影响。最终规则 `task-verify-independent.v3` 在生成模型输入时隐藏原判词和声明，由代码比较义务观察与原判词。额外的三例挑战集在此次盲评前固定，包含缺少前置观察、错误阻塞和违反执行顺序三种情况。

| 案例组 | 与参考一致 | 已知关键误放行命中 | 额外误报警 | 弃评 | 待评 |
|---|---:|---:|---:|---:|---:|
| 原校准集 | 5/5 | 1/1 | 0 | 1 | 0 |
| 保留集 | 4/5 | 1/1 | 0 | 1 | 0 |
| 新增挑战集 | 2/3 | 1/1 | 1 | 0 | 0 |

两组缺少宿主输出的案例曾因空引用被拒绝；修正弃评的引用校验后各复核一次，最终均为弃评。额外误报警来自计时器前态没有观测，模型把缺证据解释成了已经违反原要求。13 个样本的建议对象全部是证据生产方，这次校准只能验证缺证据时能指出补证方，对 Skill、Prompt 与 Profile 之间的归因区分能力仍待验证。

在 2026-11-21 的 8×8 批次中，研究按“有归档宿主探针的通过样本”和“原判拒绝的样本”各取一张。仅对冻结数据推理。

| 历史 attempt | 原判词 | 独立模型意见 | 核对原始材料 |
|---|---|---|---|
| `ta-469564c0c15305910977` · MEDIAN | 通过 | 支持放行 | `evt-52612007bb2e` 显示 6 个行为测试通过。合同还要求断言先 RED 再 GREEN，当前包没有 RED 观察。模型对整条义务的确认过强，完整过程仍待证。 |
| `ta-7e0502bb24c74c67110a` · SUM | 拒绝 | 弃评 | 冻结包中只有验证者声明，没有可重新验证的宿主输出。模型将补证责任归给 `evidence_producer`。第一次答复给出空引用；验收器允许“证据不足”保留空引用后，同案复核成功。 |

对于跨时序合同，可把前态、后态与顺序分别列为验收义务，让证据生产方给每项绑定实际观察。保留集的票据去重案例表明，即使有明确的行数断言，小模型也可能读反预期值。下一阶段估计单个 Skill 的增益时，含此类分歧的配对案例需要独立参考答案；模型建议的责任方只是一条待核实线索。

另一个独立项目经真实 CLI 调用本机模型，对缺宿主证据的任务产出弃评修订。再次请求时模型调用次数为 0，原任务判词保留为通过。

本次实现验证覆盖 56 项定向后端测试和 53 项合并门检。Web 类型检查、单元测试、生产构建及 Docker Chromium 的评分卡交互测试通过。历史 60 张机械卡片与前一阶段的 JSONL 逐字节一致，SHA-256 为 `c0cfc99d2810c2d4b3053f53d6a80c965def0513eb51a8b95e84536b14f4d979`。

## 加载策略和首个试点

看板 → 任务详情 → Evidence → **Task Verify 技能评估**。展开一个 attempt 可核对分子、分母、未知项、关键告警、Skill 读取见证与独立复核。展开 **Skill 加载策略对照（参考试验）** 可查看随此版本发布的试验、切换案例集、核对提交偏差或下载逐例 JSON。表旁的本任务记录来自所选项目的派发，显示其模型、模式与 Skill 摘要是否吻合。切换项目会重新读取记录。[逐例结果](../../web/src/data/skill-loading-reference.json)

2026-11-24 对 `zf-project-adapter-matrix-enrichment` 做三组配对。三组共享八项其他 Skills、同一输出合同及预算，分别移除目标、保持默认必读、只在方法匹配时读取。Codex CLI 0.171.0、模型 `gpt-6-luna`、推理强度 `high`。共七个案例，每例每组两次，共 42 次隔离调用，全部完成。两个案例从 8×8 的 SUM、MEDIAN 记录提炼，其余涵盖服务、CLI、矩阵缺口、错目标截图和无关请求。参考答案保留在容器外。[冻结案例及预设门槛](../../tests/fixtures/skill-loading/task-verify.json)

| 案例集 | 加载策略 | 判词正确 | 判词、动作、收据均合规且正确 | 误放行 / 误阻塞 | 读取见证 | 平均输入 token | 耗时中位数 |
|---|---|---:|---:|---:|---:|---:|---:|
| 选择集 | 移除 | 4/4 | 4/4 | 0 / 0 | 0/4 | 46,282 | 24.61 s |
| 选择集 | 默认必读 | 4/4 | 4/4 | 0 / 0 | 4/4 | 59,281 | 29.59 s |
| 选择集 | 按需 | 4/4 | 4/4 | 0 / 0 | 0/4 | 34,478 | 21.81 s |
| 保留测试集 | 移除 | 10/10 | 9/10 | 0 / 0 | 0/10 | 36,482 | 24.36 s |
| 保留测试集 | 默认必读 | 10/10 | 10/10 | 0 / 0 | 10/10 | 44,274 | 24.63 s |
| 保留测试集 | 按需 | 10/10 | 8/10 | 0 / 0 | 2/10 | 47,369 | 31.13 s |

**采纳决定为保留默认必读。** 按需在矩阵缺口的两次试验均有实际读取，无关请求均未读取。选择集输入节省 41.84%，保留集反而增加 6.99%，低于预设的 5% 节省要求。两次 MEDIAN 都正确要求补证，其中一次将说明写入收据 ID，另一次增加了合同以外的字段，两次都触发了完整提交质量门槛。输入数含缓存部分，下载数据另列缓存和输出用量。小样本只支持本客户端、模型和案例集的决定。Claude Code 的 CLI 认证预检报告 OAuth 会话已过期，本轮没有该客户端的模型测量值。

新项目可对已启用的 Skill 按角色或阶段配置模式。下面的 PrdFlow 配置片段用于显式试验。回退时删除该覆盖，后续新绑定会使用 Skill 自身声明。已有 Task-stage 绑定在重派时复用原加载设置，派发的冻结记录可还原当次锁与物化清单中的加载模式。

```yaml
spec:
  roleDefaults:
    skillLoadingByStage:
      verify:
        zf-project-adapter-matrix-enrichment: on_demand
```

对于显式定义的角色，在该角色已有 `skills` 列表旁使用 `skill_loading: {方法名: required}` 或 `on_demand`。首轮结果用于选定试点角色的默认策略。其他角色、项目及模型可以使用同一机制独立验证。

在仓库根目录执行，选择一个尚未创建的输出目录。每轮保留冻结输入、三组物化清单、逐次调用记录及摘要。重算命令仅读取已有文件。

```sh
PYTHONPATH=src python scripts/eval-task-verify-loading.py --output /tmp/zf-verify-loading-trial --model gpt-6-luna --reasoning-effort high --jobs 3
PYTHONPATH=src python scripts/skill_loading_comparison.py /tmp/zf-verify-loading-trial > /tmp/zf-verify-loading-replay.json
```
