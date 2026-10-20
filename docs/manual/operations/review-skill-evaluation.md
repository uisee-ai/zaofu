# 评估审查技能的改动

修改 `skills/zf-cr/` 时，先固定当前包和验收样例，再比较候选。
本流程验证技能方法和分发兼容性；不替代 ZaoFu runtime 的发布验收。

## 先界定成功

1. 固定 base commit、完整技能包摘要、输入文件、模型、CLI 版本和执行环境。
2. 每个正例必须发现预设缺陷并给出正确调用链；干净样例不得产生高风险误报。
3. 输入文件必须不变；失败、超时、工具不可用和未完成不能计为通过。
4. 质量成立后，比较各 case 多次运行的 input/output tokens、读取范围和耗时。
   入口字节数只是结构指标，不能直接换算成本或质量。
5. 强制 `$zf-cr` 只验证内容；自然触发与无关请求单独评估，不混合统计。

默认样例在 `tests/fixtures/review_skill_eval/cases.json`，含调用链缺陷、
干净状态路径和 full review。期望答案只交给评审者，不进入被测项目。
对已用过的样例调参后，要另取未参与修改的 holdout；这里的少量样例不能证明全局增益。

## 运行

先将 base commit 中的 `skills/zf-cr/` 导出到隔离目录，例如通过
`git archive <base> skills/zf-cr`，保留整个包而非单个 `SKILL.md`。
然后在当前 worktree 执行：

```bash
python scripts/eval-review-skill.py \
  --baseline /tmp/zf-review-eval-<UTC>/baseline/skills/zf-cr \
  --output /tmp/zf-review-eval-<UTC>/paired \
  --model <固定模型> --repetitions 3
```

`--prepare-only` 仅生成样例，不调用模型。`--routing --repetitions 1`
生成独立触发 smoke；正式 routing 验收应扩大提示分布和重复次数。
`--case <id>` 可按 case 分批，`--cases <json>` 可加载另行冻结的负例；
`holdout.json` 是首次评估发现样例测试可移植性问题后补充的干净路径负例。
输出目录必须不存在，避免覆盖历史证据。每次调用有时间上限；执行失败立即停止批次。

本机 Codex read-only sandbox 必须先通过读取检查。如果宿主机不能运行 Bubblewrap，
可显式选择已有的含 bash/git/Python 的容器镜像：

```bash
python scripts/eval-review-skill.py \
  --baseline /tmp/zf-review-eval-<UTC>/baseline/skills/zf-cr \
  --output /tmp/zf-review-eval-<UTC>/container-paired \
  --model <固定模型> --repetitions 3 \
  --docker-image <已有镜像>
```

脚本固定镜像 ID；只读挂载样例、Codex/rg 二进制与已有登录文件。
容器根文件系统只读，临时会话数据放在 tmpfs；不挂载主 checkout、真实状态目录或
Docker socket。容器内 CLI 使用 `danger-full-access` 是因为外层容器负责文件隔离，
不能把这个参数直接复制为宿主机执行方式。网络用于模型调用；host 网络也支持本机
已有代理。登录文件默认 `~/.codex/auth.json`，可用 `--auth-file` 指定；
不打印或复制凭据到报告。若认证必须刷新而只读登录文件阻止刷新，停止并修复认证环境。

## 读证据、判结果

每轮保存 `prompt.txt`、`answer.txt`、`trace.jsonl`、`stderr.txt`、`result.json`。
manifest 记录完整包摘要、case 摘要、模型、版本、镜像 ID 和每轮 usage。
先检查工具是否真正读到了代码，不能只看 CLI exit code 或最终回答。

按隐藏 rubric 人工核对触发条件、调用链、严重度、误报和范围声明；
关键词命中不能作为语义质量判定。记录逐轮结论及原始 trace digest。
同一 case 按臂比较中位数，同时保留所有单轮值和缓存 token。
小样本只支持本次固定场景结论，不能宣称统计显著、所有技能提升或生产零风险。

分发回归覆盖：

```bash
python scripts/dev-verify.py plan --base dev \
  --tests tests/test_review_skill_package.py \
  --tests tests/test_channel_skill_materialization.py \
  --tests tests/test_runtime_injection.py \
  --tests tests/test_channel_templates.py \
  --tests tests/test_autoresearch_self_repair.py
# 检查 plan 后，以相同参数执行 run。
```

其中完整包一致性、相对引用可达、Codex/Claude copy/symlink、
metadata 按需加载和 Channel 自定义状态目录都有真实函数调用回归。
发生已有基线失败时，在固定基线重现并单列，不能改验收规则把红灯藏起来。

方法参考 [OpenAI 技能评测说明](https://developers.openai.com/blog/eval-skills)：
用真实执行轨迹核对行为，区分确定性检查与语义评价。
本次改进借鉴 `/tmp/gm/agent-skills@b8caa26` 的渐进披露组织，
未复制其 React/Next.js 平台规则。

首次实施记录见 [2026-10-20 对照报告](../../records/2026-10-20-review-skill-evaluation.md)。
