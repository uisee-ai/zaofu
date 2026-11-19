> 状态: done

# P2 真实验收补丁：字面量 stdin 的受控提交

承接已批准的P0–P2修复、mock优先与真实8×8验收。基线dev `acfdf141`。
真实Run `workflow-863563136fc24d34` 的Mask与Average按briefing提交
`zf result validate --stdin --submit`失败事实时，被工作区守卫拒绝。
单引号here-doc中的Markdown反引号被误判为shell替换，受控失败结果无法入账。
当前实验保持冻结源码，补丁不直接写运行状态、提交结果或放宽工作区权限。

## 验收

- 使用真实生成器输出复现 -> verify: 原提交的字面量正文被旧守卫误拒。
- 只识别单个受控result CLI的完整、带引号stdin here-doc -> verify: 原始说明正文经Codex/Claude hook可到达既有CLI校验；不会产生产品通过。
- 保持实际shell副作用和路径边界 -> verify: 未引用delimiter、头部替换、重定向、管道、分号、尾随命令、提前delimiter、其他程序或缺失stdin均保留拒绝。
- 影响闭包、sentinel与确定性mock -> verify: 通过后才进入获准真实验证；区分旧轮证据与新源码证据。

实现说明：[持续推进与无人资格](../../docs/impl/unattended-progress-recovery.md)。
真实复现记录：`reports/eight-enforce-real-20261119/mask-literal-stdin-classifier-reproduction.json`。

## 验证记录

- 原始回归：8 failed / 10 passed，`reports/literal-reader-submit-20261119/red.txt`。
- 修复后定向 hook 测试：72 passed。
- `python scripts/dev-verify.py run --base dev`：168 passed；覆盖直接调用方及结果/指令合同。
- 八槽 Verify mock、TaskAttempt 故障矩阵、8×8 fixture、字面量 patch 保护：35 passed。
- `bash scripts/dev-premerge-gate.sh`：53 passed；文档与结构检查通过。
- `git diff --check`：通过。范围是确定性验证；旧真实Run尚未使用本补丁，不能据此声明完整无人资格。
- 日志：`reports/literal-reader-submit-20261119/{verification-plan,impact-closure,mock,sentinel}.txt`。

实现提交：`5b2782c5 fix: preserve literal stdin in controlled reader submissions`。
范围完成：生成命令的字面量正文误拒修复及确定性回归；真实无人资格仍由P2主任务验收。
