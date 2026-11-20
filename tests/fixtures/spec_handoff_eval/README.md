# Spec 生成与独立交接试点

六类合成输入在 `cases.json`，每类四项评审预期和两个最小实现的独立测试在
`oracle.json`。先冻结二者，再执行；oracle 不进入作者或接手者输入。
这些案例是可公开的校准/回归材料，不能继续冒充未见过的盲测样本。

```bash
.venv/bin/python scripts/eval-spec-handoff.py \
  --output /tmp/zf-spec-handoff-<utc> --baseline fe39e0f8 \
  --model gpt-6.1-sol --transport host-text
```

默认六案例 × 两组 × 作者/接手者，共最多 24 次调用，单次上限 240 秒，最多两个
并发；每组每案例一次。`--case <id>` / `--arm candidate` 用于明确标识的后续复测。
`--prepare-only` 不调用模型。输出目录必须不存在，避免覆盖旧证据。

三种方式：默认 Docker 使用只读挂载与已有镜像；`host-read-only` 用宿主机原生
只读沙箱；`host-text` 在同样只读权限下直接提供冻结正文并禁止工具调用。后两者
不提供容器级读取隔离，不可把工具轨迹未发现越界当作物理隔离证明。切换方式必须
使用新的输出目录并保留原失败，不混算不同方式的质量/速度。

作者仅取得需求及对应方法。接手者开启新会话，仅取得同一需求及作者产物，不取得
方法、作者会话、自评或 oracle。输入/方法/提示摘要、原始轨迹、usage 与失败均保留。
`completed_ungraded` 只表示传输和输出结构完成，必须再看语义；阻塞说明也可能有
合法 JSON，不能用格式正确自动算成功。

纯文本方式验证规格生成和交接，不验证文件检索、v5 编排、审批准入或完整产品交付。
同模型新会话不是跨模型/真人盲评；一次运行不能证明稳定收益。分别报告来源覆盖、
具体缺陷、合理未知、独立实现验证、耗时和 token；价格未知不能填零成本。

最小实现验证使用已冻结轨迹及独立 oracle：

```bash
.venv/bin/python scripts/verify-spec-handoff.py --run /tmp/zf-spec-handoff-<utc>
```

验证器校验 oracle/交接轨迹摘要，从轨迹最终回答读取实现；在无网络、只读、限制资源的
容器中执行预先冻结测试，记录摘要、镜像与结果，拒绝覆盖已有验证目录。每项通过仅覆盖
对应小接口，不代表整个应用。不要在宿主机执行模型代码。

输入澄清必须作为新实验，用 `--case dependency-cycle --case-addendum
 tests/fixtures/spec_handoff_eval/clarification.json` 对两组提供相同补充；保留原 corpus
摘要和补充摘要，不能用澄清后结果替换原始结果。`calibration*.json` 包含手写正反例及
修订：初始未来决定正例实际不完整，两次修订及评审发现全部保留。这些公开样本不再是盲测集。

本次结果、校准次序及运行限制见[实施评估](../../../docs/impl/206-spec-quality-evaluation.md)。
