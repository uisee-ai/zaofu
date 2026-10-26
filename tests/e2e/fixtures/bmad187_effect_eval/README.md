# 187 方法效果：冻结输入的真实模型对照

三个固定案例，各运行 baseline/candidate 两个版本、各两次。它们是合成的行为
审查样本，不是完整产品交付，不证明实际吞吐提升。输入先于运行冻结并计算摘要。
评分标准在 `rubric.json`，不复制到模型可见的 `project/`。

每案用现有 `scripts/eval-plan-execution.py --inputs <case-dir> --baseline <ref>
--candidate <ref> --model <model> --docker-image <local-image> --repetitions 2
--timeout 240 --output <new-output-dir>` 运行。两个版本使用相同输入、模型和资源
限制，不传 candidate-only facts，避免把技能差异与额外提示混在一起。

评分由本回合操作者按答案内容逐项复核：0=遗漏或结论错误；1=提到风险但缺具体
来源/后果/处置；2=准确定位且给出符合边界的处置。正例误阻断单独计数。评分者
能看见版本标签，属于非盲人工评分，原始答案保留以供复核。载入 Skill、schema
齐全、关键词匹配都不自动得分。超时/无法读取/未完成属于无效运行，不能当低分。

验证真实工具轨迹、输入未变、完整答复及 token/time；费用无可核实单价时保持未知。
模型复跑只能有限观察稳定性，不把 2 次重复当统计显著性证明。
