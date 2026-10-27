# BMAD 增量方法的固定评估案例

四组只读案例：routing、review（含保留清单）、retrospective、spike。
每组 prompt.md 与 project/ 交给现有 eval-plan-execution.py；rubric.json 只给评分者，不能拷入 project/。
案例属于合成、局部决定评估，不是实际 workflow/故障恢复或长程产品 E2E。
review 的两项局部 unittest 为正例；ledger 故障注入可显示真正原子性失败。
所有路线、版本、receipt 和任务记录均为受控样例，不可作为实际平台证据。

本轮预先固定这些案例，禁止根据答案放宽标准；六条 mandatory 逐项按证据 pass/fail。
没有留出案例时不得声称泛化。后续真实请求须以新案例/独立留出集评估，不反复优化本组分数。
匿名评分包见 scripts/eval-method-report.py；语义评分仍由审阅者负责。
