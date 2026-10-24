> 状态: active

# 184/185 监工修复记录

关联[监工任务](../../tasks/active/2026-10-24-0702-185-supervised-repair.md)、
[设计185](../design/185-agent-routing-delegation-and-workflow-recovery.md)。
基线0ac28644，用户授权逐一修复1—9，并由当前session监督暂停、修复、断点继续。

## 签收边界与工作顺序

| 项 | 当前具体问题 | 下一验证 |
| --- | --- | --- |
| 1 | 第五轮产品容器校验失败，旧Verify超2M | 保持原需求/预算，冻结新版本从入口运行，失败先保存现场 |
| 2 | 旧候选reader取消修复尚无真实新Run验证 | 断点及迟到结果/旧Provider退出均取证 |
| 3 | Task Map后配置变更缺新代际受控链 | 实测复用/变化两分支，旧批准失效、新消费者生效 |
| 4 | Verify重复读、递归hook结果及短轮询增大累计上下文 | 查真实调用，精确证据索引，避免无效模型往返，预算不变 |
| 5 | 通用源码single入口未接通 | 复用writer/Task/Operation及独立验证，不另建状态机 |
| 6 | Codex原生助手身份/权限/子树取消未签收 | 当前adapter资格逐项验证，未支持能力保持拒绝 |
| 7 | 主机unshare拒绝；其他Codex正向能力已有证据 | 查实际隔离能力；Kimi不调用 |
| 8 | 恢复包缺预算/Git/权限/副作用完整事实 | 精确Run/Attempt，原子副本、未知项与受控接替 |
| 9 | PRD未交付，新修复尚未真实重验 | 各分支独立记录pass/fail/blocked，不把mock当真实E2E |

## 监工协议

每条问题记录：发现时间、Run/Task/Operation/Attempt、源码/配置、复现证据、暂停动作、
现场与checkpoint、根因、修改、验证、恢复动作及结果。禁止直接改canonical状态。
当前run-pause只阻止后续派发，不能据此认定在途Provider已停；预算墙钟仍继续计算。
暂停需要另行通过现有checkpoint/停止机制核验进程已停；不重置token预算或伪造新Attempt。
需替换冻结运行源码时必须先停相关进程，记录新版本及兼容性；配置变更走受控批准。

已清理的第五轮没有完整Git现场，不能伪称原地恢复。新运行建立新的、明确记录的起点；
后续失败现场保留直到完成诊断和恢复。终态后才归档和清理。

## SUP-001 · Verify上下文放大（2026-10-24 07:02 UTC）

证据：第五轮`verify-lane-0`的01:59:01 Codex transcript（独立CLI Verify），
证据根`/home/min/workspace/public/zaofu-evidence/185-prd-planner-20261024T050500Z/live/prd/`。
实际出现：必读集合截断后重复输出大段正文；对artifact read正文误取`.content`字段；
hook查询包含查询自身的大输出；约1秒yield的反复wait。上下文从15,597输入token增长
至124,808，单操作累计2,066,868触发原上限。不是简单的“产品测试太慢”。
脱敏归档第207行不是有效JSON，分析明确排除，不能声称完整重建所有工具调用。
修复方向：增加可回溯的事件索引输出，保留按ID取完整正文；澄清artifact read输出形状；
对在途工具使用合理等待，减少不产生证据的模型轮询。不得用索引代替实际验收证据。

SUP-001实施：新增 `zf events --index`（可配合`--json`）；先筛选再取last，不改ledger，
按ID仍返回完整原事件。索引仅供发现，语义验收必须读原证据。补单项artifact read正文
与批读JSONL形状、适度等待方法，三份skill同步。2项RED、36项CLI/方法及20项
输出合同/工件读取调用者通过；Ruff/diff通过。dev-verify保守闭包106文件升级全仓，
本次只读CLI增量用上述直接闭包，自动宽deterministic/serial层显式跳过，非full pytest。

真实旧ledger确定性重放：同样5条hook记录，完整查询193,383 bytes、索引5,271 bytes，
ID集合完全一致，输出减少97.27%。这是定位开销证明，不代表整轮token降幅或新Provider
通过。自动run未执行被跳过的全仓层，直接测试36+20为本批证据（不相加为唯一测试数）。
