# 规划缺口 A/B 样例

两个独立候选共用预约需求：alpha 包含动作前置、竞态和 Preview 交接反例；beta 用于
检查任务池复用、早期真实入口验证和条件决定是否被误拒绝。不是完整产品、真实用户
资料或已通过运行验收的计划。隐藏 rubric 不复制到 provider 工作目录。

运行入口：`scripts/eval-plan-execution.py --inputs tests/e2e/fixtures/planning-gap-ab`
并显式提供 `--baseline <commit> --candidate <commit> --model <本账号可用模型>`、
新的 `--output /tmp/zf-plan-ab-<UTC>/`、`--docker-image mcp/playwright:latest`。
`--repetitions 3` 以 AB/BA/AB 顺序执行；`--timeout 0` 不按墙钟截断，但连接失败仍失败。
只对已完成且没有修改输入的结果评分。缺口命中要有语义证据，不能按关键词自动评分。

该工具会冻结 Git 版本的 skills/yoke，输入与 prompt 两组相同，不默认添加 candidate-only
事实。模型版本、采样及缓存仍可能影响结果；三次重复不足以证明跨业务统计优势。
本样例偏向已观察到的故障类型，需要后续独立 holdout 与真实 Plan 生成验证。

Docker 以显式用户运行，只读挂载输入和认证，HOME 在容器内临时可写；从主机只读
挂载 CA 根证书，不关闭 TLS 验证。可用 `SSL_CERT_FILE` 指定 CA bundle。某些 uv Python
只报告 capath，runner 也检查其中的 `ca-certificates.crt`。不安装或修改主机浏览器。
