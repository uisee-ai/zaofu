# ZaoFu 测试发现的问题

> 文档核对（2026-09-30，重启后）：本台账保留原始问题、复现和修复记录；本次不重跑、不新增或关闭 Issue。各段“当前”“未登录”“CLI 缺失”只指段落所记时点，不能作为今天的宿主诊断。K600/K610 原生测试当前为操作者跳过，不等于认证失败。产品自身的业务缺陷、ZaoFu 宿主缺陷和 TestKit harness 问题必须分层，旧报告不改写为当前通过。现行状态入口见 [ZaoFu 实施步骤](../03_plans_and_steps/steps.md) 与 [产品实施步骤](../03_plans_and_steps/Product_steps.md)。

## 合并取舍与当前状态口径（2026-09-30，操作者确认）

`/issue-merge` 全流程、Triage 管理及相关生产行为只保留
`/home/uisee/zaofu-worktrees/feature-issue-stability` 的实现，不接入 v7 的对应生产修复。
即使 v7 测试检出问题，也只登记、保留证据，不因此修改 feature 的这些功能。
这条规则替代此前“有台账依据的 v7 修复可以优先”的一般规则在上述范围内的适用。

本范围原为 `fixed`、但其 v7 修复不纳入整合的条目改为 `open`；这里表示整合目标的
问题闭环仍开放，不是否定 v7 历史修复，也不等于已在 feature 重新复现所有问题。
不删除 v7 现存源码修复，不改写历史报告，不把旧的通过证据当作 feature 验收。

| Issue | 当前状态 | 本次处理及边界 |
| --- | --- | --- |
| ZF-TEST-085 | open | 取消受管 Triage 与项目合同共存的 v7 修复移植；feature 的单合同拒绝已在合并检查中复现。 |
| ZF-TEST-087 | open | v7 受管角色源码根重定向不接入；候选生产接线已撤回，保留 feature 执行项目模型及隔离回归测试，不声称完整原场景已验收。 |
| ZF-TEST-047 | open | 属于普通 synthesis 与 Self-Issue 根分离的混合边界；保留 feature 当前实现，不移植 v7 的旧根模型，整合目标尚无本 Issue 的完整验收。 |
| ZF-TEST-040 | open | 总体结构门禁仍开放；其中 Triage 页面/CSS/路由/ingress 的 v7 重构不接入，其余通用结构子项的历史证据保留，不全部推翻。 |

ZF-TEST-086 等仅测试框架/夹具修复不受此禁入规则影响，仍按实际证据保留状态。
ZF-TEST-045 的通用 IssueFlow 验证消费/重放问题不等同于 `/issue-merge` 或 Triage 管理，
本次不更改其状态；后续若某补丁实际跨入禁入范围，必须排除该部分并更新对应 Issue。
后续每发现新的相关 `fixed` 条目，按上述规则改为 `open` 并说明范围，不自动修复生产代码。

## Feature 整合观察（2026-09-30 收尾；不是原生验收）

目标为 `/home/uisee/zaofu-worktrees/feature-issue-stability`，来源 v7 保留不动。
本记录只说明选择性整合结果，历史 `fixed` 不代表对应修复已经全部迁入 feature。

- ZF-TEST-079：按实际 spawn cwd 探测 transcript 的三行通用修复已迁入，先在 feature
  重现缺少 `--resume`，再通过隔离模拟回归。最新聚焦批 `fixes-final3.xml` 为
  131 passed；生命周期/runner 批为 51 passed。未运行真实 Claude/Codex。
- 未编号观察，状态 open：`test_cli_restart_preserves_task_stage_spawn_cwd[None]`
  在 CLI 加载渲染后的配置时返回 1，错误为
  `Unknown key(s) in self_issue: 'settings_config_path'`。更宽调用方批
  `fixes-closing-final2.xml` 为 153 passed / 1 failed；这不是该 cwd 修复已失效的证据。
  按 feature/Self-Issue 保留边界，仅登记，不修改 schema、render 或 loader。
- ZF-TEST-099 / ZF-TEST-096：feature 尚缺 v7 的 `run_write_scope` /
  `active_task_request_blocker` 实现；迁入测试保留失败能力，import 延后到执行时，
  没有改为 skip/xfail。不能以收集通过认定这两项业务修复已移植；本轮未复现其完整业务场景。
- 预合并门禁仍为 45 passed / 6 failed，失败节点集合与整合前记录一致。
  ZF-TEST-040/047/085/087 继续保持 open；没有通过放宽门禁或覆盖受保护代码换取全绿。

证据目录：`/home/uisee/zaofu-merge-backups/testkit-reports-6fQ8QtMG/`。
当前框架入口、已迁入边界与限制以目标工作树 `tests/testkit/MERGE_STATUS.md` 为准；
报告不相加，不换算为端到端完成率。本轮未提交、未合入 dev、未推送。

## 环境/测试入口标记：K600 原生 Codex/Claude 按要求跳过（2026-09-29）

> 状态: skipped-by-operator（不是 ZaoFu 产品缺陷；测试方法已搭建，未运行原生测试）

本条只记录当前原生验收的环境边界，避免把“跳过”误记为通过，也避免把宿主问题误记为
ZaoFu 产品问题。操作者要求本阶段直接跳过 Codex/Claude 原生执行，因此没有启动模型、
没有修改认证或安装、没有新增测试结果。

- **Codex/K547**：`codex-cli 0.155.1` 存在。生产默认认证源
  `/home/uisee/.codex` 的 login probe 为 `Not logged in`；另一个
  `/home/uisee/.codex-direct` 返回 `Logged in using ChatGPT`，但不是生产默认
  `CODEX_HOME`。K547 的前置合同要求登录探针与生产 credential source 相同，不能直接
  混用或复制凭据，因此标记为 `SKIPPED_BY_OPERATOR_ENVIRONMENT`。可执行方法和固定节点
  见 `steps.md` K600、K594；不代表用户没有登录，也不代表 Codex 产品失败。
- **Claude**：`@anthropic-ai/claude-code@2.1.282` npm 包已安装，但 PATH 中无
  `claude` 命令；包内 shim 报 native binary 未安装，包内 Linux binary 启动为 Bun
  `Bus error`（exit 135）。因此标记为 `SKIPPED_BY_OPERATOR_ENVIRONMENT`，分类为
  CLI 接线/宿主启动阻断，不是“未安装”，也不是 ZaoFu 产品 bug。
- **可复用方法**：Codex 使用固定 K547 native queue node 和
  `ZF_RUN_NATIVE_CODEX_LEGACY_QUEUE=1`；Claude 使用 `tests/testkit/native_run.py`
  的 `normal/impl/verify` 精确节点和 `ZF_RUN_NATIVE_CLAUDE_PIPELINE=1`。两者均要求
  host/real_provider 显式层级、source/JUnit/transcript/cleanup 证据；离线合同不替代
  原生验收。

只有操作者明确恢复原生执行后，才重新核对同一生产认证源和 CLI 能力并运行既有精确节点；本标记不得改写为 pass，
也不影响当前 deterministic MAINLINE、K599 单项审计或 `release_authorized=false`。

## K596-F1：静态Codex hook hash期待值与精确输入错配

> 状态: fixed（2026-09-29；仅测试fixture/来源修复，未提交；不占生产ZF编号）

K593-C和K595-B的十个普通失败来自`tests/test_codex_hook_settings.py`静态向量，
不是048迁移引入的生产回归。历史041a931曾改过十个摘要，793199c又恢复失败组；
历史差异本身不足以判定算法或期待值正确，因此本次没有直接回退某次提交。

独立构造两占位state_dir×五hook精确command，在私有CODEX_HOME调用真实Codex
0.155.1的initialize/config/read/hooks/list，十个Provider currentHash都与生产结果
一致，与原期待值不一致。原始input/reply已保存；读取过程未使用生产hash生成oracle，
全部hook保持untrusted，没有thread/turn/model或hook执行。旧红报告保留在K595-B归档。

已修复：把精确command/Provider currentHash/版本/发现范围固化为
`tests/fixtures/codex_hook_hashes_0_155_1.json`，静态十节点校验命令字节和摘要；
显式host复验十映射，并检验Stop命令变化只改变对应hash。新增fixture完整性合同与
十节点登记S5/MAINLINE；没有改`src/`、hash算法或`CODEX_HASH_VERIFIED_VERSION=0.144`。

验证：直接19 passed/1 host sensor deselected；前序影响闭包191 passed/89.489349s，
完整session/source stable/exit0，无skip/xfail/collection/deselection/timeout/evidence错误；
两新增hash host＋K595-B两私有权限host共4 passed/4.37s。详见steps K596及
`attachments/k596-hook-hash-provenance-20260929/`。

这里只修复静态测试输入/期望来源；当前hook实际执行/审批/原生恢复仍待验，0.144
执行基线传感器未改、未再运行，也未宣称已绿。K547认证阻断和最终统一验收仍开放。

## K594-F1：原生legacy队列夹具准入与权限目录配置

> 状态: fixed（2026-09-29；仅TestKit夹具，未提交；原生业务尚未实跑）

初稿缺dev.blocked显式rework路由、task_map.ready的workflow_run_id/task_map_ref，
完整配置检查或Kernel准入拒绝；补齐Given后真实Kernel派发/hook合同通过。进一步
审查发现default模式不传--add-dir，补充权限合同先检出1 failed，改为restricted＋
私有project allowed_paths后，最终26 passed/22.31s，包含同夹具自然超时/CLI缺口恢复。
证据在`attachments/k594-native-queue-20260929/`，首轮配置错误仅终端输出，不伪造JUnit。
初期两次pytest默认marker导致0执行/exit5，不计覆盖。该修复没有更改生产适配器、
认证、共享Provider配置或放宽sandbox；原生1 skipped不能算通过。

既有ZF-TEST-048在当前Codex再次复现：`codex -a untrusted -s workspace-write --help`
退出2，错误为`invalid value 'untrusted'`，允许值仅`on-request, never`。本轮未修复
生产048；新host节点在认证后还会执行argv前置，拒绝时不启动runtime。后续先调研
支持的受限权限映射，再修复这个目标阻断；不得改为bypass使测试通过。

## K590-F1/F2：guest panic 探针漏验 QMP 与 UUID 参数错误

> 状态: fixed（2026-09-28；仅 TestKit 夹具，未提交；不占生产 ZF 编号）

F1：首轮 QMP 线程向 `socket.connect` 传入 Path 导致 TypeError，旧判定仅看串口与
QEMU 退出，错误输出 pass；宽泛的 `panic` 子串也可能匹配 kernel command line。
原报告位于 `attachments/k590-panic-20260928/invalid-first/panic-probe.json`，保持原字节，
**其 pass 无效，不计能力/恢复验收**。现在改用单线程、有界 QMP 采集，并要求握手确认、
匹配 nonce/guest identity、精确 SysRq panic 文本、唯一 GUEST_PANICKED、guest-panic
SHUTDOWN 和 owned cleanup；缺证、错误、畸形回包均拒绝。

F2：第二轮 `-uuid` 传无连字符 hex，QEMU 拒绝启动；原件在
`attachments/k590-panic-20260928/uuid-failure/`，已改为标准 UUID 字符串。没有触发 guest
业务，也不是 ZaoFu 生产失败。最终完整探针约 11.156s，QMP 两事件/串口/清理/镜像摘要
成立；直接与前序 VM/S0/caller 合同共 41 passed。K591 随后以独立 acceptance 来源完成真实
在途 Run 的 guest panic→同盘 full-start 恢复：host 1 passed、VM verdict=pass、两 boot、
cleanup_ok=true；该结果不关闭物理掉电/其他 OS crash，也未发现新的 ZaoFu 生产 bug。

进度报告纠正：本会话此前把 10/31 称为“主干完成 32.3%”是错误口径，现撤回。
31 行审计混合能力、历史红灯和取证条目，且并非全部需求；不得用它作总体完成分母。

## K586–K589：历史测试证据丢失与本轮来源参数纠正

> 状态: partially-rebuilt（2026-09-28；非生产bug。K565两项新来源已补齐，其余14份原报告仍缺失）

库存`attachments/evidence-inventory-k586-20260928.json`及原始SHA扫描
`attachments/evidence-search-k586-20260928.sha256`在明确范围内未找到16份旧报告。
Claude命令不可用（既有软链接目标缺失），Codex认证状态Not logged in；不将宿主环境
问题归因为ZaoFu生产bug，不修改共享安装/认证，不拿scripted worker代替原生证明。

本轮操作偏差已纠正（非代码修复）：默认S4误跑中断、不存在的host profile被拒绝、
backend来源报告不能满足原acceptance要求。原始输出/拒绝审计保留；没有补签历史来源，
没有降低审计字段。正确补验从清单直接读取nodeids/source_profile后完成。

有效恢复证据：`attachments/k587-k565-host-acceptance-20260928/testkit-run.json`
3 passed/238.911067s，37个登记PID全退出；
`attachments/k589-k565-closeout-20260928/testkit-run.json`原127节点passed/227.307829s。
两报告acceptance来源一致，统一审计相应两条pass；这不是找回历史原件。
当前审计10 pass/1 fail/6 missing/14 harness_failure，仍不授权发布；详见steps K587–K589。

## K584-R：PRD正式重启的judge-prd shell握手超时

> 状态: open（2026-09-28；与K580启动握手同签名，根因未定，不提前认定认证或业务错误）

`attachments/browser-prd-k584-20260928/testkit-run.json`：1 failed/292.654408s，
报告SHA `4dad2cb61ed8251a1d567b438c8040ebd46940f545d948f19a9aaaf446beb9bd`。
业务Run在请求后98.596秒完成；随后正式restart的`runtime-2.log`记录
`judge-prd provider readiness failed: failure_class=shell_handshake_timeout, current_command=bash`。
测试使用真实CLI/tmux/Docker与scripted worker，不是原生Claude认证测试。
cleanup_ok=true，18个登记PID退出，精确session不存在；不能以业务已完成把整条验收改绿。

局部源码核对：`TmuxReadinessMixin._wait_for_shell_handshake`要求新nonce实际出现在
capture里；错误对象已有屏幕/进程诊断属性，但`src/zf/cli/start.py`启动异常分支只输出
简短异常，随即kill-session，重启失败现场屏幕没有持久化。当前证据不足以区分shell
未执行、capture/归属失败或3秒窗口内宿主调度开销；不扩大预算或自动重试。
下一步K585先补失败观测及确定性调用方合同，再定位最小阻断；未修复，不标fixed。

K585补记：**诊断缺口K585-D fixed（仅TestKit，未提交）**，显式计时入口增加握手、
send/capture、诊断标量及nonce观测，不记录命令/屏幕正文，不改变生产代码或预算。
新增合同2红→2绿，完整直接14 passed，含审计/transport/CLI清理前序联调72 passed。
单次PRD诊断复验`attachments/browser-prd-k585-20260928/testkit-run.json`为
1 passed/219.794489s，正式restart成功，16次握手0.631–1.712秒、34 PID全退出。
报告SHA `dfd5cdc6d44aa1f63a7a6b3d679603c15fadbc0a9895fd901a65e2eabb3aada2`。
K584-R仍open：这次未复现不代表根因修复，不擅自归因为Claude登录或提高3秒预算。
后续同签名再现时用该诊断，不为关闭旧问题循环重跑绿色PRD。

同轮双lane`attachments/browser-two-lane-k584-20260928/testkit-run.json`已1 passed，
正式restart/cleanup通过，50个登记PID退出；只关闭该复验子项，不证明握手稳定性或
K557-F2整体关闭。统一审计7 pass/2 fail/6 missing/16 harness_failure，仍不授权发布。

## K576：VM历史证据未持久归档，当前审计无法回读

> 状态: fixed-by-new-evidence（2026-09-28 K577；新来源可回读已修复，历史原件未恢复，不占生产ZF编号）

K571-F6的历史host report、vm-run、payload及私有磁盘路径当前均未找到，attachments
中也没有相应原始副本。历史通过文字和SHA不等于当前证据可回读；不能据此撤销111/112/F6
的历史修复记录，也不能据此把D4标完成或声称磁盘已安全清理。K576随后从固定官方来源
重建了私有工具、镜像和payload，并保留了一次新的失败尝试。

核查与恢复条件见[清单](../attachments/vm-evidence-inventory-k576-20260928.json)：KVM权限/
资源满足门槛；私有QEMU/OVMF/固定镜像/payload已重建。新来源的统一审计将
`K571-owned-vm-recovery`标为fail：首轮workflow start --apply在既有30秒命令预算内
未完成，尚未进入SIGKILL/第二boot/恢复。因此这不是生产恢复失败，而是当前离线
payload/启动前置的待查阻断；原始host、串口、overlay、supervisor和payload provenance
均在`attachments/vm-k576-20260928/`。
关闭条件：恢复可校验的原始副本，或用新来源执行既有单条owned-QEMU同盘恢复；完整
报告/source/phase/JUnit/serial/事件前缀/payload与磁盘出处立即归档、校验并回接审计。
K577已满足上述新来源关闭条件：`attachments/vm-k577-20260928/host-acceptance/testkit-run.json`
为1 passed/200.714213s，source stable；两boot、SIGKILL、同盘恢复、终态与幂等通过，
完整磁盘/ISO/serial/报告/来源归档。统一审计VM条目pass，总体仍有4 fail/6 missing/
16 harness_failure；不补写旧证据，不放宽预算，没有实施生产修复。

### K576-T：首次workflow准入偶发30秒超时

> 状态: open（K577两次未复现；原因未定，不宣称已修复）

旧账本停在operator.action.proposed，旧payload没有保留超时子进程输出。K577已修复测试
诊断缺口：faulthandler及TimeoutExpired输出留存，不改变argv/退出码/业务预算，43项合同
通过。两次新运行准入与恢复均成功，但不能证明历史超时根因；tmux无server也出现在
成功运行的清理期。后续同签名再现时使用admission.log定位，不为追绿重复运行或新增矩阵。

## TestKit 归档 premerge 报告回读误判

> 状态: fixed（2026-09-28；测试框架问题，不占生产 ZF 编号）

`_bound_premerge` 原先把报告中包含的临时绝对 JUnit 路径与归档后的目录强比较，导致
真实的 premerge 报告在归档回读时被错误标为 `harness_failure`。修复后只校验完整脚本、
参数形态和固定的 `premerge-junit.xml` 文件名；新增归档后回读及篡改拒绝回归。

验证：定向回归 7 passed；归档后仍正确保留 47 passed / 4 ZF-TEST-040 failed，
篡改脚本/选例/文件名/摘要/路径/伪造 verdict 均 fail-closed。该项不代表 ZF-TEST-040
结构债务已修复。

## K571-F6：整机恢复测试的首次worker收据未同步，重启后变空

> 状态: fixed（2026-09-26 K571-F6；测试取证问题，不占生产ZF编号）

tests/conformance/test_zaofu_testkit_s4_isolated_vm_recovery_host.py
tests/testkit/test_tmux_general_worker.py

`/tmp/zf-k571-f-host-20260925-v1/testkit-run.json` 1 failed/195.024108s；正式
恢复已越过verify、Task done和delivery audit，随后scenario第165行读取收据JSON失败。
原盘诊断副本journal replay后只读debugfs确认`evt-a1a62de8eea2.json`长度0；
它是checkpoint中原始scope的收据，其余五份恢复后收据均非空。证据根
`/tmp/zf-k571-run-20260925-2kpzgg5z-evidence/`；原overlay不修改。

原因：tmux_general_worker用仅replace、不fsync的write_json_atomic记录收据。
修复限定worker的两次收据发布，改用atomic_write_text的flush/fsync后replace；
不改全局证据writer，不同步业务账本、不补写损坏收据、不放宽六收据断言。

修复与验收：F6专项6项红测→6项通过；`tests/testkit/test_tmux_general_worker.py`
完整合同 **33 passed**。来源绑定联调 `/tmp/zf-k571-f6-impact-20260925-v2/testkit-run.json`
为 **92 passed**，无 collection/evidence error，source stable。新载荷
`/tmp/zf-k571-payload-20260925-f6` 共6377项，manifest SHA
`8ce3f9c2d6dc3ed27e958032120b1549d2d1d8969e3ce56f758d00d623c0eba5`；真实同盘 VM
`/tmp/zf-k571-f6-host-20260925/testkit-run.json` **1 passed/209.523896s**。
两次boot_id不同，首轮自有QEMU SIGKILL后同overlay恢复；六份收据完整、5份含提交，
唯一 `run.goal.completed`、Task done、delivery audit passed，重复startup幂等通过，
`vm-run.json` 的 `verdict=pass/cleanup_ok=true`，两QEMU均已清理。仅关闭这条
worker收据持久发布缺口及其真实覆盖路径，不宣称目录fsync、物理掉电或整体目标完成。

## ZF-TEST-112：整机中断后工作流输入变空，恢复链在必读证据门禁阻断

> 状态: fixed（2026-09-25 K571-F；四类原始输入原子同步发布已修复并真实VM复验，未提交；整例因测试收据K571-F6仍失败）

tests/conformance/test_zaofu_testkit_s4_isolated_vm_recovery_host.py::test_owned_vm_inflight_run_recovers_through_full_cli_start

**证据：** `/tmp/zf-k571-e-host-20260925-v1/testkit-run.json` 为1 failed/232.302156s，
source stable、session完整、无runner超时/证据错误；业务100秒等不到终态。对应
`/tmp/zf-k571-run-20260925-27ko1o3u-evidence/` 的第二boot串口证明正式CLI启动；
恢复账本已有scope、collect-1、collect-2、synthesize四个完成事件、五个结果准入，
随后verify进程在`GeneralRoute.result → validate_required_reads`失败，三次watchdog
替换仍失败并打开respawn circuit。此处不是认证缺失，也不能用增加等待预算解决。

首次scope source manifest中四项输入有非空SHA；恢复后verify manifest和read ledger
记录同四项为empty SHA `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`、
`returned_bytes=0`，而required_reads要求`min_returned_bytes=1`：

- `docs/intake/workflow-851ba1467aced787.md`
- `artifacts/intake/workflow-851ba1467aced787.json`
- `artifacts/workflow/workflow-851ba1467aced787/skill-adapter-plan.json`
- `artifacts/workflow/workflow-851ba1467aced787/workflow-input-manifest.json`

`workflow_intake.build_flow_intake` 在发现时对这四类文件直接write_text，无文件fsync。
`artifact_read_ledger._copy_attempt_input` 对各attempt的副本有fsync，但后续stage重新
从项目路径构造输入，不能把早先副本耐久性当原始输入耐久性。已确认跨重启内容丢失
和门禁拒绝；最小修复仍需红测核对正式intake caller、发布次序及出错传播。
原`ZF-TEST-111`活动账本前缀丢失本轮已消失，不把112并入111冒充修复失败或通过。

取证：保留原overlay和解码raw；第二次host清理同样是进程退出，未经日志重放的ext4
直接debugfs出现bitmap checksum错误。只在单独`guestfs-journal-replayed.raw`副本
执行`e2fsck -p -E journal_only`，随后只读debugfs导出文件，未改原overlay或运行态。
`read-ledger-diagnostic/`保留verify manifest/两份ledger；`testkit-worker-processes/`
保留四个verifier完整traceback；首次manifest在`first-source-manifest.json`。
rdump尝试保留guest root属主被拒，但字节已导出；不提权。该诊断不充当来宾验收。

**K571-F修复：** 四处write_text改用已有atomic_write_text，文件flush/fsync后replace；
测试在正式CLI发布workflow.intake.created前核对四个文件实际inode及完整字节已同步。
四文件分别注入fsync EIO/replace ENOSPC，均传播异常、保留旧目标、不注册请求。
10例原实现全红，修复后全绿；见test_workflow_intake_durability.py。

新payload为/tmp/zf-k571-payload-20260925-f1，manifest SHA
ef3f1d691a7136333868cccbfcf4984957672e6d9c4ed0defe28ac79e835bbbf。
真实VM报告/tmp/zf-k571-f-host-20260925-v1/testkit-run.json为1 failed/195.024108s，
失败已推进到交付后读取测试收据，而非required_reads。原盘诊断导出四输入长度
655/1051/826/1978 bytes；恢复账本含scope/两collect/synthesize/verify五个完成、
6个result准入、唯一run.goal.completed；122822-byte原前缀保持。测试不补写业务
文件、不放宽门禁、未提高预算。K571-F6首次worker测试收据变空另记，D3/D4仍open。
仅关闭本项四文件发布缺口，不承诺目录项fsync、四文件整体事务、其他kind草稿或物理掉电。

tests/test_workflow_intake_durability.py

## ZF-TEST-111：EventLog 在整机 SIGKILL 后丢失已观察账本前缀

合并补记（2026-09-30）：仅活动账本 flush/fsync 的通用主干修复已选择性移入 feature，
不涉及 `/issue-merge`/Triage 行为。feature 原调用方回归 3 failed；移植后日志/归档/
投影/签名/索引与模拟重启影响闭包 74 passed。未重跑真实 VM，不能把 v7 历史 VM
证据当作 feature 新验收；整仓预合并仍 45 passed / 6 failed，未提交或重启服务。
本次证据：`/home/uisee/zaofu-merge-backups/testkit-core-UI19YheE/111-before.xml`、
同目录 `111-impact.xml`、`premerge.xml`。

> 状态: fixed（2026-09-25 K571-E；活动账本文件同步已最小修复并真实VM复验，未提交；后续112亦fixed，当前测试收据F6仍open）

tests/conformance/test_zaofu_testkit_s4_isolated_vm_recovery_host.py::test_owned_vm_inflight_run_recovers_through_full_cli_start
tests/testkit/test_isolated_vm_guest.py::test_runtime_restore_does_not_bootstrap_or_modify_canonical_bytes
tests/test_event_log_durability.py

**原影响范围：** ZaoFu 生产 `EventLog.append()` 原先只写入文件上下文并关闭，close 会刷新
Python 缓冲，但没有对 `events.jsonl` 显式 fsync。K571 使用无网 Ubuntu 来宾、同一 qcow2 overlay、
`cache=none`，在正式 `workflow start --apply` 已产生真实 `workflow_run_id`、
`task.attempt.started` 和未提交 scope receipt 后，仅对本轮 owned QEMU 发送 SIGKILL。

**复现证据：** `/tmp/zf-k571-d2b-host-20260925-v3/testkit-run.json` 为 1 个 host case
失败，来源稳定、session 完整、清理成功；firstboot 已实际进入在途 Run，第二 boot
`boot_id` 改变。中断前 `events.jsonl` checkpoint 为 122822 bytes、77 行，SHA-256
`74685eaf1bc636f888b52b9183024fb94ebff40279ff84cf3af8b815d32e4bc0`；恢复后读回文件
仅 32768 bytes，前 31099 bytes 相同，之后为 1669 个 NUL，账本前缀不再保持，导致
恢复入口在 `restore_runtime()` fail-closed。独立 sidecar checkpoint 使用 guest 原子
写入且可读，两个 QEMU PID 均已清理。

**判定：** 这是生产持久性/恢复边界问题，不是夹具失败；测试保留该红灯，不额外在故障前
调用 sync/fsync 求绿。若要关闭 K571-D3，需要产品明确并实现 EventLog 的耐久性合同，
同时补充性能/批量写入影响评估；在此之前不得把 OS-crash/整机恢复标为通过。

边界：故障为 QEMU 进程 SIGKILL，不是物理宿主断电或 guest panic。恢复前缀断言在
第二次正式 `zf start` **之前**失败，尚未观察生产启动如何处理这一损坏状态；不能将
它表述为“CLI恢复已执行失败”。已有 EventLog 合同不等于 fsync 耐久承诺，本项是当前
整机恢复目标发现的产品缺口，修复方案仍需核对 Store/sidecar/append/rotation 的一致性。

原始证据根：`/tmp/zf-k571-run-20260925-s_q70glq-evidence/`。关机后通过
qemu-img 只读解码私有 overlay 的 GPT root 分区，用 debugfs（无 `-w`）导出
`guest-checkpoint.json` 和 `guest-events.jsonl`；检查点文件 SHA 与 firstboot 串口一致。
后者 SHA `5c2ff0ffdc3278a12e22cfce535294112b15a65135aa7d570ca8b93bb403480c`，
检查点 SHA `0301568c8c3fc1fd1825447b965d40ebf7cc68013aba1344939c2c36953664a8`。
没有挂载宿主块设备、恢复运行态、补写事件或修改生产源码。此问题阻断 K571-D3，下一步
先研究最小产品耐久性修复，授权沿用用户既有阻断修复范围，不改测试预期求绿。

**K571-E修复与复验：** 以上为发现时历史。现`EventLog.append`在已有锁内写完整行后
flush/fsync，再允许index和EventWriter projectors；EIO/ENOSPC直接传播、无截断补写。
`/tmp/zf-k571-e-ledger-red-20260925.xml`三红，修复后green三绿；影响闭包236 passed/
1既有ZF-TEST-001 xfailed，premerge仍47 passed/4既有040 failed。实现边界见
`docs/impl/event-ledger-append-durability.md`。

新真实VM报告`/tmp/zf-k571-e-host-20260925-v1/testkit-run.json`整例仍fail（112），但
第二次full CLI start前的原前缀断言通过；独立导出再次核对122822 bytes的SHA为
`1c1d6b51e9004a67ad27b9b3be8fb1e924af0392aa6795d28f91ec3f88d4827a`，与首次
checkpoint和串口一致。恢复后账本511842 bytes/256行；原operation出现新的attempt/
retry-1，旧attempt被superseded。两个QEMU PID2413670/2413971已消失。
没有测试侧sync或提高预算。只关闭本项活动文件数据丢失，不承诺目录项/轮转事务、
全部sidecar、物理掉电或完整D3终态/幂等已通过；每append同步的性能代价仍须后续主干验证。

## K571-F4/F5：无网来宾启动等待与测试checkpoint丢失

> 状态: fixed（2026-09-25；F4/F5 已修复，生产111仍open；仅测试夹具，未提交）

tests/testkit/test_isolated_vm_run.py::test_no_network_seed_declares_only_an_optional_wildcard_link
tests/testkit/test_isolated_vm_guest.py::test_restore_succeeds_once_without_repeating_firstboot
tests/conformance/test_zaofu_testkit_s4_isolated_vm_recovery_host.py

- F4：空 `ethernets` 没有触发 netplan 的 networkd optional override，首轮约120秒
  wait-online；主线程在168.062s主动SIGINT，非自然超时、非绿色。核对 netplan1.1
  `generate.c` 的 any_networkd 与 `networkd.c::_netplan_networkd_write_wait_online`，
  改为不匹配实际NIC的 optional `en*`、关闭DHCP，QEMU仍 `-nic none`；v2/v3来宾
  约11–13秒进入network stage，实际接口只有lo。没有提高预算或启用外网。
- F5：v2 firstboot 真实进入在途Run，SIGKILL后测试checkpoint普通write_text为空。
  `tests.testkit.evidence.write_json_atomic` 只有replace、并不fsync，因此最终采用
  guest专属临时sidecar的fsync→replace；没有更改业务EventLog或全盘sync。
  v3同盘重启可读且检查点SHA与故障前串口一致，随后真实检出生产111，故F5可关闭，
  不能把其修复当111修复。文件fsync本身也不外推目录/硬件掉电耐久保证。

## K571-F1/F2/F3：隔离VM校准seed、项目解释器和监督器中断清理

> 状态: F1/F2/F3 fixed（2026-09-25；F3经新执行器、独立负向及显式QEMU caller复验；仅测试夹具，非ZaoFu生产Issue，未提交）

- F1：首轮Linux没有枚举CIDATA CD-ROM，cloud-init未运行；官方NoCloud按标签识别、
  不要求sr0，改第二只读virtio block后真实来宾读到nonce，两次启动通过。具体SATA
  未枚举根因未证明，不宣称修复QEMU或guest kernel。
- F2：第二候选从/tmp直接`uv run`选错解释器而缺yaml，未启动QEMU；固定
  `uv run --project /home/uisee/zaofu_v7`，第三轮成功。旧失败报告保留。
- F3：首轮监督器被SIGTERM终止退出143，没有进入finally，owned QEMU短时残留；
  已经私有QMP quit清理且PID2375250消失。历史补handler未验证的状态保留；K571-C现已
  改为handler只记flag、直接Popen幂等回收及父死亡exec guard，11项独立合同、127项
  自动影响闭包、15项来源绑定前序联调通过。显式暂停/无磁盘QEMU监督器SIGTERM也通过，
  **F3已修复**，不再用正常两轮cleanup=true替代异常证明。父SIGKILL无final报告仍非绿。

tests/testkit/test_isolated_vm_supervisor.py
tests/conformance/test_zaofu_testkit_s4_isolated_vm_host.py

复验：`/tmp/zf-k571-c-devverify-20260925.json`，
`/tmp/zf-k571-c-host-bound-20260925/testkit-run.json`（1 passed/source stable），
`/tmp/zf-k571-c-closeout-20260925/testkit-run.json`（15 passed/source stable）。真实QEMU
PID2386896已退出，host与closeout共同来源完全一致；详细范围和诊断误运行见steps K571-C。

证据根`/tmp/zf-k571-vm-20260925-WGNTXb/`：`calibration/serial-1.log`、
`calibration-report.json`（F2红），`calibration3-report.json`（基础校准pass/260.730324s）。
没有生产代码修改；仅监督器CLI/host TestKit已接入，正式ZaoFu在途恢复与其最终TestKit
验收仍待完成；guest panic/物理掉电未实施，不因F3修复而关闭。

## K569-F1/F2：批次恢复测试夹具与S4登记遗漏

> 状态: fixed（2026-09-25；F1属性名已修正且两场景通过，F2矩阵已补齐；最终定点复验见steps K569；非生产Issue，未提交）

tests/conformance/test_zaofu_testkit_s4_watcher_batch_recovery.py
tests/conformance/test_zaofu_testkit_s0.py::test_stage_registry_and_coverage_matrix_have_the_same_test_surface

F1首轮`/tmp/zf-k569-batch-first-20260925.xml`两失败，夹具误写`EventWriter.log`，
对照生产API改为`event_log`；第二轮两例通过。不是生产批次恢复失败。
F2自动八文件影响闭包171 passed/1 failed/70.22s，新测试已进SUITES但漏进S4覆盖矩阵，
S0按预期拒绝；补齐精确测试路径，不改门禁。没有因此新增生产bug或改变K557-F2状态。
最终`/tmp/zf-k569-batch-final-20260925/testkit-run.json`两恢复例+失败S0节点
3 passed/2.620044s，source stable；确认异常发生于真实CLI batch caller的ack seam，
不是只在测试回调入口抛错。完整八文件初轮红灯保持原结果，没有声称重新全部通过。

## K567-F2：工作循环中断时JUnit未保留生命周期属性

> 状态: fixed（2026-09-25；已修复，真实超限中断清理证据已独立回读；非生产Issue，未提交）

tests/conformance/test_zaofu_testkit_s4_workloop_host.py::test_sustained_task_pipeline_releases_owned_resources
tests/test_testkit_runner.py::test_runner_capture_guard_is_fail_closed_on_overflow

首轮`/tmp/zf-k567-guard-overflow-host-20260925/run/testkit-run.json`为预期harness_failure/
exit2，但KeyboardInterrupt未生成call报告，JUnit properties为空；不能据进程退出或
捕获已压缩推定tmux/worker/FD清理。仅在Memray显式模式把同一record_property内容
原子同步到独立capture/lifecycle.json；不补造pytest阶段成功。Guard经runner直接持有
的pytest Popen发送SIGINT，保留清理宽限和强制升级；吞掉中断返回0也不能转绿。

复验`/tmp/zf-k567-guard-overflow-host-20260925-r2/run/testkit-run.json`仍为预期非绿，
53次采样/峰值69128192 bytes，未强杀；sidecar证明6PID/tmux/state清理、FD12→14，
SHA22044692448aad77414f73b256cd85c00eebfb58f8c3fa6f7d9b817e8b24f34a。
随后32Task超出2GiB捕获阈值，7done/1in_progress/24backlog，31PID/root清理证据同样
保留；这不是长测通过，也不是ZaoFu生产bug修复。详见steps K567-D。

## K567-F1：连续追踪器尚未关闭时误用最终FD门槛

> 状态: fixed（2026-09-25；已修复，完整2Task生命周期与原tracemalloc对照通过；非生产Issue，未提交）

tests/conformance/test_zaofu_testkit_s4_workloop_host.py::test_sustained_task_pipeline_releases_owned_resources
tests/testkit/test_memray_observer.py::test_memray_observer_records_retention_and_release

首轮`/tmp/zf-k567-workloop-memray-lifecycle-20260925-r1-outer.xml`保留真实红灯：
Memray单Tracker尚要覆盖GC/weakref终态时，FD16高于启动前12+2；误把追踪期间当最终
关闭后的观察点。独立探针确认Tracker开启时capture/status/pipe两端共新增4FD，关闭后
剩2个pipe FD。修正为运行期继续使用原+8，关闭后保留原+2及socket不变检查；ExitStack
保证失败路径也先关闭Tracker并验FD，再清理临时状态。不提高最终阈值、不改生产代码。

来源绑定复验`/tmp/zf-k567-workloop-memray-lifecycle-20260925-r2/run/testkit-run.json`
1 passed/63.672694s、exit0/source stable；FD12→16→14，8窗口、四阶段weakref与5文件
SHA通过，9个worker PID/root清理。原tracemalloc对照1 passed/50.084832s；sensor/runner
73 passed/56.72s。32Task/PERF-05仍未完成；此修复不是ZaoFu内存泄漏修复。

## K566-F1：JUnit 审计误拆参数中的节点分隔符

> 状态: fixed（2026-09-25；已修复，真实子pytest六项合同通过；非生产Issue，未提交）

tests/testkit/test_requirement_audit.py::test_audit_binds_real_junit_with_node_separators_inside_parameters

`/tmp/zf-k566-audit-20260925/requirement-audit.json`保留首次红灯：K565的127项收尾
报告在JUnit节点审计中被拒绝。直接按`::`拆nodeid，误把参数中的
`[TestClaudeRespawn::test_missing_probe]`当作类层级；原127项测试并未失败。
已核对本地pytest的mangle_test_address/record_testreport：先隔离`[`之后的参数，
只对节点前缀拆层级，最后原样接回参数。TestKit据此最小修正，不修改旧附件/摘要，
不删除对应需求，不忽略参数或放宽完整节点集合校验。
红 `/tmp/zf-k566-junit-identity-red-20260925.xml`：2 failed/4 passed；
绿 `/tmp/zf-k566-junit-identity-green-20260925.xml`：6 passed/7.30s。
覆盖真实子pytest的function/class与含`::`、嵌套方括号参数；更改参数或classname并
重新计算附件摘要仍必须拒绝。调用方与完整主干后续联调结果见steps K566。

## ZF-TEST-110：fanout 恢复交接已完成，丢派发扫描仍重复重派

> 状态: fixed（2026-09-25；已修复，508项影响闭包与3项真实host通过；未提交）

tests/test_fanout_recovery_handoff.py::test_successful_recovery_handoff_is_not_redispatched
tests/test_fanout_recovery_handoff.py::test_unrelated_or_failed_handoff_does_not_hide_dispatch_loss
tests/conformance/test_zaofu_testkit_s4_watcher_takeover_host.py::test_worker_sigkill_respawn_preserves_same_run_terminal_evidence

真实红 `/tmp/zf-k565-worker-kill-second-20260925.xml`（1 failed/51.72s）：
owned scoper SIGKILL后watchdog重启并重送原briefing；丢派发扫描仍将同一次launch
判为dispatch_lost，再派retry-1。原briefing提交成功后，新输入重复消费capability，
replacement以duplicate_submit退出，并触发第二次watchdog恢复。测试在owner投影尚
未完成时先报pending，掩住后续收据断言；原始账本/进程记录仍完整保留。

确定性复现不依赖活性/时间竞态：实际run_once创建reader或synth fanout，生产
_inject_fanout_recovery_briefing成功重送后，下一轮及重建后均多派发。
`/tmp/zf-k565-handoff-red-corrected-20260925.xml`：4 failed/24 passed/4.41s。
原始首轮的另外12个synth负例KeyError是测试装配问题，不算生产故障。

修复候选：两个扫描入口共同检查launch后的精确恢复交接，仅匹配当前dispatch的
role/fanout/child/run/stage/briefing/trace时抑制重复retry。legacy synth的空child_id
保持原协议。不同身份/发送失败/旧交接/后续launch/turn关闭仍重派，不更改结果
准入、watchdog频率、timeout或预算。源模块fanout_dispatch_liveness，reader与synth
真实caller均接入；说明见docs/impl/worker-respawn-result-contract.md。
直接与K564前序watchdog/worker、context recycle及General正常链联调：
`/tmp/zf-k565-direct-callers-20260925.xml` **104 passed/36.25s**。
后续影响闭包 `/tmp/zf-k565-impact-20260925.xml` **508 passed/169.87s**；mock E2E
10 passed/9.74s。真实来源绑定 `/tmp/zf-k565-host-bound-20260925/testkit-run.json`
**3 passed/222.704001s**，worker SIGKILL及两种既有watcher接管均通过，source stable。
SIGKILL后仅一次runner.failed/recovery.injected/respawned、零dispatch_lost、五份
成功worker收据、六份准入、同Run终态与本地交付；没有worker异常退出记录。
三个case的12/13/12个登记PID全部消失、tmux session均退出，初始进程记录字节和
全部briefing摘要独立回读通过。四份source/phase/JUnit附件摘要已核验。
正式premerge仍47 passed/4 failed（ZF-TEST-040）；fanout现8649行，本次4行接线
叠加原来超cap，不隐瞒增量、不抬阈值；其余三红仍既有结构债。没有宣称发布门禁通过。
K530历史首次死亡原因仍open；本项只定位其后续恢复重叠，不反推原始退出原因。

## K565-F1/F2：worker选择和交付投影等待的测试夹具缺口

> 状态: fixed（2026-09-25；已修正并通过同节点真实host；非生产Issue）

tests/conformance/test_zaofu_testkit_s4_watcher_takeover_host.py::test_worker_sigkill_respawn_preserves_same_run_terminal_evidence

F1：原型`/tmp/zf-k565-worker-kill-red-20250925.xml`误取所有角色最新进程，把verifier
当作scoper replacement。改为同角色、新PID、启动时间晚于原进程且精确stdin收据匹配。
日期写错的原证据名保留，不重命名。F2：第二轮terminal到owner request有2.56秒延迟，
夹具未等待同terminal.id的owner请求就审计交付；现增加有界等待，不扩大业务预算。
仍要求五份成功提交、同Run/operation准入、单次故障/恢复且无duplicate_submit，
不能用这两项夹具修正掩盖ZF-TEST-110生产红灯。
复验：`/tmp/zf-k565-host-bound-20260925`的同节点call耗时56.966s，完整setup/call/
teardown通过；按role/PID/启动时间取得真实replacement，绑定terminal.id后本地交付
审计通过，所有严格收据/准入/单次故障恢复断言保留。原型与第二轮失败未覆盖。

## K557-F2：Refactor live 浏览器终态耗时超过 150 秒等待预算

> 状态: open（2026-09-25；宿主/测试预算/生产慢路径归因未闭合，不宣称生产缺陷已定位）

### 2026-09-28 K583-P 双窗口查询子问题 fixed，K557-F2稳定性仍 open

K583-P（2026-09-28）：watchdog重复客户端开销已修复（未提交），正常非node的
同次观测2→1客户端，120/200分别执行capture、各自owner guard，分帧碰撞/错误回原路。
直接131 passed；含Composite/stuck/lifecycle/recovery影响闭包199 passed（含直接集，
不相加）；逐wake/批次恢复/mock E2E/context/layout51 passed/1 host deselected；
显式隔离host4 passed/18 deselected。premerge47 passed/4既有040失败，未调阈值。
原150/480秒 strong 单次 **1 passed/294.881411s**，请求后94.885秒出现
`run.goal.completed`，cleanup_ok=true且42个登记PID全退出；当前完整MAINLINE **589 passed**。
这证明本轮候选可通过，不代表稳定性已证明，K557-F2仍 open。

**K582-P：已修复（未提交）**。PaneGrid新鲜owner/PID/command和120行屏幕通过一次
客户端调用取得，状态和capture前分别核对owner；异常回旧resolver，无缓存，独立200行
窗口保留。正常is_alive+capture_log从3次调用降为2次。直接115 passed，扩展138 passed/
1 deselected（有重复选择不计新增覆盖），既有host14 passed，新快照host4 passed，跨
transport/context/mock E2E38 passed，注册与直接收尾102 passed。premerge仍47/4个040红灯。

strong正式复验`attachments/browser-strong-k582-20260928-081413/testkit-run.json`为
**1 failed/248.537658s**，原150秒仍in_progress；parity child +157.030s、judge派发
+159.660s，清理前无最终Run/根Task done。source stable/session_finished/cleanup_ok=true，
报告SHA `cf23bcfbdb2fe2fb72d02ade677dd74f05b56205f86dc44e983743242cba0ece`。
原31条审计现4 pass/4 fail/6 missing/17 harness_failure；旧MAINLINE缺新注册合同而非
发现新的产品失败，不删节点造绿。K557-F2继续open；详细证据见steps K582。

### 2026-09-28 K580 服务端 capture guard 已接线，strong 复验在启动握手阶段失败

候选由K579固定原语推进到实际 `TmuxSession.capture_pane` 调用方：只对安全ASCII
instance和稳定pane id采用服务端新鲜归属guard；拒绝/异常回包走原resolver，保留
冷启动/持久binding失效/即时重发现，无观测缓存、无Watcher合批、无窗口合并。
mock及直接caller138 passed；实际host14 passed；扩展caller171 passed/1 host deselected，
分组有重叠。premerge仍47 passed/4个040结构失败。完整strong复验尚未裁决，
不标K557-F2 fixed。方法与接线见 `docs/impl/worker-respawn-result-contract.md`。

K580 strong 单条复验沿用原150秒Task等待、480秒外层预算和正式CLI/tmux/Docker，报告
`attachments/browser-strong-k580-20260928-072334/testkit-run.json` 为
**1 failed/97.365s**：`plan-critic` 在启动阶段出现
`failure_class=shell_handshake_timeout`，未进入浏览器终态等待。`session_finished=true`、
`cleanup_ok=true`，报告SHA为
`12165574f356000b193b8393dadaf3608511707370128b6ad2b24cf0fd05bcfd`。该失败是启动
前置/宿主握手证据，不等价于K557-F2终态慢路径已修复或已定位；不扩大预算、不自动重试。

### 2026-09-28 K581 timing 复验：启动成功但终态延迟仍复现

沿用原150秒Task等待、480秒外层预算，仅开启既有诊断计时器；完整CLI/tmux/Docker启动
成功，报告 `attachments/browser-strong-k581-timing-20260928-074454/testkit-run.json`
为 **1 failed/300.353514s**。Playwright在150秒仍读取到 `in_progress`，随后
`simulation.done`约169.297s，`cleanup_ok=true`、`session_finished=true`；这次不再是
shell握手失败，因此K557-F2的终态延迟红灯得到一次同预算、同入口复现。

计时证据：84次 `run_once` 合计158.865s，85次 `_capture_logs` 合计103.102s，850次
`capture_log` 27.831s，1765次 `capture_pane` 57.203s，3004次tmux `_run` 102.025s，
16次Watcher poll 112.408s，85次 `_react_to_events` 32.973s；嵌套项不可相加。报告SHA
`916b51ff46f487d013ec88b7981aac8772409ec7aeb6db051abbddf405a16c34`，timing SHA
`1012eb74bda5c1d403050460d7009d875664452b227eeafac79bada8c2ee7558`。该证据确认K580
没有关闭终态慢路径，但尚未单独证明哪一层可安全削减；不扩大预算、不自动重试。

同一归档按精确事件ID求交集（不是新运行）：parity准入
`evt-1c11f49a4522`→child `evt-c5f25f39ee42` 7.736s内，
_capture_logs为6.104s（其中is_alive 3.079s、capture_log 1.661s），react 0.778s；
judge准入 `evt-e440b0738fb0`→child `evt-d60b968db815` 9.024s内，
capture为7.258s（is_alive 3.671s、capture_log 1.971s），react 0.930s。
嵌套项不相加。后续delivery.settled在+159.810s，清理前无run.goal.completed或根Task done；
simulation.done只是测试清理。下一步针对is_alive内状态/120窗口和capture_log的200窗口
查询成本评估等价优化，不缓存存活结果、不合并Watcher wake、不提高预算。

后续定向诊断：readiness/pane操作合同38 passed、15 host deselected；隔离socket实际
PaneGrid/TmuxTransport在10轮50次握手均通过（0.608–2.220秒），未加载完整CLI
worker/workdir栈，不能说明原故障消失。CLI失败日志没有保存异常上的完整readiness
诊断属性；下一步补同入口失败观测，不增加3秒握手或150秒业务预算。该启动失败与
K557-F2旧终态超时分别保留，不标fixed。

### 2026-09-28 K578 重复状态查询已修复，浏览器超时仍open

**K578-P：fixed（已修复，未提交）**仅指PaneGrid已绑定pane的重复归属预查询：
`TmuxSession.pane_process_status`改走layout的一次新鲜owner/PID/command原子快照，
冷启动/失效仍执行原resolver与最终归属检查；没有缓存存活值。正常watchdog一角色
is_alive+capture_log从6条tmux查询减至5条。两个capture窗口仍为-120和-200，
不改变Watcher次数、死亡计数或上下文耗尽检测。探针/布局71 passed，调用方73 passed，
最终探针/真实watchdog调用链/mock E2E39 passed，分组有重叠。
证据：`attachments/k578-probe-contracts-20260928/`，其中首轮1红和中间8红均保留；
延后rediscovery候选的71绿不作为最终实现依据，最终采用原resolver回退并通过39项。

仅复验strong：`attachments/browser-strong-k578-20260928-055640/testkit-run.json`，
SHA `1bac61151f018e18bb40334ec3b82dc456df25d5ae321e5581ad55e02c1ff087`，
**1 failed/270.644196s**，source stable/session finished/cleanup_ok=true；仍为原150秒
Task等待及480秒外层预算，无retry、无skip/flaky，无外层timeout/evidence error。
真实CLI/tmux/Docker+脚本worker，不是原生Provider。803个归档普通文件摘要一致，
21个登记PID、tmux session及容器均独立确认退出。

73次run_once累计160.450s、74次_capture_logs累计115.327s，tmux _run4129次
117.987s；嵌套时间不能相加。verify结果准入+107.985s→child完成+126.795s；
parity结果+145.188s→child+159.043s→aggregate+159.200s；simulation.done+168.438s，
没有run.goal.completed或根Task done。不同运行的总query数不可直接当成提速比例。
优化未解除K557-F2；PRD/two-lane未重跑，旧红保留；premerge仍47 passed/4个040失败。
原timing按上述事件时间窗求交集：verify间隔18.810s里_capture_logs为16.644s（10次），
react为1.104s；parity间隔13.855s里capture为11.737s（8次），react为1.086s。
因此下一候选聚焦观测子进程开销而非重新扫描Web/API；不据此授权降频/合wake。

### 2026-09-28 K579 guarded capture 候选未采用，K557-F2 仍open

隔离 tmux socket 的服务端 `if-shell -F` owner/pane guard 已完成显式 host 可行性探针：
120/200 两个 capture 窗口、owned/foreign-owner/wrong-pane 三类场景共 **6 passed**，
foreign/wrong-pane 的 capture witness 未执行。该结果只证明 tmux 原语可用，不能证明与现有
`PaneGridLayout.resolve()` 的冷启动、binding 失效、pane 替换、即时重发现、畸形输出和错误
回退语义完全等价。为保持 fail-closed 身份边界，未把 guarded capture 接入
`TmuxSession.capture_pane()`，未修改生产代码、Watcher、capture 窗口或预算。

直接影响闭包 `test_tmux_probe_cost.py`、`test_capture_logs_watchdog.py`、Pane 操作和 tmux
lifecycle 共 **90 passed、1 deselected**；K557-F2 的 strong 浏览器红灯不变。后续若重新评估，
必须先补齐上述缺失语义合同；本条不是新的生产缺陷，也不改变既有 `ZF-TEST-068` 等 Issue 状态。
归档见 `attachments/k579-capture-probe-20260928/`，S0当前19项通过；此阶段不增加浏览器
主干通过项，没有新的fixed声明。

### 2026-09-28 K573 当前源码串行观察

六条既有 browser-live 节点在相同来源、原150秒业务等待与480秒外层预算下串行执行，
general/issue/refactor通过；prd/refactor-strong/refactor-two-lane失败，均在浏览器
`expect.poll`读取Task时仍为`in_progress`。无自动重试、无MAINLINE并发、未扩大预算。
PRD在此作为同类症状观察记录，不据此断言与Refactor的根因相同，不新增已定位生产编号。

- PRD：`attachments/browser-prd-20260928-030716/testkit-run.json`，fail/212.237775s。
  UTC invoke 03:07:57.323946（evt-d211ac4e7dea）→Run 03:10:12.568061
  （evt-66571adb8651，135.244115s）→根Task done 03:10:30.344988
  （evt-d1107c373044，153.021042s）；Run→Task间隔17.776927s。
- strong：`attachments/browser-refactor-strong-20260928-032131/testkit-run.json`，
  fail/246.414240s。UTC invoke 03:22:51.271779（evt-2e0d9ced25fd）→Run
  03:25:25.315268（evt-d8a69db31faf，154.043489s）→Task done 03:25:30.812707
  （evt-aef675e35be8，159.540928s）。后台晚到终态不改写浏览器失败。
- two-lane：`attachments/browser-refactor-two-lane-20260928-033000/testkit-run.json`，
  fail/252.297001s。UTC invoke 03:27:57.522408（evt-9cb278784a71），到
  simulation.done 03:30:41.577231（evt-eec7829cefa0）未观察到run.goal.completed。

三条均source stable/session finished/cleanup_ok=true，无外层timeout、collection或
evidence错误，Playwright unexpected=1/skipped=0/flaky=0；browser-truth.errors为空。
完整case-work/events/browser-gate/Playwright JSON、截图及trace已原字节归档；登记PID
及tmux session独立检查均已退出。失败发生在restart步骤前，不宣称restart已通过。
审计`attachments/requirement-audit-k573-20260928/requirement-audit.json`保留三条fail；
本轮只补足可回读证据，不关闭K557-F2，不以延时、降频或过滤事件求绿。

同一批事件的尾部时间线显示：strong最后一份结果准入发生于invoke后131.803s，
fanout child完成在144.264s、Run完成在154.043s；two-lane最后一份结果准入在
144.526s，child完成在157.334s，随后仍需进入下一阶段，158.543s因测试清理停机。
这证明耗时不只发生在浏览器刷新：后端结果准入到聚合、交付到终态均存在间隔。
`orchestrator.decision.recorded.trigger_lag_s`还观察到双lane约40.2秒的延迟，
但这些间隔不是函数耗时剖析，不能单独归因于`_capture_logs`或宣称唯一根因。
下一步应沿具体operation/trigger身份分段核对准入→消费→聚合→Task投影，
不更改业务预算；当前只完成观察，未改生产代码。

### 2026-09-28 K574 当前源码计时复验

仅对 `refactor-strong` 做一次当前源码诊断运行，仍为原150秒浏览器等待、480秒外层
预算、无retry：`attachments/browser-refactor-strong-timing-20260928-041500/`。
结果 fail/224.301147s，source stable/session finished、cleanup_ok=true、无
collection/evidence/外层timeout；Playwright unexpected=1/skipped=0/flaky=0。

本次 `ZF_TESTKIT_RUNTIME_TIMING=1` 的 94 次 `run_once` 累计 wall 153.180s，
其中 `_capture_logs` 112.610s、`_react_to_events` 26.474s、`_dispatch_ready`
1.159s；嵌套项不可相加。`EventWatcher.poll_once` 16次累计114.288s，单次最长
21.312s；`is_alive` 967次69.702s，`capture_log` 946次34.010s，tmux `_run`
6106次111.435s，`capture_pane` 1938次69.051s。它们说明当前强耗时线索仍是每次
wake 的存活/日志观测与逐事件排空，但不是安全可删或可缓存的证明。

事件同源关联：invoke `evt-45b94011ccea`；最后 final-judge child completed
`evt-2445d194f245`（+143.911s）；aggregate `evt-e41848c39c3e`（+143.973s）；
closure `evt-f548b19945dc`（+143.992s）；claim `evt-a53b7b77a777`（+144.162s）；
Run completed `evt-17fc1e088b03`（+149.504s）；根Task done +156.251s。
浏览器仍在150秒窗口内读到`in_progress`，后台完成不能改写失败。

这次计时只证明当前源码的复现与分段线索，未证明 `_capture_logs` 是唯一生产根因，
也未证明合并 watcher wake、降低身份探测或缓存 pane 结果安全。依据现有
`EventWatcher`/`Orchestrator.run_once`/`_capture_logs` 语义和 K569 批次恢复合同，
本轮不改生产代码；K557-F2继续open。

批处理候选还有明确语义风险：`Orchestrator._react_to_events`的`reacted_tasks`
会在同一批次内抑制同Task后续primary handler，而逐条`run_once`会重新建立该集合。
因此不能把“API接受list”当成“替换现有逐wake调用一定等价”。下一步先做确定性
语义对照，不再重复当前计时或用调预算掩盖差异。

已补保护性回归 `tests/test_orchestrator_batch_semantics.py`：真实调用
`Orchestrator._react_to_events`，锁定单批次同Task只执行首个primary、分开wake执行两次；
与CLI start/Watcher batch recovery联调 **48 passed/81.14s**。这是现行语义合同，不是
批处理优化通过证据；K557-F2仍open。

tests/e2e/test_testkit_browser_live_issue.py::test_browser_issue_start_is_driven_by_live_zf_start[refactor]
tests/e2e/test_testkit_browser_live_issue.py::test_browser_issue_start_is_driven_by_live_zf_start[refactor-two-lane]

原默认Refactor报告 `/tmp/zf-k557-refactor-20260925-EUDYZb/run`：1 failed/224.647513s，
Run于invoke后148.094秒完成，Task直到161.572秒才done；浏览器150秒等待失败。
同源码、同断言、同预算独立对照 `/tmp/zf-k557-refactor-serial-20260925-tNri2h/run`
为1 passed/202.340075s，不能覆盖原红。两轮browser来源完全一致。
双lane独立运行 `/tmp/zf-k557-refactor-two-lane-20260925-PsytmA/run` 同样超时：
1 failed/207.378796s；invoke04:16:02.513396Z，最终judge提交04:18:24.193025Z，
本地ship04:18:37.665201Z完成（均UTC），未观察到run.goal.completed即因浏览器失败
进入finally。不得把ship完成改写为Run/Task验收成功。两条红灯均source stable、
cleanup=true，登记PID独立回读全部不存在。无浏览器5xx/pageerror，未证实重复派发。

默认原红与MAINLINE并行，但双lane没有该并行仍失败，因此不能归因为单一并发竞争。
多个阶段的worker提交已落账、Kernel后续聚合仍有延迟；需要进一步量化真实Watcher/
housekeeping/投影耗时及测试轮询干扰。不增预算、不改断言、不自动retry求绿，
根因分层后才决定最小修复。此项是待调查的测试阻断记录，不新造已证实生产bug编号。

K558 仪器运行（原双lane、原150秒预算）已完成：`/tmp/zf-k558-refactor-two-lane-timing-20260925-0WrPxj/run`
为 `1 failed/203.745618s`，source stable、cleanup=true。`run_once` 105 次累计
146.940s wall/39.452s thread CPU；`_capture_logs` 106 次累计 115.846s wall/20.692s
CPU；`_react_to_events` 20.731s；`_dispatch_ready` 0.873s；`poll_once` 112.877s。
这是有仪器的诊断，嵌套累计耗时不可相加，wall 减 thread CPU 也不等于已测出的 I/O。
这只证明 `_capture_logs` 是当前最强耗时线索，不能单独证明生产 bug 或授权降频，因为该
函数还负责 `is_alive`、死亡计数、respawn 和 stuck detector。K557-F2 继续 open。

K559 同预算复核：`/tmp/zf-k559-refactor-two-lane-timing-20260925-KDg3Jk/testkit-run.json`
在原 150 秒 Task 等待下 **1 passed/237.955198s**，source stable、cleanup=true，且
最终 terminal 为 `run.goal.completed`；但 K558 同预算仍为 1 failed。两轮 src/web 摘要
一致，计时器、注册及台账有变化，不是完整同源码对照；这次绿灯不是修复证据。
嵌套计时显示 `_capture_logs` 内 `is_alive` 与 tmux
`_run` 占主要 wall 时间，仍不足以证明可安全缓存/降频；K557-F2 保持 open。

K560 探针级对照：当前每worker存活＋采集观察到9条tmux命令，其中5条身份查询；
K559中12worker×115轮×9=12420，与capture_logs嵌套命令数一致。确定性回包负例
验证resolve后归属变化/消失时禁止读取旧pane，相关131项联调通过。未据数量提出
性能SLA，也未删除身份检查或改调度；K557-F2仍open。

K560 注册后 MAINLINE `/tmp/zf-k560-mainline-20260925-MhruYP/testkit-run.json`
为 **378/378 passed/281.830s**，exit=0、session完整、source stable；探针负例与既有
watchdog/stuck/transport调用方没有引入普通失败。

K562候选后的原预算无仪器复验：默认Refactor
`/tmp/zf-k562-refactor-20260925-5s4onc/testkit-run.json` **1 passed/202.215024s**；
双lane `/tmp/zf-k562-two-lane-20260925-BqBWFg/testkit-run.json` **1 passed/256.269555s**。
两份source相同，restart/cleanup通过，42/50个登记PID全部不存在。invoke→Task done
evidence分别104.621/147.829秒；后者仍接近150秒窗口，单次通过不证明稳定性。
MAINLINE387 passed，S0 19 passed、S9 80 passed；新来源审计15 pass/5 fail/6 missing。
只关闭下面的PID身份快照子问题，K557-F2仍open；原红灯和K559旧绿均保留。

K563完整原预算复验：默认Refactor `/tmp/zf-k563-refactor-20260925-KhX42H/run`
**1 passed/181.430199s**；双lane `/tmp/zf-k563-two-lane-20260925-mKbasm/run`
**1 passed/236.858628s**。invoke→Task done evidence为85.926897/125.424305秒，
invoke→Run为78.435741/120.592867秒。两次完整restart/cleanup通过，42/50个登记PID
独立检查全部不存在，无计时仪器、无retry、未扩大150秒等待。确定性探针的存活＋
采集调用从8降6，身份查询5降4（command复用同次owner/PID，不删除最终校验）。
这些是实际改善观察，不是同负载统计稳定性或唯一根因证明；不继续无界堆小优化来
声称该项fixed。MAINLINE406 passed；当前来源审计17 pass/5 fail/6 missing，
K563四份报告共同scope一致，但全局same_revision_verified=false，K557-F2仍open。

K568优化后有界计时诊断：`/tmp/zf-k568-two-lane-timing-20260925/run/testkit-run.json`
**1 passed/198.480578s**，原150秒业务等待不变，source stable、restart/cleanup通过，
50登记PID均消失。两路verify准入到child.completed仍为13.623337/14.263023秒。
第一路区间经过17次run_once，_capture_logs重叠占11.983582秒；事件快照逐个wake
处理旧批次，随后才接新verify结果。见delivery_route_terminal_research K568的事件ID、
计时/账本SHA与caller；这定位了本次实际延迟路径，不证明可安全批处理或历史唯一根因。
未改生产、未新增fixed声明，K557-F2仍open；不继续无界重复浏览器或仪器开发。

## K563-F1：pane-grid 活性判定独立读取 PID 与前台命令

> 状态: fixed（2026-09-25；同一次身份/PID/command读取与直接caller/host合同通过；不等于K557-F2已修复，未提交）

tests/testkit/test_tmux_probe_cost.py::test_pane_process_status_binds_command_to_owned_pid
tests/testkit/test_tmux_probe_cost.py::test_combined_status_preserves_liveness_verdicts
tests/testkit/test_tmux_probe_cost.py::test_node_process_probe_consumes_owned_snapshot_without_rereading
tests/runtime/test_tmux_pane_ops.py::test_pane_grid_terminate_preserves_sibling_processes

K557-F2阻断路径审查发现，K562只合并owner/PID，活性判定随后还独立读取command；
进程切换时两次观测可能把python判成shell，或把shell/node错判成agent。确定性交错
回包证明该机械缺口，不宣称观察到真实Provider误重启，也不认定它是耗时的唯一根因。
原 `/tmp/zf-k563-process-status-red-20260925.xml` 17 failed保留：其中10个为新快照
接口不存在，7个为实际活性/禁止独立command读取合同失败；不能把17个都叫生产故障。

已修复：pane-grid一次读取pane/owner/PID/current_command，transport实际消费，node
probe继续复用该PID/command；保留resolve归属检查、窗口布局/旧fake接口/dry-run，
shell/context-exhausted/wrapper判定及watchdog频率均不变。ps和日志仍是稍后独立观测，
该快照不授权后续终止。134项影响闭包、141项补充caller、1项真实sibling保护通过；
原预算browser与MAINLINE最终裁决另见steps K563，K557-F2继续open。后续正式MAINLINE
406 passed，两条原预算browser通过；报告均绑定冻结源码。该台账后记不回填历史source。

## K562-F1：pane-grid 最终归属校验与 PID 读取分属两个快照

> 状态: fixed（2026-09-25；已修复同一身份快照合同；不代表K557-F2耗时稳定性已修复，未提交）

tests/testkit/test_tmux_probe_cost.py::test_pane_pid_is_bound_to_same_identity_snapshot
tests/testkit/test_tmux_probe_cost.py::test_pane_owner_change_between_resolution_and_pid_read_is_not_alive
tests/runtime/test_tmux_pane_ops.py::test_pane_grid_terminate_preserves_sibling_processes

为处理K557-F2主干阻断，检验不降频的命令合并边界时发现：旧pane_pid先检查owner，
再独立读取PID，不能保证最终owner与返回PID来自同一tmux回包。确定性回包交错测试
模拟后续独立读取返回另一PID，原实现9个合同例全部失败；这不是已观察到真实误杀
或泄漏的断言。红证据 `/tmp/zf-k562-owned-pid-red-20260925.xml` 保留。

已修复：PaneGridLayout.owned_pane_pid一次查询pane id/owner/PID，严格拒绝错身份、
字段数量/数值异常、stderr和非零返回；TmuxSession.pane_pid实际调用该方法。默认
窗口布局、resolve校验、dry-run和独立terminate权限不变，没有新增缓存或降频。
原合同绿87 passed；影响闭包119 passed、补充直接caller112 passed、隔离host1 passed，
MAINLINE387 passed，原150秒浏览器两条通过，完整S0/S9通过；路径与边界见steps K562。
实现说明：docs/impl/worker-respawn-result-contract.md。只证明快照查询时的归属，
不保证后续进程不退出，不把该子修复冒充K557-F2唯一性能根因。

## ZF-TEST-109：结构停滞重发器重复复制尚未消费的 workflow 入口

> 状态: fixed（2026-09-25；最小生产修复，General browser-live 回归已通过）

tests/test_stall_redispatch.py
tests/test_tick_invoke_backlog_replay.py
tests/e2e/test_testkit_browser_live_runtime.py

General browser-live 在真实 `zf start`/EventWatcher 路径中发现：首次
`workflow.invoke.requested` 已由 Web 受控动作写入，但尚未完成 Kernel admission 时，
结构停滞重发器把它误判为未启动 stage，并在约 0.13 秒后由
`zf-stall-redispatch` 再写入一个同一 `request_id` 的入口事件。结果是浏览器真实账本
出现两个 invoke（同一个 Run，不是两个 Run）。第二个被 operation replay 拒绝重复派发，
业务最终完成，但多余入口阻断“一次启动”的浏览器验收；没有证据证明双派发。

根因是 workflow 入口本身已有 tick_services 的 verdict-less replay（60 秒宽限），不应
进入 stage-local structural stall redispatch。最小修复为：`workflow.invoke.requested`
不参与结构停滞检测；这样不会进入重发/错误升级分支。其它 stage-local trigger 保持
原有有界重发策略。恢复仍经 `_replay_unconsumed_invokes` 把原事件交回真实 admission，
不制造新 ID。
原始红证据保留于 `/tmp/zf-k557-general-20260925-9ntrGZ/run`，不覆盖。

新增四例在修复前为3 failed/1 passed，`/tmp/zf-k557-109-red-20260925.xml`；
修复后原四模块及新增回归为57 passed/4.17s，`/tmp/zf-k557-109-green-20260925.xml`。
直接 caller 证明新入口不提前重发/升级；漏消费的合法入口在61秒的显式时钟参数后
经真实 Orchestrator 准入并派发两个 reader，只产生一个fanout；非法pattern真实拒绝；
122秒再次恢复不重放已裁决事件。未替换生产 handler/准入，也没有真实等待61秒。
初稿测试缺flow_kind导致IndexError属夹具错误，未作为生产复现；独立红回归已纠正输入。
实施文档：`docs/impl/workflow-invoke-recovery-ownership.md`。

最终General回归 `/tmp/zf-k557-general-fixed-20260925-k4SuYw/run/testkit-run.json`：
1 passed/98.682863s，唯一invoke、同Run交付、正式重启/清理及浏览器原断言通过。
完整影响闭包135 passed，当前MAINLINE363 passed；详见steps K557-A/B/C。
本段及K557-F2在六场景冻结运行结束后补记；不篡改执行时source附件，也不声称
更新后的台账与旧执行快照字节相同。

## K557-F1：rerun 合同直接子进程继承外层 xdist 身份导致取证冲突

> 状态: fixed（2026-09-25；测试夹具隔离修复，完整自动影响闭包263 passed；未改生产）

红回执 `/tmp/zf-k557-devverify-20260925.json` 为260 passed/3 failed，单独原合同
`/tmp/zf-k557-rerun-standalone-20260925.xml` 为3 passed。原因是
`tests/testkit/test_rerun_evidence.py` 三个直接subprocess继承父PYTEST_XDIST_WORKER、
PYTEST_XDIST_TESTRUNUID、PYTEST_XDIST_WORKER_COUNT；两个单进程child报告被重定向到
worker目录，嵌套xdist控制端则被误当worker生成额外已执行记录，合并器正确拒绝重复。
runner已有同样隔离，但这些合同未通过runner启动，不是本轮来源profile改变了结果语义。

调研pytest_plugin报告寻址和runner env清理后，三处复用测试侧_nested_env清除父身份；
内层真实xdist自行生成新身份。保留rerun两轮、flaky_pass非绿及普通快照原断言。
单独修复回归3 passed；`/tmp/zf-k557-devverify-rerun-20260925.json`四worker完整
直接调用闭包 **263 passed/165.90s**，自动host/native排除，没有放宽合并器或生产修复。

## K556-F1：新增 Issue 检测入口未符合 S9 台账可执行引用格式

> 状态: fixed（2026-09-25；仅规范108/107/106/103已有检测器引用，生产状态不变；回归见steps K556）

当前完整MAINLINE原报告 `/tmp/zf-k556-mainline-20260925-eL9P7b/run/testkit-run.json`
为329 passed/1 failed，精确失败节点为
`tests/conformance/test_zaofu_testkit_s9.py::test_issue_ledger_has_a_replayable_test_reference_for_each_known_bug`。
它要求独立tests/行；四项入口已在文字/反引号中，但未按既有协议登记。先修108后定点
仍红于107；批量只读检查确认还包含106/103。现将四项已有入口改为独立路径，未添加
虚构测试或放宽检测器；这是台账记录缺陷，不是108等生产修复失效，不新建ZF生产编号。

## ZF-TEST-108：正式 fast-stop 删除 tmux 后遗留独立进程会话的工具子进程

> 状态: fixed（2026-09-25；真实Claude首轮红、独立host复现、最小生产修复、6例模式/顽固子进程回归通过；未提交）

tests/conformance/test_zaofu_testkit_s5_cli_stop_tmux.py::test_cli_fast_stop_removes_detached_tool_descendant
tests/conformance/test_zaofu_testkit_s5_native_long_task.py

以上为可执行检测入口；下文保留现场说明与原生来源。

真实Claude执行三块各60 CPU秒的checksum，首块完成、第二块仍在计算时，正式独立
`zf stop --fast --include-run-manager` 返回0，tmux及Provider已退出，但工具PID1781990
仍占用约一个CPU核、已reparent到1184。不能此时启动替代worker，否则旧工具可继续写
同一workdir。原生报告 `/tmp/zf-k553-native-long-task-20260925.xml`：
**1 failed/259.16s**，失败是 `_assert_gone`；未进入recover/candidate。
原生finally再次清理仍失败，source/cleanup最终属性未生成，不宣称来源绑定验收。
该工具后来已自然退出，独立/proc核验为空；根目录由原TemporaryDirectory清理。

脱离Provider的独立复现 `/tmp/zf-k553-108-detached-red-20260925.xml`：
**1 failed/5.53s**。pane内fake provider通过`Popen(start_new_session=True)`启动tool，
stop前核验tool是实际pane后代且SID=PID；独立CLI stop返回0且session消失，tool仍存活。
finally在保留断言失败后以精确pidfd清理该tool，未伤及其它session。

根因路径：`GracefulShutdown.execute_fast → CompositeTransport.shutdown →
TmuxTransport.shutdown → TmuxSession.kill_session`，修复前末端仅执行`tmux kill-session`。
已有`terminate_window`也只向pane PGID发信号；新SID工具不在该PGID内。仅遍历
`_launch_records`无效，因为独立stop进程没有原start的内存记录。

修复：`TmuxSession.kill_session` 现在从精确 session 列出 live pane roots，调用新
`tmux_process_cleanup.terminate_pane_descendants`；它按 PPID/birth ticks 取得叶子顺序，
用 pidfd 固定身份后 TERM→KILL，再执行 kill-session。跨进程新SID不依赖原start内存记录，
PID复用/归属变化不发送信号；无关 witness session 不受影响。Linux以外保持原有tmux
行为，采样后新fork/采样前已脱离父链仍是明确边界。

修复证据：离线清理/身份/故障负例 **48 passed/2.23s**；同一独立host复现矩阵
fast/graceful/force × 普通/忽略TERM **9 passed/24.42s**，含无关witness存活；
Ruff通过。原红 `/tmp/zf-k553-108-detached-red-20260925.xml` 与原生红
`/tmp/zf-k553-native-long-task-20260925.xml` 保留，不改写为绿。

修复验收要求：从精确所属tmux session/pane取得进程树，绑定进程birth/pidfd避免PID
复用；叶子优先有界TERM/KILL，保留不相关session/witness；停机返回成功前工具已退出。
上述离线与host范围已完成；原生K553续跑现已通过：
`/tmp/zf-k553-native-long-bound-20260925/testkit-run.json`，1 passed/685.946729s，
source stable/exit0/pass。正式stop返回后旧工具已消失，随后recover/start到唯一candidate，
首块不重写、三块完成、第三次start幂等与最终cleanup均通过，不是只在finally杀tool造绿。
最初只证实fast-stop；随后graceful/force的独立host已通过上述六例。单role respawn、
非Linux和无法从当前父链归属的daemon不由本修复证明。调用方
`/tmp/zf-k553-108-full-callers-20260925.xml`104 passed/1 host deselected/14.55s；
premerge `/tmp/zf-k553-108-premerge-20260925.xml`47 passed/4既有040 failed，未抬cap。

## ZF-TEST-107：Claude 通用命令审批提示未被就绪阻断器识别，原生恢复测试空耗预算

> 状态: fixed（2026-09-25；现场复现、最小修复、直接调用方与 dev-verify 回归通过；未提交）

tests/conformance/test_zaofu_testkit_s5_claude_readiness.py
tests/conformance/test_zaofu_testkit_s5_claude_prompt_matrix.py

K551 第二轮原生运行在新 impl pane 停于 Claude 原样提示：
`This command requires approval` / `Do you want to proceed?`。现有
`provider_interactive_prompt_marker` 只识别带 shell syntax 的审批文本，
`blocked_claude_screen` 也只识别 trust/onboarding，因而测试将人工审批停顿误判为
正常运行。确认阻塞后仅对本测试pytest发送SIGINT，让原finally停止并清理；报告
578.839371秒（pytest call575.53秒）以stopped_without_candidate失败，非预算到期。
未点击审批、未提升权限。四份来源摘要独立匹配，native_processes保留审批原文，
cleanup全true，旧root及观测PID消失。

修复：新增通用 command-approval 文本的 `permission_confirmation` 分类，并让
Claude readiness blocker 复用统一 marker；不自动确认、不改变 allowlist 或 Provider
权限。此前32绿仅覆盖旧shell-syntax提示；新增原样文本红测后，统一分类器、readiness、
pane probe 与 prompt matrix 定向 **56 passed**，dev-verify 自动层 **106 passed/9.62s**。
红证据 `/tmp/zf-k551-107-approval-red-20260925.xml` 2 failed/0.90s；绿证据
`/tmp/zf-k551-107-approval-green-20260925.xml`、`/tmp/zf-k551-107-devverify-20260925.json`。
106身份及native取证前序 `/tmp/zf-k551-107-prior-evidence-20260925.xml` 7 passed/8.04s。
不自动确认、不提升权限、不扩allowlist；随后 K551-D 第三轮原生通过，见 106 补记。

> 2026-09-23 K500 授权更新：当前 ZaoFu 自身测试主干目标内遇到的阻断可直接修复，
> 无需逐项申请；必须保留检出/回归证据并标记 fixed。下文历史“不直接修改生产代码”
> 不再限制此范围；降级项、权限提升与仓库提交/远端推送不在本次授权内。

> K501 原生修复后验收（2026-09-23）：068/072/073 修复后的同一 CLI 原生入口在显式
> 1440s 预算下 **1 passed in 913.07s**，证据 `/tmp/zf-k501-native-1440-20260923.xml`。
> 两阶段独立 session、正式提交/结算、精确 candidate、Task done、归档、重建幂等与清理
> 均通过。K500 的 720s 超时历史不改；Plan/Run 为 Given，恢复和整体目标仍未完成。

> K510 真实恢复验收（2026-09-23）：impl **1 passed / 665.38s**、verify
> **1 passed / 539.62s**；均为真实 assistant 响应后 owned Claude SIGKILL，随后
> Run Manager→respawn→redrive→新 attempt succeeded，旧 attempt superseded；candidate、
> Task done、session archive、重建幂等及专属 PID 清理均通过。确定性 MAINLINE（含 079
> detector）**108 passed / 93.99s**。075—080 均保持 fixed；整体可信闭环仍 active。

## ZF-TEST-106：短 Git OID 被成功准入后与 TaskRef 完整身份不一致，验证永久等待

> 状态: fixed（2026-09-25；主干阻断已复现、最小修复、原生 K551 复验及前序回归通过；未提交）

tests/conformance/test_zaofu_testkit_s3_impl_commit_identity.py

K551原生首轮1440秒预算耗尽、0候选；新impl真实transcript的target/self-check/receipt
均提交短SHA。源码中typed admission未约束完整OID，TaskRefManager却解析为完整SHA；
task_pipeline_targets严格等值查找不到已结算的dev.build.done，waiting_for_impl_result。
原生原state已清理，具体字段因果由下述离线真实调用链独立证明，不冒称原生账本仍在。

红证据：`/tmp/zf-k551-106-red-20260925.xml` 1 failed/1.15s；加强后的
`/tmp/zf-k551-106-causal-red-20260925.xml` 1 failed/1.27s（仅撤回本轮修复后复现并恢复），
JUnit保存短source、完整TaskRef与waiting_for_impl_result三步。operation过早settled，
worker失去修正同operation机会。HEAD符号引用也必须拒绝，不能仅拒绝hex短串。

最小修复方向：只对成功、contract-bound、semantic implementation提交要求40/64位
小写完整Git OID；通过原repair路径返回可操作错误，保留operation/attempt/凭据。
不重写worker证据，不放宽下游身份比较，无snapshot legacy与失败结果保持兼容。
实施边界见 `docs/impl/implementation-result-commit-identity.md`。
最终直接 `/tmp/zf-k551-106-direct-final-20260925.xml` **109 passed/11.27s**；短串/
HEAD/单receipt错配拒绝，同凭据修正后verify/candidate及重建幂等已验证。
`/tmp/zf-k551-106-devverify-20260925.json` **508 passed/134.73s**；相关mock E2E
`/tmp/zf-k551-106-mock-e2e-20260925.xml` **2 passed/12.04s**。premerge仍47绿4既有040红。
修复后第三轮原生 K551 复验
`/tmp/zf-k551-native-old-verify-third-20260925/testkit-run.json` **1 passed/903.459897s**
（pytest 902.08s）：
四次派发、四次 operation settlement、旧 impl 完整 OID/settlement 保留、RM
request→completed→redrive、唯一 candidate、归档/重放幂等、迟到 operation 拒绝、source
稳定和 PID/tmux/lock 清理均通过；`exit=0`、`session_finished=true`，无 skip/xfail/timeout/
evidence error。两次重建的范围是派发/candidate 不重复、最终四次结算，不外推全部事件
exactly-once。source/JUnit/phase 四摘要独立核验，10 个观测 PID 及临时 root 已消失。
收尾 S0 `/tmp/zf-k551-final-s0-20260925.xml` 19 passed/24.97s；premerge 仍为既有
040 的四红，不计发布通过。中间宽松 hex-only 候选被 HEAD 负例检出并已撤回。

## ZF-TEST-105：跨 map 槽位切换在 shell/活性探测异常时绕过旧 operation 恢复门禁

> 状态: fixed（2026-09-25；精确红绿、影响闭包和旧impl跨map原生验收通过；未提交）

检测入口：

    tests/test_testkit_native_map_replacement.py::test_map_observer_follows_real_recovery_and_candidate

K550原生首轮 `/tmp/zf-k550-native-first-20260924-_f35i3wj/testkit-run.json`
1 failed/44.455884s；真实assistant后SIGSTOP，pane记录`已停止`并回到shell；旧attempt
过期、operation interrupted后，新map直接派发新Claude，没有worker.respawn.requested/
completed/redrive。source稳定、所有观测PID和测试root清理通过，不计原生恢复通过。

根因：`task_pipeline_runtime._activate_task_stage_binding`原先用`alive and ...`决定
是否检查旧binding的active attempts/operations。TmuxTransport遇shell返回False，探测
异常也被转换为False，因此canonical suspended operation不能阻止binding切换。新slot
创建不等于经过Run Manager批准的旧worker回收；不能把interrupted/attempt.failed当作
恢复completed事实。`docs/impl/worker-respawn-result-contract.md`仍要求精确恢复授权。

精确离线复现：`/tmp/zf-k550-slot-authority-detector-red-20260924.xml`
2 passed/4 failed/7.78s。alive对照两绿；shell/probe-error × same/reconstructed四红，
失败均为expiry后、RunManager tick之前的额外scheduler wake擅自派发新map。
更早探针缺少该调度窗口或伪snapshot保留旧cwd，不作为此因果证明。

候选修复仅将旧binding存在也纳入canonical检查；物理terminate仍按实际活性执行，
expired operation的终止/重启由原RunManager consumer负责。直接13 passed/16.19s
为候选版本证据，尚不等于全影响闭包或原生验收。不降低恢复/PID/迟到结果门禁。

2026-09-25补记：最终严格oracle `/tmp/zf-k550-final-strict-oracle-20260924.xml`
13 passed/16.55s；K468/K471正式RM授权及K483真实tmux前序12 passed/49.12s。
精确dev-verify自动层240 passed/1 skipped/53.19s（历史R14本地归档缺失）；mock E2E
5 passed。premerge仍47 passed/4既有040红。第二轮原生
`/tmp/zf-k550-native-second-20260924-v77lbdaw/testkit-run.json` **1 passed/1238.71s**：
生产RM request/completed/redrive、新map impl/verify及唯一candidate、双stage归档、
迟到状态拒绝、停机前旧PID退出、最终11个已观测PID清理、source稳定均通过。
四份来源摘要独立核对一致，S0 19 passed/33.30s。105已fixed，不关闭040、104或
OS crash/长任务/旧verify跨map；中间放宽oracle的报告不计入验收证据。

## ZF-TEST-104：HTTP 额度不足经真实 Claude CLI 转为 billing_error 后仍被立即重排

> 状态: open（2026-09-24；当前工作树真实CLI/SDK/transport/Kernel caller复现；不阻断测试框架建设，未修生产代码）

检测入口：

    tests/conformance/test_zaofu_testkit_s5_http_limits.py::test_real_http_response_through_sdk_transport_and_kernel[quota]

裸红重现（只访问隔离loopback HTTP，不使用实际账户凭据）：

    ZF_RUN_LOCAL_PROVIDER_HTTP=1 uv run pytest -q --no-cov -o addopts='' --runxfail 'tests/conformance/test_zaofu_testkit_s5_http_limits.py::test_real_http_response_through_sdk_transport_and_kernel[quota]'

Given：固定HTTP 400 `invalid_request_error`、credit-balance不足响应；真正安装的Claude CLI
收到该响应，真实transcript写入`error=billing_error`、`apiErrorStatus=400`及
`Credit balance is too low`。SDK真实子进程exit=1；未替换query/parser、错误分类或恢复函数。
这是**受控服务响应下的真实客户端行为**，不是用户真实账户余额不足。

When：`StreamJsonTransport._record_background_failure`从transcript读出错误，但只把
message和`status=transport_error`送入`classify_provider_stop`；分类器缺少billing/额度
处理。事件虽保留`provider_error_kind=billing_error`和400，`provider_stop_reason`却是
`transport_error`。真实`_on_agent_api_blocked`继续消费该事件。

Then：实际decision=`dispatch`、Task `in_progress→backlog`、health=`degraded`、
action=`requeue`、`requires_operator=false`；预期阻断账户错误并要求operator处理，不能
当临时网络错误立即重派。潜在重复请求/重试消耗尚未通过长时间循环实测，不扩大结论。

证据：`/tmp/zf-k549-http-detector-first-20260924.xml` **5 passed/1 failed/10.98s**，
唯一红为`BillingFailureRequeued`；JUnit属性保存HTTP记录、真实transcript错误行及SHA、
实际事件/恢复/health、进程退出和清理。前置错误形状、CLI退出、PID/锁释放必须普通通过，
只有精确错误恢复组合才抛该专用异常；strict xfail不能吞掉环境/夹具/清理失败。

对照：429→200真实CLI重试成功；持续429在4秒transport预算后timeout；额度错误或
timeout后受控server恢复200，显式新dispatch同session/新PID得到成功文本，旧账本前缀
和Task状态保持、不产生candidate/Task done。此显式transport重试不证明自动账户恢复。

建议维护者沿transcript结构化错误→分类→恢复动作→health/人工提示修复，并用上述裸红
节点、provider-stop/recovery与health调用方回归；本阶段仅增加可重复检测，保持open。

正式source-bound报告：`/tmp/zf-k549-http-final-20260924-evk9ihja/testkit-run.json`
**5 passed/1 strict xfailed/12.144491s**，唯一xfail为104；pytest exit=0但TestKit
verdict=fail，无collection/deselection/timeout/evidence错误，source稳定。四份绑定摘要
独立回读一致，原生CLI进程均退出，server/root清理通过。修复建议未实施，104保持open。

## K549-F2/F3：前序TLS记录顺序及mock派发宿主依赖（测试框架问题）

> 状态: fixed（2026-09-24；仅修测试身份判据/隔离和finally清理，95项聚焦回归通过）

首轮`/tmp/zf-k549-devverify-20260924.json`自动并行层 **93 passed/2 failed/10.69s**：

- F2：`test_relay_forwards_verified_tls_without_terminating_it`用`records[0]`作为被切断
  的第一条连接；真实记录按线程结束顺序追加，现场首条connection_id=2。改按connection_id
  核验1被切、2未切，保持TLS字节与非明文日志判据，不改relay或生产实现。
- F3：`test_background_dispatch_returns_before_provider_turn_completes`的mock query
  一秒内未开始，孤立复跑1 passed/0.86s；首次耗时根因未被唯一证明，不称生产回归。
  调研发现该纯mock测试仍调用宿主Provider进程/锁扫描；按既有生命周期单测边界隔离
  Path.home及purge，增加finally释放query并shutdown，保留0.2秒非阻塞/1秒启动判据。
  此fixed指测试隔离/清理缺陷修复，不宣称已定位所有调度延迟原因。

修复后相同影响闭包`/tmp/zf-k549-devverify-fixed-20260924.json`
**95 passed/8.40s**（4 worker）；真实SDK/HTTP host验证单独执行，未用mock替代。
本阶段未改src，不重跑K548长原生链，不删除或改写原始失败报告。

## K549-F1：额度错误文案大小写断言不匹配（测试框架缺陷）

> 状态: fixed（2026-09-24；直接CLI两例及后续完整HTTP矩阵已验证；不占生产ZF编号）

首次`/tmp/zf-k549-http-cli-first-20260924-lhhbnsh6/testkit-run.json`
**1 passed/1 failed/3.221323s**：CLI实际返回`Credit balance is too low`，测试查找小写
`credit balance`失败。对文案执行`.lower()`后仍严格要求非零退出、is_error及400记录；
`/tmp/zf-k549-http-cli-second-20260924-l07l5ql9/testkit-run.json`
**2 passed/3.121861s/verdict=pass**。未改生产代码，也未掩盖独立104错误恢复问题。

## K548-F1：网络 wrapper 重复 --settings 丢失专属 endpoint（测试框架缺陷）

> 状态: fixed（2026-09-24；精确argv复现1红→前序62绿；原生修复后复验另行记录）

K548完整CLI首轮 `/tmp/zf-k548-native-cli-20260924-2qum43lr/testkit-run.json`
1 failed/117.304822s：真实assistant已出现，但relay accepted=0，故障注入前超时。
源码`SpawnCoordinator.spawn`会追加生产hook `--settings`，测试wrapper在前面又追加
网络`--settings`；不能将通路未生效算作断网。`test_native_wrapper_keeps_real_argv_and_private_tmux_only`
按真实argv加入hook文件后 `/tmp/zf-k548-wrapper-red-20260924.xml` 1 failed，确认重复。
测试侧`merge_claude_network_settings`保留hook/既有env，只覆盖网络env并传唯一settings；
不修改生产caller、用户配置或认证。相同wrapper的子进程检查和K543/K546/S3CLI联调
`/tmp/zf-k548-wrapper-green-impact-20260924.xml` **62 passed/24.17s**，Ruff通过。
原生第一轮红保留，source/cleanup均通过；不是生产ZF问题，也不以offline绿替代native。

补充：第二轮 `/tmp/zf-k548-native-cli-second-20260924-zb2pyouj/testkit-run.json`
1 failed/6.882239s，尚未启动Provider；合并后的settings放在`auth status`后面被CLI拒绝。
改为唯一前置全局参数，wrapper增加auth argv回归，真实只读`claude --settings ... auth
status` exit=0/loggedIn=true；前序 `/tmp/zf-k548-wrapper-order-green-20260924.xml`
**62 passed/23.95s**。同属测试wrapper接线修正，未绕过preflight或修改认证。

原生修复后验收：`/tmp/zf-k548-native-cli-third-20260924-oirz2cya/testkit-run.json`
**1 passed/802.497941s/verdict=pass**。2真实活动连接断开、3次故障期重连拒绝；正式
CLI恢复后同session新attempt、impl/verify与精确candidate、第三次start幂等通过。
source stable、248 PID及私有tmux/relay/root清理通过，故F1原生接线修复已验证。

## K547-F1：hook 队列夹具未创建临时 Git 项目目录（测试框架缺陷）

> 状态: fixed（2026-09-24；新用例3项及前序53项通过，不占生产ZF编号）

首轮`/tmp/zf-k547-hook-queue-first-20260924.xml`为3 failed/1.85s，原因是复用的
`_state`要求传入已存在的cwd，新夹具漏建project目录，git init在任何runtime/hook
行为之前FileNotFoundError。在夹具入口创建该目录，未改生产代码或放宽断言。
`tests/conformance/test_zaofu_testkit_s3_writer_queue_provider_turn.py`第二轮3 passed/25.22s，
补elapsed>=deadline后与K542/K546、hook/turn/CLI lifecycle联调
`/tmp/zf-k547-hook-queue-impact-20260924.xml` **53 passed/82.55s**。
正式hook CLI来自合成Provider输入，worker语义/transport也是明确模拟，不计native。

## K547 原生环境前置：Codex 当前未登录（非生产 Issue）

> 状态: open（2026-09-24；原生路线尚未执行，不标fixed、不计green）

只读`codex login status`输出`Not logged in`。K547-A已证明Codex prompt/stop的正式
hook入口能在writer结果后保持轮次、自然queued_wait_timeout并经CLI恢复；Claude提交后
会释放该轮次，不能用post-result sleep伪装同一占槽情形。原生仍须真实Provider执行及
transcript/进程/候选证据；待操作者提供可用Codex认证，或研究并证明独立Claude资源竞争
场景后再运行。未自动登录、未复制凭证、未改Provider配置，不因这一前置暂停整个目标；
网络隔离/恢复等其余非降级工作仍可继续。

K592补记（2026-09-28）：Claude命令不存在（127），Codex0.155.1未登录（1），
cold-start结构5/5但认证/handoff失败（1），原始输出见
`attachments/k592-provider-preflight-20260928-preflight.log`。当前原生legacy队列节点
也尚未实现，并非仅需恢复登录就已具备完整验收。正式hook离线队列/启动/环境合同
25 passed，认证与原生验收分类合同65 passed；这些不关闭原生缺项，不标fixed，
未发现新增生产bug，也未更改共享认证或Provider配置。

## K546-F2：原生批次误把跨任务新会话诊断当恢复失败（测试框架缺陷）

> 状态: fixed（2026-09-24；判据与63项调用方通过，第三轮原生批次通过；不占生产ZF编号）

第二轮 `/tmp/zf-k546-native-writer-second-20260924-8xg3jn1j/testkit-run.json`
**1 failed/1245.661539s**，全程禁止continuity downgrade误拒绝TASK-1→TASK-2正常
新session。JUnit事件索引24为诊断，28首次TASK-2派发，29才stop；恢复未新增诊断。
失败发生在双文件候选/四归档绑定检查之后、第三次start之前；不追改旧失败报告。
`tests/testkit/native_writer_batch.py::batch_continuity_evidence` 改为核验原Task完成/归档、
新Task/binding/session及事件顺序，仅允许中断派发前可证明的任务切换。恢复及terminal
replay中新downgrade、证据缺失仍严格失败；原生caller两阶段接入同一判据。
`tests/test_testkit_native_writer_batch.py`合法/缺失/篡改边界与两恢复夹具14 passed，
`/tmp/zf-k546-boundary-impact-20260924.xml`前序调用方63 passed。未修改生产代码；
诊断名称在跨任务场景需结合binding解释，不据此伪造session恢复失败或删除全部检测。

原生复验：`/tmp/zf-k546-native-writer-third-20260924-81__n4k0/testkit-run.json`
**1 passed/1128.7909s**；正常跨Task诊断保留，恢复及第三次start无新增downgrade。
同session新attempt、双文件candidate、四归档session、第三次重启幂等、source/cleanup
全通过。四份transcript摘要独立回读一致，494个observed PID均退出。旧两轮红不追改。

## K546-F1：内存规划夹具未满足独立 CLI 输入合同（测试框架缺陷）

> 状态: fixed（2026-09-24；离线两支恢复及前序调用方通过；不占生产 ZF 编号）

首轮 `/tmp/zf-k546-offline-first-20260924.xml` **2 failed/1.74s**：原 K467 规划夹具
只供直接 runtime 调用，序列化配置没有 stage.task_map；CLI 配置校验正确拒绝。
补充实际 task-map 路径。第二轮 `/tmp/zf-k546-offline-second-20260924.xml`
**2 failed/5.80s**：minimal Given run-contract 与实际加载配置不同，recover 正常追加
`config.run_contract.resume_checked` WARN，被“不追加事件”的测试检测。
离线在准入前用正式 build/write_run_contract 绑定实际加载配置，模拟完整start的Given；
保留整个账本字节不变断言，不过滤WARN、不改生产恢复行为。
`/tmp/zf-k546-offline-third-20260924.xml` **2 passed/11.16s**；调用方闭包
`/tmp/zf-k546-impact-20260924.xml` **57 passed/76.56s**。原生结论单独验收。

完整启动首轮 `/tmp/zf-k546-native-writer-first-20260924-8ppjqxr4/testkit-run.json`
**1 failed/6.9282s**，缺aggregate.success_event/failure_event，默认judge.failed无producer。
按原有candidate_integration协议配置candidate.ready/integration.failed，未绕过workflow
inspect；新增加载后inspection断言 **2 passed/11.92s**，审批caller **4 passed/14.64s**。
原生首轮source稳定、清理通过，没有Provider派发，不登记生产问题。

## ZF-TEST-103：graceful 停机按角色类型寻址，实例日志丢失

> 状态: fixed（2026-09-24；确定性两红→三绿、60项影响闭包和impl/verify graceful原生复验通过）

tests/test_shutdown_instance_logs.py::test_graceful_shutdown_preserves_instance_logs

K545首轮真实Claude impl在途graceful stop后，`logs/impl.log`缺失，
`/tmp/zf-k545-native-graceful-impl-20260924-uw18no9f/testkit-run.json`
**1 failed/20.322398s**。snapshot账本/memory检查已通过，source stable及cleanup通过。
失败前的“测试接线/断言问题”只是初步判断，现更正为下述已复现生产问题。

原因：`GracefulShutdown._save_transcripts` 将role.name传给capture_log；默认pane-grid
的物理pane按instance_id绑定。CompositeTransport的单实例fallback只选择adapter，
不会重写capture_log参数；多实例同类型还会因歧义拒绝。异常被best-effort忽略，日志
缺失。即使捕获成功，以role.name命名也会覆盖同类型另一实例。

- detector：`tests/test_shutdown_instance_logs.py::test_graceful_shutdown_preserves_instance_logs`，
  真实GracefulShutdown/CompositeTransport＋精确实例的隔离物理边界；三组为name=id、
  单个不同id、两个相同role类型。修复前 **1 passed/2 failed/0.52s**，
  `/tmp/zf-k545-103-red-20260924.xml`。
- 最小修复：仅把capture_log寻址与`logs/<instance_id>.log`命名改用instance_id；
  name=id旧路径不变，未启动角色仍best-effort。不改role路由/权限、不降低断言。
- 回归：`/tmp/zf-k545-103-impact-green-20260924.xml` **60 passed/20.90s**，
  含三组detector、snapshot oracle、shutdown/Composite/CLI调用方及agent-owned mock E2E。
  `/tmp/zf-k545-stop-host-green-20260924.xml` **3 passed/13.33s**，真实tmux三种stop
  不影响旁观session，清理通过。修复后的原生graceful候选链仍单独待验。
- S5/MAINLINE与coverage已接detector，文档`docs/impl/provider-startup-readiness.md`同步；
  premerge 47 passed/4既有040 failed，未提高阈值；未提交/merge/push。
- 修复后原生：impl `/tmp/zf-k545-native-graceful-impl-fixed-20260924-pneq1hcs/testkit-run.json`
  **1 passed/487.074128s**，verify `/tmp/zf-k545-native-graceful-verify-fixed-20260924-cfihbveq/testkit-run.json`
  **1 passed/457.013850s**；两支日志分别为`impl-1.log`/`verify-1.log`，snapshot前缀、
  memory、恢复、候选、归档、第三次start幂等和274/224 PID清理均通过。无新增生产问题。

## K544-F1：verify 原生恢复测试混淆基础提交与验收提交（测试框架缺陷）

> 状态: fixed（2026-09-24；先红后绿，63 项目标/briefing/恢复调用方通过；原生第二轮通过）

- 原生检出：`/tmp/zf-k544-native-verify-20260924-n_ye_xfq/testkit-run.json`，
  1 failed/290.503991s；impl 已完成、verify 在途，stop 前内容断言读到初始 hello。
- 原因：夹具读取 dispatch.target_ref；该字段在生产中为 workspace.base_commit。
  正式 verify 消费 TaskRef.source_commit 生成的 target_commit/target snapshot，briefing
  也使用 target_commit。此处是测试错误，不归因生产 target 漂移，不改 src/。
- 红：`tests/test_testkit_native_cli_lifecycle.py::test_verify_replacement_rejects_changed_pinned_commit`，
  `/tmp/zf-k544-f1-red-20260924.xml` 1 failed/0.91s；相同 base、不同 target_commit 未被拒绝。
- 修复：精确 Git 内容与新旧派发对比均用 target_commit，保留 same session/binding/cwd、
  succeeded impl 不变、strict candidate 和 cleanup 断言。
- 绿：`/tmp/zf-k544-f1-green-20260924.xml` **63 passed/23.16s**，包含 target/briefing、
  K469 CLI、102 detector、native observer 与 report runner。首轮148个PID及临时root
  独立确认已清理；失败报告保留，未以此关闭原生 verify 主项。
- 原生修复后：`/tmp/zf-k544-native-verify-second-20260924-q6p1d6o5/testkit-run.json`
  **1 passed/498.029855s**；impl不变、verify同目标/会话恢复、candidate/重启幂等通过，
  source稳定、248个观测PID及临时root清理。只关闭verify＋fast-stop，不关闭完整恢复矩阵。

## ZF-TEST-102：完整 CLI 重启遗漏 Task-stage cwd，旧会话被按新会话启动

> 状态: fixed（2026-09-24；最小调用方两例红→绿，影响闭包144 passed，K543原生第五轮通过；未提交）

调用方收尾：`/tmp/zf-k543-cwd-impact-final-20260924.xml` **144 passed/66.41s**，
包括 start/spawn/session/termination/lifecycle、四种 CLI stop/recover 和 mock E2E。
原生严格复验第五轮 `/tmp/zf-k543-native-fifth-20260924-0k1fvs2c/testkit-run.json`
**1 passed/375.733681s/verdict=pass**；同session不同attempt，impl/verify归档binding与
dispatch及真实transcript一致，candidate/Task done/再次启动幂等/cleanup全部通过。
未放宽原身份断言；结构门禁仍有4项既有040红，不表示整个目标或全部恢复矩阵完成。

K543 第二轮真实 `stop --fast → recover workflow → start` 到达 candidate 后，归档
binding 指向的 transcript 缺失，测试失败。旧 `a2479392...` transcript 被重命名为
`.archived-*`；新派发使用 `61a40bb6...`，并产生 `provider_session_resume_not_proven`。
原始报告 `/tmp/zf-k543-native-second-20260924-wz3ftmpj/testkit-run.json` 为 fail。
会话中一度归因“测试应改查 dispatch session”不成立，已撤回该放宽并保留严格一致性检查。

确定性真实 CLI caller 复现：已派发 Task Pipeline + 已落盘 binding/工作树/会话，
`start --dry-run` 的 launch.cwd 错为 `workdirs/impl-1/project`，应为
`workdirs/tasks/<run>/<task>/g1/project`；把后者移走仍错误返回 0。`--dry-run` 仅替代
物理 Provider/tmux，不替代配置、启动编排、SpawnCoordinator 或会话注册表。

```text
tests/test_cli_start_task_stage_cwd.py::test_cli_restart_preserves_task_stage_spawn_cwd
```

- 红：`/tmp/zf-k543-start-cwd-red-20260924.xml`，2 failed/2.28s。
- 最小修复：`src/zf/cli/start.py` 对 resume obligation 复用现有
  `task_stage_spawn_cwd`，先验证 kernel dispatch、binding、workspace ownership/metadata；
  有效 Task-stage 不再构建通用 role worktree；缺失或身份不匹配直接拒绝，不回退。
- 绿：`/tmp/zf-k543-start-cwd-green-20260924.xml`，2 passed/1.95s；正确 cwd、
  `--resume`、同 session、原 transcript 字节不变及缺目录拒绝均通过。
- 宿主 Claude 更新期间 `env: claude: 没有那个文件或目录` 为另一个环境事件，
  runtime 冷却后自行恢复，不属于 102 的生产修复。K543 原生全过程尚未记 done。

## K541-F1/F2/F3：watcher 接管夹具入口、计数与重派身份（非生产缺陷）

> 状态: fixed（2026-09-24；两种正式接管通过，未修改生产源码、未提交）

- F1：旧 workflow-submit 自动创建 backlog bootstrap Task，却要求它达到 managed
  Task done；改用 config-apply 安装流程、TaskStore 创建 managed Task、正式
  WorkflowStartService proposal 与独立 token-gated CLI apply，保留 Task done 断言。
  红 `/tmp/zf-k541-preserve-first-20260924.xml`：1 failed/113.86s。
- F2：把五物理 worker 收据当全部结果准入，漏掉 Kernel scope fanout aggregate；
  现在核对五 worker＋一汇总精确集合，汇总的 origin/schema/parent operation 与同 Run
  均需匹配。managed、managed-v2 两份红报告分别 1 failed/53.58s、1 failed/89.11s。
- F3：retry 复用 briefing_path，原 worker helper 在新 dispatch append 前可能误选
  旧事件、覆盖旧收据；若新旧事件均已存在则多匹配报错。按 briefing 顶层 run_id
  精确等待事件，保留旧 PID/hash/无 submission 的收据，新收据绑定实际 retry。
  `/tmp/zf-k541-retry-identity-red-20260924.xml`：2 failed/1.28s；修复后 worker/guard
  `/tmp/zf-k541-retry-identity-green-20260924.xml`：26 passed/1.44s。

最终 `/tmp/zf-k541-takeover-bound-20260924.xml`：**2 passed/157.11s**。preserve
保留原 worker/dispatch；full-start 保留旧收据，精确关联 retry_of_run_id、旧新
attempt 的 superseded 与同 operation；同 Run terminal、Task done、本地 dossier/
receipt、两次 heartbeat 后不重复及 cleanup 均通过。原 takeover-both 虽显示 2 passed，
因 F3 已判证据不足，不能作为身份恢复验收依据。共享 caller 联调另见 steps K541。

## ZF-TEST-101：reader retry 丢 source manifest 的历史误归类（已更正）

> 状态: misclassified（2026-09-24；撤回 production fixed 结论，转 K541-F4）

保留编号避免历史引用失效。原 General browser 红报告
`/tmp/zf-k541-live-callers-20260924.xml` 为 1 failed/1 passed，原始首个 scope worker
PID 1005741 在 `GeneralRoute.result()` 读取 fanout manifest 时 KeyError；不是 retry
worker 首错。原 dispatch 已携带 source refs，retry 的 nested payload 也有 refs，
不能仅凭顶层字段缺失认定生产丢失。后续 duplicate_submit 不用作首因。

上一轮曾从 previous dispatch 复制 handoff keys 到 retry briefing/dispatch，并以
大量 stub 的 `test_reader_retry_carries_prior_attempt_source_manifest_refs` 判 fixed；
该测试只证明复制行为，不证明真实入口缺陷。已精准撤回这两处生产补丁与该 stub 测试，
保留 completed_writer_task_ids 和项目根等既有修改。旧单次 browser 绿
`/tmp/zf-k541-general-browser-fixed-20260924.xml` 不足以证明竞态已解决。

真实 detector（转测 TestKit，见 F4）：

```text
tests/conformance/test_zaofu_testkit_s8_general_route.py::test_general_reads_exact_dispatch_before_manifest_projection
```

## K541-F4：General worker 抢读可变 fanout 投影（测试框架缺陷，已修复）

> 状态: fixed（2026-09-24；确定性红绿通过，host/caller 收尾结果见 steps）

`EventWriter._append_with_projectors` 先 append 事件，再更新 fanout manifest；独立
worker 已可收到 dispatch，但投影尚未携带 refs。投影也可能已经属于下一次 attempt。
GeneralRoute.result 现仅从收到的 immutable dispatch 读取当前 attempt 的 payload、
角色与 source refs，不读取可变 manifest；支持原派发与 nested retry handoff。

真实 GeneralRoute.start 后只替换 manifest 的 read_text，模拟 not_projected 和
newer_attempt；保留真实 refs/digest/read ledger 校验，不能靠删除红测试收尾。
`/tmp/zf-k541-f4-reconfirmed-red-20260924.xml` **2 failed/2.31s**（生产补丁仍在时）；
撤回补丁并修 helper 后 `/tmp/zf-k541-f4-direct-green-20260924.xml`
**30 passed/18.02s**，含两个反例、五 General 场景、worker 与既有 reader retry 合同。

### K541-F5：Issue verifier 读取投影身份（测试框架缺陷，已修复）

> 状态: fixed（2026-09-24；原反例、共享 caller、真实 Issue browser/restart/cleanup 已验证）

`/tmp/zf-k541-f4-host-final-20260924.xml` 实际为 **3 passed/1 failed/483.40s**；
两种接管与 General browser 通过，Issue browser 到终态后因保留 runner failure 失败。
首次 verifier PID 1120299 在 `complete_verify` 的 target_commit 比较处退出，随后
watchdog 记录真实空进程树并 respawn。原 dispatch 的顶层/nested target_commit 均等于
candidate.ready 的 fd5be43030233577ae546ed741f4bf00cb72f89c；未保存该瞬间投影，
不能宣称已证实“下一代”或“retry 丢失”，也不能追溯关闭缺少首错证据的 K530。

真实 writer→candidate→verify 后控制 manifest 读取为空 target 或其他代 target，
`/tmp/zf-k541-f5-verify-projection-red-20260924.xml` **2 failed/5.30s**，均复现现场
同一断言；dispatch 优先后 `verify-projection-green` **2 passed/16.73s**。
早先 issue-route-green/green2 各 **2 failed/11 passed**，误用没有 target_commit 的
writer 事件，是测试编写错误，不作为根因证据。尝试全去投影的 shared-callers
**29 failed/23 passed/131.62s** 则漏掉 legacy writer 事件没有 nested payload 的合同；
已保留此红报告，恢复该类事件的既有字段来源，最终共享 Issue/PRD/Refactor 调用方
`/tmp/zf-k541-f5-shared-callers-20260924-rerun.xml` **30 passed/595.06s**。
原失败入口 `/tmp/zf-k541-f5-issue-host-20260924.xml` **1 passed/300.32s**；同 Run
candidate、Task、terminal、本地 dossier/receipt、restart_verified/cleanup_ok 均通过，
runner.failed=0、respawned=0，owned PID 回读已退出。不追溯关闭 K530。

```text
tests/conformance/test_zaofu_testkit_s8_issue_route.py::test_issue_worker_reads_exact_dispatch_before_manifest_projection
```

## ZF-TEST-100：owner transport 明确负确认仍记录 delivered

> 状态: open（2026-09-24；新指令禁止继续 Feishu 联调，未修复，不作为当前主干阻断）

复现入口：

```text
tests/test_owner_visible_delivery_ack.py
```

指令变更前的 `tests/test_owner_visible_delivery_ack.py` 检出两条路径：
默认 `FeishuTransport.send_card` 忽略 `send_message=False`；卡片异常后的文本 fallback
也忽略 False，随后写入 `owner.visible_message.delivered`。成功无 message_id 的 legacy
None 与明确失败不同，不能把所有 None 当作失败。未改生产源码。

红证据 `/tmp/zf-k540-owner-ack-red-20260924.xml`：2 failed、2 passed/0.46s；
本轮指令变更前已启动的现状复验终态仍为 2 failed、2 passed/0.49s。两项成功对照通过，
两项负确认均错误 delivered=1/failed=0。当前仅记录既有发现，不继续实现、联调或扩展。
独立 HTTP 接收端历史证据 `/tmp/zf-k540-owner-http-first-20260924.xml` 为 2 passed；
它不证明真实飞书实收，不关闭本 Issue 或 K540。保留未收尾测试，不改成 fixed/xfail 假绿。

## ZF-TEST-099：并发 planner 可扩大已准入请求的写范围并派出 writer

```text
tests/conformance/test_zaofu_testkit_s8_shared_kind_plan_scope.py
```

> 状态: fixed（2026-09-24；签名 planner 扩围在 writer 创建前拒绝，合法代码终态与影响闭包通过，未提交）

在 General 的 docs/general 与代码请求的 answer.txt 已互斥准入后，planner 的
task_map 把 allowed_paths/scope 扩为 answer.txt＋docs/general，Kernel 仍派发 writer。
此前只检验 task_map 内部范围，没有校验与原请求的包含关系。
证据 `/tmp/zf-k539-c5-plan-scope-red-20260924.xml` **2 failed/45.92s**；detector 为
`tests/conformance/test_zaofu_testkit_s8_shared_kind_plan_scope.py`。没有伪造准入或
修改 invoke；两份 planner 表示在签名提交前一致，不属于 artifact digest 故障。

修复由 run_write_scope 只读核验原 admitted.source_event_id→invoke.scope，在
writer_fanout_admission 规范化 Task items 后接线；复用原 plan admission 失败/返工
通道，不直接改 Task/event、不扩大 agent 权限。plan-scope-first **4 passed/52.19s**
含两个正式 planner 拒绝与两个正式异项目根对照；scoped-terminal-first
**20 passed/61.71s** 含 18 个直接包含/绑定边界及 PRD/Refactor 合法并发终态。

## ZF-TEST-098：light 任务未消费请求 scope，并发准入后仍拥有整个工作树写范围

```text
tests/conformance/test_zaofu_testkit_s8_shared_kind_scope.py::test_formal_issue_writer_keeps_admitted_request_scope
```

> 状态: fixed（2026-09-24；light 请求 scope 接入 Task/worker 快照，直接及并发交付联调通过，未提交）

正式 General＋Issue 请求分别声明 docs/general 和 answer.txt，准入认为互斥，但
light_flow 合成 allowed_paths=["**", "README.md"]，Task.contract.scope 保留宽权限。
`/tmp/zf-k539-c5-writer-red-20260924.xml` **1 failed/8.15s**，detector 为
`tests/conformance/test_zaofu_testkit_s8_shared_kind_scope.py::test_formal_issue_writer_keeps_admitted_request_scope`。
这是下游写权限与准入声明不一致，不把此前 C3 交付成功视为写权限隔离证明。
初次 scope-red 的 writer 节点误读 TaskContract.allowed_paths 而 AttributeError，属于
测试夹具错误，已改为 canonical scope 后取得上述真实生产红；两份报告均保留。

修复只在 light task_map 合成时使用显式 scope，原目标范围保留为只读 probe 输入。
light-first **27 passed/16.53s**；scope-second **32 passed/92.72s** 含 writer Task
与不可变快照权限、项目根/范围与三个 light 直接对照；原 General＋Issue 双终态联调通过。

## ZF-TEST-097：并发准入把绝对项目 target_root 误作相对可写 scope

```text
tests/conformance/test_zaofu_testkit_s8_shared_kind_scope.py
```

> 状态: fixed（2026-09-24；项目根独立校验、相对 scope 门禁保留，正式入口与重建回归通过，未提交）

`run_isolation._scope_values` 将 source_refs.target_root 拼入 scope，导致正式 PRD/
Refactor 即使在同项目声明互斥 answer.txt，仍以 invalid work scope 排队。
`/tmp/zf-k539-c5-scope-red-20260924.xml` **2 passed/7 failed/83.79s**；其中六红是
两种流程的合法/缺范围/重叠请求被项目根判断遮蔽，另一个是上述 writer 夹具错误。
detector：`tests/conformance/test_zaofu_testkit_s8_shared_kind_scope.py`。
修复须单独核验目标属于 project_root，显式相对 scope 仍必需；不能只删根目录检查。

修复不再将 target_root 放进 scope；独立校验目标属于 runtime.project_root，含
子目录/符号链接归属及 target 子树内写范围。不从 state_dir 猜项目根。20 个直接
active/new 边界和正式两类准入/缺范围/越界/重叠/重建矩阵已绿。root-first 曾为
29 passed/1 failed：Refactor 异根本来已被更早 preflight 拒绝，夹具误要求进入
admission；改为核验正式 STOP/root_binding_conflict 后，plan-scope-first 四例通过。

097–099 共同联调 `/tmp/zf-k539-c5-impact-20260924.xml` **427 passed/1 failed/585.81s**；
唯一失败为执行期间新增 shared_code_concurrent 测试尚未注册，非生产回归。已补 S8
与 coverage 清单，定点注册复验另记 steps。premerge **47 passed/4 failed/16.08s**
仍是既有 ZF-TEST-040（fanout/server/App 超限与原 unknown oversized 集合），未改阈值。

最终 helper/注册 `/tmp/zf-k539-c5-helper-registry-20260924.xml` **5 passed/40.82s**；
双代码共享 Run `/tmp/zf-k539-c5-code-concurrent-third-20260924.xml` **1 passed/154.99s**。
后者证明互斥候选/真实终态释放/本地回执与重建，不替代外部 owner 实收或真实 Provider。

## ZF-TEST-096：正式 submit 可覆写属于其他活动 Run 的 bootstrap Task

```text
tests/conformance/test_zaofu_testkit_s8_shared_kind_identity.py::test_formal_task_collision_does_not_replace_active_owner
```

> 状态: fixed（2026-09-24；提交前按 EventLog 活动归属拒绝，CLI/ControlledAction 与前序联调通过，未提交）

Generic 的 bootstrap Task 已有运行中的 Run，但尚无 revision/origin 组成的正式
request binding。ControlledAction 的 `apply_task_request_binding` 因绑定为空而允许
另一请求改写 Task；CLI 的 `claim_submitted_workflow_task` 同样先于 Kernel admission
更改 execution binding/assignment。因此等到准入时 task collision 排队已经太迟。

正式红证据 `/tmp/zf-k539-c4-task-owner-red-20260924.xml` **2 failed/10.35s**，两入口
均错误 accepted；旧 `/tmp/zf-k539-c4-identity-second-20260924.xml` **6 passed/2 failed**
保留。旧测试 CLI queued 预期已纠正为提交前 STOP，强化为原 Task 全字段不变且没有
第二 invoke，而不是移除隔离断言。

修复：`workflow_task_request_rotation.active_task_request_blocker` 从原准入投影检查
活动父 Task 归属；Task binding decision 和 CLI submit preview 共用该检查。不同
Request 返回 workflow_task_stale（ControlledAction 409、CLI STOP），同请求重放、
不同 Task、终态 owner 的对照保持原行为，paused owner 仍受保护。原 revision/origin
rotation 和 Kernel concurrent fence 不变；不是多进程 submit 原子化证明。

绿证据：`/tmp/zf-k539-c4-identity-third-20260924.xml` **8 passed/30.50s**，含正式六例
及 095 两个恢复用例；`/tmp/zf-k539-c4-guard-registry-20260924.xml` **7 passed/0.70s**，
含五个归属对照和两项注册检查。detector：
`tests/conformance/test_zaofu_testkit_s8_shared_kind_identity.py::test_formal_task_collision_does_not_replace_active_owner`。

## ZF-TEST-095：重建重放同一无效 invoke 会重复追加拒绝事件

```text
tests/test_workflow_invoke_rejection_replay.py
```

> 状态: fixed（2026-09-24；按 source_event_id 持久去重，保留分步写入恢复，未提交）

正式损坏 Run Contract 后，初次拒绝正确，重建却再次追加 workflow.invoke.rejected；
`run.admission.rejected` 原已去重，只有前者重复。首次证据
`/tmp/zf-k539-c4-identity-first-20260924.xml` **3 passed/1 failed/22.69s**；直接 caller
`/tmp/zf-k539-c4-rejection-red-20260924.xml` **2 failed/1.38s**（普通重放及两次拒绝写入
之间中断）。修复仅位于 runtime_authority_bridge 的拒绝写入路径，使用 state_dir 内
锁和 EventWriter；已存在拒绝时仍执行原 admission 拒绝函数，补齐中断后半段。

detector：`tests/test_workflow_invoke_rejection_replay.py`；正式 C4 损坏合同/重建用例
也覆盖实际调用方。绿报告与 096 共用 identity-third，未把不同源事件误去重。

095/096 前序联调：`/tmp/zf-k539-c4-impact-20260924.xml` **167 passed/5 failed/179.78s**；
五红精确为既有 CLI 024（3）/025（1）/026（1），其余 action/request/proposal、
invoke/light/admission、Task lifecycle、EventWriter、General/Issue concurrent 交付
和 Generic mock E2E 通过。既有五红对照见 K539-C2，未将本报告说成全绿。
premerge `/tmp/zf-k539-c4-premerge-20260924.xml` **47 passed/4 failed/10.94s**，仍为
既有 040 结构门禁；本轮未改阈值、不做旁支重构。

## K539-C3-F1：并发交付夹具过早检查 Task 结算（非生产缺陷）

> 状态: fixed（2026-09-24；等待真实 Task 终态，保留 owner 和回执全部断言）

首轮 `/tmp/zf-k539-c3-concurrent-delivery-first-20260924.xml` **1 failed/21.53s**：
terminal/owner 已出现，但 TaskStore 的后续结算事件尚未消费。停止条件改为 owner
与全部 completed_task_ids 的 Task done 同时满足，仍使用既有 12 ticks 上限，不直接
写 Store 或移除终态断言。第二轮 **2 passed/37.81s**，覆盖 General-first/Issue-first；
`/tmp/zf-k539-c3-prior-integration-20260924.xml` **76 passed/127.22s**，联调 C1/C2、
共享串行、admission、worker 和 S0。无生产代码修改，不算新增生产 Issue。

## ZF-TEST-094：正式 Workflow submit/invoke 丢失已确认 scope，合法并发永久排队

```text
tests/conformance/test_zaofu_testkit_s8_shared_kind_concurrent.py
```

> 状态: fixed（2026-09-24；scope 已修复，正式矩阵/调用方通过，前序五红经对照确认是既有问题）

`build_flow_intake` 与 effective manifest 已保留确认的 scope，但
`build_flow_submit_preview` 与 `_submit_payload_to_workflow_invoke` 均未透传。
配置身份 ZF-TEST-093 修复后，即使两个请求显式声明互斥路径，也只能进入缺 scope
排队。正式入口红 `/tmp/zf-k539-scope-formal-red-20260924.xml` **1 failed/5.43s**，
assert invoke.scope 实际 None，期望 `["docs/general"]`；没有手工修改 invoke payload。

修复位于 `src/zf/runtime/workflow_delivery.py`：从已确认 effective manifest
把 scope 传到 submit，再传到 invoke。Kernel 缺范围、越界、重叠、worktree 与容量
门禁不变，不从目标根猜测可写范围。相关 detector 为
`tests/conformance/test_zaofu_testkit_s8_shared_kind_concurrent.py`。

- `/tmp/zf-k539-scope-formal-matrix-20260924.xml`：**6 passed/33.64s**；正式 General
  与 Issue 互斥 scope 同时准入/派发，第三个 PRD 被容量拒绝即时派发；缺 scope、相同
  scope、父子 scope、越界均正确排队；重建和重复 tick 不增派或重复 admission。
- `/tmp/zf-k539-scope-caller-20260924.xml`：**95 passed/56.25s**；requests、controlled
  action、admission、config identity、正式矩阵和 S0 两项注册联调通过。
- 中间 `scope-formal-second` 为 **1 failed/5.54s**：scope 和双准入已正确，测试在
  首次 tick 后提前检查下一阶段派发。改为既有有界 tick 等待真实派发，不写成功事件。

前序集成 `/tmp/zf-k539-scope-prior-integration-20260924.xml` **138 passed/5 failed/257.14s**；
五红都是 `tests/test_cli_flow.py` 的已有 ZF-TEST-024（3）、025（1）、026（1），与
S6 strict wrapper 一一对应。用独立 Python 测试进程仅加载 HEAD 原始 workflow_delivery
模块（未改工作树），`/tmp/zf-k539-scope-unmodified-caller-baseline-20260924.xml`
仍为 **5 failed/10.85s**，证明非 scope 透传回归；其他生产模块仍来自当前工作树，
这不是全仓 HEAD 基线。原集成不改称全绿，也不因此关闭 024/025/026。

premerge `/tmp/zf-k539-scope-premerge-20260924.xml` **47 passed/4 failed/17.56s**，
仍是 ZF-TEST-040。额外重复闭包 `c2-clean-integration` 已主动中止（exit 2，99 项
已执行），不计验收，避免为获得绿色重复前序链。完整 concurrent 终态交付、绝对
target_root 与相对 scope 的组合、Task/contract drift 负例及整体目标仍待完成。

## ZF-TEST-093：并发准入把请求快照 SHA 当配置身份，相同配置的不同 Run 被误拒绝

```text
tests/test_run_isolation_config_identity.py
```

> 状态: fixed（2026-09-24；不可变快照验证与配置内容身份比较已修复，前序联调通过）

正式 General 与 Issue proposal 的 effective snapshot 含不同 request_id/source path，
所以完整 SHA 不同，但两份 `config` body 完全相同。旧 `run_isolation.py` 直接比较
完整 SHA，返回 `concurrent effective config digest mismatch`，阻断合法并发入口。
初始红 `/tmp/zf-k539-concurrent-missing-scope-red-20260924.xml` 保留；随后把它误判为
正确跨配置拒绝的 `mixed-config-final` 绿报告已撤销其验收意义，不更改历史报告。

修复仅在 run_isolation：先校验 immutable sidecar，再比较完整 config 稳定 JSON SHA；
把准入事件的纯摘要关联到已校验 descriptor，保留未知摘要漂移拒绝和旧无 ref 兼容。
显式坏 ref 不回退，缺失/篡改/schema/config 结构错误返回结构化 blocker。合同绑定
事件不重复提供 effective_config_digest 时，使用 descriptor 自带 SHA 验证。

红绿证据：

- `/tmp/zf-k539-config-identity-resume-red-20260924.xml`：2 passed/1 failed，草稿漏接
  `SidecarRefError`；后续 `resume-first`、`caller`、`closure` 中间失败均保留，分别检出
  descriptor-only 事件和 raw/semantic identity 混用，不能把文件名当作绿灯。
- `/tmp/zf-k539-config-identity-impact-20260924.xml`：99 passed/20.06s，覆盖 active/new
  快照故障、摘要漂移与顺序、正式缺 scope 排队、run admission、proposal、Run Contract、
  sidecar 和 Generic mock E2E。
- `/tmp/zf-k539-config-identity-dev-verify-20260924.json`：456 passed/138.07s，12 文件
  自动影响闭包，包含 K539-A/B、generation、reader/writer、Research 和外部 Issue 调用方。
- `bash scripts/dev-premerge-gate.sh`：47 passed/4 failed/6.48s，仍为 ZF-TEST-040，未调阈值。

实现合同见 [concurrent-run-config-identity](../../../../docs/impl/concurrent-run-config-identity.md)。
这里只关闭配置误拒绝；scope 正式透传、合法 concurrent 交付、外部 owner 收件仍待完成。

## ZF-TEST-092：排队请求覆盖 active contract，导致在途 Run 完成门禁串用合同

```text
tests/test_goal_completion_contract_isolation.py
```

> 状态: fixed（2026-09-24；逐 claim 不可变合同读取已接入真实完成门禁，已修复）

共享项目先正式提交 General，再提交 Issue/PRD/Refactor 等待执行。General 五阶段签名
结果已准入且 artifact.delivery.verified 存在，但 completion 被拒绝，队列无法释放。
`workflow_submission_binding.pin_submitted_run_contract` 每次 submit 更新兼容 active
合同；`goal_completion_gate._evaluate_active_claims` 却对全部 claim 使用该指针，未读
claim 已绑定的 immutable ref/digest，导致 identity_mismatch/profile_mismatch/
undeclared_delivery_artifact。不能靠阻止用户排队或关闭终态门禁绕过。

- 真实入口红：`/tmp/zf-k539-shared-queue-fifth-20260924.xml`，1 failed/18.67s。
- 直接调用方红：`/tmp/zf-k539-contract-isolation-red-20260924.xml`，1 failed/0.77s，
  A claim 实际获得 B 的合同、delivery_policy 和 required_operation_ids。
- 检测器：`tests/test_goal_completion_contract_isolation.py` 和
  `tests/conformance/test_zaofu_testkit_s8_shared_kind_queue.py`。
- 修复方向：逐 claim 验证不可变绑定，策略与 operation 门禁均从同一合同取；损坏/
  不完整绑定不得回退 active pointer，不得让一个坏 claim 阻止其他有效 claim 验证。

修复与实证：`goal_completion_gate._evaluate_active_claims` 使用既有受限 snapshot
读取器逐 claim 校验；仅未声明绑定的历史 claim 保留兼容路径。六项隔离/负向测试
及完成门禁、authority、artifact、Run Contract、准入、Generic mock E2E 合计
`/tmp/zf-k539-contract-impact-20260924.xml` **89 passed/45.98s**。真实四请求 FIFO
与中途重建 `/tmp/zf-k539-shared-queue-seventh-20260924.xml` **2 passed/123.04s**；
增加 General 合同及 dossier/receipt 身份断言后，与前序四类及强策略/双 lane/catalog
联调 `/tmp/zf-k539-first-release-integration-20260924.xml` **47 passed/267.88s**。
premerge **47 passed/4 failed**，均为既有 ZF-TEST-040，未放宽门禁。修复仅证明
该阻断关闭；四类全部交付、并发隔离、外部 owner 实收和整体目标仍未完成。

## K539-F1/F2：共享项目夹具入口与验证命令缺失（非生产缺陷）

> 状态: fixed（2026-09-24；已修复，前序联调 47 passed）

- F1：多 stage General 必须显式传 `scope`；preview 和 submit 均需透传。初稿误用
  preview 顶层字段，实际是 `payload.pattern_id`；原“恰好五 stage”断言改为精确
  保留原 stage 集合再新增五个，拒绝丢失/重复。first/second/third/fourth 排队红报告保留。
- F2：队首 Issue 获得准入后，因共享配置漏可执行验证命令被 task_map gate 正确拒绝。
  补真实 `tests/verify_issue.py` 与 quality gate 声明后产生正式 writer dispatch；
  `/tmp/zf-k539-shared-queue-sixth-20260924.xml` 原红保留，未关闭生产 gate。

## K539-F3：多 Run 工人夹具误关联候选质量及提前检查 Task（非生产缺陷）

> 状态: fixed（2026-09-24；已修复，四类共享串行交付通过）

首轮只按 Run/trace/correlation 筛选，遗漏 `candidate.quality.passed` 的 pdd 身份；
第三轮进一步发现 PRD 的 pdd_id 是规划阶段生成的 feature/task 标识，不必等于请求
goal_id。现在只通过同 Run `candidate.ready` 的 `pdd_id + candidate_head_commit`
匹配 quality，不能借用其他 Run 或同 pdd 不同 commit 的绿回执。独立反例
`/tmp/zf-k539-worker-identity-20260924.xml` **1 passed/1.38s**。

第二轮是在 Kernel terminal 后尚未消费下游 Task done 事件就检查 Store；修正夹具为
有界 tick 等待 terminal 与其 completed_task_ids 全部 done，不直接写 Store。
原 first/second/third 红报告保留。最终
`/tmp/zf-k539-shared-delivery-fourth-20260924.xml` **1 passed/104.64s**，四类均
真实交付并反复重建保持回执 bytes 稳定。本项只改 TestKit，不算新生产缺陷。

收尾新增 operation 归属断言误以为 writer dispatch 含 nested payload，实际正式字段
为顶层 operation_id。`/tmp/zf-k539-serial-final-integration-20260924.xml` 保留
36 passed/1 failed；按真实账本更正后完整链
`/tmp/zf-k539-shared-delivery-final-20260924.xml` **1 passed/81.33s**，全部 child
operation requested/settled 精确 Run 归属通过；该夹具误读已修复，未修改生产事件。

## K537 双 lane live 联调记录（无新增 Issue）

2026-09-24：双 lane host `/tmp/zf-k537-two-lane-first-20260924.xml`
**1 passed/241.86s**，Docker **1 passed/162.42s**；十二物理收据、十三准入、组合
验收/本地 main/tag、restart/cleanup 均通过，worker.runner.failed/worker.respawned/
discriminator.failed 为 0。前序 Issue＋单 lane strong **2 passed/286.99s**，
`/tmp/zf-k537-prior-live-20260924.xml`；自动闭包最终 **52 passed/198.44s**，
`/tmp/zf-k537-dev-verify-20260924.json` status=passed。本轮无新阻断或生产改动。
K530 首次 watchdog 原因仍 open，正常路径绿灯不能追溯关闭；完整 catalog 与外部收件未证明。

## K538 TestKit catalog 部署夹具阻断（非生产缺陷）

> 状态: fixed（2026-09-24；完整 catalog 配置部署与双 lane 确定性联调通过；未修改生产代码）

1. **K538-F1 已修复：mock backend 与完整 catalog 的 on-demand lifecycle 不相容。**
   初次以 mock backend 复用 catalog 的 `on_demand` lifecycle 时，正式 preflight
   正确拒绝 native resume；该红灯是测试替身能力边界，不是 ZaoFu 生产缺陷。首次证据：
   `/tmp/zf-k538-full-catalog-first-20260924.xml`（双 lane 两例失败，失败信息为
   `clarification_required`/native resume admission）。不修改生产 preflight、不把 lifecycle
   改成较弱模式；最终使用 catalog 原始配置并改用已就绪的 recording transport。
2. **K538-F2 已修复：recording transport 缺少就绪协议导致错误 activation。**
   首次改用 codex backend 后，替身缺 `wait_ready`，触发真实 activation，并在错误 cwd
   触发 `git rev-parse` 失败；该红证据保留在 `/tmp/zf-k538-full-catalog-second-20260924.xml`
   （两例失败）。新增 `ReadyRecordingTransport.wait_ready`，让测试替身明确声明已就绪，
   不启动真实 Provider、不放宽 source/candidate/ship gate。

最终绿色证据：部署/semantic-control 直接合同 `/tmp/zf-k538-catalog-deploy-first-20260924.xml`
为 **3 passed/1.65s**；完整 catalog 双 lane、重建、shadow checkpoint
`/tmp/zf-k538-full-catalog-fourth-20260924.xml` 为 **5 passed/87.65s**。本条只记录
TestKit/transport 夹具问题，生产 Issue 不新增；真实 Provider、跨 Kind 竞争和外部 owner
收件仍未证明。

### K538-F3 采样检查点在 dispatch 后错误重建 prepared（非生产缺陷）

> 状态: fixed（2026-09-24；确定性首红、直接 caller 和前序自动影响闭包均完成；未提交）

`/tmp/zf-k538-dev-verify-closeout-20260924.json` 为 failed（80 passed/1 failed）；
失败 snapshot `/tmp/zf-model-evidence-5lv2h5i9/b2d5231332530f193294b0c3-825762/latest.json`
保存了 sampled checkpoint 的原始证据。真实账本已有 `workflow.operation.started`，
但夹具在 tick 后调用仅接受 requested/受限 retry 的 `prepared_operation_from_checkpoint_event`，
合法返回 None。此前自然 25% 采样的绿色运行不能证明该分支。

修复仅在 TestKit：tick 前读取已持久化 prepared context；tick 后仍需精确 operation
的实际 transport dispatch 才可读取必需输入、正式签名提交。生产 requested/running
准入及 shadow 权限均未修改。新增稳定 risk_level=critical 回归保持 catalog 25% 配置，
不替换采样函数，并断言一个 submitted/shadowed、无 applied/rejected、重建不重提。
首红 `/tmp/zf-k538-sampled-red-20260924.xml` **1 failed/3.56s**；中间
`/tmp/zf-k538-sampled-green-first-20260924.xml` 实际仍为 **1 failed/3.71s**，保留不改名：
复用的旧 package Given 缺 `payload.mode`，EventSchema 正确拒绝，随后判 plan_package_stale。
已给测试 package helper 增加可选 blocking mode，默认行为不变，并断言无 discriminator.failed。
最终新回归＋原 helper 的完整 caller `/tmp/zf-k538-sampled-green-second-20260924.xml`
**5 passed/4.41s**。这仍是隔离 checkpoint Given，不替代完整双 lane 流程实跑。

最终全 catalog＋双 lane＋配置与 semantic-control caller
`/tmp/zf-k538-catalog-integration-final-20260924.xml` **55 passed/174.83s**；前序四类
自动闭包 `/tmp/zf-k538-dev-verify-final-20260924.json` **86 passed/276.82s**、status=passed。
注册 **3 passed/0.32s**，Ruff/JSON/diff 通过。原失败未覆盖，生产代码、准入和门禁均未改。

## K536 两 lane 骨干夹具阻断（非生产缺陷）

> 状态: fixed（2026-09-24；严格两个 Task done、两 lane 交付/重建通过；未提交）

1. **K536-F1 已修复：错误要求 writer SHA 为候选祖先。** ZaoFu 聚合使用 scoped
   cherry-pick；读现场代码及 git cherry 确认补丁等价，改为检查无未聚合 source patch、
   两份精确文件内容和两个独立质量命令。candidate→ship 的祖先关系仍严格保留。
2. **K536-F2 已修复：teardown 使用隔离 guard 禁止的 git remote。** 仅改为已有的
   git config --local --list 检查无 remote 配置，不放宽 guard。
   上述首次红 `/tmp/zf-k536-two-lane-full-first-20260924.xml`
   **1 failed/1 error/5.77s**，没有生产改动。
3. **K536-F3 已修复：终态循环在 Task 结算前退出。** audit passed 不能代替子 Task
   done；第二轮 `/tmp/zf-k536-two-lane-second-20260924.xml` **1 failed/9.13s**。
   最终等待条件同时要求报告通过和两个 Task done，12 ticks 预算未延长，重建后也检查
   done。第三轮曾临时容许 review，其 **2 passed/21.42s** 不计作严格验收，已撤回。

最终 `/tmp/zf-k536-two-lane-final-20260924.xml` **2 passed/21.82s**；顺序与反序+
writer/verify 中途重建均通过，两 lane 实际 worktree、同候选 SHA、产品组合 probe、
main/tag/owner dossier、两个 Task done、重放无重复以及 source 不变均成立。
前序 shared worker＋单 lane 强策略联调 `/tmp/zf-k536-two-lane-direct-20260924.xml`
**25 passed/63.37s**。最终四类自动闭包 `/tmp/zf-k536-dev-verify-20260924.json`
**50 passed/99.20s**；S8/coverage 两项注册检查 **2 passed/0.25s**。这是确定性
transport 层，不是双 lane live 或完整 catalog 部署。

## K535-F1 强策略 live 夹具基线污染（非生产缺陷）

> 状态: fixed（2026-09-24；独立强策略 host 全链、重启与清理通过；未提交主仓）

K535 首次浏览器批准被正式 `git_delivery_baseline_dirty` 门禁拒绝。只读 Git diff
确认唯一 tracked 变更是 zf.yaml 的 kind_routes 和唯一 tmux_session，均由 live 夹具
在初始 Git baseline 后加入；未派发业务 worker、未 ship，不是 Provider 登录问题。

仅在 `LiveGeneralRuntime` 配置结束、workflow 提案前，将隔离 tmp_path 项目的最终
zf.yaml 精确提交为 Given，并更新 route.base；增加路径、空 index 和 clean 断言。
不改生产预检、不降低 blocking/ship 策略、不提交或合并 ZaoFu 主仓。
红证据 `/tmp/zf-k535-strong-first-20260924.xml` **1 failed/41.97s** 保留；现场
browser-gate.cleanup_ok=true。第二轮 `/tmp/zf-k535-strong-second-20260924.xml`
**1 passed/196.80s**，Docker **1 passed/126.05s**、restart/cleanup=true，证明该
基线阻断已解除。该轮另有下面 F2 的崩溃/恢复，不能作为无阻断运行证明。

## K535-F2 reader 夹具抢读落后 manifest（非生产缺陷）

> 状态: fixed（2026-09-24；确定性红绿、最终强策略 host/重启/清理通过；未提交）

第二轮 module-parity-scan 的首次 PID 627187 以 `KeyError: attempt_source_manifest`
退出，随后 watchdog 正确记录空 agent 进程树并重启，最终交付成功。首次进程失败
记录在该现场 `project/custom-state/testkit-worker-processes/`，不能被后续成功收据覆盖。

`EventWriter._append_with_projectors` 先 append，再执行 fanout manifest projector；
正式 dispatch 的嵌套 payload 已携带完整 manifest/ref/hash，而 TestKit reader 在收到
该事件后又读取可能仍停留于 queued 的 manifest。只补 child 顶层字段不能消除该窗口。
最终改为读取精确 dispatch 自带 payload 的深拷贝；不等任意时长、不绕过 source SHA/
read-ledger/签名准入，不修改生产代码或 watchdog。正常 host 增加禁止 runner failure/
respawn 的断言，不再让恢复后的绿灯掩盖首次 worker 崩溃。

确定性红 `/tmp/zf-k535-reader-race-red-20260924.xml` **1 failed/3.12s**；
聚焦绿 `/tmp/zf-k535-reader-race-green-20260924.xml` **1 passed/1.33s**。
最终强策略 `/tmp/zf-k535-strong-final-20260924.xml` **1 passed/210.31s**，Docker
**1 passed/135.09s**；九 child 加一 synth、十一准入，blocking Product Acceptance 和
真实本地 ship、source 不变、restart_verified/cleanup_ok=true；runner failed/respawn
均为 0。直接合同 `/tmp/zf-k535-reader-final-direct-20260924.xml` **32 passed/117.58s**。
前序 exploratory host 3 passed/481.50s 期间夹具代码曾变化，
不计入最终稳定代码验证。固定代码后 `/tmp/zf-k535-prior-final-20260924.xml`
PRD＋默认 Refactor **2 passed/387.80s**，restart/cleanup=true；最终自动影响闭包
`/tmp/zf-k535-dev-verify-20260924.json` **48 passed/108.59s**、errors/warnings 空。

K530 原现场没有首次 traceback，本轮相似现象不等于历史根因已被证明；该观察仍 open。

## K533 PRD live 测试夹具阻断（非生产缺陷）

> 状态: fixed（2026-09-24；PRD 真实 CLI/tmux/Docker 浏览器、正式重启与清理通过；未提交）

本轮只修复/扩展 TestKit，没有修改生产 runtime，不新增 ZF-TEST 生产编号。

1. **K533-F1 已修复：缺少 target_root。** PRD workflow-start 提案漏传正式必需参数，
   预检正确拒绝。已绑定隔离 project；红 `/tmp/zf-k533-prd-first-20260924.xml`
   **1 failed/40.95s**，未关闭 preflight。
2. **K533-F2 已修复：浏览器等待预算不足。** 原 60 秒 Task poll 在实际 PRD 多阶段链
   终态之前停止。仅显式 live delivery 使用 150 秒 poll/210 秒 test；recording 路线
   仍为 60 秒，不修改生产 watchdog/timeout。红 `zf-k533-prd-second-20260924.xml`
   **1 failed/103.74s**（同 `/tmp/`）。
3. **K533-F3 已修复：误判 synth 来源。** 正式 CLI result-submit 的 canonical 与
   admission 事件不带 kernel origin；改为精确核对 CLI admitted_event_id、canonical
   semantic_submit_admission_event_id、admission.id、zf-cli actor、blocking mode、
   同 Run/operation 及 ref/SHA。child 仍保留 worker/Kernel origin 检查。红 third
   **1 failed/135.28s**、fifth **1 failed/123.10s**，均为上述同名 `/tmp/zf-k533-prd-…`
   JUnit，不把正常业务终态强行改成生产缺陷。
4. **K533-F4 已修复：Kernel aggregate 被套用 worker 读取账本。** scan 自动聚合的
   admission.read_ledger_ref 合法为空；全部九次准入验证 envelope/control-result，
   八次实际物理提交额外验证 read-ledger ref/SHA，不虚构第九份 worker 收据。红 fourth
   **1 failed/124.09s**，保留原始 JUnit。

修复后 `/tmp/zf-k533-prd-sixth-20260924.xml`：**1 passed/160.79s**；Docker Playwright
**1 passed/94.49s**，unexpected/skipped/flaky 均 0。现场
`/tmp/zf-k533-prd-sixth-20260924/test_browser_issue_start_is_dr0/live-browser/browser-gate.json`
为 pass、restart_verified=true、cleanup_ok=true。7 child + 1 synth、9 admitted，
精确 candidate/verification/阶段顺序、父子 Task、同 Run dossier/receipt/owner-request
以及正式重启无重复均通过。五份红报告均保留，不能用通过记录覆盖历史失败。

前序联调 `/tmp/zf-k533-prior-live-20260924.xml`：General+Issue **2 passed/196.40s**，
两份 browser-gate 均 pass、restart_verified=true、cleanup_ok=true。最终 shared worker
和 PRD 重建/缺失读取/stale-target 拒绝合同 `/tmp/zf-k533-post-live-direct-20260924.xml`
**17 passed/28.73s**；自动 dev-verify 另为 **28 passed**（不含 host）。

边界：这是脚本语义经真实 transport 的本地闭环，不是外部 owner 收件或真实模型证明；
K530 首次 watchdog 原因仍 open，Refactor live/共享资源竞争仍待完成。

## ZF-TEST-091：就绪探测耗尽 deadline 时漏记超时诊断

```text
tests/test_tmux_transport_lifecycle.py::test_ready_deadline_during_liveness_check_keeps_timeout_diagnostics
```

> 状态: fixed（2026-09-24；最小生产修复，未提交）

K532 调用方联调暴露 `test_codex_ready_timeout_is_bounded_and_structured` 偶发返回
`provider_ready_unproven`。根因是 `TmuxReadinessMixin.wait_ready` 在活性检查返回后
预算恰好耗尽，直接 `return False` 绕过 `_record_readiness_failure`；不是认证问题。
新增受控时钟测试确定性重现：活性检查从 0 推进到 2 秒，预算为 1 秒，诊断为空。

修复只将提前返回改为退出循环，复用统一失败记录；不延长预算、不放宽 ready 判定。
红 `/tmp/zf-k532-readiness-red-20260924.xml` **1 failed/0.35s**；修复后的共享
transport、watchdog、SpawnCoordinator、preflight、Task Pipeline、恢复与 mock E2E
联调 `/tmp/zf-k532-impact-20260924.xml` **160 passed/19.81s**。
说明：[Worker 恢复与诊断](../../../../docs/impl/worker-respawn-result-contract.md)。

## ZF-TEST-090：CompositeTransport 丢失 worker 生命周期诊断

```text
tests/test_composite_transport.py
```

> 状态: fixed（2026-09-24；最小生产修复，未提交；不是 K530 首次判死根因修复）

实际 `make_transport` 返回 CompositeTransport，而其没有转发底层
`lifecycle_snapshot`。因此 watchdog 的 `worker.runner.failed` 仅留下空 PID、
命令及 cwd，已有底层进程树证据无法进入事件，阻断首次死亡原因追踪；Task Pipeline
调用同一可选接口也无法看到真实 cwd。未把诊断缺失等同于真实进程死亡。

修复按 instance_id 路由已有 snapshot，不支持该接口时返回 None，未知/歧义角色仍
拒绝；不编造 PID/存活证据，不修改 watchdog 阈值、重派策略或 canonical Store。
真实 `Orchestrator.run_once` 的 watchdog caller 测试对照 direct/composite，验证
故障事件原样保留 snapshot，且继续正常恢复；夹具补齐原协议已有的 context 参数。

- 红 `/tmp/zf-k532-lifecycle-red-20260924.xml` **1 failed/1 passed/1.34s**：
  直接 transport 通过，composite 丢失诊断。
- 首次联调 `/tmp/zf-k532-lifecycle-green-20260924.xml` 实际为 **41 passed/2 failed**，
  名称不代表通过：一项是旧 fake 缺 context，另一项是上述 091，不覆盖该报告。
- 直接回归 `/tmp/zf-k532-lifecycle-final-20260924.xml` **43 passed/4.09s**；
  Task Pipeline/恢复/mock E2E `/tmp/zf-k532-callers-20260924.xml` **42 passed/10.71s**。
- 修复 091 后共同影响闭包 `/tmp/zf-k532-impact-20260924.xml` **160 passed/19.81s**。
  premerge 仍 **47 passed/4 failed/7.54s**，均为既有 ZF-TEST-040；未抬 cap。
- 最终 live `/tmp/zf-k532-live-final-20260924.xml` **2 passed/177.13s**：General＋
  Issue 正常链同 Run 交付、正式重启幂等与专属资源清理通过，两份 browser-gate 的
  restart_verified/cleanup_ok 均 true。本轮无 watchdog 重派，不是其历史根因修复证明。

## ZF-TEST-089：light reader retry briefing 丢失已有 Task 首行身份

```text
tests/test_fanout_retry_briefing.py
```

> 状态: fixed（2026-09-24；生产 renderer 最小修复，未提交；不代表 watchdog 根因已解决）

K530 live 红显示重派的 reader briefing 以 `# Fanout Retry:` 开头，而 dispatch 保留
task_id，严格 worker 身份检查因缺 `Active task:` 退出。K531 在当前工作树独立复现：
真实 candidate.ready→reader dispatch→timeout→第二次 transport，初次标记正确，
retry 标记丢失；不是测试手写 retry 文件。

根因：`fanout_retry_briefing.write_fanout_retry_briefing` 只给 writer 写首行，遗漏
非 blocking reader。修复复用初次 reader 的 `fanout_task_id`，优先 child.task_id，
随后取 payload/trigger 已有 Task 锚点；无身份不虚构。reader 无 canonical Task
写权限的约束不变；不修改 watchdog、retry 预算、结果 schema 或事件准入。
说明：[Worker 恢复与 retry 身份](../../../../docs/impl/worker-respawn-result-contract.md)。

- 红：`/tmp/zf-k531-retry-red-20260924.xml`，**5 failed/1 passed/1.01s**。
- 绿：`/tmp/zf-k531-direct-green-20260924.xml`，**14 passed/1.26s**；含实际 retry
  caller、Task/parent/trigger 身份、无 Task 控制、worker 无 stdin 不提交与异常证据保留。
- 联调：`/tmp/zf-k531-dev-verify-20260924.json`，reader/writer/fanout 调用方通过；
  整个确定性层 **557 passed/4 failed/7 skipped/146.69s**，4 红的修复前对照仍失败，
  见下方基线分类。mock E2E **10 passed/8.02s**；premerge 为已知 ZF-TEST-040 的
  **47 passed/4 failed/12.16s**，整体 receipt 明确保留 failed，不调高 cap。
- live：`/tmp/zf-k531-live-20260924.xml`，Issue+General **2 passed/233.35s**；
  Docker 分别 48.3s/59.1s，各 1 passed、无 skipped/unexpected/flaky，正式 restart
  幂等与清理全部通过。正常路径联调不冒充 live retry/故障恢复验收。

## K531 调用方联调的既有红分类（非本轮回归）

1. wake pattern 三项：artifact.delivery.verified、run.resumed、
   self_issue.assessment.requested，已有 ZF-TEST-028，仍 open。
2. `test_stream_json_rotates_session_id_on_already_in_use` 的旧 mock 只产 `_Msg()`，
   没有 ZF-TEST-069 修复后必须的唯一终态成功 ResultMessage，第二次 drain 被正确
   视为 incomplete_result，旧测试却期望 alive。此夹具仍待更新，不重开生产 069。
3. 在独立测试进程以内存加载本轮修复前的 retry renderer（其余脏树保持原样），
   精确重跑上述 4 case，`/tmp/zf-k531-prechange-baseline-20260924.xml`
   **4 failed/2.87s**，与本轮失败一致；未 checkout/revert 共享工作树文件。

这些红仍影响整套门禁，不因本轮局部通过而豁免；本轮不扩大为 unrelated wake/stream
生产改动，后续按是否阻断主干判断修复顺序。

## K530 后续观察：live watchdog 重派与 retry briefing 身份缺口

> 状态: open（2026-09-24；watchdog 判死根因未定位；retry 首行缺口已拆出 ZF-TEST-089 fixed）

红：`/tmp/zf-k530-live-final-20260924.xml` **1 failed、2 passed/263.69s**。
General live 和旧 Issue 浏览器通过；Issue 的浏览器也完成，但严格 dispatch/receipt
集合检查发现 4 次 dispatch 只有 3 份收据，因此整个 host case 正确保持 fail。
证据根：`/tmp/zf-k530-live-final-20260924/test_browser_issue_start_is_dr0/`。

已证实的顺序：verify 首次 dispatch `evt-ca7886a80e8d` → dead-watchdog 的
`worker.runner.failed` → respawn 注入旧 briefing → retry dispatch `evt-c2d7f1ee36de`
→ 首次 briefing 的结果入账 → Kernel terminal。retry briefing 首行是
`# Fanout Retry:`，不是 `Active task:`；脚本 worker 对该次输入的身份断言失败。
最终 pane 日志只保留 respawn 后的进程，不能据此判断原 worker 死亡原因；失败事件的
lifecycle 也是空诊断，不能证明真实 pane 死亡或 EOF。

排除错误归因：曾推测 EOF 并短暂加入 live worker EOF 常驻逻辑，核对日志后已完全
撤回；没有任何常驻/关闭 watchdog/放宽收据断言的修复留在代码中。
`/tmp/zf-k530-live-final2-20260924.xml` **3 passed/211.06s** 仅证明随后未复现，
不能当作该问题的修复证据（运行中撤回了推测性代码）。

下一步：保留原红、补首次 pane/worker 失败的诊断证据，区分进程退出、探测误判与
恢复派发；单独复现 retry briefing 的 Active task 缺口。若成为主干稳定性阻断，按
用户现有授权直接修复并新增红绿记录；不可把第四轮正常路径绿灯外推为本项 fixed。

K531 更新：已独立复现并修复 Active task 缺口（089），worker 初始化/执行异常和
EOF 现按 PID+启动时间保存到 custom-state/testkit-worker-processes，不因 respawn
覆盖。两个异常阶段与随后 replacement 的确定性控制通过，live 两链已实际接线。
本轮 live 的 EIO 发生在正式 stop 的终端关闭阶段，不是首次 worker 异常证据。
SIGKILL 可留下 running 记录，它不证明进程还活着；仍须结合真实 PID/生命周期判断。

K532 更新：已修复诊断转发缺口 ZF-TEST-090。修复前本轮正常 Issue live
`/tmp/zf-k532-live-issue-20260924.xml` **1 passed/75.26s**，只说明未复现。
旧 retry briefing 的 AssertionError 发生在重派之后，不能倒推首次死亡原因；
首次 watchdog 观察仍 open，不以正常链绿灯或诊断修复冒充根因修复。

K564 更新（2026-09-25）：真实 Python 子进程已验证 EOF、初始化 KeyError、
briefing AssertionError、语义执行 KeyError 和 SIGKILL；replacement 后旧记录字节
不变，执行失败的 transport receipt/PID 对应失败进程，SIGKILL 父进程返回 -9、
worker 仅留下无 finished_at/traceback 的 running，不把它判作仍存活或 EOF。
`/tmp/zf-k564-process-watchdog-final-20260925.xml` **44 passed** 包含上述控制和
Composite→watchdog 的缺失/异常/空快照矩阵。生产代码未改，不是原始失败根因复现。
原 respawn 事件 causation_id=null，只能确认同角色先失败后重启的账本顺序；本轮
没有补造历史因果引用。保留本项 open，后续用新现场进程记录判定退出原因。

tests/testkit/test_tmux_general_worker.py::test_sigkill_keeps_unrecorded_exit_distinct_from_eof
tests/testkit/test_tmux_general_worker.py::test_real_worker_exit_evidence_survives_replacement
tests/test_capture_logs_watchdog.py::TestWatchdogEvents::test_missing_process_evidence_is_not_replaced_by_respawn_identity

## K530 Issue 真实 CLI/tmux 夹具阻断（非生产 Issue）

> 状态: fixed（2026-09-24；Issue 浏览器/真实 runtime/三 worker/重启闭环通过；未修改生产代码）

原始失败证据保留，未覆盖：

1. `/tmp/zf-k530-issue-first-20260924.xml`，1 failed/206.70s：writer dispatch 的
   顶层 task_id 为 null，真实子任务在 payload.task_id；stdin worker 错用顶层字段核对
   Active task，尚未提交就退出。修复为解析两种正式 dispatch 形状并严格核对 briefing；
   新增两种形状的确定性回归，仍禁止无匹配 stdin 时自行提交。
2. `/tmp/zf-k530-issue-second-20260924.xml`，1 failed/90.93s：真实 CLI 的 schema gate
   拒绝 verify 报告，缺少顶层 summary 与 verification_result.failure_class。
   `/tmp/zf-k530-issue-third-20260924.xml`，1 failed/90.90s：verify 已通过，Judge
   报告仍缺顶层 summary。补齐真实 probe/closure 结果的摘要与 failure_class=none；
   旧 Issue 确定性入口也检查正式 EventSchemaRegistry，提前检出不合规夹具。

同时修正共享 host helper 的两项夹具适配：mock-bin/cat 对 shell 的普通 cat 调用转发
到 /bin/cat；清理时按配置读取 pane-grid binding 并保存 pane 日志。等待终态期间若
浏览器已失败立即报告，不再无条件耗尽 180s。未放宽生产 gate、未改 Issue light 策略。

绿色：`/tmp/zf-k530-issue-fourth-20260924.xml`，**1 passed/76.84s**；Docker
Playwright **1 passed/30.3s**。同 Run 的三份实际 stdin/briefing SHA/PID 收据、正式
emit→Kernel admission、真实 candidate ref/commit/probe、父子 Task done、terminal/
dossier/receipt/owner-request 均通过。真实 CLI stop/start 后两次 watcher heartbeat
无重复业务事件或交付字节变化，browser-gate 的 restart_verified/cleanup_ok 均 true。

固定代码最终复核：`/tmp/zf-k530-issue-stable-20260924.xml` **1 passed/77.59s**，
Docker **1 passed/30.9s**，restart/cleanup 均 true；共享确定性合同
`/tmp/zf-k530-deterministic-20260924.xml` **37 passed/130.85s**。本段 fixed 只覆盖
已定位的 Task ID/schema 夹具错误，不包含上方另列的 watchdog/retry open 观察。

边界：脚本 worker 语义，不是真实模型；local owner-request 不代表外部收件。后续最终
General live/Issue live/旧 Issue 浏览器与直接共享合同联调结果见 K530 done/coverage。

## K529 真实 CLI/tmux 浏览器测试夹具阻断（非生产 Issue）

> 状态: fixed（2026-09-24；真实 CLI/五 worker/浏览器/重启闭环已通过；未修改生产代码）

裸红均保留，未覆盖旧报告：

1. `/tmp/zf-k529-live-first-20260924.xml`：runtime 已 terminal，但浏览器未结束就读取
   browser-truth.json。修复为等待 Docker Playwright 正常退出后读取证据。
2. `/tmp/zf-k529-live-second-20260924.xml`：Given Task 缺 workflow ownership，live startup
   按普通任务领取，导致待批 proposal 正确被 workflow_task_stale 拒绝。使用正式
   `mark_workflow_managed_task` 初始化专管父 Task；不延后启动来避开 live 调度。
3. `/tmp/zf-k529-live-third-20260924.xml`：错误用任意 owner-visible 通知代表流程终态；
   supervisor 通知更早出现。改为等待同 Run terminal，再按 terminal_event_id 筛选
   交付 request；保留系统通知，不关闭 supervisor。
4. `/tmp/zf-k529-live-fourth-20260924.xml`：terminal 出现后立即断言 Task done，未等待
   下一 EventWatcher 消费周期。改为有界等待真实 TaskStore settlement，不手动 tick。

绿色：`/tmp/zf-k529-live-fifth-20260924.xml`，**1 passed / 102.80s**；Docker
Playwright **1 passed / 48.7s**。五个 worker 的 stdin prompt、briefing SHA、PID、
dispatch/admission id 一一对应；正式 `zf result submit --scratch` 后由独立 CLI runtime
完成 Task/terminal/dossier/receipt/owner-request。CLI stop/start 不重复交付，
`browser-gate.json` 为 verdict=pass、restart_verified=true、cleanup_ok=true。

最终联调：`/tmp/zf-k529-live-final-20260924.xml` **2 passed/136.75s**（新 live + K528
General 基线），live browser 1 passed/45.4s、cleanup/restart 均 true；
`/tmp/zf-k529-dev-verify-20260924.json` **35 passed/23.62s**，无新增失败。

范围：mock 语义通过真实 tmux，非真实 Provider；未关闭其它三类 live 流程、同进程多 Run
资源竞争或外部 owner 收件。后续最终联调报告见 K529 done/coverage。

## K528 浏览器交付夹具初始化阻断（非生产 Issue）

> 状态: fixed（2026-09-24；已保留裸红，并通过四流 Docker 同 Run 矩阵；未修改生产代码）

从 General 扩展至交付流程时，旧 conformance fixture 缺少浏览器启动所需 Given：

- 事件账本尚未建立，snapshot 被正确拒绝为 project_uninitialized。
  红：`/tmp/zf-k528-issue-first-20260924.xml`，1 failed/13.49s。
- 单 Kind fixture 未注册 optional workflow.kind_routes，route 被正确拒绝为 unavailable。
  红：`/tmp/zf-k528-issue-second-20260924.xml`，1 failed/13.64s。
- 待启动 Task 缺 source precedence、workflow execution owner 与 verification_tiers；
  第一版又误用了 command tier `task_non_smoke`，合法 Task tier 应为 `e2e`。
  红：`/tmp/zf-k528-issue-third-20260924.xml`（1 failed/13.73s）与
  `/tmp/zf-k528-issue-fourth-20260924.xml`（1 failed/13.67s）。

修复：EventWriter 写入模拟 bootstrap 事实；临时 zf.yaml 注册已存在的 entry stage；
Task 使用真实 spec/product contract ref、sanctioned workflow ownership 与 e2e tier。
未手写 task_map.ready/candidate/terminal、未另造 Run、未放宽生产预检或 gate。

绿证据：`/tmp/zf-k528-matrix-first-20260924.xml`，**4 passed/107.37s**。
General/Issue/PRD/Refactor 分别有 Docker Playwright 1 passed、0 failed/skipped/flaky；
各自 browser-gate.json 均 verdict=pass、cleanup_ok=true。原始目录保留红绿证据，
仅专属临时服务/容器已关闭。此条不新增生产 Issue 编号。
前序 PRD/default Refactor/catalog strong 联调 **15 passed/75.29s**，Web typecheck/unit
通过；`/tmp/zf-k528-dev-verify-20260924.json` 所有自动 tiers exit 0。

## ZF-TEST-088：canonical Run 深链未显示已存在的交付 dossier

```text
tests/e2e/test_testkit_browser_workflow_terminal.py
```

> 状态: fixed（2026-09-24；生产 Web 修复，未提交；Docker 同 Run、刷新与历史链接回归通过）

浏览器批准 workflow-start 后，同一 Run 的 Kernel terminal、Task done 和真实
dossier/receipt/owner-request 已产生，但 `page=runs&run_id=<run>` 没显示 dossier。
归档 Run detail 接口为 404，Goal dossier 接口实际为 200。根因是
`ObservabilityPage.readRunDossierDeepLink()` 只读取旧 `obs_run_id`，canonical
URL 中的 `run_id` 丢失；因此无法独立于归档详情渲染已存在的 Goal dossier。

修复：优先读 `run_id`，兼容历史 `obs_run_id`；不伪造归档、不变更 Kernel/Store
或弱化完成条件。说明：[Run dossier 深链](../../../../docs/impl/run-dossier-deep-links.md)。

- 裸红：`/tmp/zf-k527-browser-third-20260924.xml`，1 failed/42.26s；Docker trace、
  DOM 快照、请求/响应及清理证据在同名目录的 `browser-workflow-terminal/`。
- 首次绿：`/tmp/zf-k527-browser-fourth-20260924.xml`，Python 1 passed/27.35s，
  Docker Playwright 1 passed/11.7s，same-Run 终态/交付/重建及刷新断言通过，cleanup=true。
- 最终绿：`/tmp/zf-k527-browser-final-20260924.xml`，Python **1 passed/28.99s**，
  Docker **1 passed/10.4s**，另增加历史链接转 canonical 与 API dossier terminal ID
  精确核对；全部通过。Web typecheck/unit exit 0；前序影响闭包 **41 passed/177.14s**：
  `/tmp/zf-k527-impact-20260924.xml`；记录位于 steps.md K527 done。
- 边界：修复的是 UI canonical 深链选择；归档详情接口的 404 合同未修改。
  其他三流浏览器后续执行、外部 owner 收件与真实 Provider 不在本次证明内。

## K527 测试夹具接线阻断（非生产 Issue）

> 状态: fixed（2026-09-24；测试夹具已修复并随 Docker 回归通过）

1. 初稿误认为 pending proposal 的 Details 必须展示 task_id；实际组件只列其支持的
   contract 信息。改为浏览器读取真实 pending API，并核对点击生成的精确 Task 与
   proposal_event_id，未取消身份断言。红：`/tmp/zf-k527-browser-red-20260924.xml`。
2. 初稿在 terminal 刚写入时立即断言 Task done，未给真实 reactor 下一 tick 消费
   terminal。改为有界驱动直到 terminal 与 Task done 同时成立，不改生产结算逻辑。
   红：`/tmp/zf-k527-browser-second-20260924.xml`。

两项的首次共同绿证据：`/tmp/zf-k527-browser-fourth-20260924.xml`。不冒称生产 bug。

## issue found from product test

截至 2026-09-16，通用 Product TestKit 的独立参考产品闭环没有发现新的 ZaoFu `src/` 生产缺陷。具体生成产品项目 暴露的 SBOM 摘要、跨语言脚本入口和浏览器场景失败属于产品项目结果，按约定不在本节登记。若后续产品测试发现 ZaoFu 生产缺陷，应在本节下新增可复现条目并保留对应检出测试，不直接修改生产代码。

## K526 测试夹具阻断：签名提交 attempt identity 混用（非生产 Issue）

> 状态: fixed（2026-09-24；测试夹具已修复并回归，未修改生产代码；未提交）

强策略 Refactor 首次接线把旧 `task.attempt.sent` 的 TaskAttempt ID 直接填入 signed
semantic scratch；正式 `SemanticResultSubmitService` 以 operation request 签发的
active attempt identity 校验，因而在 writer 处得到 `impl_self_check_invalid`。
attempt 混用红证据：`/tmp/zf-k526-strong-second-20260924.xml`。更早的
`/tmp/zf-k526-strong-first-20260924.xml` 则停在夹具错误地从 writer manifest payload
读取 semantic submit 字段；它也已改为从正式 operation request 读取，不能冒称同一失败。

修复：夹具从当前 operation request/result scratch 读取签发 identity，再生成 semantic
result；不放宽 attempt 校验、不修改 runtime。强策略最终矩阵
`/tmp/zf-k526-strong-matrix-third-20260924.xml` 为 **6 passed/40.62s**，单独标记为
测试接线 fixed，不新增 ZaoFu 生产 Issue。
后续 S0 + 正常强策略完整交付（含 final tag SHA）为 **20 passed/34.81s**：
`/tmp/zf-k526-closeout-20260924.xml`。

## K525 测试夹具阻断：Refactor source/target 重叠（非生产 Issue）

> 状态: fixed（2026-09-24；夹具已修复并回归，未修改生产代码；未提交）

检测入口：`tests/conformance/test_zaofu_testkit_s8_refactor_route.py`。
初稿把 source_root 和 target_root 指向同一目录，正式准入正确拒绝
`workflow_source_target_overlap`；红证据
`/tmp/zf-k525-refactor-plan-first-20260924.xml`。

修复：`tests/testkit/refactor_route.py` 使用互斥的兄弟 source/target 临时目录，
并在每例结束断言 source 文件集合和内容未变；未关闭 overlap 预检。
直接绿 `/tmp/zf-k525-refactor-plan-second-20260924.xml`（1 passed/3.10s）；
完整默认链矩阵 `/tmp/zf-k525-refactor-matrix-first-20260924.xml`（4 passed/27.42s），
影响闭包 `/tmp/zf-k525-impact-20260924.xml`（213 passed/149.77s）。
这是测试输入错误，单独标记已修复，不新增 ZaoFu 生产缺陷编号或修复数。

另一次强策略探针 `/tmp/zf-k525-refactor-blocking-red-20260924.xml` 在夹具的
off 模式断言停止；它是尚未支持强策略的测试缺口，不是已复现生产缺陷，也不标 fixed。
K525 仅验收编译默认链，catalog blocking/ship_candidate 模板仍待完成。

## ZF-TEST-087：锁定 Self-Issue 根污染普通流程的 worktree 源仓库

> 状态: open（2026-09-30，操作者要求相关生产行为只保留 feature；v7 K519 修复不接入，原有 fixed 证据保留为历史）

本次为合并取舍重开，不是认定 feature 当前仍存在相同的全局根污染。候选受管角色
重定向及生产接线已撤回，feature 原实现保留；隔离回归通过不替代本条原场景的整体验收。
第一处冲突 `src/zf/runtime/self_issue_target.py` 也按同一规则处理：已与首次整合前的
feature 工作文件备份逐字核对一致，v7 新增 `workflow_role_project_root()` 及其候选
测试 `tests/test_self_issue_role_roots.py` 均已撤回。保留 feature 原有未提交改动，
不是用 Git HEAD 覆盖工作文件，也不再次修复该范围的生产代码。
以下 canonical ZaoFu 目标规则及 K519 修复描述仅适用于当时 v7，不覆盖 feature 的执行项目模型。

检测入口：

    tests/e2e/scripts/run_four_flow_kanban_playwright_e2e.sh

红证据：`/tmp/zf-k518-four-flow-20260924.log` 和
`/tmp/zf-four-flow-k518-20260924/project/.zf/events.jsonl`。PRD 的 invoke（seq 301）
明确指定临时项目为 source_refs.target_root，但 `flow.roles.activation.failed`
（seq 398）显示 WorkdirManager 在 `/home/uisee/zaofu_v7` 执行 worktree add，碰到
其他现存工作树占用的 `worker/prd-dev-lane-0` 分支；未清理或修改那个工作树。
seq 400 的 run.admission.rejected 保留实际失败。自动 `self_issue.intake.auto_detected`
（seq 409）创建报障问卷，随后问卷遮挡 Refactor 单选框，浏览器 300 秒超时。
不得关闭 Self-Issue、force click 或删除别人的 worktree 来绕过此失败。

根因：Orchestrator 构造时仍把全局 project_root 改为 Self-Issue 的 ZaoFu 根。
K516 只让 synthesis 使用 workflow_project_root；普通 role activation/workdir、
dispatch 和恢复调用仍消费旧 project_root。doc147 的“普通项目工作流不受影响”
因此未成立。Self-Issue 服务/triage/Fix 则仍必须保留 canonical ZaoFu 目标，不能
不加区分地把所有路径改回当前项目。

### K519 修复与验证（v7 历史，不接入 feature）

Orchestrator 保留选中项目根；Self-Issue/Ingress/Candidate Delivery 继续独立解析
canonical 目标。受管角色通过完整编译合同和精确 role.name 选择源码根，禁止与项目
合同复用同一角色；普通同名前缀不重定向。CLI 首次启动、activation、worktree、reader
pin/write guard、恢复 cwd/briefing 与技能物化统一使用该选择。角色 workdir 生命周期
实现放在 `workflow_role_workdir.py`，不扩大已超限 lifecycle 文件。

回归 `tests/test_workflow_project_roots.py` 使用两个真实临时 Git 仓库和嵌套 state，
覆盖四类普通流程 activation/dispatch/reopen、受管 reader、CLI 启动、关闭 workdirs、
同名前缀及合同冲突。Provider 为 mock，不代表真实 Provider 全链。早期草稿
`/tmp/zf-k519-roots-red-20260924.xml` 为 5 failed/4 passed；原始裸红仍是 K518 Docker。
测试夹具的 GitHub 默认客户端、冻结配置、已有 state 初始化及 Provider readiness
错误保留失败报告，按 mock 边界修正，未据此改生产规则或取消断言。

- `/tmp/zf-k519-start-impact-20260924.xml`：95 passed/37.77s。
- `/tmp/zf-k519-impact-20260924.xml`：321 passed/63.02s，含 services、activation、
  fanout、spawn、lifecycle、managed contract 和 synthesis 调用方。
- `/tmp/zf-k519-final-verified-20260924.xml`：108 passed/43.24s，补 reader 写保护、
  idle recovery briefing 与 CLI workdirs 开/关；同逻辑迁至独立模块后再做直接回归。
- `/tmp/zf-k519-roots-final-owner-20260924.xml`：独立模块接线后 13 passed/5.43s。
- `/tmp/zf-four-flow-k519-20260924`：build/typecheck/bundle 通过；安装 1 passed/19.5s；
  selector 与四流程旅程 3 passed/1.3m；launcher 审计通过。日志
  `/tmp/zf-k519-four-flow-20260924.log`；simulation.done 已写入，专属 tmux 与 8002 已清理。
- `/tmp/zf-k519-s8-20260924/testkit-run.json`：226 passed/11 strict xfailed，完整 session、
  pytest exit=0、TestKit verdict=fail；已有缺陷不计绿色。
- `/tmp/zf-k519-premerge-final-20260924.xml`：47 passed/4 failed；仍为 ZF-TEST-040
  结构债务，不抬 cap。dev-verify 共享脏树规划仍列出无关未映射改动，不宣称整体门禁通过。
- `/tmp/zf-k519-closeout-20260924.xml`：S0 与 S9 Issue 引用审计 20 passed/22.47s；
  JSON、Ruff 与 git diff --check 通过。

只关闭 087 的源码根污染。浏览器证明四流程请求/计划/调用关联，不证明四条最终交付，
未重跑无关 native 三链，未提交本仓库、merge、push 或实现 `/issue-merge`。

### K520 验证范围补记（2026-09-24）

087 仍为 fixed。对 K519 现场只读回报
`/tmp/zf-k520-final-invocation-report-20260924.json` 明确显示 PRD admitted、Issue queued、
Refactor/General 尚无 admission，四流均无终态；入口报告已显式标为 invocation_handoff。
worker 使用 cat，缺少语义结果属于未实现的终态测试链，不是 087 复发或新的生产缺陷。
报告与 admission/flow smoke/terminal audit/S8 operation 影响闭包 **76 passed/13.12s**，
证据 `/tmp/zf-k520-impact-20260924.xml`；不据此关闭四流程最终交付或其他 open Issue。
同轮 premerge 哨兵仍为 **47 passed/4 failed**，证据 `/tmp/zf-k520-premerge-20260924.log`；
失败保持归属 ZF-TEST-040，未抬结构 cap、未将其伪装为 K520 新缺陷。

## ZF-TEST-086：四流程浏览器测试将后台交付任务误当作当前用户任务（测试框架）

> 状态: fixed（2026-09-24 K518；精确事件关联通过，完整四流程另受 087 阻断；未提交）

检测入口：

    web/tests/four-flow-kanban-prd.spec.ts
    tests/e2e/scripts/run_four_flow_kanban_playwright_e2e.sh

红证据 `/tmp/zf-four-flow-k517-20260924/test-results-four-flows`：第二阶段 Refactor
创建后的等待在 90s 超时。EventLog 明确有当前用户任务 `TASK-341FC3` 与对应
`kanban.agent.plan.requested`，但此前约 0.3s 插入了上一条 Issue 流的后台交付任务
`WORKFLOW-F326324B13B7CA7C-DELIVER-001`；旧测试取窗口内第一个 `task.created`，
导致等待另一个 task_id 的计划。不是 085 的配置安装问题，也不是缺少真实 plan。

K517 首稿依赖 `task.created.payload.task.title`；第二次浏览器现场
`/tmp/zf-four-flow-k517-selector-fixed-20260924` 的 trace 证明 API 使用 `payload_slim`
且不带 task 标题，因此首个 PRD 任务也等待超时，尽管账本有实际任务及计划。

K518 同一 spec 改为按 `source=kanban-agent`、task_id、计划中的 task_event_id 与
task_workflow question 的当前 flow marker 关联，不要求 slim 列表携带完整 task。
新增 slim payload、交错后台/其他用户任务、缺失创建事件、错误流程和 task_id 负向对照。
确定性红证据 `/tmp/zf-k518-selector-red-20260924.log`：1 failed；绿证据
`/tmp/zf-k518-selector-green-20260924.log`：2 passed/1.4s。仍要求实际计划及批准后
invoke，不修改生产投影、不删断言或放宽等待时限。

Docker `/tmp/zf-four-flow-k518-20260924`：build/typecheck/bundle 通过；安装 1 passed/19.5s；
第二阶段 2 个 selector 对照通过，真实 PRD、Issue、Refactor 三个 handoff 已选中正确
任务/计划（seq 268/269、345/346、457/458），未混入 seq 467 后台任务。随后真实
页面在 Refactor 单选框被 087 的自动报障问卷遮挡，故完整旅程仍 1 failed/2 passed，
不冒充全链绿。086 仅关闭选择器缺陷；087 独立保留 open。

S0 首次还检出本节只写 TS 路径、缺 shell 复现入口（18 passed/1 failed），已补上
上述正式 launcher；收尾 S0+S9 引用审计 20 passed/32.90s：
`/tmp/zf-k518-closeout-20260924.xml`。

## ZF-TEST-085：自动注入的 External Issue Generic Workflow 阻断普通工作流安装

> 状态: open（2026-09-30，操作者要求 Triage 相关生产行为只保留 feature；停止接入 v7 K517 共存修复）

feature 当前工作文件的正式配置装配入口仍可复现
`one effective config may declare only one safe Generic Workflow contract`。
本条仅登记，不修改 loader/kind envelope/合同选择及 Triage 生产行为；K517 的修复与通过
证据仅代表历史 v7，不代表 feature 已修复。此前建议的下一步移植已由本次操作者决定取消。

### K517 修复与验证（v7 历史，不接入 feature）

保留一份项目 Generic 合同，允许其与当前正式生成器的受管 triage 合同共存；普通多合同、
重复受管、第三份合同与阶段重叠继续拒绝。默认元数据取项目合同；fanout/External Issue
ingress/Run Contract 按 entry/digest 选择，冲突身份拒绝，不用项目产物要求替代 triage。
原 strict xfail 已移除，新增直接测试：

    tests/test_managed_workflow.py

修复后直接矩阵 18 passed/2.72s：`/tmp/zf-k517-scoped-green2-20260924.xml`；
15 文件影响闭包 179 passed/37.62s：`/tmp/zf-k517-impact-20260924.xml`。
S8 完整注册面 `/tmp/zf-k517-s8-20260924/testkit-run.json` 为 226 passed/11 strict
xfailed，pytest exit=0、session 完整、TestKit verdict=fail（保留其他开放缺陷）。
Docker 原入口 `/tmp/zf-four-flow-k517-20260924` 第一阶段 **1 passed/19.7s**，实际
proposal/approval/config apply/restart 均已越过 085；第二阶段暴露独立测试问题 086，
不能把此修复当作四流程全链通过。

草稿中两次新 ingress 测试失败分别因夹具缺 GitHub target 和误读 snapshot envelope；
已修正夹具，不算生产修复。`/tmp/zf-k517-085-red2-20260924.xml` 实为修复后的 1 pass；
`/tmp/zf-k517-085-green-20260924.xml` 实为尚未移除标记时的 XPASS(strict)，均不按文件名
误称红/绿。原始真实裸红仍是下述 K516 报告。

### K516 原始发现（历史）

检测入口：

    tests/conformance/test_zaofu_testkit_s8_workflow_operation_contract.py::test_managed_self_issue_does_not_block_general_workflow_proposal
    web/tests/four-flow-workflow-install.spec.ts

复现命令：

    uv run pytest -q --no-cov -o addopts='' --runxfail tests/conformance/test_zaofu_testkit_s8_workflow_operation_contract.py::test_managed_self_issue_does_not_block_general_workflow_proposal

红证据 `/tmp/zf-k516-085-red-20260924.xml`：1 failed/0.90s。隔离 `_generic_config`
经正式 `inject_self_issue_policy` 注入 locked policy 后，synthesis 仍正常完成，但
`build_workflow_proposal` 抛出 `base config contains a different Generic Workflow`。
浏览器现场 `/tmp/zf-four-flow-k516-20260924` 中 synthesis 已 settled；proposal 重试
三次后 `workflow.operation.failed`，未进入 config apply，Playwright exit=1。

根因：`self_issue_policy.py` 强制注入 `external-issue-triage/v1` Generic Workflow；
`workflow_proposal.py::_materialize_generic_candidate` 拒绝已有不同 Workflow；下游
`kind_envelope.py`、`loader.py::_build_generic_workflows` 也限定一个 effective Generic
contract。不是原来的 request hash 漂移，也不是测试选择器超时。

期望：普通项目 General Workflow 的 proposal/config apply 与受管 Self-Issue triage
能够共存，且各自 entry、completion profile、digest、运行目标及事件账本归属不混淆。
修复不可只放宽 proposal 检查、删除受管 triage、关闭 Self-Issue 或改测试 fixture 躲过问题。
下一步审查受管合同与项目合同的隔离边界，再补 caller/配置重载/四流程浏览器绿证据。
当时尚未修改生产实现；v7 后续有上方 K517 修复证据，但当前合并口径已按本条开头重新开放。

## ZF-TEST-084：完整预合并门 wrapper 初稿阻断与零退出码误报通过（测试框架）

> 状态: fixed（2026-09-23 K514；仅 TestKit，未修改生产 runtime；未提交）

检测入口：

tests/testkit/test_premerge_run.py

初稿把日志 Path 当成已打开句柄，导致
AttributeError；源码采集新增 scope 后夹具未同步也阻断测试。红证据分别为
`/tmp/zf-k514-premerge-draft-red-20260923.xml`、
`/tmp/zf-k514-initial-red-20260923.xml`（1 failed/70 passed）。中间一次夹具修改存在
重复 lambda 参数，收集报错记录在 `/tmp/zf-k514-green-20260923.xml`；该文件名不是绿证据。

补充裸红 `/tmp/zf-k514-verdict-red-20260923.xml` **2 failed/13 passed**：脚本 exit=0
时 skip 和畸形 JUnit 被误报 pass。修复使用 with 管理真实日志句柄，并共用严格 JUnit
校验；skip 保留 fail、畸形/缺失证据为 harness_failure，退出码与测试结果分别保留。
source scope、完整脚本、日志摘要、目录边界及跨报告 verdict 都校验。

直接绿 `/tmp/zf-k514-direct-final-green-20260923.xml` **84 passed/24.45s**，覆盖新入口、
native/mainline 来源、汇总及离线调用方。前一轮调用方闭包
`/tmp/zf-k514-callers-green-20260923.xml` **133 passed/90.73s**；发生在补充零退出码修复前，
不冒充最终完整闭包。最终主干/门禁实跑见 steps.md K514。历史四项结构债务仍未修复，
不得算作本 Issue 已关闭的内容。

最终主干 `/tmp/zf-k514-mainline-ledger-fixed-20260923/testkit-run.json` **192 passed/110.39s**，
来源稳定且 session 完整；首次 191 passed/1 failed 的台账格式红报告完整保留。
五项汇总 `/tmp/zf-k514-final-acceptance-20260923/native-acceptance.json` 各项来源有效，
顶层因既有 ZF-TEST-040 保持 fail；native 与 mainline/premerge 不同来源，未冒充同版本。

## ZF-TEST-083：原生来源绑定汇总误分类失败，并漏校验跨报告矛盾（测试框架）

> 状态: fixed（2026-09-23 K512；新增证据入口的阻断，未修改生产 runtime；未提交）

检测入口：

    tests/testkit/test_native_acceptance.py::test_bound_native_preserves_failure_category_in_source_mode
    tests/testkit/test_native_acceptance.py::test_bound_native_rejects_inconsistent_source_and_execution
    tests/testkit/test_native_run.py::test_real_offline_runner_report_is_consumed_by_source_bound_acceptance

红证据 `/tmp/zf-k512-083-red-20260923.xml`：**7 failed / 6 passed**。来源绑定初稿仅将
通过的 native 行纳入来源比较，导致有来源的测试失败被误归为 harness_failure；反向又把
runner 的 harness_failure 降成 fail。runner HEAD 不匹配、bool 冒充退出码、phase 存在
deselection、外层 session 未完成或 timeout 也可能被当成 pass。

修复：来源证明独立于测试 verdict，保留 fail/harness_failure；交叉核验 HEAD、精确节点、
session、phase、退出码类型和值、清理和 SHA-256。原生 JUnit 的身份及安全解析在所有
分支执行；历史 JUnit 不补写来源。直接回归 **58 passed / 8.81s**，
`/tmp/zf-k512-083-direct-green-20260923.xml`。调用方联调和真实运行另记 K512，不将
合成报告或离线子进程结果视为真实 Provider 通过。
后续调用方联调 **107 passed / 42.85s**，
`/tmp/zf-k512-083-callers-green-20260923.xml`；统一 MAINLINE **167 passed / 91.07s**，
`/tmp/zf-k512-mainline-20260923/testkit-run.json`（verdict=pass，全部普通通过，session 完整）。
K512 三条 source-bound native 实跑也已完成：normal **1 passed/524.40s**、impl
**1 passed/788.71s**、verify **1 passed/610.83s**；均来源稳定、阶段/退出/清理完整。
统一五项汇总唯一保留的红是 premerge 4 项既有结构债务，未将其误登记为 ZF-TEST-083。

## ZF-TEST-082：S0 审计不支持精确 nodeid，且负向夹具依赖已修复 Issue 状态（测试框架）

> 状态: fixed（2026-09-23 K511；测试注册/证据审计阻断，未修改生产 runtime；未提交）

检测入口：

    tests/conformance/test_zaofu_testkit_s0.py::test_stage_registry_and_coverage_matrix_have_the_same_test_surface
    tests/conformance/test_zaofu_testkit_s0.py::test_registered_xfails_are_strict_and_mapped_to_each_executing_stage
    tests/conformance/test_zaofu_testkit_s0.py::test_registry_node_selector_is_checked_not_treated_as_a_filename
    tests/conformance/test_zaofu_testkit_s0.py::test_host_issue_audit_rejects_corrupt_metadata

红证据 `/tmp/zf-k511-callers-final-20260923.xml`：**6 failed / 72 passed / 14.97s**。
S5 中 079 的 `file.py::Class::test` 被当完整文件路径读取；另一处审计会直接过滤掉
该入口。host audit 的损坏注入仍试图替换 071 的 strict xfail/open，但 071 已修复，
注入不再生效或基线断言失败。修复路径读取后，还检出覆盖矩阵漏记 S5 的 079 节点和
S7 的 Task Pipeline hook 回归，继续补齐而不撤销注册；中间证据
`/tmp/zf-k511-082-audit-fixed-20260923.xml` 为 **1 failed / 18 passed**。

修复：文件读取统一取 nodeid 的路径部分；入口存在性仍校验 AST 中的 class/function，
不把拼错节点静默当存在。损坏测试改为只在 monkeypatch 读取中追加合成 open Issue、
strict xfail 和 stage 映射，先证明正向成立再逐项破坏，不写真实 Issue/runtime 状态。
缺失 class/function 控制组、合法节点、合成基线及四类损坏均验证。

最终报告/CLI/原子证据/S0 联合闭包 **82 passed / 32.09s**，
`/tmp/zf-k511-082-callers-green-20260923.xml`。保留既有 137 项主干联调；
不因审计修复重跑真实 Provider，不改变任何 strict xfail 的生产判定。

## ZF-TEST-081：统一原生报告初版无法保留并分类预合并失败（测试框架）

> 状态: fixed（2026-09-23 K511；本轮新增 TestKit 草稿的阻断，非既有生产 runtime 缺陷；未提交）

检测入口：

    tests/testkit/test_native_acceptance.py::test_premerge_any_red_is_preserved_never_waived
    tests/testkit/test_native_acceptance.py::test_native_acceptance_cli_real_entrypoint

首轮证据 `/tmp/zf-k511-acceptance-red-20260923.xml`：**2 failed / 1 passed**。通用
JUnit reader 在解析任何失败项前就要求全绿，导致预合并红报告无法作为完整结果汇总。
这发生在本轮新增报告入口草稿中，不是历史运行产品的新缺陷。后续夹具 testcase name
与 failure message 混用也已修正；早期小范围绿测不替代下述最终严格闭包。

修复：分离 JUnit 格式/计数校验与 verdict；已知结构失败和未知失败均逐项保留、均使
汇总 verdict=fail/exit=1。取消初稿的 `pass_with_known_structure_debt` 判词和名称白名单。
同时读取正确的 testcase 属性、校验原生 stage 身份和输入哈希；历史 PID 不查当前 `/proc`。
29 项回归通过，`/tmp/zf-k511-acceptance-strict-20260923.xml`；注册后 MAINLINE
**137 passed / 76.23s**，`/tmp/zf-k511-mainline-20260923/testkit-run.json`。

真实历史报告经正式 CLI 汇总：`/tmp/zf-k511-native-acceptance-20260923/native-acceptance.json`，
三个原生链和主干 pass，四项原有结构失败保留，最终 fail，证明没有吞掉预合并红。
旧 JUnit 缺 source fingerprint/XPASS 分类，明确 same_revision_verified=false；不宣称发布验收。

## ZF-TEST-079：Claude respawn 在项目根探测会话，遗漏真实 spawn cwd 的 transcript

> 状态: fixed（2026-09-23 K510；生产修复、确定性回归及真实 impl/verify 恢复验收通过；工作区未提交）

检测入口：

    tests/test_spawn_coordinator.py::TestClaudeRespawn::test_respawn_finds_transcript_in_actual_spawn_cwd

真实检出 `/tmp/zf-k510-native-recovery-impl-cwd-fixed-20260923.xml`：078 已保持正确 cwd，
但恢复 argv 仍用相同 UUID 的 `--session-id`，没有 `--resume`；旧 transcript 实际存在于
Task Workspace 的 Claude projects 目录。`SpawnCoordinator._claude_session_exists()`
固定使用 canonical project_root，漏掉 `spawn(cwd=...)` 对应路径，随后重复启动/换会话。
本次运行在出现 080 后主动 SIGINT，813.95s / 1 failed；不是预算超时，专属进程、tmux、
临时项目由 finally 清理完成，Provider transcript 保留。

修复：会话存在检查显式接收本次 spawn cwd，仅 cwd 缺省时沿用 project_root；UUID、
权限、活进程锁防护不变。隔离 Provider home 的真实文件探测红测 **1 failed / 0.64s**，
`/tmp/zf-k510-079-red-20260923.xml`；修复后的 SpawnCoordinator/role lifecycle/Task-stage
调用方 **71 passed / 14.31s**，`/tmp/zf-k510-079-impact-20260923.xml`。测试不启动 Provider，
不改用户配置；真实 impl/verify 恢复验收分别为 **1 passed / 665.38s** 和 **1 passed / 539.62s**，
对应 `/tmp/zf-k510-native-recovery-impl-resume-fixed-20260923.xml`、
`/tmp/zf-k510-native-recovery-verify-resume-fixed-20260923.xml`。

## ZF-TEST-080：Task Pipeline 恢复混入普通 idle/Task briefing，与阶段契约冲突

> 状态: fixed（2026-09-23 K510；生产修复、确定性回归及真实 impl/verify 恢复验收通过；工作区未提交）

检测入口：

    tests/conformance/test_zaofu_testkit_s3_respawn_termination.py::test_run_manager_respawn_requires_old_worker_termination[terminated]
    tests/conformance/test_zaofu_testkit_s3_respawn_termination.py::test_running_task_stage_recovery_reuses_exact_stage_briefing

真实检出 `/tmp/zf-k510-native-recovery-impl-cwd-fixed-20260923.xml`：恢复 worker 读取通用
`impl-1-recovery.md`，其中任务为 `(idle)`、resume packet 为 task_missing，而阶段 attempt
已重新派发。worker 正式发出 `context.incomplete`（evt-c70682a0e257），说明没有修改项目。
真实 transcript 在 `/home/uisee/.claude/projects/-tmp-zf-k486-native-20260923-bnlfnsfd-runtime-state-workdirs-tasks-run-1-TASK-BLOCKING-f628cb4f0aa8-g1-project/72f1bfee-77ec-58e0-b090-91d9a1227854.jsonl`；
该用户 transcript 仅只读取证，未改写/删除。

根因：生命周期在 Run Manager 完成、正式 redrive 之前先注入普通恢复任务；Task-stage 的
运行态恢复也走普通 task.md/capsule 合同，没有沿用当前阶段的正式 result submit briefing。
红测 **2 failed / 6 passed / 3.83s**，`/tmp/zf-k510-080-red-20260923.xml`：确认多发一次
通用 prompt，且运行阶段恢复不能复用原 stage briefing。

修复：`task_pipeline_recovery.inject_task_stage_recovery` 由实际生命周期入口调用；
suspended operation 仅记录等待 redrive，不发送旧/idle prompt，由新 attempt 正式派发；
running operation 校验当前 binding、operation、sent attempt 和 Kernel dispatch，复用精确
阶段 briefing 与 Task Pipeline prompt。continuation 不携带新 dispatch token，不创建或改写
TaskAttempt；缺 binding/briefing 时拒绝，不降级到普通任务。普通角色恢复路径保持不变。
初次闭包 **44 passed / 25.58s**，`/tmp/zf-k510-080-impact-20260923.xml`；不外推为原生通过。
079/080 与 078 联合影响闭包 **119 passed / 33.07s**，
`/tmp/zf-k510-079-080-final-20260923.xml`；覆盖真实 RM/生命周期接线、目录、旧进程终止、
阶段会话、SpawnCoordinator、缺 binding/briefing 拒绝以及确定性 mock E2E。
主干统一复验 **107 passed / 77.43s**，`/tmp/zf-k510-080-mainline-20260923/testkit-run.json`，
无 skip/xfail/未执行/超时/证据错误。079 的精确 cwd 会话探测节点另已注册 S5/MAINLINE，
注册测试 **5 passed / 27 deselected**，`/tmp/zf-k510-080-registration-final-20260923.xml`；
此新增节点不追溯计入前述 107 项。原生正在运行
`/tmp/zf-k510-native-recovery-impl-resume-fixed-20260923.xml`；实时 launch 已为 is_resume=true，
且出现 task_pipeline_waiting_for_redrive，无旧 idle prompt。最终原生 impl 中断恢复
**1 passed / 665.38s**：旧 attempt superseded、新 attempt succeeded；两个阶段 transcript、
精确 candidate 内容、Task done、归档、重建幂等与专属 PID 清理全通过。verify 的一次
target_commit receipt 不匹配被正确拒绝后由 worker 自行修正，不属于新增生产 bug。
079/080 保持 fixed。后续真实 verify 中断验收 **1 passed / 539.62s**，证据
`/tmp/zf-k510-native-recovery-verify-resume-fixed-20260923.xml`；不代表 OS crash、网络断流
或整体端到端目标完成。

## ZF-TEST-078：Task Pipeline 生命周期恢复丢失任务工作目录

> 状态: fixed（2026-09-23 K510；生产修复、确定性调用方及真实 impl/verify 恢复验收通过；未提交）

检测入口：

    tests/conformance/test_zaofu_testkit_s3_respawn_termination.py::test_run_manager_respawn_requires_old_worker_termination[terminated]
    tests/conformance/test_zaofu_testkit_s5_native_claude_pipeline.py::test_native_claude_interrupted_stage_recovers_to_candidate[impl]

真实证据 `/tmp/zf-k510-native-recovery-impl-workdir-fixed-20260923.xml`：
1 failed / 1035.34s。旧 attempt superseded、新 attempt succeeded，Run Manager 请求、
respawn、redrive 与唯一性检查已通过；impl/verify 均结算，candidate_count=1。
最终读取 impl transcript 抛 FileNotFoundError，后续精确 candidate/Task done/重建幂等
断言未执行，不能计为完整通过。

恢复后的 session UUID 未变，但 Claude 实际启动目录从任务 workspace 漂到
`runtime-state/workdirs/impl-1/project`，而派发事件仍声明任务 workspace。根因：
`orchestrator_lifecycle._role_spawn_cwd()` 无条件准备普通 role slot，没有恢复 Task-stage
绑定的任务目录。不能用 transcript UUID glob 兜底掩盖错误 cwd。

当前工作树红测：`tests/conformance/test_zaofu_testkit_s3_respawn_termination.py`，
真实 dispatch → lease expiry → Run Manager → 实际生命周期/SpawnCoordinator → recording
transport，断言 spawn cwd 精确等于原派发目录；**1 failed / 1 passed in 2.43s**，
`/tmp/zf-k510-078-red-20260923.xml`。旧进程终止失败时禁止替换的对照仍通过。

修复：生命周期 cwd 入口先读取当前活动 Task-stage binding，以 Kernel 派发的 binding、
role、run、task、stage、workspace generation、placement epoch 核对真实目录；复用
TaskWorkspaceManager 的路径、ownership 与 metadata 校验，不创建普通角色目录、不重建
丢失的任务 workspace。无 binding 的普通角色保持原行为；有 binding 但证据/路径失配时
fail-closed，不回退到角色目录。显式拒绝 workspace symlink，保留原任务文件。

确定性影响闭包 **64 passed / 29.47s**：`/tmp/zf-k510-078-impact-final-20260923.xml`；
Task-stage/session/lifecycle/SpawnCoordinator/mock E2E 调用方 **72 passed / 9.08s**：
`/tmp/zf-k510-078-callers-20260923.xml`。安全与真实 RM/respawn caller **7 passed**，
`/tmp/zf-k510-078-security-20260923.xml`。回归已注册 MAINLINE；Ruff、diff check 通过。
首次统一主干 103 passed / 1 failed，仅因本项 detector 使用行内文字、未符合台账引用行格式；
已补齐独立 executable detector 行，未削弱检查，原证据保留在
`/tmp/zf-k510-078-mainline-20260923/testkit-run.json`。
补齐引用后的统一主干 **104 passed / 81.35s**，TestKit verdict=pass、session_finished=true，
无 skip/xfail/collection/deselection/timeout/evidence error：
`/tmp/zf-k510-078-mainline-final-20260923/testkit-run.json`。

预合并门最终 **47 passed / 4 既有结构债务失败**：
`/tmp/zf-k510-078-premerge-final-20260923.xml`；本次接线引入的 lifecycle 5 行超限已消除，
不调高 cap。规划器选出 193 文件并升级全确定性层，未运行该全套，采用上述定向闭包。
原生复验：`/tmp/zf-k510-native-recovery-impl-cwd-fixed-20260923.xml`，813.95s / 1 failed，
新 pane 与后续 watchdog pane 均保持原任务 workspace，078 未再现；后续检出 079/080，
因 worker 已明确 context.incomplete 而主动结束，并非超时，finally 清理通过。

## ZF-TEST-075：Claude transcript 路径未转义下划线，真实在途恢复观察失联

> 状态: fixed（2026-09-23 K510；路径修复、确定性回归及真实 impl/verify 恢复验收通过；工作区未提交）

检测入口：

    tests/test_session_tailer.py::TestPathConstruction::test_claude_session_path_escapes_underscores_like_claude
    tests/test_backend_session_reader.py::TestClaudeReader::test_session_path_escapes_underscored_workdir
    tests/test_web_terminal_usage.py::test_claude_launch_escapes_underscored_project_path
    tests/test_cost_usage_capture_probe_b_cost_02.py::test_capture_miss_diagnostic_escapes_underscores
    tests/conformance/test_zaofu_testkit_s5_native_claude_pipeline.py::test_native_claude_interrupted_stage_recovers_to_candidate[impl]

K510 真实检出：`/tmp/zf-k510-native-recovery-impl-final-20260923.xml`，约 172.73s 后
`stopped_without_candidate`。临时项目 `/tmp/zf-k486-native-20260923-_2f1fwoz` 的
ZaoFu 推导目录为 `-tmp-zf-k486-native-20260923-_2f1fwoz`，Claude 实际目录为
`-tmp-zf-k486-native-20260923--2f1fwoz`；真实 transcript 已存在，但观察器找不到，
因此不能确认 assistant 响应后安全注入中断。该失败不证明生产恢复决策本身错误。

修复：tailer、BackendSessionReader、生命周期 capture-miss 诊断与 Web terminal binding
均补 `_ → -`，保持已有 `/`、`.` 转义和 UUID fallback。读路径回归禁止 glob fallback，
防止目录推导错误被兜底掩盖。最终直接闭包和 S9 台账 **65 passed / 3 real-provider
deselected**：`/tmp/zf-k510-075-impact-20260923.xml`；调用方 **122 passed**：
`/tmp/zf-k510-075-callers-20260923.xml`。Ruff、diff check 已通过。

首次复验 `/tmp/zf-k510-native-recovery-impl-fixed-20260923.xml` 在 215.91s 后主动停止，
原因是夹具过期时间被缓冲 usage 心跳续写（见 077），不是完整恢复失败结论。后续
`/tmp/zf-k510-native-recovery-impl-clock-fixed-20260923.xml` 的路径含多个 `_`，已找到真实
transcript、注入中断并进入新 attempt，但被 076 写入守卫阻断；075 保持 fixed。

## ZF-TEST-076：Task Pipeline 恢复后的合法 workspace 被普通 role workdir 守卫拒绝

> 状态: fixed（2026-09-23 K510；用户授权阻断直接修复，真实 impl/verify 恢复验收通过；工作区未提交）

检测入口：

    tests/test_hook_task_pipeline_workdir.py::test_pipeline_hook_uses_pinned_workdir
    tests/conformance/test_zaofu_testkit_s5_native_claude_pipeline.py::test_native_claude_interrupted_stage_recovers_to_candidate[impl]

K510 真实检出证据：`/tmp/zf-k510-native-recovery-impl-clock-fixed-20260923.xml`。
路径转义修复后，真实 Claude 已被安全 SIGKILL，Run Manager 已发出
`worker.respawn.requested`，旧 attempt 已过期并生成新 attempt；新 impl worker 随后提交
`dev.blocked`：`Implementation blocked: the stage workspace is not writable under the active
harness path guard.` 具体对照是：Task Pipeline workspace 为
`runtime-state/workdirs/tasks/<run>/<task>/<generation>/project`，而守卫只授权
`runtime-state/workdirs/<role>/project`。这不是 Provider 拒绝或恢复决策失败，而是 ZaoFu
hook guard 的生产路径合同缺口。

修复：`hook_workdir_guard` 从当前 running operation 对应的 Kernel
`task.pipeline.stage.dispatched` 事件读取 `operation_id + active_attempt_id + dispatch_id`
绑定的 `workdir`，只允许该真实 workspace；旧 role slot、canonical project root、
workspace 内 symlink 越界仍拒绝。确定性影响闭包 **81 passed**：
`/tmp/zf-k510-077-impact-20260923.xml`；Ruff 通过。

最终安全与旧 attempt 时钟联调 **8 passed**：`/tmp/zf-k510-security-clock-final-20260923.xml`。
该 hook 回归已注册 S7 和 MAINLINE，统一确定性主干 **97 passed / TestKit verdict=pass**：
`/tmp/zf-k510-mainline-20260923-xRFUizdP/testkit-run.json`，无 xfail/skip/未执行或证据错误。

真实 impl 恢复重跑已结束：`/tmp/zf-k510-native-recovery-impl-workdir-fixed-20260923.xml`，
1 failed / 1035.34s；合法写入、impl/verify 结算及 candidate 已成功，后续 transcript
校验被另一个 cwd 恢复缺陷 078 阻断。076 保持 fixed，不把本次整体记为通过。

## ZF-TEST-077：原生恢复夹具的一次性 lease 过期注入被缓冲心跳续写（测试框架）

> 状态: fixed（2026-09-23 K510；不修改生产恢复策略，工作区未提交）

检测入口：

    tests/test_testkit_native_recovery.py::test_fault_requires_assistant_and_is_injected_only_once

检出：`/tmp/zf-k510-native-recovery-impl-fixed-20260923.xml`，SIGKILL 后已排队的 usage
使生产心跳把夹具注入的过期时间续到 900 秒后。约 215.91s 时主动 SIGINT 停止该专属测试，
保留失败 JUnit；finally 已回收临时 tmux/PID/目录。不能把这次人工停止当作生产恢复超时。

修复：观察器仅针对已确认被杀的旧 attempt，在其仍为 sent 且 lease 被续写时重新注入
过期时钟输入；不再次杀进程、不更改新 attempt、不直接写恢复/结算事件。
定向观察器+native diagnostic **16 passed / 3 deselected**；真实复验已产生生产
`task.attempt.failed → worker.respawn.requested/completed → redrive_admitted → 新 attempt`，
证据 `/tmp/zf-k510-native-recovery-impl-clock-fixed-20260923.xml`。后续遇到的是 076，
此处不把时钟注入称作真实 900 秒等待或网络断流。

## ZF-TEST-072：Claude 信任准备忽略 CLAUDE_CONFIG_DIR，写入了 Provider 不读取的文件

> 状态: fixed（2026-09-23 K500；用户已授权阻断缺陷直接修复，工作区未提交；不代表原生主链通过）

K500 修复：trust 准备和 stale-session 配置锁清理共用 `_claude_config_path()`，
遵循非空 CLAUDE_CONFIG_DIR，否则回退 Path.home；保留活进程 UUID 保护与角色 allowlist。
移除 072 strict xfail，补空覆盖回退及只清理有效配置、不改旧配置的回归。
直接影响闭包 **70 passed**：`/tmp/zf-k500-072-impact-20260923.xml`；清理 caller
**3 passed**：`/tmp/zf-k500-cleanup-callers-20260923.xml`；生命周期/Task-stage/
blocking caller **47 passed**：`/tmp/zf-k500-lifecycle-callers-20260923.xml`。
原生实测已越过 trust 阻断，发生真实 impl artifact 读取与本地提交；两轮均在 720s
预算内未完成 verify，无 candidate，完整原生验收仍未通过（见 073 后补充记录）。
配置隔离收尾：spawn/lifecycle/prompt matrix 的模拟测试隔离用户主目录与配置覆盖，
防止继承真实 CLAUDE_CONFIG_DIR 后污染用户配置；最终 **94 passed**，证据
`/tmp/zf-k500-isolation-final-20260923.xml`。S0 **15 passed**，证据
`/tmp/zf-k500-s0-20260923.xml`。

以下保留 K499 修复前定位与证据：

检测入口：

    tests/conformance/test_zaofu_testkit_s5_claude_readiness.py::test_spawn_trust_uses_effective_claude_config_directory

裸红复现：

    uv run pytest -q --no-cov -o addopts='' --runxfail tests/conformance/test_zaofu_testkit_s5_claude_readiness.py -k effective_claude

Given：本机及 tmux server 的 `CLAUDE_CONFIG_DIR` 均为 `/home/uisee/.claude`。
Claude Code 2.1.275 安装二进制中的配置解析 `QTn()` 使用
`process.env.CLAUDE_CONFIG_DIR || home` 拼接 `.claude${suffix}.json`。
当前有效文件 `/home/uisee/.claude/.claude.json` 已完成 onboarding，但无 K498 原生
工作目录的项目记录；`/home/uisee/.claude.json` 中该目录的 trust 字段为 true。
仅检查路径、onboarding 和项目是否存在，未更改真实配置。
安装二进制 SHA-256：`13586f3150a7ca1655f36e1dba759fb404e0f7cf7021d4a3dcd5e6f604e56156`。

When：`SpawnCoordinator.spawn()` 在物理 spawn 之前调用
`_ensure_claude_workspace_trusted(cwd)`。
Expected：既有信任准备行为须使用 Provider 实际配置路径，不在无效文件中宣称完成准备。
Actual：`spawn_coordinator.py` 硬编码 `Path.home() / ".claude.json"`，忽略配置目录覆盖。
新测试隔离 Path.home 和环境变量，用 recording transport 在真实 spawn 边界读取有效
配置；默认路径对照通过，配置目录覆盖场景抛出专用 `ClaudeConfigTrustPathBug`。
裸红结果 **1 failed / 1 passed / 6 deselected in 0.68s**，证据
`/tmp/zf-k499-config-path-red-20260923.xml`；没有启动 Claude 或写真实用户配置。
联调原有 readiness、prompt matrix、spawn trust caller 和 S0 为
**43 passed / 1 strict xfailed in 30.06s**，唯一 xfail 对应本 Issue；证据
`/tmp/zf-k499-config-path-impact-20260923.xml`。pytest exit=0 不代表全部通过。

关联：K498 原生证据 `/tmp/zf-068-fix-native-20260923.xml` 的
`provider_trust_interactive` 与此路径错位一致。本轮未重复真实 Provider 链；
068 的误判修复仍有效，但不能把剩余阻断仅归因于操作者没有确认。
本项未证明修正路径后完整 impl/verify/candidate 一定成功，也未扩展裁决 purge、
认证配置、配置并发写或 tmux 环境传播；后续修复必须保持 allowlist 和人工权限边界。
禁止通过自动确认、清除环境变量或复制整份配置绕过当前阻断。

## ZF-TEST-073：Task Pipeline 阶段提示落入普通 Task 分支，误导 worker 查找未引用的 task.md

> 状态: fixed（2026-09-23 K500；用户授权阻断修复，工作区未提交）

检测入口：

    tests/test_runtime_injection.py
    tests/conformance/test_zaofu_testkit_s3_blocking_dispatch.py::test_blocking_dispatch_prompt_uses_stage_contract

K500 原生运行 `/tmp/zf-k486-native-20260923-fd_8n_sd` 已成功派发 impl，但 worker
因启动提示要求先读 kernel-managed task.md，尝试 tasks/task 两种路径及 task-doc verify，
均发现 task_doc_missing。该阶段实际已有 contract snapshot、Controlled Artifact Inputs
及 typed result submit，不应要求一个未由 briefing 引用的 task.md。
源码定位：`task_pipeline_dispatch.py` 传入 `prompt_kind="task_pipeline_stage"`，
但 `injection.py::build_task_prompt` 未处理此值，落入普通 Task 提示，并引入通用完成协议冲突。

修复：增加已存在的阶段 kind 对应分支，以 stage briefing/required artifacts/typed result
为权威，不查未引用的 task.md、不以通用 role completion event 代替 submit；普通 Task/
fanout 路由不变。单元与实际 blocking dispatch caller 共 **74 passed**，证据
`/tmp/zf-k500-stage-prompt-impact-20260923.xml`。运行中的原生任务已收到旧提示，
不能用它证明更新后提示的原生效果；后续新运行才计该项原生验证。

K500 新进程复验：`/tmp/zf-k500-native-fresh-20260923.xml`，**1 failed in 724.49s**。
已加载 072/073，impl 正确读取阶段契约及必读 artifact，未重复查找 task.md；真实修改
README、本地提交 `c21860205e2ccdd7375d9682e800178c6da9bc45`、正式 result submit
并以 `admitted_call_result` 结算。独立 verify 会话持续读取必读 artifact，720s 总预算
到期时尚未提交，无 candidate。impl 约 8m41s，verify 仅运行约 3m29s；所检查 verify
transcript 未见 tool error。不能把剩余 active binding 判为归档生产 bug。
测试 finally 的专属 tmux/PID/loop.lock 清理断言通过，临时目录已删除。
该 fixture 的 Plan/Run admission 为 Given，本轮更不构成全产品端到端闭环通过。

## ZF-TEST-074：原生测试超时先报归档断言，掩盖未完成原因（测试框架）

> 状态: fixed（2026-09-23 K500；工作区未提交；仅诊断修复，不是生产归档修复）

检测入口：

    tests/conformance/test_zaofu_testkit_s5_native_claude_pipeline.py
    tests/conformance/test_zaofu_testkit_s5_native_claude_pipeline.py::test_native_candidate_diagnostic

检出：上述两轮原生测试在总预算到期、verify 尚未结算时，首先失败于
`binding.status == archived`。终态前置条件缺失，导致真实的未完成状态难以判读。
修复：先验证 candidate 前置条件并写入 `native_outcome` JUnit property，区分
runtime_blocked、budget_expired、stopped_without_candidate、candidate_ready；同时记录
派发阶段、已结算 operation 数和 candidate 数。明确阻断优先于预算/成功信号。
后续归档、真实 transcript、精确 candidate 内容、Task done、重建幂等及清理断言全部
保留；预算仍为 720s，未放宽权限、未把超时改为 skip/xfail。
确定性分类回归 **5 passed / 1 deselected in 1.11s**，证据
`/tmp/zf-k500-native-diagnostic-final-20260923.xml`。本次未再启动第三轮 Provider；
该证据只证明诊断分支，原生闭环仍未通过。

## ZF-TEST-068：Claude 新版交互启动屏幕被误判为 ready，Task Pipeline 已派发但未消费

> 状态: fixed（2026-09-23 K498；用户授权修复，工作区未提交。关闭启动屏幕误判/误派发缺陷，不表示原生主链通过）

K498 修复与复验：`provider_interactive_prompt.py` 补齐新版信任页的双文案识别
（`trust_prompt`）及安全说明继续确认的双文案识别（`onboarding_prompt`）。
复用既有 readiness 阻断与 Supervisor 人工处理路径，不自动确认、不提升权限。
原始确定性检测修复前为 **4 failed / 2 passed**；修复后移除 4 个 strict xfail，
补入新版屏幕的诊断、角色清理/重试及 Supervisor 检测；blocking Task Pipeline
改为使用真实分类器/ready matcher 与屏幕 I/O 替身，不再注入 readiness 布尔结论。
影响闭包（readiness、prompt matrix、tmux lifecycle、pane probe、role lifecycle、
SpawnCoordinator、task-stage sessions、blocking dispatch）为 **132 passed**，
证据：`/tmp/zf-068-fix-impact-20260923.xml`。
仓库 `dev-verify plan/run --base dev --paths src/zf/runtime/provider_interactive_prompt.py`
加显式回归文件，补选 Supervisor/Run Manager 直接调用方后 **95 passed in 30.23s**；
回执：`/tmp/zf-068-dev-verify-20260923.json`。仅执行定向确定性层与上述单次原生层，未跑全量 pytest。

同一原生入口复验为 **1 failed in 78.67s**，不是端到端绿灯：JUnit `native_events`
已出现 `task.pipeline.stage.dispatch_deferred`，原因明确为 `provider_trust_interactive`；
任务发送 contexts 为空，不再出现旧的“信任页仍在、却已派发”表现。
证据：`/tmp/zf-068-fix-native-20260923.xml`。原生测试移除旧缺陷 xfail，保留完整
impl/verify/candidate 强断言；前置条件未满足时继续真实失败，不改成 skip/xfail/pass。
测试专属 tmux session 已回收；此次未派发，PID 采样为空，不能据此宣称进程树完整验证。
K499 补充定位：信任准备忽略 `CLAUDE_CONFIG_DIR`，另见 ZF-TEST-072；
后续待办是修复该配置路径缺陷并通过正式权限流程满足 Provider 前置条件后再跑原生链，
不重复修分类器，也不把本次 fixed 外推为整体目标完成。
实现边界见 [Provider 启动判定](../../../../docs/impl/provider-startup-readiness.md)。

以下为修复前的历史定位与证据，保留原始失败结论：

检测入口：

    tests/conformance/test_zaofu_testkit_s5_claude_readiness.py
    tests/conformance/test_zaofu_testkit_s5_native_claude_pipeline.py

确定性裸红复现：

    uv run pytest -q --no-cov -o addopts='' --runxfail tests/conformance/test_zaofu_testkit_s5_claude_readiness.py

显式真实入口（会消耗 Provider 资源；只操作临时项目）：

    ZF_RUN_NATIVE_CLAUDE_PIPELINE=1 uv run pytest -q --no-cov -o addopts='' tests/conformance/test_zaofu_testkit_s5_native_claude_pipeline.py

Given：Claude CLI 2.1.275；隔离 `/tmp/zf-*` 项目、自定义 state dir、on-demand impl/verify、
生产 `zf start --simulation` 完整 preflight、SpawnCoordinator、pane_grid TmuxTransport。
Plan/package 准入为 fixture，观察器只委托生产 send/poll，不代写成功结果。

Expected：Provider 仍要求启动安全确认或目录信任时，应视为未就绪，禁止把交互确认屏幕的
`❯` 或 scrollback 提示符作为任务输入就绪证据；不能记录任务已送达却没有真实消费。

Actual：首次探索中 ledger 已有 `role.lifecycle.ready`、`task.attempt.sent` 和
`task.pipeline.stage.dispatched`，pane 停在 `Security notes: ... Press Enter to continue…`，
无真实任务 transcript。人工在**同一隔离 pane**确认安全说明和目录信任后，Claude 输入框为空、
0 tokens。此探索已终止，不计自动 E2E 成功。独立自动检测运行再次观察到已派发后的
`Quick safety check: ... ❯ No, exit / Yes, I trust this folder / Enter to confirm`；
专用 `ClaudeInteractiveReadinessBug` 使真实测试 strict xfail，TestKit 外层 verdict=fail。

确定性定位：`src/zf/runtime/provider_interactive_prompt.py` 只识别旧文案
`do you trust the files`，新版信任页及 onboarding 均返回空 blocker；
`TmuxTransport.wait_ready()` 使用 `ClaudeCodeAdapter.ready_pattern=[❯>]`，
`TmuxSession.wait_for_prompt()` 扫描含历史文本的 capture，故信任页选择箭头也命中。
测试复用生产 matcher/classifier，仅替换 pane I/O：4 个错误场景裸红，2 个 ready/legacy 控制通过。
onboarding 的 readiness 用例显式含旧 `❯`，不声称单独 onboarding 文案会匹配正则。

真实证据：`/tmp/zf-k486-native-detector-20260923-pxzs89a_/testkit-run.json`，1 strict xfailed，
pytest exit=0、TestKit verdict=fail、session_finished=true，无 collection/timeout/evidence error；
同目录 JUnit `native_events`/`native_processes` 保存派发身份、PID、pane 诊断和清理观察。
长探索报告 `/tmp/zf-k486-native-layout-20260923-amn4l_tw` 为人工终止，不作完整通过证据。
其中 `interrupted-native-audit.json` 保存脱敏 ledger 和人工干预说明；原临时项目已清理。
联调报告：`/tmp/zf-k486-impact-20260923-791d1i5e/testkit-run.json` 为 90 passed/4 strict xfailed；
完整 S5 `/tmp/zf-k486-s5-20260923-ubxlcdu6/testkit-run.json` 为 221 passed/4 strict xfailed，
TestKit verdict 均为 fail。S0 `/tmp/zf-k486-s0-20260923-gxf70r3i/testkit-run.json` 为 11 passed。

建议维护者修复：完善真实版本交互屏幕识别，readiness 必须检查当前可交互输入面；
启动阻断时 fail-closed，不自动接受用户信任，不重放已丢失的任务为成功。
`_ensure_claude_workspace_trusted()` 写入信任配置的兼容性也需独立核实，当前不声称唯一根因。
修复后先跑确定性 `--runxfail`，再执行相同 native 测试并验证 impl/verify/candidate，不能只删除 xfail。
完整原生闭环仍未完成；`/issue-merge` 不实现。

K487 补证（2026-09-23）：新增 `tests/conformance/test_zaofu_testkit_s5_claude_prompt_matrix.py`
的 15 个正常对照，覆盖已识别 blocker 的旧提示符干扰、诊断按角色隔离、非阻断 banner，以及
按需 activation 失败后的清理调用/dormant/显式重试/session ID 保持。生产 lifecycle、SpawnCoordinator、
Registry/EventWriter 与 readiness 参与，物理进程为替身；不证明真实清理或 Task-stage binding 恢复。
K486 的 4 个严格失败保留，未重复增加检测，也未关闭本 Issue。新版屏幕修复后仍须跑原生链。

K493 主干优先复核（2026-09-23）：Claude 2.1.275 已登录、tmux 3.4 可用；原生入口
`--runxfail` 再现 **1 failed in 10.96s**，strict 留证 **1 xfailed in 16.55s**，未人工确认。
证据目录 `/tmp/zf-k493-native-20260923-k5xJXG/` 的 `pytest-junit.xml` 实际含 native_events/
native_processes：仅一次 impl dispatch，无 operation settlement/candidate，屏幕仍为新版
目录信任页。两个 observed PID 和专属 tmux session 均已回收；四个直接 caller 文件
（role lifecycle、SpawnCoordinator、Task-stage sessions、blocking dispatch）**86 passed**。
这是直接 pytest 留证，不是 TestKit runner 全绿。该问题是当前原生主干的最前置阻断；
修复后应先跑已有 readiness 裸红测试，再跑同一原生链。仅正确识别并拒绝信任页并不足以
证明完整主链成功，仍需正式满足 Provider 启动前置条件。生产代码/环境无变化时不重复消耗
Provider 复跑同一 blocker，也不靠扩展边缘矩阵提升主干完成度。

K494 定向接线（2026-09-23）：生产 `SpawnCoordinator.spawn` 已调用
`_ensure_claude_workspace_trusted(cwd)`；新增
`tests/test_spawn_coordinator.py::TestClaudeFirstSpawn::test_first_spawn_prepares_claude_workspace_trust`
以隔离 Path.home 和 recording transport 证明 allowlist/Read 角色在物理 spawn 边界前，
实际 cwd 的 trust 字段已由生产代码写入，argv 未提升为 bypass。只读检查历史原生临时
worktree 的事后记录也存在该字段，但不据此推断新版 Provider 是否加载/接受此字段。
主闭包 `/tmp/zf-k494-impact-20260923-1te1c1lt/testkit-run.json` 为 **42 passed/4 strict
xfailed**，verdict=fail；相邻 lifecycle/blocking caller 为 **5 passed**，证据
`/tmp/zf-k494-callers-20260923-tw_10jsl/testkit-run.json`。两者 session 完整且无基础设施错误。
本次不启动 Provider、不改实际 trust 设置；不是 068 已修复，也没有新生产 Issue。

K495 主干 fail-closed/retry 联调（2026-09-23）：在确定性 blocking Task Pipeline 中注入
`provider_trust_interactive` readiness 失败，首轮只产生一次 spawn/activation.failed/deferred，
没有 `task.pipeline.stage.dispatched`、`task.attempt.sent`、`candidate.ready`，Task 保持
backlog，attempt store 为空；随后将 readiness 作为显式 Given 恢复，生产 impl→verify 结果
提交产生唯一 candidate，重建两轮不重复派发。影响闭包
`/tmp/zf-k495-impact-20260923-pq7u0itz/testkit-run.json` 为 **59 passed/4 strict xfailed**，
4 项仍为 ZF-TEST-068，故没有新增 Issue，也不表示 068 已修复或真实 Claude 主链已通过。

## ZF-TEST-071：stream-json 超时只回收 SDK 父进程，拥有的子进程仍残留

> 状态: fixed（2026-09-23 K508；用户授权主干阻断修复，工作区未提交）

检测入口：

    tests/conformance/test_zaofu_testkit_s5_stream_interruptions.py::test_sdk_timeout_reaps_owned_process_tree

裸红复现：

    uv run pytest -q --no-cov -o addopts='' --runxfail tests/conformance/test_zaofu_testkit_s5_stream_interruptions.py -k process_tree

Given：真实 Claude SDK subprocess transport、离线 CLI 父进程和其自有的 Python 子进程；
子进程与父进程同 PGID、父进程终止时子进程仍保持运行。Linux 测试进程启用
`PR_SET_CHILD_SUBREAPER`，确保 orphan 可被本测试验证和回收，不依赖 host init。

When：transport deadline=1s，SDK 终止父进程；测试保存父/子 PID、parent/child PGID、cwd/session、
真实 timeout 事件，并在断言后只 kill/reap 已核验属于该 fixture 的子进程。

Expected：停止应回收整个 owned process tree，不能留下子进程、锁或 fd；父进程终止事实
应保持 failed，不应留下可继续工作的 Provider 后代。

Actual（修复前）：父进程真实 `returncode=-15`，但子进程仍为运行态；精确 `--runxfail` 为
1 failed，正常模式 1 strict xfailed。该项只证明资源/生命周期残留，不声称 Task 错误结算。

修复：新增 `stream_owned_process.py`，复用 SDK 的命令/解析边界，以
`start_new_session=True` 创建 ZaoFu-owned process group；关闭在 shield 下执行 TERM→有界等待→
KILL，父进程已退出仍继续检查组内后代。清理失败保留 process handle、抛出失败并禁止替代
spawn；非 owned PGID 拒绝发信号。补充忽略 TERM 子进程、正常退出遗留子进程、双 PGID 隔离及
原始 asyncio 取消竞态测试。

修复后证据：`/tmp/zf-k508-071-red-20260923.xml` 保留修复前 1 failed；
`/tmp/zf-k508-owned-final-20260923.xml` **37 passed**（进程边界、生命周期与 S5）；
`/tmp/zf-k508-sdk-expanded-20260923.xml` **22 passed**（S5 stream-json）；
`/tmp/zf-k508-callers-20260923.xml` **95 passed**（结果/健康、transport、生命周期、
provider-stop 与 S5 调用方）。扩展矩阵证明普通子进程、忽略 TERM 的 KILL fallback、自然
成功后同组残留回收；测试 finally 只回收已核验自有子进程，不替代生产清理。

边界：仅 POSIX 同组后代；setsid 脱离的后代、OS crash takeover、真实 Provider 网络恢复和
业务 attempt 恢复准入仍未闭合。`/issue-merge` 和降级项不做。

根因边界：已安装 SDK 0.0.25 的 `_internal/transport/subprocess_cli.py::close` 仅对
`self._process` terminate/wait；ZaoFu `_drain` 的 timeout 路径没有额外 owned descendant
回收。正式夹具没有忽略 SIGTERM，也没有 setsid；不能把证据扩大为复杂逃逸进程树。
初稿曾同时加入 setsid/忽略信号，但没有安全的 orphan reap，已替换为同 PGID 普通子进程。

正式证据 `/tmp/zf-k491-impact-20260923-v17shmcf/testkit-run.json`：99 passed/6 strict
xfailed，069 三项、070 两项、071 一项；pytest exit=0、TestKit verdict=fail，session 完整，
无 collection/deselection/timeout/evidence/bundle error。JUnit `process_tree_observation`
保存真实父子关系与 `/proc` 状态；teardown 的 waitpid/不存在检查失败不能被该 xfail 隐藏。

## ZF-TEST-070：stream-json 停止后 SDK 进程仍运行，迟到结果重新恢复健康状态

> 状态: fixed（2026-09-23 K507；用户授权主干阻断修复，工作区未提交）

修复：`stream_query_lifecycle.py` 已接同步/后台派发与 terminate/shutdown，先封住新派发、
取消 owned async task 并等待 SDK/SessionLock/派发线程退出，再清理映射。共享停止等待预算
5 秒，未退出则抛错、禁止替代 spawn；取消窗口覆盖 loop 未注册、并发停止与结果发布，
吞掉 CancelledError 后返回的旧成功也不能发布 usage/completed 或恢复健康。
新显式 init/spawn 后可以重试；单角色停止不影响其他角色，不自动授权新的业务 attempt。

证据：原检出 `/tmp/zf-k507-070-red-20260923.xml` **2 failed**；修复后
`/tmp/zf-k507-cancel-impact-20260923.xml` **54 passed**；SDK 结果/停止/超时/限流/
失败重试联调 `/tmp/zf-k507-sdk-stop-final-20260923.xml` **15 passed / 1 deselected**，
未执行项是仍 open 的 071。新增 Kernel cancel 调用与生命周期/provider-stop/067
`/tmp/zf-k507-stop-callers-20260923.xml` **46 passed**。
最终三入口 terminate/shutdown/Kernel cancel 的停止后准备重试
`/tmp/zf-k507-stop-retry-final-20260923.xml` **3 passed**：旧 PID=-15、线程退出/锁释放
均发生在 fixture release 之前；同 session 新 PID 的新轮次成功，旧轮次保持 failed。

取消竞态文件独立初跑 `/tmp/zf-k507-cancel-races-final-20260923.xml` 为 7 passed / 1 failed，
失败在查询启动前的 2 秒等待，不在停止断言；同节点冷启动复探为 1 passed。补齐单测
provider home/锁清理边界隔离，并将启动等待与停止预算分开后，
`/tmp/zf-k507-cancel-races-isolated-20260923.xml` **8 passed**；未改变生产停止预算或
取消超时拒绝替代的断言。预合并 `/tmp/zf-k507-premerge-20260923.xml` 仍为
**47 passed / 4 既有结构失败**，未放宽 cap。合同见 `docs/impl/stream-json-result-contract.md`。
本项不关闭 071 整个进程树回收或真实 Provider 网络恢复。以下保留 K490 修复前历史。

检测入口：

    tests/conformance/test_zaofu_testkit_s5_stream_interruptions.py::test_stop_cancels_active_sdk_subprocess_and_releases_worker

裸红复现：

    uv run pytest -q --no-cov -o addopts='' --runxfail tests/conformance/test_zaofu_testkit_s5_stream_interruptions.py -k stop

Given：真实 background StreamJsonTransport、Claude SDK 0.0.25 和离线 CLI 子进程；CLI
输出部分帧后等待 fixture release 文件，8s 未释放则 exit 42，transport deadline=30s。
已验证真实 PID 活跃、SessionLock 正在持有、cwd/session 正确；Task/context 为 Given。

When：分别调用生产 `terminate(role)`、`shutdown()`；随后仅通过 release 文件允许仍存活
的离线 CLI 返回合法 result。测试不伪造 SDK 返回、事件或生产停止事实。

Expected：停止应取消/回收正在执行的 provider turn，释放锁；已终止角色不得被迟到结果
重新标为 healthy/completed；不能仅清空映射而保留正在执行的旧进程。

Actual：两例均裸红。停止返回后 `is_alive=false`，但真实 process.returncode=None、
dispatch thread 存活、SessionLockBusy；release 后子进程正常 exit 0，产生成功 agent.usage
和 completed runtime log，`is_alive` 反而变回 true。Task 仍 in_progress，无 task.done/candidate，
故本 Issue 不声称 Task 已错误结算。最终实际 PID 退出、锁释放、重复 drain 无新事件均独立断言。

根因：`src/zf/runtime/transport_stream_json.py:886` 的 terminate 与 `:897` 的 shutdown
只清映射/队列，不取消 `_dispatch_loop` 当前 `_send_task_sync`/SDK query；后者完成后再次写
`_last_query_ok`、runtime log 和 pending events。调用方包括 `role_lifecycle_runtime.py` 的
hibernate/失败清理、`orchestrator_lifecycle.py` 的 worker terminate 与 `cli/start.py` 的 shutdown。
这些调用方仅定位与相邻回归，本探针未证明完整 CLI/Run Manager 停机链或真实网络 Provider。

维护者修复后先执行相同裸红节点，再联调 stream-json background/liveness、SessionLock、
lifecycle/stop 和 K488/K489 检测；取消后不能由晚到事件复活旧角色。不要只删除 strict xfail。
测试 finally 只释放自有离线夹具并 join，明确不把该清理算生产取消。`/issue-merge` 和降级项不做。

K490 正式证据：`/tmp/zf-k490-impact-20260923-e6os2ng5/testkit-run.json` 与同目录 JUnit
两条 `stop_observation`。九入口闭包 **99 passed、5 strict xfailed**（069 三项、070 两项），
pytest exit=0、TestKit verdict=fail、session_finished=true，无 collection/deselection/timeout/
evidence/bundle error。精确 stop `--runxfail` 为 **2 failed、8 deselected**。缺陷修复前不将
pytest exit=0 当作停止链通过；进程/锁清理和 Task 不误关闭均在专用 xfail 之外独立断言。

## ZF-TEST-069：stream-json 进程在无完整成功结果时仍被标记为健康

> 状态: fixed（2026-09-23 K506；用户授权主干阻断修复，工作区未提交）

修复：`stream_result_contract.py` 的机械证据规则已接入实际 `_drain` 和消息投影；
只有唯一、末尾、subtype=success 且 is_error 严格为 false 的完整结果可记成功。
缺失/截断/重复/非末尾结果及错误结果产生 api_blocked，健康 false，运行日志 failed；
完整 dispatch context 与部分输出保留。错误 usage 仍为诊断，不再授权健康/完成。
同时修正历史 B7“assistant 后限流算成功”规则：有文字无成功结果仍为 rate_limited/failed，
测试升级为保留部分文字且明确阻断，不降低验收断言。

证据：`/tmp/zf-k506-069-red-20260923.xml` **3 failed / 2 passed**；修复后
`/tmp/zf-k506-stream-unit-20260923.xml` **44 passed**；结果边界/transport drain/
telemetry/S5/session resume 调用方 `/tmp/zf-k506-result-callers-20260923.xml`
**50 passed / 1 real-provider deselected**。真实 SDK + 离线 CLI 的最终结果/超时/限流/
四类失败后显式重试 `/tmp/zf-k506-sdk-result-final-20260923.xml` **13 passed / 3 deselected**，
三项未执行的是仍 open 的 070×2、071×1；真实 PID 退出、SessionLock 释放、同 session
新 PID 成功重试及 Task 不误关闭均通过。
前序 063/067 + provider stop 恢复联调 `/tmp/zf-k506-prior-recovery-20260923.xml`
**23 passed**，包含 Run Manager 恢复到 candidate 的确定性链。
预合并 `/tmp/zf-k506-premerge-20260923.xml` **47 passed / 4 failed**，仍为既有结构债务。
实现合同：`docs/impl/stream-json-result-contract.md`。070/071 和真实 Provider 网络断流
仍未关闭；以下 K488/K489 内容为修复前历史，不再代表当前 069 状态。

检测入口：

    tests/conformance/test_zaofu_testkit_s5_stream_interruptions.py
    tests/testkit/fixtures/stream_interruption_cli.py

确定性裸红复现：

    uv run pytest -q --no-cov -o addopts='' --runxfail tests/conformance/test_zaofu_testkit_s5_stream_interruptions.py

Given：隔离 pytest 临时项目、fixture state dir、真实 `StreamJsonTransport`、真实
`claude_code_sdk` subprocess transport/parser/session lock/background dispatch；仅把 CLI
可执行文件替换为仓库内离线夹具，不访问 Provider 网络；所有写入均在 fixture 私有根目录内。

When：夹具分别输出 assistant 部分帧后 (1) 截断 JSON 并以 exit 0 结束，(2) 无 `result` 即 EOF，
(3) 输出 `is_error=true` 的 result；另有正常 result 和 exit 41 对照，以及失败后显式重试。

Expected：没有完整、`is_error=false` 的 result 时，transport 应产生结构化失败/阻断事实，
`is_alive()` 为 false，runtime log 为 failed；不得把 assistant 部分帧、EOF 或错误 result
记为 provider dispatch completed。显式重试应释放真实 session lock、复用稳定 role/session
身份但产生新的进程，不产生 task.done/candidate.ready。

Actual：正常 result、exit 41 阻断和失败后显式重试通过；三类 exit 0 异常均当前裸红：
`is_alive()` 仍为 true，runtime log `status=completed`，没有对应 `agent.api_blocked`/失败事实。
`is_error=true` 还被映射为普通 `agent.usage`；三类均未观察到 Task 被错误关闭，范围暂限定为
Provider 健康/完成状态误报。每个夹具进程 PID、exit code、cwd、session id、运行日志、事件和
session lock 释放均保存于 TestKit evidence property；fixture finally 确认 owned process 已退出。

根因定位：`src/zf/runtime/transport_stream_json.py::_drain` 默认 OK，未要求成功 Result；
`_send_task_sync` 未将上述三类结果判为失败，`_record_provider_operation` 因而记 completed；
`_messages_to_events` 保留 `is_error` 为 usage 字段但不改变健康状态。已安装
`claude-code-sdk 0.0.25` 的 `_internal/transport/subprocess_cli.py::_read_messages_impl`
在 exit 0 时未拒绝 EOF 残留 json_buffer。不是仅凭 SDK 问题推断 ZaoFu 故障：三种输入均有实测。

K488 证据：`/tmp/zf-k488-impact-20260923-6cz62rjk/testkit-run.json` 及同目录
`pytest-junit.xml` 的 `stream_observation`。正式 12 入口闭包为 **94 passed、7 strict xfailed**，
其中本 Issue 3 项、既有 ZF-TEST-068 4 项；pytest exit=0、TestKit verdict=fail、
session_finished=true，无 collection/deselection/timeout/evidence/bundle error。
已联调 S5 stream-json/status/alive/stuck、transport drain、S3 blocking dispatch、readiness
及 session/resume adapter caller；未运行真实 headless Provider 网络断流。
维护者修复后先跑该节点 `--runxfail`，再跑同一闭包；Task-stage context 为 Given，不能当作
完整 admission/binding 证明。测试不修改 `src/`。
此 Issue 不涉及 `/issue-merge`，也不解除 `[降级]` 项。

K489 相邻控制（2026-09-23）：同一真实 SDK/离线子进程的静默与部分输出超时均被 SIGTERM
回收，真实退出码 -15、锁释放、唯一 timeout 事件及显式同 session/新 PID 重试成功均通过。
`/tmp/zf-k489-impact-20260923-s8vow96c/testkit-run.json` 为 84 passed/3 strict xfailed，
其中三项仍为本 Issue；TestKit verdict=fail。两条 timeout 正向控制不是 EOF/error-result
缺陷修复，也不证明运行中 shutdown 或真实网络断流。完整进程与事件证据见 JUnit `timeout_retry`。

## ZF-TEST-067：旧 worker 终止失败后 respawn 仍被当作成功并提前重派

> 状态: fixed（2026-09-23 K505；用户授权主干阻断修复，工作区未提交）

修复：`_respawn_instance_with_lease` 不再吞掉 transport terminate 异常；失败进入既有
respawn_failed 回执/预算路径，禁止 spawn、恢复 briefing、completed 与 operation redrive。
旧 worker 停止成功之前不再清除/轮换 Provider session binding；Codex 上下文检测仍在
终止前读取旧输出。原检出移除 strict xfail，并增加关联失败回执、无续接与 expired 状态断言。

证据：修复前 `/tmp/zf-k505-067-red-20260923.xml` **1 failed / 1 passed**。
第一轮直接闭包 `/tmp/zf-k505-067-impact-20260923.xml` 为 **32 passed / 2 failed**：
原始两项检出及既有恢复回归已通过，两个失败是新增测试遗漏 Registry project_root 参数；
修正夹具并补 Claude/Codex × 普通/新会话保留矩阵后，完整 pane recovery 文件
`/tmp/zf-k505-session-binding-final-20260923.xml` **15 passed in 29.97s**。
Run Manager、worker state、生命周期调用方 `/tmp/zf-k505-recovery-callers-20260923.xml`
**29 passed in 22.84s**。预合并 `/tmp/zf-k505-premerge-20260923.xml` 为
**47 passed / 4 failed**，均为既有结构债务；未增加 lifecycle 文件行数或调整 cap。

合同见 `docs/impl/worker-respawn-result-contract.md`。本项关闭显式终止异常被吞的问题，
不证明 transport 正常返回后的全部 OS 子进程回收；069/070/071 与真实 Provider
中断恢复仍开放。以下保留 K477 修复前诊断。

检测入口：

    tests/conformance/test_zaofu_testkit_s3_respawn_termination.py::test_run_manager_respawn_requires_old_worker_termination

裸红复现：

    uv run pytest -q --no-cov -o addopts='' --runxfail tests/conformance/test_zaofu_testkit_s3_respawn_termination.py

Given：隔离 Git/custom-state 与 blocking Task Pipeline，首个 impl attempt 经真实
Orchestrator 派发后由真实 TaskAttempt expiry、Run Manager recovery request 和
`worker.respawn.requested` 进入生命周期消费者。仅把 transport 的 terminate/spawn
物理边界替换成可观测函数，不启动 Provider/tmux；SpawnCoordinator、respawn lease、恢复
briefing、operation redrive 与新 attempt 派发均使用生产实现。

When：旧 worker 的 `transport.terminate()` 明确抛出异常并保持 `old_alive=true`。
`_respawn_instance_with_lease()` 当前吞掉 terminate 异常后继续 `coordinator.spawn()`，并返回
`action="respawn"`；消费者随后写入 `worker.respawn.completed`，授权
`workflow.operation.redrive_admitted`，派发第二个 impl attempt。

Expected：无法证明旧 worker 已终止时不得声称 respawn 完成，不得授权同 operation redrive，
不得在旧进程仍存活时派发替代 attempt；operation 应保持 suspended，等待可证明的受控恢复。

Actual：当前裸红为 `1 failed、1 passed`；失败观察为 `terminate -> spawn`（spawn 时
`old_alive=true`）、`completed=1`、`redrives=1`、impl dispatch 从 1 增至 2，旧 attempt
变为 `superseded`。正常 terminate 对照为 `1 passed`，并只产生一次 completed/redrive。
正常终止对照中，重建实例后的重复 `run_once()` 不会再次终止或派发；故障分支在检出违例时停止。

建议修复方向（维护者实施）：终止操作必须返回可验证的成功事实；异常、仍存活或无法探测
时应写结构化失败/延迟回执并保持 operation suspended，不能用“非 respawn_failed”作为成功
判据。修复后执行本节点 `--runxfail`、K465/K466 worker recovery caller、TaskAttempt/
Run Manager 影响闭包和完整 S3；不得删掉检测或仅移除 xfail。

当前处理：已新增 strict 检出测试和成功控制；待维护者修复；未进入 `/issue-merge`。

K477 证据：`/tmp/zf-k477-respawn-impact-20260923-jmxiaoij/testkit-run.json` 为
64 passed、3 strict xfailed（本 Issue 1 项、既有 ZF-TEST-063 2 项）；S0 报告
`/tmp/zf-k477-s0-20260923-owgqfowi/testkit-run.json` 为 11 passed。含缺陷的 TestKit
verdict=fail，不能把 pytest exit=0 当作恢复主链全绿。

## ZF-TEST-063：worker respawn 冷却被误报为完成，提前授权 Task Pipeline 重派

> 状态: fixed（2026-09-23 K502；用户授权主干阻断修复，工作区未提交）

K502 先在当前工作树复现 **2 failed**：`/tmp/zf-k502-063-red-20260923.xml`。
修复 `_apply_worker_respawn_request` 的成功判定，只有 action=`respawn` 才投递续接/
重新排队 checkpoint 并发 completed。失败、冷却、熔断、锁竞争均发 failed，保留具体
action/reason，不误报 continuation_delivered；请求按既有规则收口，不自动绕过冷却或
重复重试，后续由新的合法恢复请求处理。详见
[重启结果与恢复授权](../../../../docs/impl/worker-respawn-result-contract.md)。

原有 cooldown 检出移除 strict xfail，保留 suspended/零 redrive/零新增派发断言；补充
四种未成功结果不执行两个续接副作用的直接 caller 回归。worker-action 与完整 blocking
recovery 文件 **19 passed in 58.97s**：`/tmp/zf-k502-063-impact-20260923.xml`。
Run Manager、TaskAttempt、pane recovery 和真实 tmux/mock worker 替换到 candidate 的调用方
**24 passed in 105.19s**：`/tmp/zf-k502-recovery-callers-20260923.xml`（2 条 JUnit family
兼容性警告，不是测试失败）；两种 impl/verify 物理替换均清理自有进程。
premerge **47 passed/4 failed** 仍是既有 source-size debt，未调宽门禁。
本项不证明真实 Claude 中途崩溃后的恢复，也不关闭 065/066/069/070/071 等其他缺陷。

以下保留 K465 修复前诊断：

检测入口：

    tests/conformance/test_zaofu_testkit_s3_blocking_recovery.py::test_full_orchestrator_run_once_consumes_recovery_and_fails_closed

裸红复现：

    uv run pytest -q --no-cov -o addopts='' --runxfail tests/conformance/test_zaofu_testkit_s3_blocking_recovery.py -k 'full_orchestrator and respawn_cooldown'

Given：隔离 Git/custom-state 与 blocking Task Pipeline，实际 `run_once()` 首派发后触发
lease expiry；真实 Run Manager tick 自动批准 worker-lifecycle-recover 并生成准确绑定旧
operation/attempt 的 respawn request。对物理恢复的冷却判定注入 true，其余 SessionLock、
`_respawn_instance()`、`_respawn_instance_with_lease()` 冷却分支和消费者均真实执行；没有
启动 Provider，也没有手写 completed/redrive/dispatch 事件。

When：`run_once()` 消费请求；生命周期返回 `WorkflowRuntimeDecision(action="respawn_cooldown")`，
实际没有执行 spawn。`agent_view_runtime.py::_apply_worker_respawn_request` 只以
`decision.action != "respawn_failed"` 判断成功，故把 cooldown 当成功；队列随后移除此请求。

Expected：冷却不是恢复完成，不可产生该请求的 completed，不可据此 admit redrive 或派发新
attempt；旧 operation 应保持 suspended，待合法后续恢复路径处理。

Actual：same-process/reconstructed 两例均得到 **completed=1、redrive=1、dispatch=2**，
期望 **0/0/1**。实际 `task_pipeline_recovery.py::_completed_run_manager_recovery` 接受这个
completed，导致原 operation 新 attempt 被派发。专用异常 `IncompleteRespawnPromoted`
只覆盖这个已复现的违例；其他 setup/准入/housekeeping 异常不会被 strict xfail 吞掉。

正向控制：成功和显式 respawn_failed × 同实例/重建四例通过；成功支进一步真实执行
impl commit/submit、verify、rolling smoke、candidate integration/freeze；显式失败支保持
suspended，无 completed/redrive/第二次派发，重建 sweep 仍不变。

影响边界：证明的是确定性恢复回执语义，未证明真实 Provider 故障概率。类似
`respawn_circuit_open`/`respawn_in_progress` 返回值也被相同条件覆盖，但本条没有实跑这些
分支，不将推断当已复现矩阵。

建议修复方向（维护者实施）：明确区分成功、失败与延迟恢复；只有真实成功可产生 completed。
冷却/锁竞争/熔断应保留各自可重试或受控处置状态，不能用通用非失败判定授权 redrive。
修复后执行本节点 `--runxfail`、worker-action/respawn lease/TaskAttempt/Run Manager caller
及完整 S3；不得删掉检测、调整断言或仅消除 xfail 标记来制造绿色。

当前处理：已新增 strict 检出测试与绿色控制；待用户修复；未进入 `/issue-merge`。

K465 证据：裸红双节点 `/tmp/zf-k465-cooldown-raw-e7fcizhf/testkit-run.json` 为 2 failed；
恢复影响闭包 `/tmp/zf-k465-recovery-impact-dh1irmuy/testkit-run.json` 为 91 passed、2 strict xfailed；
完整 S3 `/tmp/zf-k465-s3-registry-ppc0ekps/testkit-run.json` 为 133 passed、5 strict xfailed
（本 Issue 占 2 项）；S0 `/tmp/zf-k465-s0-final-20260923/testkit-run.json` 为 11 passed。
两个含已知缺陷的闭包均保持 TestKit verdict=fail，不将 xfail 算作生产通过。

## ZF-TEST-066：完整启动 preflight 接受 eager Task Pipeline role，运行时却拒绝派发

> 状态: fixed（2026-09-23 K504；用户授权主干阻断修复，工作区未提交）

修复：启动 preflight 与实际 Task Pipeline dispatch 共用角色生命周期校验。
blocking policy 引用的 eager role 在 session.started 前拒绝；direct/composite 配置均覆盖，
不误拒 shadow policy 或无关 eager role，不绕过原有 Provider readiness 门禁。

回归证据：修复前 `/tmp/zf-k504-066-red-20260923.xml` 为 1 failed / 1 passed；
修复后 `/tmp/zf-k504-066-impact-20260923.xml` **43 passed**，启动调用方
`/tmp/zf-k504-startup-callers-20260923.xml` **64 passed**。原检出移除 strict xfail，
额外断言拒绝时无 session.started/dispatch_deferred。预合并哨兵为 47 passed / 4 failed，
失败均为既有结构债务，未调宽基线。合同见 `docs/impl/provider-startup-readiness.md`。
本项不证明真实 Provider 中断恢复；以下保留修复前诊断。

检测入口：

    tests/conformance/test_zaofu_testkit_s3_cli_full_preflight.py::test_cli_full_preflight_rejects_eager_task_pipeline_before_start

裸红复现：

    uv run pytest -q --no-cov -o addopts='' --runxfail \
      tests/conformance/test_zaofu_testkit_s3_cli_full_preflight.py

Given：隔离 blocking runtime 使用当前真实 `zf start --simulation` 路径；为通过启动静态
preflight，将 impl mock role 的 lifecycle 改为 `eager`，其它 config/state、
workflow inspect、static dispatch、environment preflight 均保持真实。tmux/worker transport
仅作 recording/dry-run，watcher loop 有界退出。

Expected：启动 preflight 应与 Task Pipeline dispatch 合同一致；如果该 workflow 的实际
Task Pipeline 要求 role `on_demand`，启动应明确拒绝，或 runtime 应能合法派发。

Actual：`zf start --simulation` 返回 0、写入 `session.started`，workflow inspect artifact
存在；随后实际 dispatch 产生 `task.pipeline.stage.dispatch_deferred`，reason 包含
`Task Pipeline role ... must be on_demand`，transport 没有送出任务。对照配置保持 on_demand
时，静态 preflight 会因 mock `native session resume` 正确拒绝；因此不是把既有 ZF-TEST-004
误报为本 Issue，而是 eager 通过后与 runtime 合同漂移。

证据：

- strict 节点：**1 xfailed**；`--runxfail`：**1 failed**，普通 on-demand 对照 **1 passed**。
- 未修改 `src/`/`web/src/`，未启动真实 Provider/网络，不访问远端。
- JUnit 属性 `preflight_pipeline_observation` 保留 exit、dispatch_count=0 和 deferred
  payload；除专用异常外不吞掉其它启动/账本断言。

建议修复方向（不在本测试任务中实施）：启动 preflight 应读取 Task Pipeline role lifecycle
约束，或 dispatch/runtime 应接受被 preflight 合法化的 eager role；两者必须共享同一合同，
不能启动成功后静默 deferred。修复后用同一 strict/`--runxfail`、CLI lifecycle、blocking
dispatch、Task Pipeline caller、完整 S3 和 S0 回归。

当前处理：已登记 Issue，保留严格检测；不进入 `/issue-merge`。

## ZF-TEST-065：queued writer 批次 gap-only 恢复生成候选时丢失已完成任务内容

> 状态: fixed（2026-09-23 K503；用户授权主干阻断修复，工作区未提交）

K503 先复现 **1 failed**：`/tmp/zf-k503-065-red-20260923.xml`。修复队列超时事件的
completed_task_ids 传递，并让 writer candidate 聚合合并 gap-only 的已完成上游集合。
共享规则位于 `writer_completed_tasks.py`，被 timeout/aggregate 两个真实入口调用；
本批次重新出现的任务以当前状态为准，旧完成记录不能掩盖 failed/queued 重试。
非 gap-only 不继承；reader timeout 不携带 writer 完成集合；既有 TaskRef/CandidateRebuilder
负责实际集成，不直接写 canonical state。

原检测移除 strict xfail，保留真实两任务提交、正式 ControlledAction resume、精确候选
内容断言，并新增 cancellation/checkpoint/resume 三处集合及重建幂等断言。
writer/timeout/resume/candidate 直接影响闭包 **262 passed in 253.75s**：
`/tmp/zf-k503-writer-impact-20260923.xml`。前序 blocking recovery、reader batch resume、
plan approval→候选→本地交付 **13 passed in 90.22s**：
`/tmp/zf-k503-prior-mainline-20260923.xml`。premerge **47 passed/4 failed**：
`/tmp/zf-k503-premerge-20260923.xml`，仍为既有 source-size debt，未调宽 cap。
实施说明：[Writer 缺口恢复候选输入](../../../../docs/impl/writer-gap-resume-lineage.md)。
本项修复新产生的队列超时/恢复链，不迁移历史缺字段 ledger，不证明真实 Provider/OS takeover。

以下保留 K475 修复前诊断：

检测入口：

    tests/conformance/test_zaofu_testkit_s3_writer_batch_resume.py::test_writer_queue_timeout_resume_preserves_completed_task_in_candidate

裸红复现：

    uv run pytest -q --no-cov -o addopts='' --runxfail \
      tests/conformance/test_zaofu_testkit_s3_writer_batch_resume.py

Given：隔离 Git/state 中配置单 affinity writer lane 与两个独立任务。真实
Orchestrator 派发 TASK-1，TASK-2 进入 `fanout.child.queued`；TASK-1 通过真实
`dev.build.done` 完成并在其 worktree 提交 `a.txt`。受控 provider-turn 只用于保持 queued
child 等待，随后真实 timeout sweep 生成 `fanout.cancelled`，`failure_kind` 为
`scheduler_queue_timeout`、`semantic_attempt_consumed=false`。正式
`ControlledActionService(workflow-batch-resume)` 生成 gap-only `task_map.ready(task_ids=[TASK-2])`，
重建 Orchestrator 后 TASK-2 再派发并提交 `b.txt`。

Expected：gap-only 恢复继续构建 candidate 时，候选应保留已完成 TASK-1 的 accepted TaskRef
及 `a.txt`，并包含新恢复的 TASK-2 `b.txt`；恢复只补缺口，不应把已完成上游从 candidate
lineage 中删除。

Actual：恢复派发与 TASK-2 candidate 生成路径可到达，但当前 candidate commit 只包含
`README.md` 与 `b.txt`，不包含已完成 TASK-1 的 `a.txt`；同时 `task/TASK-1:a.txt` 仍存在，
证明内容在已完成 task ref/worktree 中而非恢复 candidate 中。检测专用
`ResumedCandidateDropsCompletedTask` 严格 xfail 只包住这一产品违例，其余 queue、checkpoint、
controlled action、dispatch 断言仍真实执行。

证据：

- strict 节点：`1 xfailed`；`--runxfail`：`1 failed`，无 collection/timeout。
- 正向前置：真实 writer queue、queued timeout、`resume_queued_children` checkpoint、gap-only
  `task_map.ready`、重建后仅 TASK-2 一次 dispatch 均通过。
- 未修改 `src/`/`web/src/`，未访问 Provider/远端；本条不是 reader candidate K474 的重复，
  针对 writer candidate rebuild 的 completed-task lineage。

建议修复方向（不在本测试任务中实施）：恢复 candidate rebuild/merge 时，必须从 checkpoint
`completed_task_ids`、TaskRef index 与 candidate base/ref 重新组装完整已完成集合；gap-only task
map 只能约束重新派发集合，不能成为 candidate 内容集合。修复后使用同一 strict/`--runxfail`
节点、writer fanout caller、candidate rebuild、S3 和 S0 回归；不得删检测或改成只检查派发。

当前处理：已登记 Issue，保留严格检测；不进入 `/issue-merge`。

源码证据：`fanout_timeout_policy.py::close_expired_queued_wait` 的取消事件只包含 queued
children，不包含已完成 child 集合；`workflow_resume.py::_batch_checkpoint_from_event` 与
`workflow_resume_apply.py::_apply_batch_task_map_ready` 沿用空的 completed_task_ids。
`orchestrator_fanout.py` writer candidate-integration 分支从恢复批次自身已完成 children
构造 `CandidateRebuilder.rebuild(task_ids=...)`，该批次仅有 TASK-2。这解释了本场景为何
重新派发集合正确而 candidate 内容集合不完整；不据此推断 blocking Task Pipeline 也受影响。

最终相关闭包：`/tmp/zf-k475-writer-impact-20260923-i7jj5cn1/testkit-run.json`，
**51 passed、1 strict xfailed**，pytest exit=0 / TestKit verdict=fail；JUnit 属性
`writer_resume_candidate_observation` 留存实际 candidate files 与空 completed-task 列表。
前缀-only 草稿曾通过；补齐 candidate 内容断言后检出本 Issue，旧草稿通过不再作为闭环证据。

## ZF-TEST-064：同一 dev.build.done 重放会重复追加 task.ref.updated

> 状态: open（2026-09-23 K470 当前工作树复现；仅检测，不修改生产代码）

检测入口：

    tests/conformance/test_zaofu_testkit_s3_task_ref_replay.py::test_dev_build_replay_preserves_downstream_effects[reopened-push]

裸红复现：

    uv run pytest -q --no-cov -o addopts='' --runxfail tests/conformance/test_zaofu_testkit_s3_task_ref_replay.py -k reopened-push

Given：隔离 blocking Task Pipeline 中，真实 impl semantic submit 产生一个
`dev.build.done`；首次 reactor 处理写入一条 `task.ref.updated` 并派发 verify。随后重建
`Orchestrator`，用**相同 event ID** 的原始 `dev.build.done` 事件再次 `pushed`。

合同依据：`ZaoFu_TestKit.md` §1.3 不变量 3 要求重复事件不能重复投影，§11/REC-07 要求同一
事件只触发一次有效动作。本条针对**已成功更新**的同一 trigger 在无输入变化时重新投影，
不是要求所有具有同一 causation 的不同事件都必须合并，也不是 EventWriter 的重复 ID 准入。

Expected：已成功处理的同一 durable occurrence 重放不得再次产生 `task.ref.updated` 或
改写 TaskRef index 的创建时间，不得重复 operation/attempt/dispatch/settlement 或下游阶段。

Actual：当前 HEAD 再次追加一条新的 `task.ref.updated` occurrence；两条记录的
`trigger_event_id` 都指向原始 `dev.build.done`，但新 occurrence ID 和 `created_at` 不同。
TaskRef index 因重放被重新覆盖。正向控制（same-instance、reopened-poll）与同一场景的
下游验证/集成/候选冻结均通过，因此本 Issue 只记录 occurrence/index 幂等缺陷，不把它扩大
为已证明的下游重复执行。

证据：

- 裸红 `--runxfail -k reopened-push`：**1 failed**；`task.ref.updated` 从 1 增至 2；无 collection error 或 timeout。
- strict xfail：只用专用 `SameTriggerTaskRefReplay` 异常捕获已知违例；其它断言仍保持真实失败。
- 正向控制与下游闭环：**2 passed**；无新增 operation、attempt、dispatch、settlement、integration、task.done 或 candidate.ready。
- 11 文件影响闭包：**131 passed、3 strict xfailed**，无普通失败、collection/deselection/timeout/evidence/bundle error；S0 **11/11 passed**。
- 注册后的完整 S3：**149 cases，143 passed、6 strict xfailed**；新增 xfail 为本 Issue，其他为既有
  `ZF-TEST-028/029/031/063`；无 collection/deselection/timeout/evidence/bundle error。
- 未修改 `src/`/`web/src/`，未访问 Provider/远端，未触碰 8001。

报告：裸红 `/tmp/zf-k470-raw-20260923-03ialys3/testkit-run.json`；影响闭包
`/tmp/zf-k470-impact-20260923-7kt7czho/testkit-run.json`（063 两例、064 一例，外层 verdict=fail）；
S0 `/tmp/zf-k470-s0-20260923-pv8qju3g/testkit-run.json`；完整 S3
`/tmp/zf-k470-s3-20260923/testkit-run.json`。JUnit 的
`task_ref_replay_observation` 保存三种 delivery 下的计数、trigger ID 与 before/after 时间。

源码接线：`orchestrator.py::_react_to_events` 重建后仅有空的进程内 processed-ID 集合；
`dev.build.done` 分支再次调用 `TaskRefManager.process_dev_build_done`，后者 `_upsert_index`
重新取墙钟时间，返回 updated 后 reactor 再 append。该路径没有本场景可用的 durable
成功投影去重；不因此推断所有 fanout/repair 分支存在同样问题。

建议修复方向（不在本测试任务中实施）：

在 TaskRef consumer 或 reactor 入口按 durable 原始 `trigger_event_id` 和成功更新记录做
幂等判定；无变化的重复 delivery 应保持已有 index/occurrence。不得全局抑制 rejected →
updated 的合法修复重试；`tests/test_task_refs.py::test_task_ref_update_after_rejection_unblocks_same_build_event`
为回归控制。修复后必须使用同一
`--runxfail` 节点、TaskRef/Orchestrator/Task Pipeline 影响闭包和完整 S3 回归；确认修复后再移除
该专用 xfail 标记，保留原始断言。

当前处理：已保留可检出的 strict 测试和正向下游幂等控制；待维护者修复；未进入 `/issue-merge`。

## ZF-TEST-062：`/api/events` malformed/cursor 分页在读模型滞后窗口返回空页

> 状态: open（2026-09-22 K420 S6 当前 HEAD 复现；仅检测，不修改生产代码）

检测入口：

    tests/test_web_server.py::TestApiTraceCandidateFanoutEventsSearchDiagnostics::test_events_exposes_malformed_and_cursor_paginates

间歇性复现入口（不要用单次通过判定已修复）：

    uv run pytest -q --no-cov -o addopts='' --runxfail \
      tests/test_web_server.py::TestApiTraceCandidateFanoutEventsSearchDiagnostics::test_events_exposes_malformed_and_cursor_paginates

Given：在隔离 `state_dir/events.jsonl` 中写入两个合法事件和一行 malformed JSON，随后通过
真实 FastAPI `/api/events?limit=2&cursor=0` 请求第一页。

Then：第一页应包含 seq `[1, 2]`（第二项为 `event.malformed`），下一页应继续返回 seq `[3]`。
Actual：读模型尚未包含该日志时，`read_model.events_page()` 返回空页，API 没有可靠地回退到
完整 EventLog；请求结果为 `items=[]`，分页合同失败。该问题与 `ZF-TEST-009/010` 的
read-model freshness/WAL 竞争面相关，但本条单独记录 malformed/cursor 语义，避免合并不同
可观察合同。

当前证据（2026-09-22 K420）：

- 单次精确运行：`1 passed`；独立进程 20 次重复为 **18 passed、2 failed**，失败均为
  `items=[] != [seq 1, 2]`，无 collection error 或 timeout。
- K420 S6 组合首次观察到该节点普通失败：**905 passed、8 strict xfailed、1 failed**，
  `session_finished=true`；其余非绿色仍是既有 Issue。
- 失败发生在 `src/zf/web/projections/events.py::_events_page()` 调用
  `read_model.events_page()` 的 projection 优先路径；异常被吞掉后，滞后 projection 的空结果
  没有充分触发可靠的 EventLog fallback。

### 2026-09-22 K425 当前 HEAD 重复复核

- [x] 同一精确节点以 20 次独立 `uv run pytest` 进程重复执行：**16 passed、4 failed**；失败仍为
  `items=[]` 而非预期 seq `[1, 2]`，无 collection error 或 timeout。该比例与 K420 的
  18/2 结果不同，但仍明确证明问题是间歇性裸红，不能用单次通过关闭 Issue。
- [x] 运行目录：`/tmp/zf-zf062-repeat-QlK2U2`；每次输出独立保存，未使用 pytest 自动重试或
  xfail 掩盖失败；未修改 `src/`/`web/src/`、未访问 Provider/远端、未触碰 8001。
- [x] 与前序调用方联调：S6 projection contract + `tests/test_web_server.py` 选定集合为
  **20 passed、3 strict xfailed、104 deselected**；非绿色节点均为既有 projection Issue，
  未出现新的普通失败。S0 收尾 **11/11 passed**（`/tmp/zf-s0-k425-zf062-closeout`）。
- [x] K425 随后重跑完整 S6：**910 collected / 914 reported，906 passed、8 strict xfailed**，
  无普通失败；证据 `/tmp/zf-s6-current-20260922-k425`。该次完整 S6 的单次窗口未触发此
  间歇性缺陷，不抵消同轮独立 20 次中的 4 次真实失败，也不能关闭 Issue。

建议修复方向（不在本测试任务中实施）：在 projection 返回空页但源 EventLog 已有可见记录时，
核对 freshness/watermark 并 fail-closed 或同步回退到 EventLog；修复后必须用同一
`--runxfail` 节点、20 次独立进程重复、`tests/test_web_server.py` caller 和完整 S6 验证无 XPASS。

当前处理：

- [x] 保留精确检测节点和 malformed/cursor 正向断言；
- [x] 完成独立进程重复复现，未用自动重试掩盖失败；
- [x] 保留普通 S6 的真实断言与间歇性失败；不把单次通过改写为修复，也不添加自动重试或 xfail；
- [ ] 尚未修复；
- [ ] 尚未进入 `/issue → Fix Run → 回归 → merge` 流程。

### 2026-09-22 K439 当前 HEAD malformed/cursor 分页独立重复复核

- [x] 精确节点单次 `--runxfail`：**1 passed**；与 read-model freshness、malformed EventLog
  和 Web server 调用方联调：**23 passed、3 strict xfailed、20 deselected**。
- [x] 同一节点执行 20 个独立 pytest 进程：**19 passed、1 failed**；失败进程为第 17 次，
  原始断言明确为 `first["items"] == []` 而不是期望的 seq `[1, 2]`，证实仍是读模型滞后窗口的
  既有分页缺陷，不是 collection error、timeout 或测试环境失败。
- [x] 证据目录：`/tmp/zf-k439-zf062-20260922/`；每次输出独立保存，未启用自动重试、
  未增加 xfail、未修改 `src/`/`web/src/`、未触碰 8001 或远端。
- [x] S0 最终收尾：`/tmp/zf-s0-k439-final/pytest-testkit.json` **11/11 passed**，
  `exit_code=0`、`verdict=pass`、`session_finished=true`，无 collection error、deselection、
  timeout 或 evidence error。
- [ ] 维护者修复前，`ZF-TEST-062` 仍保持 `open`；修复后仍须用同一 `--runxfail`、20 次
  独立进程、调用方和完整 S6 影响闭包回归，不能以本轮 19 次通过关闭 Issue。

### 2026-09-22 K443 fresh route malformed/cursor 分层对照

- [x] 新增 `test_registered_project_fresh_route_preserves_malformed_cursor_pagination`：
  在隔离非默认 `runtime-state` 中写入两条合法事件和 malformed 行，显式
  `/api/projects/{project_id}/events?require_fresh=true` 返回 seq `[1,2]`（第二项为
  `event.malformed`），下一页返回 `[3]`；**1 passed in 2.59s**。
- [x] 与既有精确 malformed/cursor 节点及 freshness 路由合并：**3 passed in 4.28s**；
  Web/read-model/Channel 影响闭包：**11 passed、3 strict xfailed in 5.83s**。
- [x] 新增 fresh-route 节点 10 次独立进程重复：**10 passed、0 failed**；日志目录
  `/tmp/zf-k443-zf062-fresh-cursor-repeat-PN49df`。该结果仅证明显式 fresh 路由稳定，
  不替代 legacy 路由的独立复现。
- [x] 新目录 S0：`/tmp/zf-s0-k443-final-d9R5Kr/pytest-testkit.json`，**11/11 passed**，
  exit=0、`verdict=pass`、`session_finished=true`；JSON、ruff 和 `git diff --check` 通过。
- [x] 该对照未修改生产代码，未访问 Provider/远端或 8001；没有新增生产 Issue。
- [ ] 该结果只证明显式 fresh 路由正确，不能证明 legacy `/api/events` 的 bounded-stale
  空页已修复；`ZF-TEST-062` 仍保持 open，修复后仍须重跑独立进程与完整 S6。

### 2026-09-22 K444 K443 后完整 S6 联调

- [x] K443 新增测试注册后执行完整 S6：**916 reported、908 passed、8 个既有 strict
  xfailed**；pytest `exit_code=0`、`session_finished=true`，TestKit wrapper 按 strict
  xfail fail-closed 为 `fail`，无普通失败、collection error、deselection 或 timeout。
- [x] 8 个 xfail 仍仅对应 `ZF-TEST-012/022/024/025/026`；fresh-route malformed/cursor
  合同未引入新的普通失败或 Issue。
- [x] 新目录 S0：`/tmp/zf-s0-k444-closeout-DQrLmy/pytest-testkit.json`，**11/11 passed**，
  exit=0、`verdict=pass`、`session_finished=true`；coverage JSON 与 `git diff --check` 通过。
- [x] 报告：`/tmp/zf-s6-k444-k443-RO2qsL/pytest-testkit.json`；未修改生产代码、未访问
  Provider/远端、未触碰 8001。
- [ ] 本次 S6 只证明 K443 测试注册面没有污染前序 truth-chain，不改变 `ZF-TEST-062` 的
  open 状态，也不替代其 legacy 独立重复复现和修复后回归。

## ZF-TEST-061：Web 非可信 query 边界逃逸为 HTTP 500

> 状态: open（K362 当前 HEAD ZAP active scan + 确定性 Web contract 复现；仅检测，不修改生产代码）

检测入口：

    tests/test_web_zap_active_input_boundaries.py

裸红重现：

    uv run pytest -q --no-cov -o addopts='' --runxfail \
      tests/test_web_zap_active_input_boundaries.py

Given：向只读 Web 查询入口提供 ZAP 发现的非可信参数：artifact catalog 的非法
`view=http://www.google.com/`，以及 profile detect/recommend 的 4096 字符路径。

When：请求经过真实 FastAPI route、ArtifactQueryService 或 profile detector。

Then：非法/不可解析输入应被结构化拒绝为 4xx（400/404/422），且不泄漏 traceback 或返回
通用 500。Actual：三个请求均返回 HTTP 500 `Internal Server Error`。

当前复现证据（2026-09-21 K362）：

- strict 模式 **3 xfailed in 2.45s**；`--runxfail` **3 failed in 2.45s**，无 collection
  error、timeout 或 XPASS。
- 完整直接调用方联调：`tests/test_web_profile.py`、`tests/test_artifact_query_consumers.py`
  与本探针共 **20 passed、3 xfailed in 16.71s**；合法 profile、artifact catalog、CLI/Web
  对照保持通过。
- 全量隔离 ZAP active scan 也观察到相同 500；webhook 的 503 是未配置 secret 的设计性
  disabled 响应，不归入本 Issue。

建议修复方向（不在本测试任务中实施）：在 route/service 边界捕获 `ValueError`、`OSError`
等输入解析异常并映射到有界 4xx；限制 query 长度/路径语义，避免把内部异常直接转为 500。
修复后必须运行同一 `--runxfail`、完整 Web 调用方和 ZAP active scan 复核。

当前处理：

- [x] 已增加三个 strict-xfail 检出节点和合法输入对照；
- [x] 已用裸红命令确认当前 HEAD 稳定返回 500；
- [x] 已完成 profile/artifact 直接调用方联调；
- [x] 未修改生产 `src/`，未访问真实 Provider 或远端服务；
- [ ] 尚未修复；
- [ ] 尚未进入 `/issue → Fix Run → 回归 → merge` 流程。

### 2026-09-22 K384 当前 HEAD 裸红复核

- [x] 精确节点 strict **1 xfailed**；`--runxfail` **1 failed**，仍在
  `_evolution_policy_error()` 构造 registry key 时抛出 `ValueError: invalid literal for int()`，
  未进入结构化 `invalid_payload` `_failed` 回执。
- [x] Evolution policy、transition/outcome handlers、runtime、environment、skill-overlay caller
  联调 **92 passed、1 xfailed in 4.31s**；authority、enablement、registry identity、mode/kind、
  digest、target-state 与 overlay lifecycle 对照继续通过。
- [x] 证据日志：`/tmp/zf-k384-zf056-strict.log`、`/tmp/zf-k384-zf056-runxfail.log`、
  `/tmp/zf-k384-zf056-callers.log`；未修改生产代码、未启动 Provider/tmux、未访问网络或远端写入。

### 2026-09-22 K446 当前 HEAD 输入边界与调用方复核

- [x] `tests/test_web_zap_active_input_boundaries.py` strict 模式：**3 xfailed in 5.44s**；
  `--runxfail`：**3 failed in 5.69s**。非法 artifact `view` 与两个超长 profile `path`
  仍稳定返回 HTTP 500，未出现 XPASS、collection error 或 timeout。
- [x] profile/artifact 直接调用方联调：**20 passed、3 strict xfailed in 25.77s**；合法
  profile、artifact catalog 与 Web 对照保持通过，未发现新的普通失败。
- [x] 证据日志：`/tmp/zf-k446-zf061-1790079178716/strict.log`、
  `/tmp/zf-k446-zf061-1790079178716/runxfail.log`、
  `/tmp/zf-k446-zf061-1790079178716/callers.log`。
- [x] 本轮只复核现有 `ZF-TEST-061`，未修改生产 `src/`/`web/src/`，未访问 Provider、
  远端或 8001；Issue 继续保持 `open`。
- [x] 新目录 S0：`/tmp/zf-s0-k446-closeout/pytest-testkit.json`，**11/11 passed**，
  `exit_code=0`、`verdict=pass`、`session_finished=true`。
- [ ] 维护者修复后仍须使用同一 `--runxfail`、调用方和 active ZAP 严格门禁回归。

## ZF-TEST-060：Traces canonical route 首屏仍触发旧 `/events` bootstrap

> 状态: open（K271 SPA/AJAX 当前 HEAD Docker Playwright 复现；仅检测，不修改生产代码）

检测入口：

    tests/e2e/scripts/run_traces_page_regression.sh
    (内部执行 web/tests/traces-page.spec.ts::traces load a bounded index and disclose detail, history, and raw data on demand)

裸红重现：

    ZF_TRACES_KEEP=1 ZF_TRACES_RUN_ROOT=/tmp/zf-traces-regression-current-20260921-k271b \
      tests/e2e/scripts/run_traces_page_regression.sh

Given：打开 `/?project=zaofu-915bc1fe&page=observability&obs_tab=traces`，测试阻塞 workspace
project 解析并只允许 Traces scoped endpoint；Traces 列表请求应为
`/api/projects/<project>/traces?contract=v2&limit=50`，首屏不应加载旧的全量
`/api/projects/<project>/events` bootstrap。

When：当前 Web 首先按 `observability` 页面挂载，随后再 canonicalize 到 Traces；
`useProjectObservabilityData` 的 `isObservabilityPage(page)` 分支在路由切换期间发出
`/api/projects/zaofu-915bc1fe/events`。

Then：期望首屏 `forbiddenBootstrapRequests` 为空。Actual：收到
`["/api/projects/zaofu-915bc1fe/events"]`，测试在
`web/tests/traces-page.spec.ts:321` 失败；同文件第二个 unscoped route 测试通过。

影响边界：当前证据证明的是多余的旧 events 读取和页面请求契约漂移，不等同于 EventLog
数据损坏、写操作或外部副作用；Traces 的 scoped list/detail/spans/raw 其余断言尚未运行到
完成阶段。修复应由维护者决定路由 canonicalization 与 observability bootstrap 的所有权，
再用同一用例验证无旧 `/events` 请求且 scoped Traces 仍可用。测试不修改生产代码。

当前 HEAD 复核（K274，2026-09-21）：

    ZF_TRACES_KEEP=1 ZF_TRACES_RUN_ROOT=/tmp/zf-traces-regression-current-20260921-k274 \
      tests/e2e/scripts/run_traces_page_regression.sh

结果仍为 `1 failed, 1 passed`（4.4s），实际请求仍为
`/api/projects/zaofu-915bc1fe/events`，因此 Issue 保持 `open`；未修改生产代码。

当前 HEAD 裸红复核（K298，2026-09-21）：

    ZF_TRACES_KEEP=1 ZF_TRACES_RUN_ROOT=/tmp/zf-traces-regression-current-20260921-k298 \
      tests/e2e/scripts/run_traces_page_regression.sh

结果为 `1 failed, 1 passed in 6.2s`，无 collection error 或 timeout。首个 Traces 用例仍在
`web/tests/traces-page.spec.ts:321` 观察到
`/api/projects/zaofu-915bc1fe/events`，期望的 `forbiddenBootstrapRequests == []` 断言失败；
第二个 unscoped route 等待 workspace project resolution 的对照用例通过。Docker Playwright
trace、error-context 与 `docker.log` 保留在上述运行目录；未修改生产 `src/`，未访问真实
Provider 或远端服务，Issue 继续保持 `open`。

### 2026-09-21 K356 当前 checkout 隔离 Web 复核

- [x] 使用 `tools/start-webkanban.sh --host 127.0.0.1 --port 8002` 从当前 checkout 构建并
  启动临时 Web；前端 TypeScript/Vite build 与 bundle budget 均通过。首次误用非 loopback
  绑定被 trusted-session guard 拒绝，Playwright 只得到连接失败，未计为产品结果；修正后重跑。
- [x] 有效 Docker Playwright 结果为 **1 failed / 1 passed in 4.1s**；首个用例仍在
  `web/tests/traces-page.spec.ts:321` 观察到 `/api/projects/zaofu-915bc1fe/events`，第二个
  unscoped route 等待 workspace project resolution 的对照通过。
- [x] 证据保留于 `/tmp/zf-traces-regression-current-20260921-k356-v2`；临时
  `zf-web-8002` session 已停止、端口已释放。未修改生产代码、未访问 Provider 或远端服务，
  Issue 继续保持 `open`。

### 2026-09-22 K395 深层旅程联调

- [x] 为避免首屏已知 bootstrap 缺陷短路后续验证，测试保留 `forbiddenBootstrapRequests` 观测，
  将其断言延迟到完整 Traces 旅程末尾；没有删除或放宽旧 `/events` 禁止项。
- [x] 临时 Web 8002、onboarding `skip` 和 Docker Chromium 回归结果为 **2 passed in 4.4s**。
  同一 run 完成 scoped list、detail/history 分页、spans cursor、waterfall、degraded child、
  raw、empty trace、深链 span focus 以及 820/600 响应式布局；终态仍由旧 bootstrap 断言守护。
- [x] 证据：`/tmp/zf-k395-traces-run6`。临时 `zf-web-k395-8002` 已停止，8002 已释放；未修改
  生产代码，`ZF-TEST-060` 继续保持 `open`。修复首屏 canonical/bootstrap 所有权后，必须用同一
  完整旅程验证无 XPASS，并继续执行合规 Firefox/WebKit 矩阵。

### 2026-09-22 K396 合规三浏览器深层旅程复核

- [x] 能力探针确认 `mcp/playwright:latest` 只有 Chromium；使用本地已存在的精确版本官方镜像
  `mcr.microsoft.com/playwright:v1.59.1-noble`（含 Chromium/Firefox/WebKit），未拉取远端镜像。
- [x] `playwright.multibrowser.config.ts` 运行 Traces 两个节点，Chromium/Firefox/WebKit 各两项，
  共 **6 passed in 22.7s**；detail/history/spans/waterfall/raw/empty/deep-link/responsive 旅程均
  通过，旧 `/events` bootstrap 仍由终态断言保留。
- [x] 临时 Web 8002、tmux、浏览器资产和 workspace registry 均已清理；未修改生产代码，
  `ZF-TEST-060` 继续保持 `open`。这不是 `mcp/playwright:latest` 能力缺口的修复，也不关闭 active
  ZAP 或整体可信闭环。

## ZF-TEST-059：非 mapping 的 judge.passed payload 在 evidence gate 前抛出 AttributeError

> 状态: open（K190 module-parity bridge contract 发现；仅检测，不修改生产代码）

检测入口：

    tests/test_orchestrator_module_parity_mutation_contract.py::test_judge_evidence_gate_normalizes_non_mapping_payload_fail_closed

裸红重现：

    uv run pytest -q --no-cov --runxfail tests/test_orchestrator_module_parity_mutation_contract.py::test_judge_evidence_gate_normalizes_non_mapping_payload_fail_closed

Given：配置启用 `quality_floor=issue-regression`，向 `_reject_flow_judge_evidence_gap()` 传入
`judge.passed`，但事件 `payload` 是 list 而不是 mapping。事件边界应保持 fail-closed，并
返回结构化 `WorkflowRuntimeDecision(action="block")`。

When：生产实现把 `event.payload` 原样传入 `flow_metadata_for()`；该函数继续调用
`flow_kind_from_payload(payload)` 的 `.get()`。

Then：预期得到结构化 block；Actual：`AttributeError: 'list' object has no attribute 'get'`
从 evidence gate 逃逸，未生成 `flow.goal.blocked` 事件。

边界与对照：mapping payload 的缺失 evidence、各 quality-floor ref alias、flow-kind scoped
metadata、context identity fallback 和重复输出均有绿色 contract 覆盖。本 Issue 只说明
非 mapping payload 的异常分类/结构化回执缺失。

建议修复方向（不在本测试任务中实施）：在 `flow_metadata_for`/`flow_kind_from_payload` 的
边界将非 mapping payload 归一化为空 mapping，或在 gate 入口先进行类型保护并生成明确的
blocked decision。修复后执行该测试的 `--runxfail`、K190 mutation contract 及 module-gap
调用方联调。

当前处理：

- [x] 已增加确定性 strict-xfail 复现测试；
- [x] 已以裸红命令确认 `AttributeError` 在结构化 block 前逃逸；
- [x] 未修改生产代码；
- [ ] 尚未修复；
- [ ] 尚未进入 `/issue → Fix Run → 回归 → merge` 流程。

### 2026-09-22 K387 当前 HEAD 裸红复核

- [x] 精确节点 strict **1 xfailed in 0.43s**；`--runxfail` **1 failed in 0.42s**，非 mapping
  judge payload 在 `flow_metadata.py:21` 的 `.get()` 处抛出 `AttributeError`，未形成 block decision。
- [x] 完整 module-parity caller **179 passed、1 xfailed in 0.68s**；其它 flow metadata、evidence、
  context fallback 与 projection 对照通过。
- [x] 证据日志：`/tmp/zf-k387-zf059-strict.log`、`/tmp/zf-k387-zf059-runxfail.log`、
  `/tmp/zf-k387-zf059-callers.log`；未修改生产代码、未访问 Provider/网络或远端写入。

### 2026-09-21 K352 当前 HEAD repair validation 联调

- [x] 两个精确节点 strict **2 xfailed in 0.28s**；`--runxfail` **2 failed in 0.31s**：
  `shlex.split()` 的 `ValueError: No closing quotation` 仍逃逸，空 `worktree=""` 仍解析为当前
  checkout 并返回 `ok=true`。
- [x] 完整 `tests/test_run_manager_repair_validation_mutation_contract.py` strict **30 passed、
  2 xfailed**；裸红 **30 passed、2 failed**，普通 allowlist、shell 注入拒绝、timeout、spawn
  error、manual review 和显式 worktree 控制均通过。
- [x] 未执行恶意命令、Provider 或远端写入，未修改生产代码；两个 Issue 继续 `open`，修复后须
  以同一联合节点和完整调用方矩阵回归。

### 2026-09-21 K349 当前 HEAD candidate replay 联调

- [x] 精确节点 strict **1 xfailed in 2.81s**；`--runxfail` **1 failed in 2.81s**。跨越
  1.1 秒后，同一 canonical task ref 的第一次与重启重放 candidate commit hash 仍不同。
- [x] 完整 S8 E2E 为 **8 passed、1 xfailed in 5.72s**；其它 candidate、acceptance 和
  replay 控制通过，无 collection error 或 timeout。
- [x] 未修改生产代码、未访问 Provider/网络；Issue 继续 `open`，维护者修复后须以同一跨秒
  replay、manifest 和 lineage 断言验证无 XPASS。

### 2026-09-21 K309 当前 HEAD 裸红复核

复核命令：

    uv run pytest -q --no-cov -o addopts='' tests/test_orchestrator_module_parity_mutation_contract.py::test_judge_evidence_gate_normalizes_non_mapping_payload_fail_closed
    uv run pytest -q --no-cov -o addopts='' --runxfail tests/test_orchestrator_module_parity_mutation_contract.py::test_judge_evidence_gate_normalizes_non_mapping_payload_fail_closed

结果：精确节点 strict **1 xfailed in 0.92s**；`--runxfail` **1 failed in 0.77s**，
抛出 `AttributeError: 'list' object has no attribute 'get'`，位置为
`flow_metadata.py::flow_kind_from_payload()`。完整模块 strict **179 passed、1 xfailed in
1.12s**；`--runxfail` **179 passed、1 failed in 1.16s**。非 mapping 以外的 quality-floor、
evidence ref、identity/context fallback、blocked event 与 dedup 对照均通过；未访问 Provider、
未修改生产代码，Issue 继续保持 `open`。

### 2026-09-21 K354 当前 HEAD 联合复核

- [x] 精确节点 strict **1 xfailed in 1.68s**；`--runxfail` **1 failed in 1.36s**，非 mapping
  payload 在 `.get()` 处抛出 `AttributeError`，未形成结构化 block。
- [x] 完整调用方模块 strict **179 passed / 1 xfailed in 1.17s**；未修改生产代码、未访问
  Provider 或远端副作用；Issue 保持 `open`。

## ZF-TEST-046：基础 wheel 未声明 CLI 导入所需的 httpx 运行依赖

> 状态: open（当前工作树 `142a62ac` 的真实 clean-venv 安装复现；仅检测，不修改生产代码）

检测入口：

    tests/conformance/test_zaofu_testkit_s10_clean_install_host.py::test_clean_venv_install_matrix_runs_cli_and_declared_extras

重现：

    ZF_RUN_CLEAN_INSTALL_MATRIX=1 uv run pytest -q --no-cov -m host tests/conformance/test_zaofu_testkit_s10_clean_install_host.py

Given：从当前仓库构建 `zaofu-0.0.7` wheel，在不继承系统 site-packages 的全新 Python 3.11 venv 中只安装基础 wheel（不使用源码目录、不设置 `PYTHONPATH`）。

When：在该 venv 执行 `from zf.cli import main; main(['--help'])`。

Then：安装后的 CLI 应能完成帮助输出。实际在导入 `zf.cli` 的运行时链路中经 `zf.core.cost.catalog` 导入 `httpx`，而基础 wheel 及 `web`、`stream-json`、`feishu` extras 的 METADATA 均未提供这个运行依赖，出现：

    ModuleNotFoundError: No module named 'httpx'

同一测试会继续执行 Python 3.11/3.12 的 `base`、`dev`、`web`、`stream-json`、`feishu` 组合，并为每一项保留 venv、安装、CLI receipt；当前 `base`、`web`、`stream-json`、`feishu` 均在 CLI 导入处失败，`dev` 因包含 `httpx` 可通过。失败以本 Issue 精确 strict-xfail 暴露，不把依赖缺失吞成绿色。现有 `--no-deps --target` 测试不能发现该问题，因为它没有模拟干净依赖环境。

归属：这是发布包依赖声明/安装闭包问题，不是测试工具或用户项目问题。维护者应补齐实际运行依赖或调整 CLI 的可选导入边界；本任务不修改 `src/`。修复后该 strict-xfail 必须 XPASS，届时移除标记并重新验证所有 extras、Python 3.11–3.13、CLI validate/doctor/start-stop。

### 2026-09-21 当前 HEAD clean-venv 裸红复核（K297）

- strict host 矩阵：`ZF_RUN_CLEAN_INSTALL_MATRIX=1 ...` **1 xfailed in 66.88s**，无普通
  failure、XPASS、collection error 或 timeout。
- `--runxfail` 同一矩阵：**1 failed in 42.14s**。当前可用 Python 3.11/3.12/3.13 的
  `base`、`web`、`stream-json` 安装后 CLI 均在 `from zf.cli import main` 链路退出 1；
  `dev`、`feishu` 组合通过，失败汇总仍精确为基础 wheel/这些 extras 的 `httpx` 运行依赖
  缺失。测试真实构建 wheel、创建隔离 venv、安装包并确认 import 来自 `sys.prefix`，未使用
  源码 `PYTHONPATH`，未修改 `src/`、未访问 Provider/远端服务。
- 无 XPASS、collection error 或 timeout；维护者修复后须用同一 Python/extra 全矩阵验证
  无 XPASS，并保留 clean home、无 `.zf` 泄漏和 CLI help 正向断言。

### 2026-09-21 K311 当前 HEAD clean-venv 裸红复核

复核命令：

    ZF_RUN_CLEAN_INSTALL_MATRIX=1 uv run pytest -q --no-cov -o addopts='' -m host tests/conformance/test_zaofu_testkit_s10_clean_install_host.py::test_clean_venv_install_matrix_runs_cli_and_declared_extras
    ZF_RUN_CLEAN_INSTALL_MATRIX=1 uv run pytest -q --no-cov -o addopts='' --runxfail -m host tests/conformance/test_zaofu_testkit_s10_clean_install_host.py::test_clean_venv_install_matrix_runs_cli_and_declared_extras

结果：strict host 矩阵 **1 xfailed in 79.71s**；`--runxfail` **1 failed in 68.48s**。
当前可用 Python 3.11/3.12/3.13 中，`base`、`web`、`stream-json` 均在安装后 CLI help
导入处失败（exit=1），`dev` 与 `feishu` 通过；构建 wheel、隔离 venv、空 `PYTHONPATH`、
venv 内 `zf` 路径和 clean HOME 断言均执行。无 collection error、XPASS 或 timeout；未修改
生产代码，未访问 Provider/远端服务，Issue 继续保持 `open`。

## ZF-TEST-045：Issue 旧 candidate 重放改写同一 Goal closure，重建可能废弃未消费的 Judge 结果

> 状态: fixed（2026-09-24 K522；正式 Issue 三个重放节点及影响闭包通过；未提交）

### K522 修复与回归（当前状态，历史红证据保留）

当前裸红 `/tmp/zf-k522-045-red-20260924.xml`：终态同对象 replay **1 failed/3.79s**；
完整模块 `--runxfail` `/tmp/zf-k522-045-all-red-20260924.xml`：**8 passed/3 failed**。
直接 caller 新增 discovery/closure × 两种 source ref 合同，修复前 **2 passed/2 failed**
（`/tmp/zf-k522-consumed-red-20260924.xml`），精确检出 closure 未计为已消费。

生产修复只有 `flow_verification_identity._verification_was_consumed` 的事件集合：
同时识别 `flow.discovery.requested` 与 `flow.goal.closed`，仍须精确绑定原验证事件 ID。
不改变 closure identity/digest、generation/head 匹配、Judge gate 或 canonical ownership。
方法与真实调用方见 [实现说明](../../../../docs/impl/flow-verification-consumption.md)。

- 原 045 三节点已移除 strict xfail；初步 direct+完整 Issue **15 passed/27.73s**，
  `/tmp/zf-k522-045-green-20260924.xml`。
- 最终扩展验证包含旧验证消费不阻断新验证、Judge 派发仅一次、恢复后的真实 tick owner
  交接，以及 candidate currentness/S3 generation：**41 passed/40.84s**，
  `/tmp/zf-k522-final-direct-20260924.xml`。
- module-parity/gap-plan、closure identity/runtime/result、Issue、dossier/tick、K520 report
  与三个 mock E2E：**306 passed/1 strict xfailed/53.79s**，
  `/tmp/zf-k522-impact-20260924.xml`；唯一 xfail 属既有 ZF-TEST-059，不属于 045。
- 原 `scripts/run-flow-smoke.sh`：**10 passed/6.01s**，
  `/tmp/zf-k522-flow-smoke-20260924.xml`。premerge：**47 passed/4 failed/9.85s**，
  `/tmp/zf-k522-premerge-20260924.xml`，仍为 ZF-TEST-040，不抬 cap。
- 正式 S8 `/tmp/zf-k522-s8-20260924/testkit-run.json`：**239 passed/8 strict xfailed**，
  完整 session、无普通失败/取证错误；045 三节点均通过，其他已知红灯使 TestKit
  verdict 继续为 fail，未被本修复掩盖。

只关闭本问题的已验证重放/对象重建边界；没有真实 Provider/硬崩溃/远端副作用证明。
K521 的历史 xfail 报告不改写。未运行 native 三链、8001、`/issue-merge` 或提交/merge/push。

检测入口：

    tests/conformance/test_zaofu_testkit_s8_issue_route.py::test_issue_old_candidate_replay_preserves_goal_closure

裸红重现：

    uv run pytest -q --no-cov -o addopts='' --runxfail tests/conformance/test_zaofu_testkit_s8_issue_route.py::test_issue_old_candidate_replay_preserves_goal_closure

Given：隔离 Git main baseline/custom-state；YAML 预先启用 Goal 与真实 worktree，配置 local_only/report_only，关闭两种 auto ship，声明一个标准库回归命令。真实 request/proposal/submit、light task-map/package/contract、impl 派发与本地提交、必读 artifact 回执、自测、候选质量检查、verify 与 Thin Judge；仅 Provider 输出是固定夹具。三个正常顺序/较早重建对照均达到真实 run.goal.completed 与 Task done，未手写 canonical 终态。

When：三个独立参数：Judge worker 结果已追加但未消费时重建 Orchestrator/EventLog/Store 并重放；正常终态后在原 Orchestrator 重放 ledger；正常终态后重建并重放。重建不复制 Python 缓存；事件原文、candidate、task-map、package、goal claim-set 均未修改。

Then：同一个 run/goal/generation/candidate/package 应复用既有 closure，不因重复历史输入改变身份、取消既有 Judge 或重复派发。实际：

| 场景 | 实际 closure / 后果 |
|---|---|
| Judge 结果已落盘、消费前重建 | `flow.goal.closed` 从 1 增至 3，identity A→B→A；两个 superseded、旧 final fanout 被取消、新 final child 再派发；有限 12 tick 后无 Goal terminal，Task 仍未 done |
| 终态后同对象重放 | closure 从 1 增至 2，identity A→B；原 Goal terminal 仍唯一，不是已证明的重复完成 |
| 终态后重建重放 | 同样 A→B；原完成记录保留，不能称为任务已被重新打开 |

原因链：`orchestrator.py` 对无 task_id 的 candidate.ready/test.passed 调 `_bridge_verify_passed_to_flow_discovery`。`flow_verification_identity.py::_verification_was_consumed` 只识别 flow.discovery.requested，未将无 discovery 的 flow.goal.closed 视为已消费；重放旧 candidate 时又找到已使用的 verification。`orchestrator_module_parity.py:148–195` 合并 verification/candidate payload 并以 candidate 作为新的 source；`goal_closure_identity.py::build_closure_identity` 将 source_event_type 与 source_payload 纳入 fact digest，因此与 test.passed 原先铸造的 closure 不同。`goal_closure_bridge.py::_emit_current_goal_closure` 据此写 superseded/新 closure，后续真实 fanout supersede 路径取消原 Judge。上述不是语义输入或目标发生变化。

原始矩阵 `/tmp/zf-issue-route-replay-reproduction-t25zvtxy`：4 passed/5 failed（当时成功基线与终态重放仍在同一 case）。拆开正向控制与缺陷检测后的 `/tmp/zf-issue-closure-confirmed-l9m12rct`：7 passed/3 failed，37.337559 秒。精确签名复核 `/tmp/zf-issue-closure-exact-signature-d8lu2ar1`：三个均为 `IssueClosureReplayDrift`，13.197152 秒；三个 latest 内 523 个嵌入文件大小/SHA 和夹具源摘要已校验。之后只对该异常设 strict xfail，其他断言/基础设施错误保持普通失败，TestKit 总 verdict 仍 fail。

最终联调 `/tmp/zf-issue-route-stable-integration-2lvybfvc`：24 入口、266 passed/10 strict xfailed；本模块十一例为 8 passed/3 strict xfailed，045 的三个精确签名均保留。24 个 bundle 文件、20 份快照内 1678 个嵌入文件及来源摘要核验；无普通失败、收集错误或超时，外层 verdict=fail。新增候选独立命令拒绝错误归属的绿色自测回执，是通过的安全对照，不是另一个生产 Issue。过程中的旧 S1 child-ready 夹具竞态已修测试并保留原失败报告，未改生产代码。

边界：证明的是默认 Issue light、合法固定 worker 事实、同目录对象重建及 ledger 重放，不是硬杀进程或真实 Provider canary。终态后的两例只证明额外 closure/身份污染，不夸大为 duplicate remote effect；所有远端调用禁用。没有更改 production 去重、closure 身份、验收门槛或重试规则。配套读取/旧 commit/blocked Judge/失败自测对照已有真实拒绝证据；PRD standard、完整重试 cap 与产品质量不因本模块而视为完成。

### 2026-09-21 K312 当前 HEAD 裸红复核

复核命令：

    uv run pytest -q --no-cov -o addopts='' tests/conformance/test_zaofu_testkit_s8_issue_route.py::test_issue_old_candidate_replay_preserves_goal_closure
    uv run pytest -q --no-cov -o addopts='' --runxfail tests/conformance/test_zaofu_testkit_s8_issue_route.py::test_issue_old_candidate_replay_preserves_goal_closure
    uv run pytest -q --no-cov -o addopts='' tests/conformance/test_zaofu_testkit_s8_issue_route.py

结果：精确节点 strict **3 xfailed in 24.09s**；`--runxfail` **3 failed**，三种 replay
时机均抛出 `IssueClosureReplayDrift`，表现为相同 run/generation/candidate 的 closure
identity 在 `test.passed` 与 `candidate.ready` 间变化并写入 superseded。完整模块 **8 passed、
3 xfailed in 69.10s**；未修改生产代码，未访问 Provider 或远端服务，Issue 继续保持 `open`。

## ZF-TEST-044：Kernel Research 重规划改写 reason 后被自身 generation gate 拒绝

> 状态: open（当前工作树 `142a62ac` 正式受控入口已复现；仅检测，不修改生产代码）

检测入口：

    tests/conformance/test_zaofu_testkit_s8_research_replan.py::test_research_failures_retry_with_valid_identity_then_stop_at_cap

重现：

    uv run pytest -q --no-cov -o addopts='' --runxfail tests/conformance/test_zaofu_testkit_s8_research_replan.py

Given：沿用正式 fixed/adaptive 模板，使用隔离 custom-state、真实 ControlledAction research-start、不可变 generation/prompt/effective-config/run-contract。正常准入并派发后，所有 reader 报告失败，fixed 的 synth 再以 reject 收束；failure 事件由真实 Kernel 产生。Provider 仅 recording transport，Popen/socket 拒绝陷阱阻止外部调用。

When：把实际 stage failure 交给真实 run_once，Kernel 生成 `workflow.invoke.requested(rework_attempt=1)`，随后消费这个未经测试改写的 replan。四个参数为 fixed/adaptive × 旧 Orchestrator/第一次 failure 后重建 Orchestrator。

Then：失败路线应当通过合法重规划身份继续有界重试，达到 cap 后明确升级；至少不应由 Kernel 生成自身无法接受的请求。实际四个参数均出现 `workflow.invoke.rejected(reason=research_generation_workflow_generation_stale)`，只有最初一个 fanout；Task=in_progress、result/done 为零。再执行三次空闲 tick 仍无新 fanout、human.escalate 或 run.goal.blocked，未走到重试封顶。

根因证据：`src/zf/runtime/stage_failure_replan.py::plan_reader_stage_replan` 沿用原 generation/contract refs，却将 reason 改为 `stage replan 1/2 after research.*.failed`；`research_generation.py::_prompt_contract` 将 reason 纳入 prompt contract digest，`research_generation_binding_error` 因而重新算出不同 workflow_generation；`durable_call_workflow.py::_on_workflow_invoke_requested` 正确拒绝不一致身份。只在内存诊断副本恢复原 reason（不追加、不执行、不改 sidecar），四个参数的 binding_error 均变为空；两个独立负向输入对照确认人为篡改 reason 仍会被拒绝。因此不能通过放宽 generation gate“修复”。

首次原始 `/tmp/zf-research-replan-reproduction-pn30_2x5`：2 passed/4 failed，4.528576 秒。加入诊断和三次 idle 后再次裸红 `/tmp/zf-research-replan-confirmed-4tv57qyp`：2 passed/4 failed，6.238894 秒；四个 latest 快照共 178 个文件的字节数/SHA-256 与该次测试源摘要已核验。之后只对精确 `ResearchReplanIdentityDrift` 标 strict xfail；其他错误不能被吞并，修复后测试还会继续尝试两次重派发和 cap 断言，不能把当前未到达的后半链称为已验证。

最终联调 `/tmp/zf-research-replan-integration-9y_056bh`：129 passed/7 strict xfailed，62.134351 秒；本模块 4 passed/4 strict xfailed，新增重复 failure/重建幂等对照通过，其他三个红灯为 036/037。18 个 bundle 文件、14 份快照共 632 个嵌入文件及 source/helper/trace/config/lock 摘要全部核验；无普通失败、收集错误、timeout 或 bundle error，外层 verdict=fail。

边界：已证明两种声明模板、默认机械契约下的 Kernel replan 身份冲突；不是无限次数活锁或所有 Flow/Provider 的共同故障证明。三次 idle 是有限观测，不声称永远无法人工恢复。未制造假 stage failure、aggregate 或 Run blocked。关联正常交付、重建与 036/037 在 `test_zaofu_testkit_s8_research.py`；本缺陷是重规划自产输入与代际契约冲突，不与旧 child run 身份问题合并。

### 2026-09-21 K313 当前 HEAD 裸红复核

复核命令：

    uv run pytest -q --no-cov -o addopts='' tests/conformance/test_zaofu_testkit_s8_research_replan.py::test_research_failures_retry_with_valid_identity_then_stop_at_cap
    uv run pytest -q --no-cov -o addopts='' --runxfail tests/conformance/test_zaofu_testkit_s8_research_replan.py::test_research_failures_retry_with_valid_identity_then_stop_at_cap
    uv run pytest -q --no-cov -o addopts='' tests/conformance/test_zaofu_testkit_s8_research_replan.py

结果：精确节点 strict **4 xfailed in 9.00s**；`--runxfail` **4 failed in 8.93s**，四个
fixed/adaptive × same-process/reconstructed 参数均抛出 `ResearchReplanIdentityDrift`：Kernel
生成的 `workflow.invoke.requested` 因 `research_generation_workflow_generation_stale` 被自身
拒绝，未产生第二次 fanout，Task 保持 `in_progress`。完整模块 **4 passed、4 xfailed in
13.35s**；reason tampering 等负向对照通过，未修改生产代码、未访问 Provider/远端服务，Issue
继续保持 `open`。

## ZF-TEST-043：validate 忽略父项目配置，实际报告目录与 JSON 项目身份不一致

> 状态: open（真实 CLI 固定输入复现；未修改生产代码）

对应 CFG-08 / CLI-02；源码与已有测试调研见 [cli_workflow_coverage_research.md](../02_research_reports/cli_workflow_coverage_research.md)。检测入口：

    tests/conformance/test_zaofu_testkit_s2_cli_validation_context.py::test_validate_reads_and_reports_the_same_project_context[text-nested-False]
    tests/conformance/test_zaofu_testkit_s2_cli_validation_context.py::test_validate_reads_and_reports_the_same_project_context[json-nested-False]

裸红重现：

    uv run python -m pytest -q --no-cov -o addopts='' --runxfail tests/conformance/test_zaofu_testkit_s2_cli_validation_context.py

Given：隔离项目根有有效 `zf.yaml`，`project.state_dir: custom-state`，roles 为空；真实 TaskStore 已持久化 SEED，子目录 `src/worker` 没有自己的配置；清除继承的项目环境变量。共享 `resolve_project_context(require_config=True)` 正确识别父配置、父项目根和 custom-state。

When：从 `src/worker` 执行 `main(["validate"])` 或 `main(["validate", "--json"])`，不指定 `--path`。

Then：按统一项目上下文验证父配置，返回成功；允许的 validation-report/LKG 只落到父项目的 custom-state/config。实际两个 case 均返回 1，并创建 `src/worker/.zf/config/validation-report.json`，内容称子目录 `zf.yaml` 不存在。JSON 模式的 identity 却指向父项目和 custom-state，与真正被验证的配置路径、报告目录矛盾。原配置和既有 TaskStore 字节保持不变，未发现 canonical 数据损坏，不能把诊断文件错误位置夸大成任务状态丢失。

六个独立正向对照全部通过：项目根默认路径、项目根显式绝对路径、子目录显式绝对路径，各有文本/JSON 两种模式；校验 report 的 config_path、sha256、valid/errors、LKG 内容和 state root。临时规避方式是从项目根执行，或显式传入父配置的绝对路径。

根因范围：`src/zf/cli/validate.py::_run_impl` 默认采用 `Path.cwd()/"zf.yaml"`；错误报告经 `src/zf/core/config/lkg.py::infer_state_dir` 回退到该路径父目录的 `.zf`；JSON 包装器随后独立调用共享 context resolver，因此标识与真实操作分叉。同属 019/023 的上下文绕过问题族，但这是不同生产入口，且新增了 JSON 身份/实际报告不一致的检测。

原始正式报告 `/tmp/zf-cli-k2-reproduction-lsgdb085/testkit-run.json`：整个新增 CLI 矩阵 **610 passed / 2 failed**（85.483086 秒；本模块 6 passed / 2 failed，parser 模块 604 passed）。pytest/外层 exit=1、verdict=fail，session_finished=true；没有收集错误、deselection、timeout 或 bundle error。8 个 bundle 文件及两份 latest 内 10 份原始文件的字节数/SHA-256 已核验。原始结果在添加 strict xfail **之前**取得；此后只对精确 `ValidationContextMismatch` 签名设置 strict xfail，其他错误仍为普通失败，修复后 XPASS 会要求更新测试和 Issue。

建议维护者统一默认路径解析与报告身份来源，同时保留显式 `--path` 的语义；本测试任务只检出并记录，不实施修复。

后续正式联调 `/tmp/zf-cli-k2-integration-6toc3vu6`：本模块 6 passed/2 strict xfailed，整个 66 入口闭包 1402 passed/36 strict xfailed；pytest exit=0 但外层 verdict=fail。无未分类失败/收集错误/超时；当前测试源及包含本问题现场的失败包摘要核验通过。Issue 保持 open。

### 2026-09-21 当前 HEAD 裸红复核（K296）

- 普通模式 CLI context 矩阵：**6 passed、2 strict xfailed in 2.71s**；项目根默认/显式、
  nested 显式路径与 text/json 对照均通过，只有 nested 默认路径两例由 `ZF-TEST-043` 守护。
- `--runxfail`：**6 passed、2 failed in 2.52s**；从嵌套目录执行无 `--path` 的 text/json
  `validate` 都忽略父级 `zf.yaml`，返回 1，并在 `src/worker/.zf/config/validation-report.json`
  写入缺失子配置错误；JSON identity 同时指向父项目 `custom-state`，形成操作路径与报告
  身份分叉。无 XPASS、collection error 或 timeout。
- 两项均精确抛出 `ValidationContextMismatch`，真实 `main()`、共享 context resolver、TaskStore
  seed 保留和报告扫描均执行，未修改 `src/`、未访问 Provider/网络。

### 2026-09-21 K314 当前 HEAD 裸红复核

复核命令：

    uv run pytest -q --no-cov -o addopts='' tests/conformance/test_zaofu_testkit_s2_cli_validation_context.py::test_validate_reads_and_reports_the_same_project_context
    uv run pytest -q --no-cov -o addopts='' --runxfail tests/conformance/test_zaofu_testkit_s2_cli_validation_context.py::test_validate_reads_and_reports_the_same_project_context
    uv run pytest -q --no-cov -o addopts='' tests/conformance/test_zaofu_testkit_s2_cli_validation_context.py

结果：精确参数矩阵 strict **6 passed、2 xfailed in 3.78s**；`--runxfail` **6 passed、2 failed
in 3.69s**，text/json 两个 nested 默认 cwd case 均抛出 `ValidationContextMismatch`：CLI 忽略
父级 `zf.yaml`，在 `src/worker/.zf/config/validation-report.json` 写入缺失子配置错误，JSON
identity 却指向父项目 custom-state。完整模块同为 **6 passed、2 xfailed in 2.46s**；未访问
Provider/网络，未修改生产代码，Issue 继续保持 `open`。
- 本轮只确认默认 cwd 解析边界；显式 `--path` 与根目录行为通过，不外推其它 CLI 入口。维护者
  修复后须用同一 text/json 矩阵验证无 XPASS，并保留父配置报告路径/identity 一致性断言。

## ZF-TEST-042：单份 spine 投影丢失后，共享游标造成漏重建或其他投影重复累加

> 状态: open（真实投影入口确定性复现；未修改生产代码）

对应原方案 STO-09；调研 [store_integrity_concurrency_research.md](../02_research_reports/store_integrity_concurrency_research.md)。检测入口：

    tests/conformance/test_zaofu_testkit_s2_projection_rebuild.py::test_rebuild_preserves_complete_projection_and_authority

裸红重现：

    uv run python -m pytest -q --no-cov -o addopts='' --runxfail tests/conformance/test_zaofu_testkit_s2_projection_rebuild.py

Given：隔离 custom-state，由真实 Task/Feature/Session/Role Store 创建初态，保存 required evidence；EventLog 追加五条固定 occurrence。调用真实 `refresh_spine_projections` 后，attempt_count=1、review rounds=1、health review.rejected=1、RUN-ONE milestones=2，四份投影均非空；再次 refresh 零事件且全文不变。

When：只删除 `projections/` 下的一份可重建文件（四个独立 case）；保留其他投影和已到尾的 cursor。Then 应与原四份完整 JSON 相同，EventLog、canonical 文件和 required evidence 字节不变。实际：

| 被删除文件 | 实际 refresh | 与 baseline 的差异 |
|---|---|---|
| task_attempts.json | 从零折叠五条事件 | attempt 正确，但 stage rounds 1→2、health rejected 1→2、run milestones 2→4 |
| stage_spine.json | 折叠零事件 | stages 变成空 map |
| workflow_health.json | 折叠零事件 | counters 变空、last_event_ts 消失 |
| workflow_spine.json | 折叠零事件 | runs 内容消失 |

全部四份同时删除是通过的正向控制；单份删除后未出现 canonical/EventLog/required evidence 字节改变，因此本 Issue 是派生状态重建错误，**不是已证明的 TaskAttempt canonical 损坏或业务预算重复消耗**。另一个从真实 Task archive 重建 terminal index 的用例通过。

根因范围：`src/zf/runtime/workflow_spine_projection.py:350` 分别加载四份投影，但只取 attempts.cursor_offset；保存分别原子写出不等于四文件具有同一个原子提交。其他文件缺失时不能据 attempts 的尾游标认定已恢复；attempts 缺失时也不能直接向其他旧聚合重复累加。维护者应定义一致的投影恢复协议，本任务不指定或实现修复。

正式未标 xfail 的复现目录 `/tmp/zf-store-k2-reproduction-yhjx7c61`：四个新增模块共 **30 passed、10 failed**，4.429906 秒；本 Issue 四个失败，另六个属于 041。pytest/外层 exit=1、verdict=fail，session_finished=true，无收集错误、deselection、timeout 或 bundle 错误。24 个 bundle 文件和 10 份 latest 中 96 个嵌入文件的大小/SHA-256 已核验。最初 `/tmp/zf-store-k2-reproduction-os9a2ulf` 另有一个误用 TaskStore.archive_dir 的测试装配错误，已仅在测试侧修正；该错误不是生产缺陷。

仅为已复现的精确丢失/翻倍形态设置 `PartialProjectionRebuildMismatch` strict xfail；额外数据损坏、canonical 修改或 IO/装配错误仍普通失败。S2 runner 对 strict xfail 仍 verdict=fail，不能算绿；维护者修复后需移除标记并同时验证完整/部分重建。不改 src，不提交/合并/推送。

## ZF-TEST-041：Task、Feature、Role Store 将非空错误根类型当作空状态并允许覆盖写回

> 状态: open（六个真实读写边界确定性复现；未修改生产代码）

对应 STO-08，检测入口：

    tests/conformance/test_zaofu_testkit_s2_store_integrity.py::test_corrupt_store_is_rejected_without_overwrite

裸红重现：

    uv run python -m pytest -q --no-cov -o addopts='' --runxfail tests/conformance/test_zaofu_testkit_s2_store_integrity.py

Given：先经所属 Store 建立非空 SEED，保留有效原始快照。测试夹具将 Task/Feature active JSON 替换为 `{"sentinel":"damaged-root"}`，Role YAML 替换为非空 `[sentinel]`。这是注入**可解析但根类型错误**的输入，不是正常空项目，也不是生产程序自行破坏原始 SEED。

When：fresh reader 读取，随后分别由 cold instance 或损坏前已加载的 warm instance 执行 `TaskStore.add(NEW)`、`FeatureStore.add(NEW)`、`RoleSessionRegistry.get_or_create(NEW)`。Role 不把 cached get/all 不刷盘误判为缺陷；fresh read 必须重新构造，warm mutation 则走真实锁内 reload。

Then：应明确拒绝，后续 mutation 不得覆盖损坏文件；字节应等于**注入后的损坏字节**。Actual：三 Store × cold/warm 六例都读成空、成功保存 NEW，损坏输入被新状态覆盖。有效 seed 已被测试夹具替换，本 Issue 不声称程序恢复或亲自删除了 seed；已证明的是 malformed canonical 被静默重新初始化，原损坏现场不再保留。

正向/对照：四个 Store 的语法错误均拒绝并保留损坏字节；Session 非空错误根 TypeError 拒绝；missing/合法空初始化通过；完整 Task/Contract/Binding/Evidence 冷读与归档/显式 reopen 对照通过。Session 不用合法 create 初始化覆盖来伪造普通 mutation 漏洞。尚未覆盖零字节截断识别、所有内部字段损坏或 archive body 损坏，不外推这些情况。

根因范围：`src/zf/core/task/store.py::_load_raw`、`src/zf/core/feature/store.py::_load_raw` 在根不是 list 时返回 `[]`；`src/zf/core/state/role_sessions.py::_load` 在非 dict 时清空后返回。后续所属 Store 的真实更新接受了这一空初态。Session 对非空非 mapping 根有拒绝，与此不同。

正式复现 `/tmp/zf-store-k2-reproduction-yhjx7c61` 的六个 `CorruptCanonicalAccepted` 均保留 seed、损坏输入参数、读/写回执与最终字节；总运行与证据审计数据见上方 042。只对这六个精确异常设置 strict xfail，syntax/Session/合法初始化不标 xfail；Runner 仍裁决 fail。测试不修改 loader 或生产 Store，由维护者修复后更新标记并验证正常空状态未被误拒绝。

### 2026-09-21 当前 HEAD 裸红复核（K294）

- 普通模式完整矩阵：**10 passed、6 strict xfailed in 0.51s**；syntax 四类、合法空/缺失
  初始化及 Session 根类型边界均保持绿色，只有精确 wrong-root Task/Feature/Role 参数被
  `ZF-TEST-041` 守护。
- `--runxfail` 同一矩阵：**10 passed、6 failed in 0.66s**；Task、Feature、Role 三类
  Store 在 cold 与 warm mutation 下均把可解析但根类型错误的非空 canonical 当成空状态，
  成功写入 `NEW` 并覆盖损坏字节。无 XPASS、collection error 或 timeout。
- 失败均精确抛出 `CorruptCanonicalAccepted`，与既有 Issue 判据一致；真实四类 Store、
  lock/reload 和 ModelEvidence 路径均执行，未修改 `src/`、未访问 Provider/网络。
- 本轮只确认六个 wrong-root 读写边界；不外推零字节、内部字段、archive 损坏或 Session
  非 mapping 行为。维护者修复后须用同一矩阵 `--runxfail` 验证无 XPASS，并保留 syntax、
  missing/empty 正向对照。

### 2026-09-21 K316 当前 HEAD 裸红复核

复核命令：

    uv run pytest -q --no-cov -o addopts='' tests/conformance/test_zaofu_testkit_s2_store_integrity.py::test_corrupt_store_is_rejected_without_overwrite
    uv run pytest -q --no-cov -o addopts='' --runxfail tests/conformance/test_zaofu_testkit_s2_store_integrity.py::test_corrupt_store_is_rejected_without_overwrite
    uv run pytest -q --no-cov -o addopts='' tests/conformance/test_zaofu_testkit_s2_store_integrity.py

结果：精确矩阵 strict **10 passed、6 xfailed in 0.66s**；`--runxfail` **10 passed、6 failed
in 0.66s**；完整模块同为 **10 passed、6 xfailed**。Task/Feature/Role 的 cold 与 warm
mutation 六个 wrong-root case 均将 `{"sentinel":"damaged-root"}` 或 `[sentinel]` 当空状态，
成功写入 `NEW` 并覆盖损坏字节；syntax、Session、missing/合法空对照均通过。未修改生产代码，
未访问 Provider/网络，Issue 继续保持 `open`。

### 2026-09-21 当前 HEAD 裸红复核（K295）

- 普通模式完整投影恢复矩阵：**1 passed、4 strict xfailed in 0.97s**；删除全部四份
  projection 的完整重建正向控制通过，四个单份删除面由 `ZF-TEST-042` 精确守护。
- `--runxfail`：**1 passed、4 failed in 1.03s**；删除 `task_attempts.json` 后重新折叠五条
  ledger 会令 stage rounds、health rejection counter 和 workflow milestones 翻倍；删除
  `stage_spine.json`、`workflow_health.json` 或 `workflow_spine.json` 则以尾 cursor 跳过
  事件，分别留下空 stages、空 counters 或空 workflow projection。
- 四项均精确抛出 `PartialProjectionRebuildMismatch`，无 XPASS、collection error 或
  timeout；真实 Task/Feature/Session/Role Store、EventLog、spine projector 与 required
  evidence 字节保护均执行，未修改 `src/`、未访问 Provider/网络。
- 本轮只确认四种单份缺失的漏重建/重复累加形态；完整删除重建与 terminal-index 正向控制
  通过，不外推为所有 projection 或硬崩溃恢复均失败。维护者修复后须用同一矩阵验证无 XPASS。

### 2026-09-21 K315 当前 HEAD 裸红复核

复核命令：

    uv run pytest -q --no-cov -o addopts='' tests/conformance/test_zaofu_testkit_s2_projection_rebuild.py::test_rebuild_preserves_complete_projection_and_authority
    uv run pytest -q --no-cov -o addopts='' --runxfail tests/conformance/test_zaofu_testkit_s2_projection_rebuild.py::test_rebuild_preserves_complete_projection_and_authority
    uv run pytest -q --no-cov -o addopts='' tests/conformance/test_zaofu_testkit_s2_projection_rebuild.py

结果：精确矩阵 strict **1 passed、4 xfailed in 1.03s**；`--runxfail` **1 passed、4 failed
in 1.03s**；完整模块 **2 passed、4 xfailed in 0.89s**。删除 `task_attempts.json` 会使
stage rounds、health rejected counter 和 workflow milestones 翻倍；删除另外三份单投影则因
共享尾 cursor 跳过 ledger，留下空 projection。删除全部四份的完整重建控制通过；未修改生产
代码，未访问 Provider/网络，Issue 继续保持 `open`。

## ZF-TEST-040：当前 HEAD 的结构哨兵因超长源码及未登记结构债务失败

> 状态: open（2026-09-30，feature 优先；Triage 相关 v7 重构不接入，整合目标结构门禁仍失败；历史 v7 fixed 证据保留）

本条含多个子项：K593-G1/G2/G3 的 Triage 路由、ingress、页面及 CSS 重构按操作者要求
不接入。其他通用结构子项不因本次决定被判定修复失效；但不能沿用 v7 的整体绿色结论。
feature 最近预合并门为 45 passed / 6 failed，其中含结构失败，未抬限或豁免。
下文“已修复”均是相应 v7 历史批次记录，不表示当前合并目标已关闭本 Issue。

### 2026-09-29 K593-G3 Page/CSS 子项与 040 结构门禁已修复（v7 历史）

`IssueTriagePage.tsx` 的五个筛选组件与两个共享展示 helper 已原样迁移到
`web/src/pages/IssueTriageFilters.tsx`；页面 **1349→954 行**，调用方、props、状态回调和
GET query 语义保持不变。`24-issue-triage.css` 按原始顺序拆为 controls/content 两文件，
展开拼接与迁移前 **29944 字节逐字一致**，没有改变 CSS 规则顺序。

`npm run typecheck`、`npm run build`（最大 bundle 487394/500000）和两个模型单测通过；
Docker `mcp/playwright:latest` 精确运行四个既有安全节点及一个固定只读 API 的真实 App
筛选节点，**5 passed/8.8s**。首轮夹具漏了只读
`/kanban-agent/pending-proposals` 请求，补齐 fixture 后复验通过；这不是生产 bug。
含候选 PR/Refresh PR 的独立滚动节点按范围明确未执行。最终 `dev-premerge-gate.sh`
为 **51 passed/7.41s、exit=0**。

结合 K593-B/C/D/E/F/G1/G2，原三个冻结 cap 和六个未登记大文件的红灯均解除，
本条将 `ZF-TEST-040` 标记已修复，旧 open 回执仅保留历史，不抬限、不加白名单。
真实 Provider、恢复缺陷、其他 OS/性能、历史证据及最终同源验收不属于本条修复证明，
继续按主干 todo 开放。证据归档：
`attachments/k593g3-ui-filter-style-20260929/`。

### 2026-09-28 K593-G2 ingress超长子项已修复（总体仍open）

原16个只读triage准入方法迁至`external_issue_triage_admission.py`（718行），
`ExternalIssueIngressService`通过继承继续调用，旧文件1551→853行。
全部27个方法与迁移前快照AST一致，既有pattern_id dirty修改及subprocess安全替换点
保留；EventWriter/Store/sidecar写入规则不变，Fix提案和poller仍在原服务。
首轮54 passed/23.71s；最终来源绑定68 passed/24.761099s，包含HTTP caller、前序
投影、共享合同与真实triage mock，session完整/source stable/exit0。
最终premerge50 passed/1 failed/7.58s：仅Page/CSS两个未知大文件仍红，阈值/白名单
未放宽。证据：`attachments/k593g2-triage-admission-20260928/`。
本子项已修复、未提交；040总体仍open，不代替最终同源/Provider验收。

### 2026-09-28 K593-G1 triage路由超长子项已修复（总体仍open）

既有只读`_workflow_projection`与三个状态常量原样移到
`src/zf/web/issue_triage_projection.py`（388行），原路由保持同名函数调用、缓存和写边界；
routes由1174降为796行。迁移前后全部函数/类与常量AST一致，ingress和UI共享改动未触碰。
五文件调用方72 passed/5.78s；该次整文件选择误包含两项既有候选交付/PR测试，已记执行
偏差而非新功能验收。随后精确API/缓存/身份/终态＋前序auth/Event合同来源绑定
**24 passed/6.637062s**，session完整、source stable、无收集/取证错误。
premerge **50 passed/1 failed/8.69s**，唯一失败仍检出ingress、IssueTriagePage、CSS三个
大文件；不抬限、不豁免，040总体保持open。证据与静态校验回执：
`attachments/k593g-issue-triage-projection-20260928/`。本子项已修复、未提交。

### 2026-09-28 K593-F server冻结cap已修复（总体仍open）

server的16个Web会话/授权函数与共享内存表原样迁移至`src/zf/web/web_auth.py`，
同名导入保持HTTP/CLI/WebSocket接口、口令限流与token/trusted授权策略，旧文件
**9554→9353行**，旧cap9360不变。迁移前后全部函数AST一致，portable Issue共享
dirty代码不变。新增lock/expiry旧cookie拒绝及operator socket拒绝4项已登记S6。
既有server/S7/CLI366 passed；terminal/workflow-request直接caller43 passed；
来源绑定S0/auth/mock组合25 passed，完整session/source stable/exit0。
premerge **50 passed/1 failed/13.13s**，只剩四未知大文件集合，040总体仍open。
证据：`attachments/k593f-web-auth-20260928/`；本子项已修复、未提交，不表示最终同源验收。

### 2026-09-28 K593-E fanout冻结cap已修复（总体仍open）

`orchestrator_fanout.py`的schema education占位表和顶层/report两方法成对原样移至
`fanout_schema_education.py`，经mixin保持实际briefing调用；**8649→8560行**，
旧cap8564不变。正文与HEAD对应段仅差末尾空行，前序dirty改动完整保留。
直接schema/reader/independence/task-map闭包192 passed；来源绑定mock/EventWriter/
fanout五文件59 passed，S0 19 passed。premerge **49 passed/2 failed**：fanout红灯
消除，server9554>9360和四个未知大文件继续检出，不能关闭040总体。
证据：`attachments/k593e-fanout-schema-20260928/`；实现边界见
`docs/impl/fanout-schema-education-boundary.md`。未提交，未执行真实Provider或最终同源验收。

### 2026-09-28 K593-D App冻结cap已修复（总体仍open）

首页projection/pulse/Focus/Spine职责原样移到`web/src/pages/ProjectHomePage.tsx`，
App从3707降至3231行；冻结cap从3580收紧为3555，不抬限或加白名单。真实App调用方
Docker浏览器3 passed，既有Web模型unit/typecheck/build通过；premerge最终
**48 passed/3 failed/16.55s**，App红灯消除。剩余fanout/server和四个未知大文件未关闭。
归档`attachments/k593d-project-home-20260928/`含原始夹具失败和最终JUnit；未提交。
本修复仅结构债务，不等于Provider、性能、历史缺证或最终同源验收通过。

### 2026-09-28 K593-B 局部已修复（总体仍open）

`control_actions_workflow_request.py`超长子项**已修复，未提交**：取消/拒绝两方法
原样移到有真实service caller的`control_actions_workflow_decisions.py`，旧文件
1016→824行、新文件201行。没有抬cap、增加白名单、改变事件/业务语义或改共享脏文件。
直接动作/Web/router/payload/Event/mock E2E七文件389 passed，S0 19 passed；
premerge仍47 passed/4 failed，但未知大文件由6降至5，三个冻结cap保持红。
证据与原始测试夹具失败见`attachments/k593b-decisions-20260928/`及steps K593-B。
K593-F1（新测试误用dataclass.model_dump）已修复为账本原字节比较，不是新生产bug。
K593-A只检查dirty状态不足以认定其它会话冲突；后续可继续选择不覆盖既有改动的责任边界，
不因dirty本身请求新的授权。下方历史行数/六文件集合仅表示各自当时快照。

### 2026-09-28 K593-C 局部已修复（总体仍open）

`spawn_coordinator.py` 中 Codex role-local hook trust 的 TOML 写入逻辑已原样移到
`src/zf/runtime/codex_runtime_hook_trust.py`，保留原导入调用和真实 `SpawnCoordinator.spawn`
路径；文件从 **1049 行降至 969 行**，未覆盖其他会话的 Claude 分支改动。新增真实 spawn
入口/直接 writer 合同 2 passed；含 spawn、生命周期、CLI、Event 合同和 mock E2E 的影响闭包
**125 passed/53.11s**。新测试已登记 S5 与 MAINLINE。

本子项只修复结构债务，不改变 Codex hash、认证、权限、Provider 命令或 SessionStore 语义；
全局 `~/.codex` 配置在隔离测试中保持字节不变，role-local `config.toml` 重复写入幂等。
`dev-premerge-gate.sh` 仍为 **47 passed/4 failed**：三个冻结 cap 继续红，未知大文件由
6 减至 4；`ZF-TEST-040` 总体仍为 `open`。证据：
`/tmp/zf-k593c-20260928-vxEi4l/impact-final.xml`、
`/tmp/zf-k593c-20260928-vxEi4l/new-direct.xml`，设计边界见
`docs/impl/codex-runtime-hook-trust-boundary.md`。

收尾：S5 **491 passed/157.252529s**、最终S0 **19 passed/44.954353s**，完整session且无
缺证/收集/超时错误。上述临时原件的字节相同副本已归档至
`attachments/k593c-hook-trust-20260928/`，含原红JUnit、phase/JUnit/runner和日志。
本轮未知大文件实际为5→4，6→4包含K593-B；最终同源验收未重跑，不授权发布。

同时复跑的 Codex hash/version 传感器仍报告既有环境/基线漂移（10 个静态 hash 向量不匹配，
当前 `codex 0.155` 而基线为 `0.144`）；该失败早于并独立于本次原样拆分，未改写基线、未
新增生产 Issue，也不把 deterministic/mock 结果冒充原生 Provider 通过。

复现入口（保持普通失败，不改为skip/xfail）：

    uv run pytest -q --no-cov -o addopts='' -m 'not host' tests/test_codex_hook_settings.py
    uv run pytest -q --no-cov -o addopts='' tests/test_codex_hook_settings.py::test_codex_version_matches_hash_baseline

独立hash实跑 **10 failed/8 passed/1 host deselected**；扩展首轮版本探针红灯保存在
`attachments/k593c-hook-trust-20260928/focused.xml`，未重复执行host版本探针。
这里只确认原生兼容性证据不足，不宣称hash算法必错；下一步应以可信Provider原件核验，
不能用函数自己算出的摘要更新期待值。

### 发现与复现

### 2026-09-21 K319 当前 HEAD 复核

复核命令：

    uv run python scripts/dev-verify.py plan --base dev --tests tests/test_structure_discipline.py --json
    bash scripts/dev-premerge-gate.sh

计划命令 exit **0**，`errors=[]`、`warnings=[]`；实际 premerge 哨兵 exit **1**，结构集合
**47 passed、4 failed**，输出保留于 `/tmp/zf-k319-premerge-gate.log`。三个冻结 cap 仍超限：
`src/zf/runtime/orchestrator_fanout.py` 8647/8564、`src/zf/web/server.py` 9554/9360、
`web/src/app/App.tsx` 3707/3580；另有六个未登记的 >1000 行源文件债务：
`control_actions_workflow_request.py`、`external_issue_ingress.py`、`spawn_coordinator.py`、
`web/issue_triage_routes.py`、`IssueTriagePage.tsx`、`24-issue-triage.css`。这仍是结构维护/
发布门禁问题，不新增运行时 Issue；未调整阈值、未修改 `src/`/Web，维护者修复或登记后才可
重新验证 exit=0。

K1 联调额外执行 `bash scripts/dev-premerge-gate.sh`：manual docs currentness=ok，pytest **47 passed、4 failed**。通过原 TestKit runner 对同五个测试文件再次复现，报告 `/tmp/zf-event-ingress-sentinel-ot9qfahh/testkit-run.json`：47 passed/4 failed，7.02711 秒，pytest/外层 exit=1、verdict=fail，无收集错误、deselected、timeout 或 bundle 错误。

    tests/test_structure_discipline.py::test_oversized_file_growth_frozen
    tests/test_structure_discipline.py::test_unknown_oversized_source_files_are_not_silent_debt

第一入口三个失败参数：

| 文件 | 当前行数 | 注册上限 |
|---|---:|---:|
| `src/zf/runtime/orchestrator_fanout.py` | 8647 | 8564 |
| `src/zf/web/server.py` | 9436 | 9360 |
| `web/src/app/App.tsx` | 3707 | 3580 |

第二入口发现未登记的 >1000 行文件：`control_actions_workflow_request.py`（1016）、`external_issue_ingress.py`（1546）、`spawn_coordinator.py`（1043）、`web/issue_triage_routes.py`（1174）、`web/src/pages/IssueTriagePage.tsx`（1349）、`web/src/styles/24-issue-triage.css`（1225）；前三项位于 `src/zf/runtime/`，`web/issue_triage_routes.py` 位于 `src/zf/`。

### 判据、归属与处理

期望：既有超长文件不突破冻结上限；新增超长文件不能成为无记录的结构债务。实际上述条件被当前 HEAD 违反，故哨兵不能通过。十个相关源文件/结构测试文件逐一与 `git show HEAD:<path>` 比较字节完全一致，HEAD 为 `142a62ac763b148cbe2b401ac1cd7ae0f8d1c072`；不是本轮新增测试改变了生产文件。

这是维护/发布门禁问题，不据此宣称运行时功能错误。现成测试已经检出，无须另造 xfail；不得直接抬上限或豁免债务以变绿。维护者需依据真实结构拆分或有依据地登记债务与触发条件。本任务仅记录，未修改 src/Web/结构阈值，未提交、合并或推送。

### 2026-09-21 K350 当前 HEAD premerge gate 复核

- [x] `uv run python scripts/dev-verify.py plan --base dev --tests tests/test_structure_discipline.py --json`
  返回 0，`errors=[]`、`warnings=[]`；计划覆盖 4545 个历史 changed files 与 1021 个 selected
  tests，不能误读为本轮全部执行。
- [x] `bash scripts/dev-premerge-gate.sh` 返回 **47 passed、4 failed**、exit=1；失败仍精确
  为 3 个冻结 oversized cap（`orchestrator_fanout.py` 8647/8564、`web/server.py` 9554/9360、
  `App.tsx` 3707/3580）及 6 个未登记 >1000 行源文件债务。
- [x] 未修改结构阈值、生产 src/Web、远端引用或提交；Issue 继续 `open`，维护者需拆分或登记
  具体债务和 defer trigger 后再验证 gate exit=0。

### 2026-09-22 K422 当前 HEAD 发布前门禁复核

- [x] `bash scripts/dev-premerge-gate.sh` 再次返回 **47 passed、4 failed**、exit=1；manual docs
  currentness 为 `ok`，四个失败与 K350 完全一致。
- [x] 冻结 cap 仍为 `orchestrator_fanout.py` 8647/8564、`web/server.py` 9554/9360、
  `App.tsx` 3707/3580；未登记 >1000 行源文件仍为
  `control_actions_workflow_request.py`、`external_issue_ingress.py`、`spawn_coordinator.py`、
  `web/issue_triage_routes.py`、`IssueTriagePage.tsx`、`24-issue-triage.css`。
- [x] `dev-verify plan --base dev --json` 因共享工作树的历史/并行差异发现多项 unmapped Python
  change 并 exit=2；自动计划仍正确列出 deterministic、serial、Web、premerge、flow-smoke 和
  explicit real-provider tiers，不能把共享工作树 errors 误报为本阶段代码回归。
- [x] 未修改 cap、结构债务白名单、生产 `src/`/Web 或远端引用；Issue 保持 `open`，发布前
  merge gate 继续 fail-closed。

## ZF-TEST-039：EventWriter 接受重复事件 ID 并再次驱动真实阶段报告

> 状态: open（生产入口确定性复现；未修改生产代码）

### 需求与检测入口

对应 `ZaoFu_TestKit.md` EVT-05：同一 accepted occurrence 只接受一次，projector 不重复运行，attempt 不额外增加。调研见 [event_ingress_dedup_research.md](../02_research_reports/event_ingress_dedup_research.md)。

    tests/conformance/test_zaofu_testkit_s2_event_ingress.py::test_writer_rejects_reaccepted_occurrence

裸红复现命令（6 个参数化 case）：

    uv run python -m pytest -q --no-cov -o addopts='' --runxfail tests/conformance/test_zaofu_testkit_s2_event_ingress.py

### 初态、动作、期望与实际

1. 隔离 `custom-state`，经 TaskStore 创建 in_progress Task；真实默认 Writer 追加 parent 和一条合法 `dev.build.done`，固定完整 ID/ts/origin/cause/correlation。首条账本/真实报告/投影调用均为 1，Task 不变。
2. 经同一 Writer、重建 Writer 或正常 size archive 后重建 Writer，再追加完整副本；另一组只改 payload.result 为 conflict，但保留同 ID。
3. 期望：第二份不成为 accepted occurrence、不再次驱动原业务投影，已有身份内容保持 first；冲突可以明确拒绝，不能覆盖原事实。
4. 实际：6 个 case 都成功返回并保存第二行；`impl-report.json` 的事件 ID 列表含两个同 ID，event_count=2；透明 observer 记录真实默认 stage projector 调用 2 次。冲突组 EventIndex.lookup_event 同时返回 conflict，覆盖 first。没有伪造 reducer 或依靠自去重 spy。

原始未标 xfail 的正式 runner：`/tmp/zf-event-ingress-reproduction-5zqctf_w`，**4 passed、6 failed**，2.769427 秒；pytest/外层 exit=1、verdict=fail，session_finished=true，无收集错误/deselection/timeout/bundle 错误。16 个 bundle 文件及 6 份 latest 快照的原始字节/SHA-256 已独立核验，输入、首次报告、Task、最终账本/报告/Index/调用记录可回读。

最初一次装配把 `lookup_event()` 返回的 ZfEvent 直接 JSON 序列化，导致 8 个 TypeError；已在测试侧使用 asdict 更正。该次 `/tmp/zf-event-ingress-reproduction-mhoayonh` 是测试基础设施错误，不作为本 Issue 的生产证据。

### 正向对照与影响边界

- 相同内容换新 ID 正常保留两次；不能修成 payload 全局去重。
- blocking schema 两次拒绝各有独立诊断 ID；更正后首次合法接受通过，不能把未接受输入的 ID 永久占用。
- 真实 task.created→run_once 的同实例/新实例重复消费对照通过：TaskAttempt 1 条、transport 1 次、task.dispatched/attempt.started 各 1；Task 仍 in_progress。**本 Issue 未证明重复 worker 执行或重复 canonical mutation**，生产调度层的局部保护不能掩盖 Writer/投影缺陷。
- stage report 为派生投影，不是 canonical Task；不把报告计数翻倍写成 Task 状态损坏。
- 与 ZF-TEST-013 的归档碰撞崩溃、034 的旧 Index 漏读、035 的排序漂移、038 的重建后 rework 重计费不同。本次无崩溃或乱序时间，正常归档只是重送前置边界。
- CLI emit 不提供事件 ID；相同 CLI 文本会产生新 occurrence，本测试不宣称所有 CLI/Feishu 重试都复用同 ID。

### 根因范围与当前处理

`src/zf/core/events/writer.py::EventWriter._append_with_projectors` 无去重准入，直接 append 后运行默认 projector；`src/zf/core/events/log.py::EventLog.append` 的路径锁只保护写入，未做唯一 ID 检查；`stage_reports.py::build_stage_report` 读取全部 occurrence 并计数。Index 按 ID 后写覆盖使其不能独立证明 ledger 唯一。

这里只定位边界，未规定必须在哪层修复低层 append 原语。只为 `DuplicateOccurrenceAccepted` 加 strict xfail；projector/IO/场景装配等其他错误不会被豁免。S2 runner 对 xfail 仍裁决 fail，修复后必须移除/更新检测标记并验证合法新 ID 和 schema 控制。实际修复由维护者处理，不改 src、不提交/推送。

### 2026-09-21 当前 HEAD 裸红复核（K285）

- 普通模式：6 个参数化 case 均 strict xfailed，5 个正向对照被 deselect；无普通失败。
- `--runxfail`：6 个参数化 case 全部 failed（1.65s）；same-writer、reopen、archive
  三种边界及 identical/payload-conflict 两类重复输入均真实写入第二个 occurrence，
  再次调用 stage projector，报告 event_count 变为 2。
- 无 XPASS、collection error 或 timeout；测试使用隔离 state、真实 EventWriter/Projector/
  EventLog/Index，不访问 Provider/网络；未修改 `src/`。既有 `ZF-TEST-039` 保持 open，
  修复后需用同一 node 验证无 XPASS，并保留新 ID 正向对照。

### 2026-09-21 K317 当前 HEAD 裸红复核

复核命令：

    uv run pytest -q --no-cov -o addopts='' tests/conformance/test_zaofu_testkit_s2_event_ingress.py::test_writer_rejects_reaccepted_occurrence
    uv run pytest -q --no-cov -o addopts='' --runxfail tests/conformance/test_zaofu_testkit_s2_event_ingress.py::test_writer_rejects_reaccepted_occurrence
    uv run pytest -q --no-cov -o addopts='' tests/conformance/test_zaofu_testkit_s2_event_ingress.py

结果：精确矩阵 strict **6 xfailed in 1.60s**；`--runxfail` **6 failed in 1.63s**；完整模块
**5 passed、6 xfailed in 1.63s**。same-writer、reopen、archive 三种边界以及 identical /
payload-conflict 两类重复输入均写入第二个同 ID occurrence，重复调用 stage projector，
报告 event_count 变为 2；新 ID、schema blocking retry、index-only error 对照均通过。未修改
生产代码，未访问 Provider/网络，Issue 继续保持 `open`。

### 2026-09-21 K351 当前 HEAD EventWriter ingress 联调

- [x] 完整 S2 event-ingress 模块 strict **5 passed、6 xfailed in 1.77s**；`--runxfail`
  为 **5 passed、6 failed in 1.79s**。same-writer/reopen/archive × identical/payload-conflict
  六种重复 occurrence 均被接受并再次驱动 projector；5 个新 ID/schema/index 正向控制通过。
- [x] 无 collection error 或 timeout；使用隔离 state、真实 EventWriter/EventLog/Index/stage
  projector，未访问 Provider/网络，未修改生产代码。
- [x] Issue 继续 `open`，维护者修复后须用同一六参数矩阵验证重复 ID 不再 accepted，同时保持
  新 ID 与未接受输入的正向语义。

## ZF-TEST-038：重复 rejection 在重建后再次消耗业务 rework 预算并提前 capped

> 状态: open（当前工作树 `142a62ac` 已确定性复现；仅记录，不修生产代码）

### 复现入口与判据

tests/conformance/test_zaofu_testkit_s11_rework_model.py::test_rework_failure_redelivery_does_not_spend_budget_twice

```bash
uv run python -m pytest -q --no-cov -o addopts='' --runxfail tests/conformance/test_zaofu_testkit_s11_rework_model.py::test_rework_failure_redelivery_does_not_spend_budget_twice
```

Given：独立临时 project/state、active Session、review Task、dev/review mock roles，dev.max_rework_attempts=1；使用 legacy/shadow 模式，初始 review 为合法夹具前置条件。通过 EventWriter 追加一条 worker `review.rejected`，由真实 `run_once(events=batch, consumed_offset=boundary)` 消费。首次为 retry_count=1、rework/dispatch/transport 各 1 次，无 capped。

When：分别测试两个恢复表面：

- `old_batch`：成功保存 checkpoint 后重建 Orchestrator，先正常消费尾部，再显式重送原已处理事件。
- `checkpoint_interrupted`：仅在本实例 `_persist_offset` 保存前抛测试异常，保留此前已执行的状态/派发；同目录重建后从实际账本读取未确认批次并处理。不是人为复制第二条 rejection，也不是 SIGKILL/机器断电测试。

Then：同一个 failure event 应只计费一次，Task.retry_count 仍为 1，不应达到 cap。实际两个参数均变成 retry_count=2，新增 `task.rework.capped`；该事件自身却记录 failure_count=1、failure_event_ids 只有原 ID。账本仍只有一条 rejection，rework/dispatch/transport 仍各 1 次，Task 为 in_progress。已证明预算漂移和提前移交 Run Manager，未证明重复执行 Provider 或 Task/Run 终态。

正向对照：cap=1/2/3 的多轮 reject→带完整身份及 delta 证据的 build→review、正常 checkpoint 重开、同实例重复消费及 capped 后重复 tick 均通过；不能扩大声称“任何正常重启都会重复计费”。

### 源码位置、边界与证据

- `src/zf/runtime/orchestrator.py::_react_to_events` 的 `_processed_event_ids` 为实例内集合；在整批副作用完成后才保存消费 offset。重建不保留该集合。
- `src/zf/runtime/housekeeping.py::apply_rework_failure_event` 对普通非 fanout failure 更新 Task.retry_count，缺少跨实例 event-id 去重；`orchestrator_dispatch.py::_dispatch_rework` 使用该计数参与 cap 判定。此归因由读源码与两条真实入口轨迹共同支持，不是修复实现建议已获验证。
- 此 Issue 是 Task 业务 retry_count，不是 ZF-TEST-033 的 scheduler Attempt deadletter、ZF-TEST-037 的 reader child run 身份，亦不外推为 Task Pipeline/enforce 模式都存在相同缺陷。
- 原始正式复现 `/tmp/zf-rework-model-reproduction-tp29o97l`：**8 passed、2 failed**，39.64 秒；pytest/外层 exit=1、verdict=fail，session_finished=true，无收集错误/deselected/timeout，failure-bundle 正常生成。
- 两组 first/latest 在临时目录删除前保存原始 Task/Attempt/Session/EventLog/反馈 sidecar、参考状态、动作和源码摘要；两份 latest 已逐文件解码校验大小/SHA-256，均可看到一个 failure ID 对应 retry_count=2 和 premature cap。
- 仅这两个固定探针以 `strict=True, raises=ReworkBudgetReplayed` 保留已知失败；生成模型和正常恢复仍须通过。其他异常不能被吞为本 Issue，修复后 XPASS 必须经维护者核验并移除标记。
- 关联回归：本文件、S0/S1/S3/S10、`tests/test_rework_cap.py`、`tests/test_rework_attempt_ready.py`、`tests/test_rework_owner_authority.py`、`tests/runtime/test_rework_routing.py`。未修改 `src/`、真实运行态或远端引用。
- 最终扩大联调 `/tmp/zf-rework-model-integration-4yb6d3z1`：**430 passed、15 strict xfailed**，本 Issue 命中 2 项；新增模块其余 8 项和 agent-owned verification mock E2E 均通过。外层 verdict=fail，无收集错误/超时/未分类失败；26 个证据文件与 22 份模型快照已独立校验。

### 2026-09-21 当前 HEAD 裸红复核（K290）

- 普通模式：两个参数化 case strict xfailed，8 个同文件用例 deselected；结果 `2 xfailed`
  in 1.77s。
- `--runxfail`：两个 case 均 failed in 1.66s。`old_batch` 与
  `checkpoint_interrupted` 两种真实 redelivery surface 都把同一 rejection 的
  `retry_count` 从 1 增加为 2，触发 `ReworkBudgetReplayed`。
- 无 XPASS、collection error 或 timeout；使用真实 ReworkMachine、TaskStore、EventLog、
  Orchestrator 重开/未确认批次路径，不访问 Provider/网络，未修改 `src/`。既有 `ZF-TEST-038`
  保持 open，修复后需用同一 node 验证无 XPASS。

### 2026-09-21 K318 当前 HEAD 裸红复核

复核命令：

    uv run pytest -q --no-cov -o addopts='' tests/conformance/test_zaofu_testkit_s11_rework_model.py::test_rework_failure_redelivery_does_not_spend_budget_twice
    uv run pytest -q --no-cov -o addopts='' --runxfail tests/conformance/test_zaofu_testkit_s11_rework_model.py::test_rework_failure_redelivery_does_not_spend_budget_twice
    uv run pytest -q --no-cov -o addopts='' tests/conformance/test_zaofu_testkit_s11_rework_model.py

结果：精确节点 strict **2 xfailed in 1.45s**；`--runxfail` **2 failed in 1.41s**；完整
S11 rework 模块 **8 passed、2 xfailed in 41.75s**。`old_batch` 与 `checkpoint_interrupted`
均在同一 failure 重送后把 `retry_count` 从 1 增至 2，`ReworkMachine.consistent()` 抛出
`ReworkBudgetReplayed`；无 XPASS、collection error 或 timeout。该复核继续使用真实
ReworkMachine/TaskStore/EventLog/Orchestrator 重开与未确认批次路径，不访问 Provider/网络，
未修改 `src/`；维护者修复后须用同一入口验证无 XPASS。

## ZF-TEST-037：reader 完成入口接受非当前 child run 并回写旧身份

> 状态: open（当前工作树 `142a62ac` 已确定性复现；仅记录，不修生产代码）

### K2 正式 Research 路由补充复现

新增 `tests/conformance/test_zaofu_testkit_s8_research.py::test_research_rejects_unissued_child_run[fixed/adaptive]`，经真实 `ControlledActionService.execute(research-start)` 生成 generation/config/prompt/run-contract 身份并通过 Kernel admission；只有完成事实的 child run_id 改为从未派发的值，actor 与 origin=worker 保持正确，实际 Provider/网络由拒绝陷阱隔离。

原始报告 `/tmp/zf-research-admission-reproduction-f3l4iu7p`：27 个结果 **24 passed/3 failed**，本 Issue 两个失败。fixed 的四次合法 child 派发后出现一个错误 run 的 canonical completion；adaptive 的单 root 错误 run 不仅被接受，还产生 **aggregate=completed、result.available、task.done.evidence 各一次，Task=done**。这是正式 Research 配置下的新影响证据，不代表全部 blocking profile 或远端交付。

两个 latest 快照分别保留 46/39 个文件，原始内容、大小、SHA-256 与该次测试源摘要已核验。正向当前 run、异常失败、重复、重建对照均通过；最终仅精确 `WrongResearchChildRun` 使用 strict xfail，修复后 XPASS 仍要求更新检测器。重现命令：`uv run pytest -q --no-cov -o addopts='' --runxfail tests/conformance/test_zaofu_testkit_s8_research.py::test_research_rejects_unissued_child_run`。不新增重复 Issue，不修生产代码。

最终联调 `/tmp/zf-research-k2-final-integration-rdcl5jog`：491 passed/4 strict xfailed，本 Issue 两项；adaptive 夹具补齐必填的合成 usage/cost 后，正常 summary 实物/身份对照通过，错误 run 仍导致 Task done。全轮 10 个 bundle 文件及 6 份快照中 276 个嵌入文件已核验，verdict=fail。

### 复现入口与判据

tests/conformance/test_zaofu_testkit_s11_fanout_model.py::test_wrong_child_run_cannot_settle_current_dispatch

```bash
uv run python -m pytest -q --no-cov -o addopts='' --runxfail tests/conformance/test_zaofu_testkit_s11_fanout_model.py::test_wrong_child_run_cannot_settle_current_dispatch
```

三个参数分别是从未派发的 run、真实 timeout retry 前的旧 run、retry 后同目录重开再递送旧 run。均用真实 `EventWriter.append → Orchestrator.run_once` 处理 `origin=worker`、正确 reader actor 的 `workflow.child.completed`；fanout_id/child_id/workflow_run_id 正确，仅 child 的 run_id 不正确。

retry Given 沿用仓库已有方法：timeout=1、max_retries=1，测试显式让 inert mock worker idle，推进 `_now` 后执行真实空 tick。先证明 fanout/child ID 未变、child 各派发两次、run_id 已换代且 retry_of_run_id 正确；这一步是 fixture 控制，不冒充真实 Provider 故障恢复。

预期：非当前 child run 的结果不得产生有效 `fanout.child.completed`，当前 child 仍为 dispatched 且保持新 run_id；当前 retry 的正向结果仍可正常聚合一次。

实际：三个参数均产生了以错误输入为 causation 的 `fanout.child.completed`。投影把 review-a 标为 completed，并将 run_id 改成 `run-never-dispatched` 或 retry 前的旧 run；新服务对象重开也未阻止该行为。独立正向用例 `test_current_retry_results_still_complete_once` 通过，不是简单地让所有结果都失败。

### 源码位置、影响边界与证据

- `src/zf/runtime/orchestrator_fanout.py::_maybe_update_reader_fanout` 使用 payload.run_id，普通 reader 路径未拒绝其与当前 child.run_id 不一致；`src/zf/runtime/fanout.py::FanoutManifestProjector.rebuild` 忠实投影后覆盖 child 的 status/run_id。
- 已证明普通 reader/默认非 blocking call-result 路径的身份准入缺陷；未证明 TaskAttempt、Task Pipeline、plan-synth 或所有 blocking profile 同样失效，也没有证明此次旧 child 单独导致整个 Run 完成。
- 原始联合复现 `/tmp/zf-fanout-model-reproduction-l2fd6cve`：**7 passed、5 failed**，本 Issue 占 3 个，另 2 个属于 ZF-TEST-036；pytest/外层 exit=1，verdict=fail，29.52 秒，无收集错误、超时或 deselection。
- JSON/JUnit、failure-bundle、5 组 first/latest 模型快照保留动作、派发身份、完整账本、manifest 和摘要；本 Issue 的三个 latest 快照均已解码校验字节数及 SHA-256。仅 `raises=WrongChildRun` 的精确异常标为 strict xfail；其他错误不得吞并，修复后 XPASS 会保持门禁失败直到移除标记。
- 关联回归：本文件、`tests/test_fanout_hardening.py`、`tests/test_reader_fanout_runtime.py`、`tests/test_fanout_identity.py`、S0/S3/S10。未修改 `src/` 或远端引用。
- 最终扩大联调 `/tmp/zf-fanout-model-integration-89uc8b70`：617 passed、13 strict xfailed，本 Issue 仍命中 3 项；pytest exit=0，外层 verdict=fail，未分类失败/收集错误/超时均为 0。failure-bundle 的 22 个文件及 18 份模型快照已校验。

### 2026-09-21 当前 HEAD 联合裸红复核（K291）

- 当前 HEAD `142a62ac` 普通模式联合执行 S11 通用 fanout 模型与 S8 正式 Research
  路由的两个固定探针，5 个参数全部 strict xfailed；无普通失败、XPASS、collection error
  或 timeout。
- 同一命令加 `--runxfail` 后 **5 failed in 2.71s**：`unissued`、`previous_retry`、
  `previous_retry_reopen` 都以错误 run 产生 `fanout.child.completed`；fixed/adaptive Research
  也都接受 `never-dispatched-run` 并写入 canonical child completion。
- 五个失败分别精确抛出 `WrongChildRun` 或 `WrongResearchChildRun`，与本 Issue 的既有判据
  一致；测试使用隔离 state、真实 EventWriter/Orchestrator/fanout projector 和正式
  Research controlled admission，不访问 Provider/网络，未修改 `src/`。
- 本复核只证明 child-run 身份准入缺陷仍存在，不外推为所有 blocking profile、TaskAttempt、
  Task Pipeline、Provider 执行或整个 Run 均受同一影响；维护者修复后须用相同联合命令验证
  无 XPASS，并保留 current-run 正向对照。

## ZF-TEST-036：synth 未派发且 reader 未齐时仍可提前完成聚合

> 状态: open（当前工作树 `142a62ac` 已确定性复现；仅记录，不修生产代码）

### K2 正式 Research 路由补充复现

新增 `tests/conformance/test_zaofu_testkit_s8_research.py::test_research_rejects_synth_before_dispatch`。先执行真实受控 research-start 和 generation admission，派发四个 reader；尚无任何 reader 完成或 synth dispatch，追加一个 origin=worker、actor=synthesizer 的提前完成事实。测试没有追加假 dispatch/aggregate/result/done，也没有替换准入或 finalize。

原始报告 `/tmp/zf-research-admission-reproduction-f3l4iu7p`：**24 passed/3 failed**，本 Issue 一个失败。实测 reader completed=0、synth dispatched=0，却有 **completed aggregate/result.available/task.done.evidence 各一次，独立 Research Task=done**。这把下文历史“只证明局部 aggregate”的边界扩大到该正式 Research→Task 交付链；不外推为 run.goal.completed、所有工作流或远端 merge。

latest 快照的 52 个文件已解码并核验大小/SHA-256，保存真实 generation sidecars、派发、完整账本、产物和 Task 状态；正常全 reader→synth→交付的对照通过。仅精确 `PrematureResearchDelivery` 标为 strict xfail。重现命令：`uv run pytest -q --no-cov -o addopts='' --runxfail tests/conformance/test_zaofu_testkit_s8_research.py::test_research_rejects_synth_before_dispatch`。生产实现保持不变。

最终联调 `/tmp/zf-research-k2-final-integration-rdcl5jog`：491 passed/4 strict xfailed，本 Issue 一项，另两项为 037、一项为 006。新的合法报告、失败、缺证据、timeout、重复和重建对照通过；verdict 仍为 fail，不是系统已可信的完成证明。

### 复现入口与判据

tests/conformance/test_zaofu_testkit_s11_fanout_model.py::test_synth_cannot_complete_before_reader_barrier

```bash
uv run python -m pytest -q --no-cov -o addopts='' --runxfail tests/conformance/test_zaofu_testkit_s11_fanout_model.py::test_synth_cannot_complete_before_reader_barrier
```

Given：隔离 state_dir、已运行 run 的合法初始事件、两名 mock reader、一名声明发布权限的 mock synth，wait_for_all 聚合。`candidate.ready` 经真实 run_once 产生两次 child dispatch；参数分别让 0 或 1 个 child 完成，尚未出现任何 `fanout.synth.dispatched`。

When：通过 EventWriter 追加 actor=review-synth、origin=worker、正确 fanout/run 关联的 `fanout.synth.completed(recommendation=approve)`，再真实 run_once 处理。没有直接调用 finalize，也没有伪造 kernel 的 aggregate 事件。

Then：应拒绝提前完成；缺失 child 仍 dispatched、aggregate 不得 terminal、review.approved 为 0 次。实际两个参数均出现 **1 个 completed aggregate + 1 个 review.approved**，synth dispatch 仍为 **0**；因此正常 all-terminal 后才派发 synth 的正向 barrier 测试不足以保护反向输入入口。

### 源码位置、影响边界与证据

- `src/zf/runtime/plan_synth_runtime.py::_handle_fanout_synth_completed` 的普通非 plan-synth 路径进入 `src/zf/runtime/orchestrator_fanout.py::_finalize_fanout_synth`；后者检查 currentness/已有终态，但未要求所有 child 已终态且 synth 曾合法派发。
- 已证明普通 reader fanout 的局部假完成，不等于已证明 `run.goal.completed`、TaskStore done、远端 merge 或 blocking Product Flow 全链路也被绕过。初始 run.started 是 Given，不作为 workflow admission 测试结果。
- 原始联合复现 `/tmp/zf-fanout-model-reproduction-l2fd6cve` 为 **7 passed、5 failed**，本 Issue 占 2 个；JSON/JUnit、日志、failure-bundle 与模型快照保存了 child 未齐、synth dispatch=0 和 aggregate/review.approved=1 的同轮证据。
- 仅 `raises=PrematureAggregate` 标为 strict xfail；生成模型的合法 synth 前置条件仍保留，非法前置条件由上述独立固定探针覆盖，不把已知缺陷混入绿色统计。
- 关联回归：本文件、`tests/conformance/test_zaofu_testkit_s3_fanout.py`、`tests/test_reader_fanout_runtime.py`、`tests/test_run_admission.py` 与 S0/S3/S10。未修改生产实现。
- 最终扩大联调 `/tmp/zf-fanout-model-integration-89uc8b70`：617 passed、13 strict xfailed，本 Issue 仍命中 2 项；正常 child barrier、取消及当前 retry 的正向控制通过。外层 verdict=fail，不是整体通过证明。

### 2026-09-21 当前 HEAD 联合裸红复核（K292）

- 当前 HEAD `142a62ac` 普通模式联合执行 S11 fanout barrier 与 S8 正式 Research 提前
  synth 两个入口，3 个参数全部 strict xfailed；无普通失败、XPASS、collection error 或
  timeout。
- 同一命令加 `--runxfail` 后 **3 failed in 5.64s**：S11 的 `completed_children=0` 和
  `completed_children=1` 均在 synth 未合法派发、reader 未齐时生成一个
  `fanout.aggregate.completed(status=completed)` 与 `review.approved`；S8 的正式 Research
  在 reader completed=0、synth dispatched=0 时仍生成 completed aggregate、
  `workflow.result.available`，并把 `TASK-RESEARCH` 置为 `done`。
- 三个失败分别精确抛出 `PrematureAggregate` 或 `PrematureResearchDelivery`，与既有
  `ZF-TEST-036` 判据一致；使用隔离 state、真实 EventWriter/Orchestrator/fanout projector
  及 controlled Research admission，不访问 Provider/网络，未修改 `src/`。
- 本复核只证明 synth barrier/合法派发前的提前聚合缺陷仍存在，不外推为所有 blocking
  profile、TaskAttempt、Task Pipeline、Run Goal 或远端交付均受同一影响；维护者修复后须用
  相同联合命令验证无 XPASS，并保留 all-reader/current-synth 正向对照。

## ZF-TEST-035：EventIndex 刷新使任务查询与账本及冷重放顺序不一致

> 状态: open（当前工作树 `142a62a` 已确定性复现；仅记录，不修生产代码）

### 2026-09-21 K320 当前 HEAD 联合裸红复核

复核命令：

    uv run pytest -q --no-cov -o addopts='' tests/conformance/test_zaofu_testkit_s11_eventlog_model.py::test_task_index_preserves_complete_occurrence_order
    uv run pytest -q --no-cov -o addopts='' --runxfail tests/conformance/test_zaofu_testkit_s11_eventlog_model.py::test_task_index_preserves_complete_occurrence_order
    uv run pytest -q --no-cov -o addopts='' tests/conformance/test_zaofu_testkit_s11_eventlog_model.py

结果：精确节点 strict **2 xfailed in 0.50s**；`--runxfail` **2 failed in 0.39s**；完整
EventLog 模块 **5 passed、2 xfailed in 6.06s**。`timestamp_order` 在 `EventLog.close()`
后使 warm query 返回 `[index-b,index-a]`，而 canonical ledger 与 cold query 为
`[index-a,index-b]`；同一联合运行中的 `external_append` 另命中 ZF-TEST-034。无 XPASS、
collection error 或 timeout；未修改 `src/`、未访问 Provider/网络，维护者修复后须用同一入口
验证无 XPASS。

### 复现入口与判据

tests/conformance/test_zaofu_testkit_s11_eventlog_model.py::test_task_index_preserves_complete_occurrence_order

```bash
uv run python -m pytest -q --no-cov -o addopts='' --runxfail 'tests/conformance/test_zaofu_testkit_s11_eventlog_model.py::test_task_index_preserves_complete_occurrence_order[timestamp_order]'
```

1. 隔离目录中依次追加新 ID `index-a`（12:00）、`index-b`（11:00），同属 `TASK-INDEX`；时间戳乱序但追加顺序明确，未回拨文件 mtime、未重复 ID。
2. 调用原实例 `EventLog.close()` 刷新索引，再查询 `query(task_id=...)`。
3. canonical `read_all()` 与新建冷实例任务查询均为 `[index-a, index-b]`，原实例却返回 `[index-b, index-a]`。

预期：同一账本、同一查询没有选择另一种排序语义时，warm/cold 结果顺序应一致；重建或刷新可丢弃的索引不应改变 occurrence 顺序。这里不主张所有 API 都必须按追加顺序展示，缺陷是同一 API 因缓存状态而变化。

### 源码位置与影响边界

`src/zf/core/events/index.py::EventIndex.flush` 中 `_merge_task_events` 对 ID 按 event.ts 排序，并把结果替换为当前内存映射；`EventLog.events_for_task`（log.py:410）直接返回缓存。冷实例不加载持久化 payload，因此退回按文件追加顺序读取，产生分歧。

已证明查询投影不一致，没有证明原始 EventLog 被重写、任务状态错误或远端副作用。禁止用对 expected 再按 ts 排序的方法让探针变绿；维护者需明确统一的查询语义并修复其一致性。

### 证据与处理

- 未加 xfail 的联合原始复现 **5 passed、2 failed**，5.27 秒，pytest exit=1、verdict=fail，目录 `/tmp/zf-event-index-reproduction-w2ib7V`；另一个失败为 ZF-TEST-034，不重复归因。
- `testkit-run.json`、JSON/JUnit、failure-bundle 及 `model-evidence/*/{first,latest}.json` 保留 query/ledger 差异、原始账本和索引字节、独立参考及源码摘要。
- 仅此参数标记 `strict=True, raises=EventIndexMismatch`；其他失败不能被吞为已知问题，修复后的 XPASS 仍要求维护者核验并移除标记。
- 关联验证：上述探针、`tests/test_events_log.py`、`tests/test_event_writer.py`、S10 EventLedger 模型与 S0 注册审计。未修改 `src/`。
- 最终联合报告 `/tmp/zf-event-model-integration-qjYzDt` 为 **353 passed、6 strict xfailed**，本 Issue 命中一个；另有 ZF-TEST-011/013/033/034，结果不归为一个缺陷。无 timeout/collection error/deselected，外层 verdict=fail；两个新索引检测器及其快照均接入原 failure-bundle。

## ZF-TEST-034：旧 EventLog 实例的任务索引漏读另一合法实例追加的事件

> 状态: open（当前工作树 `142a62a` 已确定性复现；仅记录，不修生产代码）

### 2026-09-21 K320 当前 HEAD 联合裸红复核

在与 ZF-TEST-035 相同的当前 HEAD 联合运行中，`external_append` strict **xfailed**，
`--runxfail` **failed**：实例 A 的 warm task query 只返回 `[index-a]`，而 `read_all()` 与冷
实例查询均为 `[index-a,index-b]`。完整 EventLog 模块仍为 **5 passed、2 xfailed**，无 XPASS、
collection error 或 timeout；保持 Issue open，不修改生产代码，修复后须以相同参数验证无 XPASS。

### 复现入口与判据

tests/conformance/test_zaofu_testkit_s11_eventlog_model.py::test_task_index_preserves_complete_occurrence_order

```bash
uv run python -m pytest -q --no-cov -o addopts='' --runxfail 'tests/conformance/test_zaofu_testkit_s11_eventlog_model.py::test_task_index_preserves_complete_occurrence_order[external_append]'
```

1. 实例 A 追加新 ID `index-a`，查询 `TASK-INDEX` 得到 `[index-a]`。
2. 同路径新建实例 B，追加新 ID `index-b`（时间递增），不用 raw 注入、不篡改索引、不重复 ID。
3. A 的 `read_all()` 与新建冷实例任务查询均得到 `[index-a, index-b]`，A 的 `query(task_id=...)` 仅返回 `[index-a]`。

预期：索引是可重建投影，不能遮蔽已经追加到账本的合法事件；不完整缓存应回退扫描或刷新。

### 源码位置与影响边界

`src/zf/core/events/index.py::EventIndex.events_for_task`（106–123）只验证自身已有 ID 是否有内存 payload，不检查其它 writer 是否使账本增长；`src/zf/core/events/log.py::EventLog.events_for_task`（410–425）将非 None 缓存直接视为完整。新建实例缺 payload 会 fallback，而已读过的实例持续使用旧集合。

实际复现是同进程两个独立 EventLog 对象，不声称已做多进程竞争、断电或真实运行工作流。已确认任务查询缺事实，但未证明它已经触发了重复派发或任务误完成。

### 证据与处理

- 原始复现与 ZF-TEST-035 共用 `/tmp/zf-event-index-reproduction-w2ib7V`：**5 passed、2 failed**，无 collection error/timeout，session_finished=true；不能把两个缺陷分别统计成两次全套运行。
- 对应 first/latest 快照保留 `state/events.jsonl` 和 `state/event_index.json`，expected 为两个新 ID；longrepr 明确记录实际仅一个 ID。
- 仅此参数使用 `strict=True, raises=EventIndexMismatch`，已注册 S10 的已知问题；未知断言或环境错误继续为普通失败。
- 维护者修复后执行本探针、EventLog/Writer/归档契约和 S10/S0 联调。本轮不修生产代码、不创建远端 Issue、不推送。
- 已完成的联调与上项共用 `/tmp/zf-event-model-integration-qjYzDt`：353 passed、6 strict xfailed，本 Issue 一个；pytest exit=0、外层 exit=1/verdict=fail，session_finished=true，12 个 evidence 文件摘要一致。没有用冷重开绕过 warm-instance 检测器。

### 2026-09-21 当前 HEAD 联合裸红复核（K289）

- 普通模式：`external_append` 与 `timestamp_order` 两个参数均 strict xfailed，5 个相关
  case deselected；结果 `2 xfailed in 0.55s`。
- `--runxfail`：两个参数均 failed in 0.61s。warm index 在 external append 后仍只返回
  `[index-a]`；flush 后 timestamp-order 查询返回 `[index-b, index-a]`，均与 canonical
  ledger/cold query 的 `[index-a, index-b]` 不一致。
- 无 XPASS、collection error 或 timeout；使用真实 EventLog/EventIndex 和临时 state，未访问
  Provider/网络，未修改 `src/`。既有 `ZF-TEST-034/035` 保持 open，修复后需用相同两个
  参数验证无 XPASS。

## ZF-TEST-033：TaskAttempt 预算耗尽后重放相同派发会重复写 deadletter 事件

> 状态: open（当前工作树 `142a62a` 已确定性复现；仅记录，不修生产代码）

### 2026-09-21 K321 当前 HEAD 裸红复核

复核命令：

    uv run pytest -q --no-cov -o addopts='' tests/conformance/test_zaofu_testkit_s11_attempt_model.py::test_exhausted_dispatch_replay_emits_one_budget_event
    uv run pytest -q --no-cov -o addopts='' --runxfail tests/conformance/test_zaofu_testkit_s11_attempt_model.py::test_exhausted_dispatch_replay_emits_one_budget_event
    uv run pytest -q --no-cov -o addopts='' tests/conformance/test_zaofu_testkit_s11_attempt_model.py

结果：精确节点 strict **2 xfailed in 1.02s**；`--runxfail` **2 failed in 1.03s**；完整
Attempt 模块 **3 passed、2 xfailed in 5.43s**。same-runtime 与 `reopen=True` 均对同一
`(RUN-1,TASK-1,model-dispatch-3)` 再写一条 budget-exhausted `task.attempt.deadlettered`，
计数从 1 变为 2 并抛出 `DuplicateBudgetEvent`；无 XPASS、collection error 或 timeout。未
修改生产代码、未访问 Provider/网络，维护者修复后须用同一入口验证无 XPASS。

### 发现阶段与复现命令

S10/§13 TaskAttempt 生命周期生成式测试首先发现五次 `dispatch()` 的失败序列，随后拆出固定同实例/重开实例探针：

tests/conformance/test_zaofu_testkit_s11_attempt_model.py::test_exhausted_dispatch_replay_emits_one_budget_event

```bash
uv run python -m pytest -q --no-cov -o addopts='' --runxfail tests/conformance/test_zaofu_testkit_s11_attempt_model.py::test_exhausted_dispatch_replay_emits_one_budget_event
```

### 初态、实际结果与预期

- 隔离 `state` 目录，真实 Orchestrator、TaskStore、TaskAttemptStore、EventWriter，mock recording transport；`mode=enforce`、`max_attempts=3`，固定测试时钟。
- 对同一 `RUN-1/TASK-1` 依次提交 dispatch 0、1、2：实际送达 3 次；第 4 次提交 dispatch 3 时预算耗尽，抛 `TaskAttemptLimitError`，Task 为 blocked，产生 1 个 `task.attempt.deadlettered`。
- 再次提交**同一个** dispatch 3：仍被拒绝、实际送达数仍为 3、Task 仍为 blocked，但 deadletter 增至 **2 个**，event ID 不同，run/task/dispatch/reason 相同。
- 同一实例重放和同目录重新构造 Orchestrator 后重放均复现。预期是拒绝保持幂等，预算终态事件只出现一次；不能仅凭没有第 4 次 transport 调用而判断通过。

### 根因与边界

`src/zf/runtime/task_attempt_runtime.py::_emit_attempt_exhausted` 调用 `_emit_once` 时传入合成去重键 `budget:<run_id>:<task_id>`，但写入事件的 payload 没有 `attempt_id`。`_has_attempt_event` 按已存事件的 `payload.attempt_id` 查找，因而无法命中先前的预算事件。

复现使用真实 `_send_transport_task` 边界；测试夹具会像派发入口一样设置当前 dispatch 身份。**不声称**完整 `run_once` 调度必然在 blocked 状态继续派发，也不声称已经重复调用 Provider、远端副作用或发生数据丢失。问题是该边界重复受理耗尽请求时的事件幂等性。

### 证据与处理

- 未加 xfail 的正式复现：**3 passed、2 failed**，5.25 秒；pytest/外层 exit=1，verdict=fail。证据 `/tmp/zf-attempt-budget-repro-eaQHso/testkit-run.json`、`pytest-testkit.json`、`pytest-junit.xml` 和 failure-bundle。
- 同目录 `model-evidence/*/{first,latest}.json` 保存原始 state 文件字节（base64）、EventLog、TaskAttemptStore、TaskStore、操作序列、参考状态、版本与 SHA-256；原 fixture 已清理。每份快照的 trace digest 可与 longrepr 对照，不把 latest 称为最小反例。
- 两个固定探针使用 `strict=True, raises=DuplicateBudgetEvent`，只允许这个已核实的异常作为已知失败；其他断言、环境和收集错误继续直接失败。维护者修复后 XPASS 保持红门禁，须审查后去掉标记。
- 最终接入 S10 后与 S0/S1 联调为 **199 passed、3 strict xfailed**，其中本 Issue 两个、ZF-TEST-011 一个；无普通失败、超时或收集错误，外层 verdict=fail。报告 `/tmp/zf-model-capture-final-4VujyM/testkit-run.json`；更广的 S0/S1/S4/S10/调用者闭包为 **371 passed、16 strict xfailed**，报告 `/tmp/zf-model-capture-integration-3qtaBR/testkit-run.json`。两次覆盖重叠，不相加作为测试数。
- 生成式生命周期模型每个 series 只生成一次预算耗尽通知，把重复耗尽交给上述独立探针，避免已知缺陷使所有探索提前结束；这一动作空间限制显式保留，不冒充无约束探索。
- 本次还纠正了新测试的 worker 事件来源：必须显式 `origin="worker"`；默认 kernel 来源绕过校验属于夹具错误，**不作为生产 bug 登记**。
- 未改 `src/`，未启动真实 Provider、tmux、Web、扫描器或远端写入。维护者修复后应重跑上述探针、`tests/test_scheduler_task_attempt.py`、TaskAttempt recovery 与 S10/模型证据闭包。

### 2026-09-21 当前 HEAD 裸红复核（K287）

- 普通模式：两个参数化 case strict xfailed，3 个相关 case deselected；结果 `2 xfailed`
  in 1.73s。
- `--runxfail`：两个 case 均 failed in 1.84s。相同 exhausted dispatch
  `model-dispatch-3` 在重复派发后产生第二个 `task.attempt.deadlettered`，计数为 2；
  `reopen=False/True` 均复现。
- 使用真实 TaskAttempt lifecycle、EventLog/TaskStore 与同目录重开路径，不访问
  Provider/网络，未修改 `src/`。既有 `ZF-TEST-033` 保持 open，修复后需用相同 node
  验证无 XPASS。

### 2026-09-21 当前 HEAD 裸红复核（K288）

- 普通模式：4 个故障点中 2 个通过、2 个 strict xfailed，1 个相关节点 deselected；
  结果 `2 passed, 1 deselected, 2 xfailed in 14.89s`。
- `--runxfail`：两个窗口均 failed，整体 `2 failed, 2 passed, 1 deselected in 20.30s`。
  `after_store_update_before_task_updated` 与 `after_action_result_before_resolution` 在
  新进程 recovery 后各产生第二个真实 action effect；后者同时重复 `task.updated` 和
  action completion 事件。
- 无 collection error 或 timeout；测试使用真实子进程、Store/EventLog/ControlledAction
  入口和固定 crash points，不访问 Provider/远端服务，未修改 `src/`。既有 `ZF-TEST-032`
  保持 open，修复后需用同一命令验证无 XPASS。

## ZF-TEST-032：ControlledAction 提交后、proposal resolution 写入前崩溃会重复更新

> 状态: open（当前工作树 `142a62a` 已实测；仅记录，不修生产代码）

### 2026-09-21 K322 当前 HEAD 裸红复核

复核命令：

    uv run pytest -q --no-cov -o addopts='' tests/conformance/test_zaofu_testkit_s4_action_crash.py::test_controlled_action_commit_window_recovers_without_duplicate_mutation
    uv run pytest -q --no-cov -o addopts='' --runxfail tests/conformance/test_zaofu_testkit_s4_action_crash.py::test_controlled_action_commit_window_recovers_without_duplicate_mutation
    uv run pytest -q --no-cov -o addopts='' tests/conformance/test_zaofu_testkit_s4_action_crash.py

结果：精确矩阵 strict **2 passed、2 xfailed in 13.82s**；`--runxfail` **2 failed、2 passed
in 14.37s**；完整 S4 模块 **3 passed、2 xfailed in 15.83s**。`after_store_update_before_task_updated`
恢复后出现第二次真实 update effect；`after_action_result_before_resolution` 同时重复
update、`task.updated` 与 completion 事件。无 XPASS、collection error 或 timeout；未修改
生产代码、未访问 Provider/网络，维护者修复后须用同一入口验证无 XPASS。

### 发现阶段与复现命令

S4 真实 ControlledAction 子进程提交窗口补测；方法见 [action_commit_window_research.md](../02_research_reports/action_commit_window_research.md)。

tests/conformance/test_zaofu_testkit_s4_action_crash.py::test_controlled_action_commit_window_recovers_without_duplicate_mutation

```bash
uv run python -m pytest -q --no-cov -o addopts='' --runxfail tests/conformance/test_zaofu_testkit_s4_action_crash.py
```

### 实际结果与正确预期

初态为非终态 Task、priority=3、有效且未解决的 `update-task` 提案，目标 priority=1。baseline、崩溃、恢复和再次 replay 均运行在新进程，恢复不重置 state dir 或提案身份。

| 故障点 | 故障时成功 Store 更新数 / task.updated 数 | 恢复后成功 Store 更新数 / task.updated 数 | 预期 |
|---|---|---|---|
| Store 更新前 | 0 / 0 | 1 / 1 | 通过：仅执行一次 |
| Store 更新后、task.updated 前 | 1 / 0 | **2 / 1** | 应与 baseline 一样累计 1 次更新 |
| action result 后、proposal resolution 前 | 1 / 1 | **2 / 2** | 应累计 1 次更新和 1 个业务更新事件 |
| proposal resolution 后 | 1 / 1 | 1 / 1 | 通过：新进程重放不再执行 |

两条失败路径最终 priority 都正确且只有一个 resolution；仅检查最终状态或 resolved 数量会漏报。独立 observer 在真实 `TaskStore.update` 成功返回后追加回执，**永不去重、生产代码不读取它**。第二次恢复后再 replay 不增长，问题限定于 resolution 尚未提交的窗口。

判据采用 proposal 包装的一次性执行约束，强于“同值赋值后的最终状态相同”；单独重复赋值不等于已经造成数据损坏。第二个失败窗口还实测产生了两个 `task.updated` 与两组 action completion，是独立于 observer 计数的重复业务事件证据。

### 根因证据与影响边界

- `src/zf/runtime/control_actions_product.py::_update_task` 先更新 Store，再写 `task.updated` 与动作结果；`control_actions_proposals.py::_run_controlled_action` 最后单独写 resolution。
- `kanban_proposals.py::proposal_execution_gate` 用 resolution（及 create-task 特例）去重，不把现存 `task.updated` / `runtime.action.attempt.completed` 当作该提案已执行的证据。
- 这与 ZF-TEST-002/007 的 dispatch 窗口、ZF-TEST-013 的归档窗口是同类但不同真实入口，不复用其失败结论。
- 已证明的是本地 Task mutation 的重复调用及重复业务事件；没有测试或声称 GitHub/飞书远端副作用重复，也不代表断电持久性结论。

### 证据与当前处理

- 首次未加 xfail 的运行：**3 passed、2 failed**，14.40 秒；退出码 1。证据目录 `/tmp/zf-action-crash-reproduction-xDq9BI`，包含 `pytest-testkit.json`、`junit.xml`。
- 接入后 S0+S4+S10/直接调用者联调为 **237 passed、14 strict xfailed**，其中本文件 3 passed、2 个 ZF-TEST-032 xfailed；无 timeout/collection error/deselected，外层 verdict=fail。报告 `/tmp/zf-action-model-integration-66S08W/testkit-run.json` 与 failure-bundle 保留缺陷失败信息，未把 xfail 算作产品通过。
- 两条失败分别位于 `cases/test_controlled_action_commit_1/fault/` 与 `cases/test_controlled_action_commit_2/fault/`；保留原始 `state/events.jsonl`、Store、`evidence/observed-updates.jsonl`、`fault-witness.json`、`recovery-comparison.json` 以及每个 child 的命令回执/stdout/stderr。
- 各故障进程都命中精确 witness 并以 86 退出；各次恢复有 30 秒独立超时，不删除锁文件来协助恢复。
- 已加入 S4 registry；仅两条已确认重复异常使用 `strict=True, raises=DuplicateActionEffect`。环境失败、超时、中间状态不匹配及其他断言不能被 xfail 吞掉。修复后出现 XPASS 会保持门禁为红，须核验后更新此 Issue 和探针。
- 未改 `src/`，未执行 push、merge 或远端写入；由维护者修复后重跑本探针与相关 proposal/ControlledAction/S4 影响闭包。

### 新旧 nested proposal 的结果丢失组合补证（2026-09-14）

tests/conformance/test_zaofu_testkit_s11_action_loss_model.py::test_missing_resolution_replay_never_recommits

```bash
uv run python -m pytest -q --no-cov -o addopts='' --runxfail tests/conformance/test_zaofu_testkit_s11_action_loss_model.py::test_missing_resolution_replay_never_recommits
```

新增参数覆盖 legacy `kanban.agent.action.proposed` 和 nested `operator.action.proposed`，在 `after_store`、`before_resolution` 两点注入专用 BaseException，再构造新 Service 从同目录重放。Hypothesis 为每条轨迹生成最多 6 步 reopen/tamper/before-store 中断前缀；副作用许可不由 service 返回值或 observer 推导，也不清空账本恢复。不是 operator-intent v0 执行链，也不是新的硬进程崩溃实验。

未加 xfail 的正式运行 `/tmp/zf-action-loss-model-reproduction-5tprmzn9`：**22 passed、4 failed**，54.52 秒；pytest/外层 exit=1、verdict=fail，session_finished=true，无收集错误/deselection/timeout。四份 latest 原始快照均显示：同一 proposal version 预期更新 1 次，真实 observer 记录 **2 次**，最终 priority=1、resolution=1；after_store 的 task.updated=1，before_resolution 的 task.updated=2。与本 Issue 已核实的 legacy 根因一致，因此扩展 032，不新增重复 Issue。

12 个 failure-bundle 文件及 4 份 latest 的嵌入文件字节数/SHA-256 已核验；first/latest 不保证是最终最小反例。只有这四个精确故障检测器使用 `strict=True, raises=DuplicateActionEffect`，正常生成模型、固定完整轨迹、16 个部分提交后 expiry/dismiss/revision/supersedes 交错及 2 个 oracle canary 不豁免。

正向结论：客户端丢回复和 resolution 后中断不会重新提交；过期/撤销/换代后的旧请求无新增作用；已部分提交而未 resolution 时，新获批 revision 可以产生自己的一次作用，不能把两个不同获批版本误算成同一操作重复。撤销不会回滚历史更新，过期后 pending 消失也不代表缺失账本已自动修复。未修改生产代码。

最终 S0/S1/S4/S10 与 Web/Feishu mock 审批调用者联调 `/tmp/zf-action-loss-model-integration-p70n74md`：**492 passed、29 strict xfailed**（本 Issue 六个：原硬崩溃两个 + 新组合四个），520.04 秒；新增模块 22 passed/4 strict xfailed，pytest exit=0 而外层 exit=1/verdict=fail。无收集错误、deselection、timeout，34 个失败包文件及 30 份模型快照的全部 530 个嵌入文件校验通过。故障仍开放，门禁未转绿。

## ZF-TEST-031：依赖屏障满足后未派发下游 synthesizer

> 状态: open（当前 v7 未同步；origin/dev `603ad9b` 已验证修复，待回合并后回归）

### 2026-09-21 K323 当前 checkout 裸红复核

复核命令：

    uv run pytest -q --no-cov -o addopts='' tests/conformance/test_zaofu_testkit_s3_regressions.py::test_dependency_barrier_dispatches_downstream_role
    uv run pytest -q --no-cov -o addopts='' --runxfail tests/conformance/test_zaofu_testkit_s3_regressions.py::test_dependency_barrier_dispatches_downstream_role
    uv run pytest -q --no-cov -o addopts='' tests/conformance/test_zaofu_testkit_s3_regressions.py

结果：精确节点 strict **1 xfailed in 0.81s**；`--runxfail` **1 failed in 0.88s**；完整 S3
regression **3 xfailed in 0.90s**。`dependency.barrier.satisfied` 已写入且 source IDs 正确，
但 recording transport 的 synthesizer dispatch 数为 **0** 而期望 1；无 XPASS、collection
error 或 timeout。`origin/dev` 的历史 1 passed 不能替代当前 checkout 回归，Issue 继续 open；
未修改生产代码、未访问 Provider/网络，维护者同步修复后须用同一入口验证无 XPASS。

### 发现阶段

dev-verify broad deterministic 影响闭包。

### 复现命令

    tests/test_workflow_dependency_barrier.py::test_orchestrator_run_once_reconciles_and_dispatches_downstream

    uv run pytest -q --no-cov tests/test_workflow_dependency_barrier.py::test_orchestrator_run_once_reconciles_and_dispatches_downstream

### 现象与期望

同一 run 的 `collect-a.completed` 和 `collect-b.completed` 已满足 dependency barrier，
且 `dependency.barrier.satisfied` 只产生一次；随后 `Orchestrator.run_once()` 应向
`synthesizer` 派发一次。当前 barrier 事件虽然写入，但 recording transport 未收到
任何 synthesizer dispatch。

### 当前处理

- [x] 保留原始失败测试和复现命令；
- [x] 使用临时 state dir 与 mock transport，未访问 Provider、网络或远端；
- [x] 未修改生产代码；
- [ ] 尚未进入当前 v7 的 Issue→Fix Run→回归→merge 流程；
- [x] 在隔离 worktree `/tmp/zf-remote-dev-test-20260914-080000` 对 `origin/dev` 的 `603ad9b` 单独执行同一探针，结果 `1 passed`，日志为 `/tmp/zf-remote-dev-zf-test-031-20260914-081500.log`；该证据只说明远端候选已修复，不改变当前 v7 状态。

### 2026-09-22 K390 当前 HEAD 裸红复核

- [x] 精确节点 strict **1 xfailed in 0.89s**；`--runxfail` **1 failed in 0.84s**，
  `dependency.barrier.satisfied` 仍只写入一次且 source IDs 正确，但 synthesizer dispatch count 为 0。
- [x] dependency-barrier/dispatch-fence/S3/generic-workflow caller 联调：**51 passed、1 failed in 9.98s**；
  唯一普通失败就是同一 downstream dispatch 断言，其余对照通过。
- [x] 证据日志：`/tmp/zf-k390-zf031-strict.log`、`/tmp/zf-k390-zf031-runxfail.log`、
  `/tmp/zf-k390-zf031-callers.log`；未修改生产代码、未访问 Provider/网络或远端写入。

## ZF-TEST-030：housekeeping 测试夹具未初始化 project_root（已解决）

> 状态: verified-resolved（测试夹具误报；非生产运行时缺陷）

### 发现阶段

dev-verify broad deterministic 影响闭包。

### 复现命令

    tests/test_auto_ship_on_judge.py::test_judge_passed_ships_through_apply_housekeeping

    uv run pytest -q --no-cov tests/test_auto_ship_on_judge.py::test_judge_passed_ships_through_apply_housekeeping

### 现象与期望

原测试通过 `Orchestrator.__new__` 构造对象，绕过生产构造器对 `project_root` 的初始化，
导致 `_apply_housekeeping()` 抛出 `AttributeError`。生产 `Orchestrator.__init__` 会正确设置
该属性；问题在测试夹具而非生产运行时。

### 当前处理

- [x] 在测试夹具中补齐 `project_root = state_dir.parent`；
- [x] focused 回归通过，未执行真实 merge/push；
- [x] 未修改生产代码；
- [x] 不再作为生产缺陷或 strict xfail 追踪。

## ZF-TEST-029：run_once housekeeping 机制数超过冻结预算

> 状态: open（当前 v7 未同步；origin/dev `603ad9b` 已验证修复，待回合并后回归）

### 2026-09-21 K324 当前 checkout 裸红复核

复核命令：

    uv run pytest -q --no-cov -o addopts='' tests/conformance/test_zaofu_testkit_s3_regressions.py::test_run_once_housekeeping_count_stays_within_frozen_budget
    uv run pytest -q --no-cov -o addopts='' --runxfail tests/conformance/test_zaofu_testkit_s3_regressions.py::test_run_once_housekeeping_count_stays_within_frozen_budget
    uv run pytest -q --no-cov -o addopts='' tests/conformance/test_zaofu_testkit_s3_regressions.py

结果：精确节点 strict **1 xfailed in 0.24s**；`--runxfail` **1 failed in 0.24s**；完整 S3
regression **3 xfailed in 0.94s**。真实 `_run_once_calls()` 返回 housekeeping 数 **19**，
冻结预算为 **18**；无 XPASS、collection error 或 timeout。当前 checkout Issue 继续 open，
未调整预算、未修改生产代码、未访问 Provider/网络；维护者同步修复或审议预算后须用同一入口
验证无 XPASS。

### 发现阶段

dev-verify broad deterministic 影响闭包。

### 复现命令

    tests/test_tick_mechanism_budget.py::test_run_once_housekeeping_budget
    tests/test_tick_mechanism_budget.py::test_budgets_match_reality_exactly

    uv run pytest -q --no-cov tests/test_tick_mechanism_budget.py::test_run_once_housekeeping_budget tests/test_tick_mechanism_budget.py::test_budgets_match_reality_exactly

### 现象与期望

`tests/test_tick_mechanism_budget.py` 冻结 `RUN_ONCE_HOUSEKEEPING_BUDGET=18`，用于防止
恢复机制无界增长。当前 AST 实测 `_safe_housekeeping` 调用为 19 个，预算检查和精确匹配
检查均失败。应由维护者完成 one-in/one-out 审计并同步预算或移除机制，而不能让预算
静默失效。

### 当前处理

- [x] 保留两个确定性失败测试和机制清单；
- [x] 未修改生产代码或测试预算；
- [x] 在隔离远端 worktree 对 `origin/dev` 执行原始预算探针并通过，结果 `1 passed`，日志为 `/tmp/zf-remote-dev-zf-test-029-20260914-084500.log`；
- [ ] 当前 v7 尚未同步该修复，尚未进入 Issue→Fix Run→回归→merge 流程。

### 2026-09-22 K388 当前 HEAD 裸红复核

- [x] 精确预算节点 strict **1 failed in 0.27s**；`--runxfail` **1 failed in 0.25s**，
  `_run_once_calls()` 实测 housekeeping 19 项，冻结预算 18；完整机制名称序列已保留。
- [x] S3 regression caller **3 xfailed in 0.91s**；`ZF-TEST-028` wake-pattern 与
  `ZF-TEST-031` dependency-barrier 保持独立 xfail，没有混入预算归因。
- [x] 证据日志：`/tmp/zf-k388-zf029-strict.log`、`/tmp/zf-k388-zf029-runxfail.log`、
  `/tmp/zf-k388-zf029-callers.log`；未修改生产代码/预算，未访问 Provider/网络。

## ZF-TEST-028：外部 housekeeping 事件未注册 wake pattern

> 状态: open（确定性复现；未修改生产代码）

### 2026-09-21 K325 当前 checkout 裸红复核

复核命令：

    uv run pytest -q --no-cov -o addopts='' tests/conformance/test_zaofu_testkit_s3_regressions.py::test_external_housekeeping_events_have_wake_patterns
    uv run pytest -q --no-cov -o addopts='' --runxfail tests/conformance/test_zaofu_testkit_s3_regressions.py::test_external_housekeeping_events_have_wake_patterns
    uv run pytest -q --no-cov -o addopts='' tests/conformance/test_zaofu_testkit_s3_regressions.py

结果：精确节点 strict **1 xfailed in 0.22s**；`--runxfail` **1 failed in 0.21s**；完整 S3
regression **3 xfailed in 0.91s**。当前 `wake_patterns.py` 未包含
`artifact.delivery.verified`（首个缺失；`run.resumed` 与
`self_issue.assessment.requested` 同属待核对集合）；无 XPASS、collection error 或 timeout。
Issue 继续 open，未修改生产代码/阈值、未访问 Provider/网络，维护者修复后须用同一入口验证。

### 发现阶段

dev-verify broad deterministic 影响闭包。

### 复现命令

    tests/test_wake_pattern_coverage.py::test_housekeeping_branch_has_wake_pattern

    uv run pytest -q --no-cov tests/test_wake_pattern_coverage.py::test_housekeeping_branch_has_wake_pattern

### 现象与期望

`orchestrator.py` 的 housekeeping 分支处理 `artifact.delivery.verified`、`run.resumed` 和
`self_issue.assessment.requested`，但这些事件既不在 `WAKE_PATTERNS`，也不在明确的内部
事件白名单中。外部事件进入 EventWatcher 后可能被静默丢弃，分支只能依赖 tick fallback。
期望每个外部事件均注册 wake pattern，或有带 emit 位置引用的内部事件声明。

### 当前处理

- [x] 现有参数化测试稳定检出 3 个缺失事件；
- [x] 未修改生产 wake pattern 或 orchestrator；
- [x] 2026-09-21 K343 当前 HEAD 复核：精确节点 strict `1 xfailed`、`--runxfail` `1 failed`；
  完整 `tests/conformance/test_zaofu_testkit_s3_regressions.py` 为 `3 xfailed`，对应裸红为
  `3 failed`，三个缺失事件仍可由 wake-pattern coverage 直接检出；
- [x] 与 `tests/test_wake_pattern_coverage.py` 直接调用方联调：覆盖矩阵中同三事件均裸红，
  其它已注册 housekeeping/reactor 项通过；未修改生产代码、未访问 Provider/网络。
- [ ] 尚未进入 Issue→Fix Run→回归→merge 流程。

### 2026-09-22 K389 当前 HEAD 裸红复核

- [x] 精确节点 strict **1 xfailed in 0.24s**；`--runxfail` **1 failed in 0.24s**，首个缺失
  `artifact.delivery.verified`；完整三事件集合仍未进入 wake/内部白名单。
- [x] watcher 影响闭包 **205 passed、3 failed、7 skipped、3 xfailed in 2.93s**；三普通失败
  精确对应 `artifact.delivery.verified`、`run.resumed`、`self_issue.assessment.requested`，
  无 collection error 或 timeout。
- [x] 证据日志：`/tmp/zf-k389-zf028-strict.log`、`/tmp/zf-k389-zf028-runxfail.log`、
  `/tmp/zf-k389-zf028-callers.log`；未修改生产代码、未访问 Provider/网络或远端写入。

## ZF-TEST-027：dev-verify CI 合同测试硬编码读取不存在的 GitLab pipeline

> 状态: verified-resolved（恢复测试存在性保护；非生产运行时缺陷）

### 发现阶段

在恢复后的确定性测试复核中执行：

    uv run pytest -q --no-cov tests/test_dev_verify.py

结果为 `17 passed, 1 failed`。失败测试：

    tests/test_dev_verify.py::test_ci_uses_current_controller_and_skips_provider_auth

### 现象与期望

测试直接读取仓库根目录 `.gitlab-ci.yml`，并断言其中包含当前 controller、
`--skip-provider-auth` 和 `scripts/dev-verify.py run`。当前工作树没有该文件，仓库仅有
`.github/workflows/self-issue-labels.yml`；因此测试在读取阶段抛出 `FileNotFoundError`。

该测试应与仓库实际采用的 CI 配置保持一致：如果 GitLab pipeline 不是当前工作树的
必备入口，应明确跳过/标记不适用，或改为检查实际存在的 CI workflow；不能把缺失的
可选文件当成 ZaoFu runtime 产品失败。

### 证据与来源

- `tests/test_dev_verify.py:283-295` 直接调用 `(ROOT / ".gitlab-ci.yml").read_text()`；
- `git ls-files '.gitlab-ci.yml' '.github/workflows/*'` 仅列出
  `.github/workflows/self-issue-labels.yml`；
- 提交 `793199c` 删除了此前存在的 `pipeline_path.exists()` skip 保护；父提交
  `e37005f` 保留该保护。

### 当前处理

- [x] 已保留失败测试和最小复现命令；
- [x] 已确认失败发生在测试/CI 合同层，不是 ZaoFu runtime 生产路径；
- [x] 恢复 `pipeline_path.exists()` skip 保护；未修改生产代码或 CI 文件；
- [x] 重跑后在无 `.gitlab-ci.yml` 的工作树中按预期 skip；
- [ ] 尚未进入 `/issue → Fix Run → 回归 → merge` 流程。

## ZF-TEST-026：多 kind workflow submit 被统一 STOP，未按路由独立接收

> 状态: open（确定性复现；未修改生产代码）

### 发现阶段

S6 CLI flow 路由回归探针：

    tests/conformance/test_zaofu_testkit_s6_cli_flow.py::test_multi_kind_submit_routes_without_stop

复现命令：

    uv run pytest -q --no-cov tests/conformance/test_zaofu_testkit_s6_cli_flow.py::test_multi_kind_submit_routes_without_stop

### 现象与期望

在已初始化的 multi 项目中，分别提交 issue、prd、refactor 三种明确请求；期望三个
结果均为 `accepted`，并产生各自对应的 `workflow.invoke.requested` 与 pattern 路由。
当前三个结果均为 `STOP`，无法进入独立 kind 路由。

### 当前处理

- [x] 已增加 strict xfail 探针并绑定复现命令；
- [x] 未访问真实 Provider、网络或远端服务；
- [x] 未修改 ZaoFu 生产代码；
- [x] 在隔离远端 worktree 对 `origin/dev` 执行原始探针并通过，组合日志为 `/tmp/zf-remote-dev-cli-flow-reprobe-20260914-083000.log`；
- [x] K340 当前 HEAD 复核：strict 模式 `5 xfailed`，`--runxfail` 裸红 `5 failed`；三次独立 apply 均为 `STOP`，未产生各自 accepted 路由；
- [x] K340 已完成既有模块联调，未修改生产代码、未访问真实 Provider；
- [ ] 当前 v7 尚未同步该修复；
- [ ] 尚未进入 `/issue → Fix Run → 回归 → merge` 流程。

## ZF-TEST-025：project init 的明确 Issue 请求使用 --apply 仍返回 STOP

> 状态: open（当前 v7 未同步；origin/dev `603ad9b` 已验证修复，待回合并后回归）

### 发现阶段

S6 CLI flow apply 回归探针：

    tests/conformance/test_zaofu_testkit_s6_cli_flow.py::test_project_init_apply_accepts_clear_request

复现命令：

    uv run pytest -q --no-cov tests/conformance/test_zaofu_testkit_s6_cli_flow.py::test_project_init_apply_accepts_clear_request

### 现象与期望

`project init --request-kind issue --objective ... --acceptance ... --apply` 已提供明确
请求，不应停留在澄清或环境阻断；期望返回码为 0、submit 状态为 `accepted`，并产生
唯一 `workflow.invoke.requested`。当前返回码为 1，submit 状态为 `STOP`。

### 当前处理

- [x] 已增加 strict xfail 探针并绑定复现命令；
- [x] 失败发生在本地隔离项目，未产生 Provider、candidate 或远端副作用；
- [x] 未修改 ZaoFu 生产代码；
- [x] 在隔离远端 worktree 对 `origin/dev` 执行原始探针并通过，组合日志为 `/tmp/zf-remote-dev-cli-flow-reprobe-20260914-083000.log`；
- [x] K340 当前 HEAD 复核：strict 模式 `5 xfailed`，`--runxfail` 裸红 `5 failed`；明确 Issue 请求使用 `--apply` 仍返回 `STOP`，没有进入 `accepted`；
- [x] K340 已完成既有模块联调，未修改生产代码、未访问真实 Provider；
- [ ] 当前 v7 尚未同步该修复；
- [ ] 尚未进入 `/issue → Fix Run → 回归 → merge` 流程。

## ZF-TEST-024：project init 未保留专用 IssueFlow/RefactorFlow kind

> 状态: open（当前 v7 未同步；origin/dev `603ad9b` 已验证修复，待回合并后回归）

### 发现阶段

S6 CLI flow kind 回归探针：

    tests/conformance/test_zaofu_testkit_s6_cli_flow.py::test_project_init_preserves_specialized_flow_kind[...]

复现命令：

    uv run pytest -q --no-cov tests/conformance/test_zaofu_testkit_s6_cli_flow.py::test_project_init_preserves_specialized_flow_kind

### 现象与期望

`project init --kind issue` 生成的 `zf.yaml` 首个文档应为 `IssueFlow`；带有效 source
和 Git 的 `--kind refactor` 应生成 `RefactorFlow`；默认 multi 项目前三个 flow 应为
`IssueFlow`、`PrdFlow`、`RefactorFlow`。当前对应文档首个 kind 为通用 `Workflow`，
导致 flow-specific metadata 和后续 kind 选择无法按契约识别。

### 当前处理

- [x] 已增加 2 个参数化 strict xfail 探针并绑定复现命令；
- [x] 失败只涉及隔离项目配置产物，未访问真实 Provider、网络或远端服务；
- [x] 未修改 ZaoFu 生产代码；
- [x] 在隔离远端 worktree 对 `origin/dev` 执行 3 个参数化原始探针并通过，组合日志为 `/tmp/zf-remote-dev-cli-flow-reprobe-20260914-083000.log`；
- [x] K340 当前 HEAD 复核：strict 模式 `5 xfailed`，`--runxfail` 裸红 `5 failed`；project init 生成的首个 kind 为通用 `Workflow`，multi 顺序为 `Workflow, PrdFlow, RefactorFlow`，refactor 首个 kind 亦为 `Workflow`；
- [x] K340 已完成既有模块联调，未修改生产代码、未访问真实 Provider；
- [ ] 当前 v7 尚未同步该修复；
- [ ] 尚未进入 `/issue → Fix Run → 回归 → merge` 流程。

## ZF-TEST-023：bug-fix-cycle 入口从子目录读取错误的 `.zf` 状态根

> 状态: open（确定性复现；未修改生产代码）

### 发现阶段

S2 配置与 CLI state-root 入口测试：

    tests/conformance/test_zaofu_testkit_s2_surface.py::test_bug_fix_cycle_from_nested_directory_uses_configured_state_dir

复现命令：

    uv run pytest -q --no-cov tests/conformance/test_zaofu_testkit_s2_surface.py::test_bug_fix_cycle_from_nested_directory_uses_configured_state_dir

### 现象与期望

项目根的 `zf.yaml` 将 `project.state_dir` 配置为非默认目录时，从项目子目录执行
`zf bug-fix-cycle`，入口应读取该配置目录中的 `events.jsonl`。当前
`src/zf/cli/bug_fix_cycle.py::_resolve_state_dir` 在未传 `--state-dir` 时直接使用
`Path.cwd()/.zf`，未调用统一 project context resolver。

### 当前结果

测试在配置目录写入 `zaofu.bug.detected` 后从嵌套目录调用 `_run`；当前实现返回
`events.jsonl not found`（退出码 2），而不是读取到 bug 事件。strict xfail 稳定暴露
该 state-root 越界；未访问 Provider、网络或远端服务，未修改生产代码。

### 影响与建议

- 操作员可能看不到真实 bug 诊断，或误读另一个目录的运行态；
- 建议入口使用 `resolve_project_context()`，并增加显式 state-root 与嵌套目录回归矩阵。

### 当前处理

### 2026-09-21 K339 当前 HEAD 裸红复核

复核命令：

    uv run pytest -q --no-cov -o addopts='' tests/conformance/test_zaofu_testkit_s2_surface.py -k 'bug_fix_cycle_from_nested_directory_uses_configured_state_dir'
    uv run pytest -q --no-cov -o addopts='' --runxfail tests/conformance/test_zaofu_testkit_s2_surface.py -k 'bug_fix_cycle_from_nested_directory_uses_configured_state_dir'
    uv run pytest -q --no-cov -o addopts='' tests/conformance/test_zaofu_testkit_s2_surface.py

结果：精确节点 strict **1 xfailed in 0.40s**；`--runxfail` **1 failed in 0.40s**；完整
S2 surface **4 passed、6 xfailed in 1.68s**。嵌套目录调用返回码为 `2`，stderr 指向
`project/src/worker/.zf/events.jsonl not found`，未读取父级配置的 `runtime-state`；无普通
失败、collection error 或 timeout，未访问 Provider/网络，生产代码未修改，Issue 继续保持
`open`。

- [x] 已增加确定性 strict xfail 检测并绑定复现命令；
- [x] 未修改 ZaoFu 生产代码；
- [ ] 尚未修复；
- [ ] 尚未进入 `/issue → Fix Run → 回归 → merge` 流程。

### 2026-09-21 K250：S6 strict-xfail 裸红审计汇总

K249 完整 S6 中的 8 个 strict xfail 已使用相同 nodeid 以 `--runxfail` 裸红执行，
结果为 **8 failed / 0 passed**，退出码 1，耗时 11.62 秒；证据保存在：

    /tmp/zf-s6-xfail-audit-20260921-k250/pytest.stdout.log
    /tmp/zf-s6-xfail-audit-20260921-k250/pytest.stderr.log

其中 CLI flow 的 5 个失败仍分别对应 ZF-TEST-024、ZF-TEST-025、ZF-TEST-026；projection
contract 的 3 个失败仍对应 ZF-TEST-022 及其 freshness/source 相关契约。没有 XPASS、
collection error 或 timeout，也没有新增重复 Issue。该审计只证明当前 v7 的已登记问题仍
存在，不能替代维护者修复；修复后必须先用同一 `--runxfail` 得到 XPASS，再移除/更新
strict xfail 并重跑完整 S6。

## ZF-TEST-022：single-flight 重建失败未传播给等待中的 fresh reader

> 状态: open（确定性复现；未修改生产代码）

### 2026-09-21 K328 当前 HEAD 裸红复核

复核命令：

    uv run pytest -q --no-cov -o addopts='' tests/conformance/test_zaofu_testkit_s6_projection_contract.py::test_joining_fresh_reader_fails_closed_when_rebuild_owner_fails tests/conformance/test_zaofu_testkit_s6_projection_contract.py::test_single_flight_joiner_timeout_is_not_silent
    uv run pytest -q --no-cov -o addopts='' --runxfail tests/conformance/test_zaofu_testkit_s6_projection_contract.py::test_joining_fresh_reader_fails_closed_when_rebuild_owner_fails tests/conformance/test_zaofu_testkit_s6_projection_contract.py::test_single_flight_joiner_timeout_is_not_silent
    uv run pytest -q --no-cov -o addopts='' tests/conformance/test_zaofu_testkit_s6_projection_contract.py

结果：精确节点 strict **2 xfailed in 1.33s**；`--runxfail` **2 failed in 1.37s**；完整
projection contract **3 passed、3 xfailed in 2.41s**。owner 的 synthetic rebuild failure 后，
joiner 仍返回旧 baseline page；joiner 等待 `0.01s` 时未抛 `TimeoutError`。无 XPASS、collection
error 或 timeout；未修改 `src/`、未访问 Provider/网络，Issue 继续 open，维护者修复后须用同一
入口验证无 XPASS。

### 发现阶段

S6 projection freshness 并发失败契约测试：

    tests/conformance/test_zaofu_testkit_s6_projection_contract.py::test_joining_fresh_reader_fails_closed_when_rebuild_owner_fails

复现命令：

    uv run pytest -q --no-cov tests/conformance/test_zaofu_testkit_s6_projection_contract.py::test_joining_fresh_reader_fails_closed_when_rebuild_owner_fails

### 现象与期望

两个 `require_fresh=true` reader 同时请求同一项目的读模型重建时，owner 在
`rebuild()` 失败后会传播异常；joiner 只等待共享事件，不会获知 owner 的失败原因，
因此可能继续读取旧的 SQLite 投影并返回成功响应。

期望 owner 的重建失败或等待超时都应对所有 fresh reader fail-closed：传播可诊断
的失败，或返回明确的 stale/error 元数据；绝不能把未达到要求水位的旧投影当作 fresh
成功结果。

### 当前结果

测试通过命名 failpoint 让 owner 确定性抛出异常，并在释放前确认第二个 reader 已加入
single-flight。当前实现下该契约断言稳定失败，以 strict xfail 保留检出能力；未访问
真实 Provider、网络或远端服务，未修改 ZaoFu 生产代码。

### 影响

### 影响

- API 或 Web fresh read 可能在重建失败后返回旧事件/状态；
- 调用方无法区分 fresh 成功与 single-flight owner 失败；
- 故障期间可能出现页面、API 与 EventLog 真值不一致。

### 建议修复方向（不在本测试任务中实施）

- 为 single-flight 共享结果同时记录完成与异常状态，joiner 必须观察并传播 owner
  的失败；
- 将等待超时转换为明确的 projection error/stale 响应，禁止静默继续；
- 增加 owner 成功、owner 异常、joiner 超时和重建后重试的并发矩阵。

### 当前处理

- [x] 已增加确定性 strict xfail 检测；
- [x] 已验证 owner/joiner 的时序，不依赖随机 sleep；
- [x] 未修改 ZaoFu 生产代码；
- [ ] 尚未修复；
- [ ] 尚未进入 /issue → Fix Run → 回归 → merge 流程。

### 2026-09-22 K448 测试框架补测（不关闭 Issue）

新增确定性合同 `tests/conformance/test_zaofu_testkit_s8_workflow_operation_contract.py`。
独立子进程重开同一 durable request sidecar 时，入队 hash 保持稳定，第一次消费 settle，
第二次消费幂等返回；改变 operation context 或 allowed skill catalog 时，则在 Provider
调用前显式写出 `workflow.operation.blocked reason=request_hash_divergence`，并保留
expected/actual hash。直接 workflow 调用方 88 passed，S8 完整注册面 222 cases 为
211 passed、11 个既有 strict xfailed，新合同 3 项全部 passed。

这轮结果仅证明测试框架具备区分“跨进程重放稳定”和“真实语义漂移”的能力，未解释四流程
现场 `bf9d… → d03…` 的具体字段差异，也未修改 `src/`/`web/src/`；`ZF-TEST-047` 继续
保持 open，维护者修复后仍需用原四流程 Docker Playwright 脚本验证 synthesis result →
proposal → config apply → 后续浏览器断言完整抵达。

### 2026-09-22 K449 字段级根因定位（仍不关闭 Issue）

新增严格检测器
`tests/conformance/test_zaofu_testkit_s8_workflow_operation_contract.py::test_locked_self_issue_runtime_keeps_synthesis_project_catalog_current`，
通过真实 `WorkflowRuntimeCoordinator` 复现：隔离项目入队时使用项目根目录；运行时在
`self_issue.enabled=true + target_locked=true` 下将 `project_root` 解析到
`/home/uisee/zaofu_v7`。因此 consumer 重建 synthesis request 时，`admission_catalog()`
从主仓扫描 skill，allowed skills 从 **33** 变为 **73**，request hash 从
`bf9d6f7c…` 漂移到 `d03bedc3…`，并在 Provider 调用前写出
`workflow.operation.blocked reason=request_hash_divergence`；Agent 调用次数为 0。

严格节点当前为 **1 xfailed**，`--runxfail` 为 **1 failed**。完整 S8 收尾为 **223 cases，
211 passed、12 strict xfailed**，无 collection error、deselection、timeout 或普通失败；
S0 收尾 **11/11 passed**。这已将 K437/K448 的现象收窄为字段级生产缺陷证据，但不修改
生产代码、不关闭 `ZF-TEST-047`。维护者修复后仍需重跑 `--runxfail`、直接 callers、
完整 S8 和原四流程 Docker Playwright。

### 2026-09-21 当前 HEAD 裸红复核（K286）

- 普通模式：两个定向 case 均 strict xfailed，4 个同文件用例被 deselect；总计 `2 xfailed`
  in 5.09s。
- `--runxfail`：两个 case 均 failed in 2.34s。owner 重建抛出
  `RuntimeError("synthetic rebuild failure")` 时，joiner 仍返回旧 SQLite page；joiner
  等待 0.01s 时未抛出 `TimeoutError`，静默成功。
- 无 XPASS、collection error 或 timeout；使用真实 `read_model.events_page()`/
  `_rebuild_single_flight()`、ThreadPoolExecutor 和临时 state，不访问 Provider/网络，
  未修改 `src/`。既有 `ZF-TEST-022` 保持 open，修复后需用相同两个 node 验证无 XPASS。

### 2026-09-21 K345 当前 HEAD single-flight 联调

- [x] 两个精确节点 strict **2 xfailed in 1.76s**；`--runxfail` **2 failed in 1.68s**。
  owner failure 场景中 owner 收到 `RuntimeError`，joiner 仍返回旧 baseline page；timeout
  场景未抛出 `TimeoutError`，均违反 fail-closed 契约。
- [x] 完整 S6 projection contract 为 **3 passed、3 xfailed in 3.00s**；正向 fresh/scoped
  route 对照通过，无 collection error 或 timeout。
- [x] 未修改生产代码、未访问 Provider/网络；Issue 继续 `open`，维护者修复后须用同一两个
  `--runxfail` 节点确认所有 reader 都传播失败或明确错误元数据。

### 2026-09-22 K394 当前 HEAD owner/joiner 精确复核

- [x] owner failure 节点 strict **1 xfailed in 1.15s**；`--runxfail` **1 failed in 1.15s**。synthetic rebuild failure 后 owner 返回 error，但 joiner 返回旧 baseline page，outcomes 为 **1 error + 1 page**，未 fail-closed。证据：`/tmp/zf-k394-zf022-strict.log`、`/tmp/zf-k394-zf022-runxfail.log`。
- [x] joiner timeout 节点 strict **1 xfailed in 1.13s**；`--runxfail` **1 failed in 1.14s**。`wait_timeout_s=0.01` 未抛 `TimeoutError`，超时被静默吞掉。证据：`/tmp/zf-k394-zf022-timeout-strict.log`、`/tmp/zf-k394-zf022-timeout-runxfail.log`。
- [x] 直接 caller 联调（完整 S6 projection contract + `tests/test_read_model_single_flight.py`）：**4 passed、3 xfailed in 2.27s**；正向 single-flight 控制组通过，未发现新增未登记失败。证据：`/tmp/zf-k394-zf022-caller.log`。
- [x] 未修改生产代码；`ZF-TEST-022` 继续保持 `open`，修复后必须用相同两个精确节点和 caller closure 验证无 XPASS。

## ZF-TEST-019：attach/panes 入口绕过统一 project context 解析 state root

> 状态: open（源码审计确定；未修改生产代码）

### 2026-09-21 K329 当前 HEAD 裸红复核

复核命令：

    uv run pytest -q --no-cov -o addopts='' tests/conformance/test_zaofu_testkit_s2_surface.py -k 'known_cli_context_bypasses_are_removed or nested_cli_entrypoints_honor_inherited_project_context'
    uv run pytest -q --no-cov -o addopts='' --runxfail tests/conformance/test_zaofu_testkit_s2_surface.py -k 'known_cli_context_bypasses_are_removed or nested_cli_entrypoints_honor_inherited_project_context'
    uv run pytest -q --no-cov -o addopts='' tests/conformance/test_zaofu_testkit_s2_surface.py

结果：精确节点 strict **5 xfailed in 0.49s**；`--runxfail` **5 failed in 0.49s**；完整 S2
surface **4 passed、6 xfailed in 1.67s**。attach、panes、bug_fix_cycle 均未使用统一
project-context resolver；嵌套 attach 未捕获父级 config，panes 从子目录加载 `zf.yaml` 并抛
`ConfigError`。无 XPASS、collection error 或 timeout；未修改 `src/`、未访问 Provider/网络，
Issue 继续 open，维护者修复后须用同一入口验证无 XPASS。

### 发现阶段

S2 配置、Schema、EventLog 和 Store 的 CLI state-root 入口审计：

    tests/conformance/test_zaofu_testkit_s2_surface.py::test_known_cli_context_bypasses_are_removed[attach]
    tests/conformance/test_zaofu_testkit_s2_surface.py::test_known_cli_context_bypasses_are_removed[panes]
    tests/conformance/test_zaofu_testkit_s2_surface.py::test_nested_cli_entrypoints_honor_inherited_project_context[attach]
    tests/conformance/test_zaofu_testkit_s2_surface.py::test_nested_cli_entrypoints_honor_inherited_project_context[panes]

### 复现条件

1. 在项目根目录配置 `zf.yaml`，令 `project.state_dir` 指向非默认目录；
2. 从项目子目录或与项目根不同的当前目录调用 `zf attach` 或 `zf panes doctor/repair`；
3. 观察入口实现：`attach.py` 直接使用 `Path.cwd()/zf.yaml`，`panes.py` 的
   `_load_manager()` 也直接以 `Path.cwd()` 加载配置并拼接相对 state_dir；
4. 与其他 CLI 入口使用的 `resolve_project_context()` 结果比较。

### 期望结果

所有有状态 CLI 入口都应从同一 project context 解析项目根、配置文件和 `project.state_dir`；
从子目录调用时仍应读到项目配置，并且不产生或读取当前目录下独立的 `.zf`。

### 当前结果

`attach` 和 `panes` 源码没有统一 context resolver 标记，审计用例稳定以 strict xfail
暴露该绕过。该测试属于 S2 registry，若入口改为统一 resolver 后意外通过，strict xfail
会以 XPASS 失败，提示移除 Issue 记录并恢复全绿门禁。

### 影响

- 子目录操作可能找不到项目配置，或加载错误项目的配置；
- 相对 `state_dir` 可能被解释到错误目录，造成运行态读写分叉；
- 操作员可能在错误的 runtime state 上执行 attach/panes 修复。

### 建议修复方向（不在本测试任务中实施）

- `attach`、`panes` 使用 `resolve_project_context()` 或等价的统一解析入口；
- 增加从项目子目录调用的 CLI 回归测试，并断言配置 state root 与实际读写路径一致。

### 当前处理

- [x] 已增加可执行的 strict xfail 检测（含嵌套目录/继承 context 的动态 detector）；
- [x] 已保留 attach/panes 源码证据和复现条件；
- [x] 未修改 ZaoFu 生产代码；
- [ ] 尚未修复；
- [ ] 尚未进入 /issue → Fix Run → 回归 → merge 流程。

## ZF-TEST-018：重复重启会无界追加 worker.drift.detected 事件

> 状态: open（确定性复现；未修改生产代码）

### 发现阶段

S4 多任务重启压力测试（250 周期）：

    tests/conformance/test_zaofu_testkit_s4.py::test_multi_task_restart_soak_preserves_dispatches_and_event_snapshot

复现命令：

    uv run pytest -q --no-cov tests/conformance/test_zaofu_testkit_s4.py::test_multi_task_restart_soak_preserves_dispatches_and_event_snapshot

### 现象与期望

在同一个 state dir 中创建 4 个 backlog task，完成首次 dispatch 后连续重建
Orchestrator 100 次。Task 状态、active dispatch 和 `task.dispatched` 数量保持不变，
但恢复循环会追加 `worker.drift.detected`（`signal=node_skip`）事件，导致 EventLog
持续增长；当前测试在完整 250 周期后统一检查事件快照，而不是在首个漂移点提前退出。

期望重复恢复在没有状态变化时不产生重复诊断事件，或对等价 drift 诊断做幂等/有界
抑制；EventLog 只增加必要的恢复事实，不应因只读重启循环持续膨胀。

### 当前证据

- 测试使用 4 个任务、4 个 mock role 和临时 state dir，不访问真实 Provider、tmux
  会话或远端服务。
- 首次 dispatch 产生 4 个 `task.dispatched`；后续 250 次重启没有新增 dispatch，
  但新增 `worker.drift.detected`，因此完整 soak 结束后的事件 ID 快照断言稳定失败。
- 测试保留为 strict xfail，修复后应出现 XPASS 并移除该标记；未修改 ZaoFu 生产代码。

### 2026-09-21 当前 HEAD 裸红复核（K283）

- 普通模式：`1 xfailed in 47.05s`；严格 xfail 原因仍为 `ZF-TEST-018`。
- `--runxfail`：`1 failed in 50.03s`；4 个任务完成首次派发后，250 次真实
  `Orchestrator.run_once()` 重建期间没有新增 `task.dispatched`，但 EventLog 比基线多出
  250 个事件，最终快照断言失败。
- 失败发生在既有 `worker.drift.detected` 增长断言，未出现 XPASS、collection error 或
  timeout；测试仍只使用 mock role 和临时 state dir，未访问 Provider/远端服务。
- 这是对既有开放 Issue 的当前 HEAD 复现，不是新 Issue；未修改 `src/`。维护者修复后
  必须以同一测试和 `--runxfail` 验证无 XPASS。

### 2026-09-21 K347 当前 HEAD 长时重启联调

- [x] 精确 soak strict **1 xfailed in 35.41s**；`--runxfail` **1 failed in 35.25s**。
  4 个任务在 250 次真实 `Orchestrator.run_once()` 重建中没有新增 `task.dispatched`，但
  EventLog 比 baseline 多出 **250** 个 `worker.drift.detected`。
- [x] 完整 S4 conformance 结果为 **13 passed、5 xfailed in 82.39s**，无普通失败、
  collection error 或 timeout；其它 recovery/TaskAttempt/child-process 控制通过。
- [x] 仅使用 mock role、临时 state 和 dry-run tmux；未访问真实 Provider/远端服务、未修改
  生产代码，Issue 继续 `open`。

### 2026-09-21 K330 当前 HEAD 再复核

复核命令：

    uv run pytest -q --no-cov -o addopts='' tests/conformance/test_zaofu_testkit_s4.py -k 'multi_task_restart_soak_preserves_dispatches_and_event_snapshot'
    uv run pytest -q --no-cov -o addopts='' --runxfail tests/conformance/test_zaofu_testkit_s4.py -k 'multi_task_restart_soak_preserves_dispatches_and_event_snapshot'
    uv run pytest -q --no-cov -o addopts='' tests/conformance/test_zaofu_testkit_s4.py

结果：精确节点 strict **1 xfailed in 24.72s**；`--runxfail` **1 failed in 26.76s**；完整
S4 **13 passed、5 xfailed in 84.61s**。4 个任务首次 dispatch 后，250 次真实
`Orchestrator.run_once()` 重建没有新增 `task.dispatched`，但 EventLog 比基线多出 250 个
`worker.drift.detected` 事件，最终事件快照断言失败。失败原因与既有 `ZF-TEST-018` 完全
一致；无 XPASS、collection error 或 timeout，未访问 Provider/网络，生产代码未修改，Issue
继续保持 `open`。

> 状态: open（ZF-TEST-008 已解决；ZF-TEST-009 的测试预期已修正但原始时序待定位；ZF-TEST-011、ZF-TEST-012、ZF-TEST-014、ZF-TEST-015、ZF-TEST-016、ZF-TEST-017、ZF-TEST-018 已确定性复现）

## ZF-TEST-014：Run Archive 二进制 artifact 写入存在校验后路径替换窗口

> 状态: open（确定性复现；未修改生产代码）

### 发现阶段

S7 TOCTOU 文件 sink 测试：

    tests/conformance/test_zaofu_testkit_s7_toctou.py::test_binary_artifact_sink_rechecks_destination_after_path_guard

复现命令：

    uv run pytest -q --no-cov tests/conformance/test_zaofu_testkit_s7_toctou.py

### 现象与期望

`src/zf/runtime/run_archive.py::_copy_file_artifact` 先通过 `PathGuard.assert_under`
校验目标路径，随后对二进制 artifact 调用 `shutil.copy2`。在校验返回后、copy sink
执行前，如果目标被替换为指向允许根目录外的 symlink，当前实现仍会接受该目标；
`shutil.copy2` 会跟随 symlink，把 artifact 内容写到外部文件。

期望目标路径在真正写入前再次确认仍位于 artifact root，或使用不会跟随替换后
symlink 的安全文件描述符/原子写入方式；外部目标内容必须保持不变。

### 当前证据

- 测试只在 pytest 临时目录创建 source、archive root 和 outside 文件，并通过受控
  monkeypatch 精确模拟校验与写入之间的替换窗口；不访问真实 `.zf` 或远端。
- 当前实现会成功执行二进制 `copy2` 并改写 outside 文件，测试以 strict xfail
  保留该生产缺陷；若修复后在 sink 前拒绝替换，strict xfail 将以 XPASS 提醒移除标记。
- 未修改 ZaoFu 生产代码。

## ZF-TEST-015：Fanout artifact 搬运存在校验后路径替换窗口

> 状态: open（确定性复现；未修改生产代码）

### 发现阶段

S7 TOCTOU 文件 sink 测试：

    tests/conformance/test_zaofu_testkit_s7_toctou.py::test_fanout_artifact_sink_rechecks_destination_after_path_guard

复现命令：

    uv run pytest -q --no-cov tests/conformance/test_zaofu_testkit_s7_toctou.py::test_fanout_artifact_sink_rechecks_destination_after_path_guard

### 现象与期望

`src/zf/runtime/fanout_artifact_refs.py::_copy_relocated_source` 先通过
`PathGuard.assert_under` 校验 fanout artifact 目标路径，随后直接调用
`shutil.copyfile`。在校验返回后、copy sink 执行前，如果目标被替换为指向允许根目录外
的 symlink，当前实现仍会跟随 symlink，把产品项目产物写到外部文件。

期望目标路径在真正写入前再次确认仍位于 `state_dir/artifacts/fanouts`，或使用不会跟随
替换后 symlink 的安全文件描述符/原子写入方式；外部目标内容必须保持不变。

### 当前证据

- 测试只在 pytest 临时目录创建 source、state 和 outside 文件，并通过受控 monkeypatch
  精确模拟校验与写入之间的替换窗口；不访问真实 `.zf` 或远端。
- 当前实现会成功执行 `copyfile` 并改写 outside 文件，测试以 strict xfail 保留该生产缺陷；
  若修复后在 sink 前拒绝替换，strict xfail 将以 XPASS 提醒移除标记。
- 未修改 ZaoFu 生产代码。

## ZF-TEST-016：Run Archive 目录 artifact 写入存在父目录替换窗口

> 状态: open（确定性复现；未修改生产代码）

### 发现阶段

S7 TOCTOU 文件 sink 测试：

    tests/conformance/test_zaofu_testkit_s7_toctou.py::test_directory_artifact_sink_rechecks_parent_after_path_guard

复现命令：

    uv run pytest -q --no-cov tests/conformance/test_zaofu_testkit_s7_toctou.py::test_directory_artifact_sink_rechecks_parent_after_path_guard

### 现象与期望

`src/zf/runtime/run_archive.py::_copy_dir_artifact` 先对目录目标执行
`PathGuard.assert_under`，再调用 `shutil.copytree`。在校验返回后、复制执行前，若目标
父目录被替换为指向 artifact root 外部的 symlink，当前实现会跟随该 symlink，将目录
artifact 写到外部位置。

期望目录目标及其父目录在真正执行删除/复制前再次确认仍位于 artifact root，或使用
不会跟随替换后 symlink 的安全目录创建/复制方式；外部目录内容必须保持不变。

### 当前证据

- 测试只在 pytest 临时目录创建 source、archive root 和 outside 目录，通过受控
  monkeypatch 精确模拟校验与 `copytree` 之间的父目录替换窗口。
- 当前实现会把 `report.txt` 写入 outside 目录，测试以 strict xfail 保留该生产缺陷；
  修复后若在 sink 前拒绝替换，strict xfail 将以 XPASS 提醒移除标记。
- 未修改 ZaoFu 生产代码。

## ZF-TEST-017：tmux 窗口删除后 pane_alive 仍返回 true

> 状态: open（宿主机上确定性复现；未修改生产代码）

### 发现阶段

S5 host tmux 生命周期测试：

    tests/conformance/test_zaofu_testkit_s5_host.py::test_tmux_pane_crash_is_terminated_without_killing_the_session

复现命令：

    uv run pytest -q --no-cov -m host tests/conformance/test_zaofu_testkit_s5_host.py::test_tmux_pane_crash_is_terminated_without_killing_the_session

### 现象与期望

测试创建隔离 tmux session 和 role window，启动 `sleep` 进程后调用
`TmuxSession.terminate_window`。tmux 已删除目标窗口，并返回 `can't find window: crash-lane`，
但随后 `TmuxSession.pane_alive("crash-lane")` 仍返回 `True`。

期望目标窗口/ pane 已不存在时，`pane_alive` 返回 `False`，以便 watchdog 和恢复逻辑准确
识别 pane crash；不能把已删除目标误判为仍存活。

### 当前证据

- 测试只创建唯一命名的临时 tmux session，结束时仅 kill 自己的 session；不触碰开发会话、
  Provider 凭据或远端服务。
- 当前实现稳定触发 `ProductionTmuxLifecycleBug`，测试以 host strict xfail 保留；修复后
  strict xfail 将以 XPASS 提醒移除标记。
- 未修改 ZaoFu 生产代码。

补充复现（真实 Provider pane）：

    ZF_RUN_REAL_TMUX_CODEX_CANARY=1 ZF_RUN_REAL_TMUX_CODEX_RECOVERY=1 \
    uv run pytest -q --no-cov -m 'host and real_provider and serial' \
    tests/conformance/test_zaofu_testkit_s5_real_tmux.py::test_real_tmux_codex_pane_kill_keeps_session_and_allows_restart

该用例先在真实 Codex pane 中启动 `codex exec --ephemeral --json`，再调用
`terminate_window("provider")`。当前结果为 **1 strict xfailed**：tmux 已删除目标窗口、
session 仍存在，但 `pane_alive("provider")` 返回 `True`，与上面的 sleep 复现一致；
修复后才会继续执行 `provider-restart` window 的真实 Codex 重启和 terminal event 断言。

当前 HEAD 复核（2026-09-21，K280）：

    uv run pytest -q --no-cov -o addopts='' tests/conformance/test_zaofu_testkit_s5_host.py::test_tmux_pane_crash_is_terminated_without_killing_the_session
    # 1 xfailed in 1.41s

    uv run pytest -q --no-cov -o addopts='' --runxfail tests/conformance/test_zaofu_testkit_s5_host.py::test_tmux_pane_crash_is_terminated_without_killing_the_session
    # 1 failed in 4.93s; ProductionTmuxLifecycleBug

失败仍发生在 `pane_alive("crash-lane")`：stderr 显示 `can't find window: crash-lane`，但
返回值仍为 `True`。本轮只做复现和证据追加，未修改 `src/`，未触碰现有开发 tmux、Provider
凭证或远端服务。

### 2026-09-21 K346 当前 HEAD 隔离 host 联调

- [x] 精确 host 节点 strict **1 xfailed in 1.51s**；`--runxfail` **1 failed in 1.61s**，
  仍抛出 `ProductionTmuxLifecycleBug`，tmux stderr 为 `can't find window: crash-lane`，但
  `pane_alive("crash-lane")` 仍为 `True`。
- [x] 完整 mock host S5（`-m 'host and not real_provider'`）为 **2 passed、1 xfailed in
  1.71s**；临时 session 已清理，无 collection error 或 timeout。
- [x] 未启用真实 Provider、未触碰现有开发 tmux/凭证、未修改生产代码；Issue 继续 `open`，
  维护者修复后须用同一 host 节点回归并确认 restart/takeover 后续断言可达。

## ZF-TEST-012：EventLog fallback 响应缺少投影 freshness/source 元数据

> 状态: open（确定性复现；未修改生产代码）

### 发现阶段

S6 projection freshness 契约测试：

    tests/conformance/test_zaofu_testkit_s6_projection_contract.py::test_event_fallback_preserves_freshness_and_source_metadata

复现命令：

    uv run pytest -q --no-cov tests/conformance/test_zaofu_testkit_s6_projection_contract.py::test_event_fallback_preserves_freshness_and_source_metadata

### 现象与期望

当 `read_model.events_page()` 异常时，`_events_page()` 会直接扫描 EventLog 并返回事件，
但响应没有标识这是 fallback，也没有 freshness 元数据。调用方无法区分“索引已就绪的
投影结果”和“读模型失败后的账本回退结果”。

期望 fallback 响应至少保留稳定的 `projection_state`、`projection_source`、
`tail_behind` 和 `projection_lag` 字段，其中来源明确为 `event_log`，并以空值表达
无法计算的 freshness 数值。

### 当前结果

- 测试通过 monkeypatch 注入确定性的读模型异常，不访问网络或真实 Provider；
- EventLog fallback 能返回匹配事件，但缺少上述元数据，断言稳定失败；
- 用 strict xfail 保留失败，不把 fallback 当作完整投影通过；
- 未修改 ZaoFu 生产代码。

当前 HEAD 复核（2026-09-21，K281）：

    uv run pytest -q --no-cov -o addopts='' tests/conformance/test_zaofu_testkit_s6_projection_contract.py::test_event_fallback_preserves_freshness_and_source_metadata
    # 1 xfailed in 1.47s

    uv run pytest -q --no-cov -o addopts='' --runxfail tests/conformance/test_zaofu_testkit_s6_projection_contract.py::test_event_fallback_preserves_freshness_and_source_metadata
    # 1 failed in 1.41s; AssertionError: None == 'fallback'

裸红失败仍发生在 `payload.get("projection_state")`：实际值为 `None`，断言期望
`fallback`；事件项目仍返回，但没有 fallback 来源/新鲜度元数据。本轮只复现并追加证据，
未修改 `src/`，未新增 Issue。

### 2026-09-21 K344 当前 HEAD fallback contract 联调

- [x] 精确节点 strict **1 xfailed in 1.71s**；`--runxfail` **1 failed in 1.83s**，失败
  仍为 `projection_state is None` 而非期望 `fallback`。
- [x] 完整 `tests/conformance/test_zaofu_testkit_s6_projection_contract.py` 为
  **3 passed、3 xfailed in 2.94s**；其它 freshness、scoped route 和 EventLog 对照通过，
  无 collection error 或 timeout。
- [x] 未修改生产代码、未访问 Provider/网络；Issue 继续保持 `open`，证据已登记到 coverage
  matrix，待维护者修复后仍需用同一 `--runxfail` 回归。

### 2026-09-22 K393 当前 HEAD 裸红复核

- [x] 精确节点 strict **1 xfailed in 1.19s**；`--runxfail` **1 failed in 1.13s**，fallback
  items 正常返回，但 `projection_state` 仍为 `None`，未提供 source/freshness 元数据。
- [x] S6 projection/Web caller **19 passed、3 xfailed、105 deselected in 7.40s**；其它
  events/freshness/projection 对照通过，三个 xfail 均为已登记 Issue。
- [x] 证据日志：`/tmp/zf-k393-zf012-strict.log`、`/tmp/zf-k393-zf012-runxfail.log`、
  `/tmp/zf-k393-zf012-callers.log`；未修改生产代码、未访问 Provider/网络或远端写入。

### 2026-09-22 K440 当前 HEAD fallback metadata 调用方复核

- [x] 精确节点 strict **1 xfailed**；`--runxfail` **1 failed**，失败仍为
  `projection_state=None`，而期望是 `fallback`；EventLog fallback items 本身仍能返回。
- [x] projection contract、Web server、read-model freshness 和 malformed-event 选定调用方合计
  **51 passed、3 strict xfailed、112 deselected**；非绿色项均为已登记 projection Issue。
- [x] S0 收尾 `/tmp/zf-s0-k440-closeout-2Vrhwj/testkit-run.json`：**11/11 passed**，`exit_code=0`、
  `verdict=pass`、`session_finished=true`，无 collection error、deselection、timeout 或
  evidence error。
- [x] 未修改 `src/`/`web/src/`，未访问 Provider/网络、未触碰 8001 或远端；`ZF-TEST-012`
  继续保持 `open`，修复后仍需用同一节点和完整影响闭包回归。

## ZF-TEST-011：Kanban 只读投影每次读取都会漂移 created_at

> 状态: open（确定性复现；未修改生产代码）

### 发现阶段

S10 bounded soak 测试：

    tests/test_web_perf_smoke.py::test_repeated_projection_reads_are_stable_and_side_effect_free

复现命令：

    uv run pytest -q --no-cov -m perf tests/test_web_perf_smoke.py::test_repeated_projection_reads_are_stable_and_side_effect_free

### 现象与期望

在同一个 `state_dir`、EventLog 不变且连续调用只读 `_kanban()` 时，投影结果应保持
稳定，尤其是 `created_at` 这类身份字段不能因读取而变化。

实际结果是：当 `kanban.json` 的任务缺少 `created_at` 时，第一次读取返回当前时间，
下一次读取又生成新的当前时间。例如：

    first:  2026-09-11T18:00:53.056092+00:00
    second: 2026-09-11T18:00:53.267636+00:00

EventLog 和 canonical `kanban.json` 均未写入，但同一输入的 API 投影不稳定，可能导致
ETag/缓存、UI diff、快照和下游证据误判为任务发生变化。

### 当前证据

- 首次 bounded soak 运行确定性失败：`/0/created_at` 值在相邻读取间变化；
- 复现使用 3000 条事件、12 个任务的隔离临时 state dir，不依赖网络或真实 Provider；
- 原始失败输出保留在 pytest 临时目录；
- 对应测试保留并以 strict xfail 标记，原因明确引用本 Issue，避免把已知产品缺陷误报为
  TestKit 通过；
- 未修改 ZaoFu 生产代码。

### 2026-09-21 当前 HEAD 裸红复核（K284）

- 普通模式：`1 xfailed in 14.62s`；strict xfail 原因仍为 `ZF-TEST-011`。
- `--runxfail`：`1 failed in 14.83s`；同一临时 state dir 连续 20 次调用真实
  `zf.web.server._kanban()` 时，后续结果与 baseline 不相等，差异仍出现在任务投影字段
  （包含 `created_at` 漂移）；无 collection error、timeout 或 XPASS。
- 测试使用 12 个任务和 3000 条隔离 EventLog，不访问网络、Provider 或远端服务；未修改
  `src/`。这是既有开放 Issue 的当前 HEAD 复现，维护者修复后须用同一命令验证无 XPASS。

### 2026-09-22 K392 当前 HEAD 裸红复核

- [x] perf strict **1 xfailed in 11.36s**；`--runxfail` **1 failed in 11.42s**，20 次真实
  `_kanban()` 读取中第一次重复读取即与 baseline 不等，差异仍落在任务投影字段。
- [x] Web projection caller **19 passed、1 xfailed、104 deselected in 18.30s**；其它
  Kanban/snapshot 对照通过。
- [x] 证据日志：`/tmp/zf-k392-zf011-strict.log`、`/tmp/zf-k392-zf011-runxfail.log`、
  `/tmp/zf-k392-zf011-callers.log`；未修改生产代码、未访问 Provider/网络或远端写入。

### 2026-09-22 K441 测试框架边界补强与当前 HEAD 复核

- [x] 新增独立的 canonical truth 不变性合同：连续 20 次 `_kanban()` 与 `snapshot/light` 读取后，
  `events.jsonl`、`kanban.json`、`feature_list.json` 字节保持不变，**1 passed**。
- [x] 原始稳定性 detector 继续严格区分已知缺陷：strict **1 xfailed**；`--runxfail` **1 failed**，
  失败路径明确为 `tasks[0].created_at`，没有被其他字段或测试框架错误掩盖。
- [x] Kanban/snapshot/read-model freshness 调用方：**15 passed、1 strict xfailed**；`ruff`
  通过。测试初版曾把可重建 `events/manifest.json` 当作 canonical truth，已修正为只检查三份
  canonical 文件；该夹具误报未登记为生产 Issue。
- [x] 未修改 `src/`/`web/src/`，未访问 Provider/网络、未触碰 8001 或远端；`ZF-TEST-011`
  继续保持 `open`，维护者修复后仍须验证 created_at 稳定且 canonical truth 不变。

## ZF-TEST-009：ALL 联调中 Web Event projection 偶发返回空列表（测试预期已修正）

> 状态: open（未分类的间歇性观察；测试预期已修正，不等于生产根因已消除）

### 发现阶段

新增 S8 本地远端交付边界测试后执行 ALL 联调。

发现测试：

    tests/test_web_server.py::TestApiChannels::test_channel_projection_rebuilds_from_events_and_redacts

首次复现命令：

    uv run python -m tests.testkit --stage all --output /tmp/zf-testkit-all-s8remote-233713

### 现象与期望

测试向隔离 EventLog 写入 `channel.created`、`channel.member.invited` 和
`workflow.invoke.requested` 后，请求 `/api/events?prefix=channel.`。期望返回至少
一条匹配事件；首次 ALL 运行返回空 `items`，导致断言失败。

### 复核结果

- 该测试单独连续 3 次通过；`tests/test_web_server.py` 全文件 121 个测试通过。
- 第二次同一 ALL 组合 `/tmp/zf-testkit-all-s8remote-rerun-234923` 通过，
  631/635 结果为 623 passed、12 strict xfailed。
- 因此当前证据表明这是间歇性行为，而不是已稳定复现的确定性失败；不能用重试
  把首次失败简单改写为绿色。

### 可能原因（待验证）

`src/zf/web/projections/read_model.py` 的普通读取路径在 projection ready 但 tail
behind 时会异步 catch-up，不等待重建；若请求恰好落在该窗口，API 可能读取尚未
包含最新事件的 SQLite projection。这是基于当前代码路径的推断，尚未证明为唯一
根因。

### 当前处理

### 2026-09-21 K338 当前 HEAD 裸红复核

复核命令：

    uv run pytest -q --no-cov -o addopts='' tests/test_web_perf_smoke.py -k 'repeated_projection_reads_are_stable_and_side_effect_free'
    uv run pytest -q --no-cov -o addopts='' --runxfail tests/test_web_perf_smoke.py -k 'repeated_projection_reads_are_stable_and_side_effect_free'
    uv run pytest -q --no-cov -o addopts='' tests/test_web_perf_smoke.py

结果：精确节点 strict **1 xfailed in 11.46s**；`--runxfail` **1 failed in 11.47s**；完整
web perf smoke **2 passed、1 xfailed in 24.45s**。第二次及后续 `_kanban` 结果与首次
baseline 不相等，违反重复只读 projection 稳定性；无普通失败、collection error 或 timeout，
未访问 Provider/网络，生产代码未修改，Issue 继续保持 `open`。

- [x] 保留首次 ALL 失败证据，未将一次性失败改写成绿色；
- [x] 通过源码、既有 freshness 测试和新增 S6 合约测试确认：普通
  `/api/events` 允许 bounded-stale，并以 `tail_behind`/水位显式告知；
- [x] 将原用例改为使用项目作用域 events 路由的 `require_fresh=True` 断言，
  新增无 sleep 的普通读取与 fresh 屏障测试；
- [x] 未修改 Web/读模型生产代码，未添加 xfail 或自动重试；
- [x] 已补注册隔离 workspace 后的 API-level fresh 路由测试，检查水位、事件、
  lag 与 EventLog 不变；此前 409 来自无 `zf.yaml` 的未初始化测试场景。
- [ ] 原失败没有保存响应的 freshness 字段和 fallback 来源；而前置
  `/api/channels` 本身会请求 fresh，因此普通读取允许滞后并不足以证明首次
  失败的唯一根因。保留历史证据和本记录，不据通过复跑宣称原始时序问题已解决。
- [x] 2026-09-13 当前 HEAD 的 ALL 联调再次出现同一 freshness case 的单次失败：
  `/tmp/zf-testkit-all-cli-flow-final-yoIJxC` 中 `1271 passed`、`36 strict xfailed`、
  `1 failed`；随后 S6 独立重跑与下一次 ALL（`/tmp/zf-testkit-all-final-baseline-QLANbC`）
  均通过。该证据追加到本 Issue，仍按间歇性读模型时序观察处理，未新增 xfail 或修改生产代码。

### 2026-09-21 K341 当前 HEAD 独立进程重复复核

- [x] 先执行单次基线：目标 node `1 passed in 1.57s`。
- [x] 随后启动 30 次相互独立的 pytest 进程，不使用自动重试、并发复用或 xfail；结果为
  **30 passed / 0 failed**，逐次日志保留于 `/tmp/zf-zf009-k341-20260921-lCWF3j/`。
- [x] 同批次完整 `TestApiChannels` 类为 **22 passed in 8.81s**；无 collection error 或
  timeout，未访问真实 Provider/网络，未修改生产代码。
- [ ] 本轮未再次复现首次 ALL 联调的空列表时序；这只是降低当前批次复现概率，不能证明
  freshness/catch-up 根因已消失，Issue 继续保持 `open`，仍需保留后续组合联调观察。

### 2026-09-22 K442 bounded-stale 契约分层

- [x] 新增 `test_legacy_events_route_marks_bounded_stale_reads`：重建 baseline 后追加事件，
  拦截异步 catch-up；旧 `/api/events` 返回空页时仍明确带有 `projection_state=ready`、
  `tail_behind=true`、`source=read_model.sqlite`，并请求本项目的 catch-up。**1 passed**。
- [x] 原始 Channel caller、S6 projection 与 freshness 合同联调：**10 passed、3 strict
  xfailed**；已知 xfail 仍对应 `ZF-TEST-012/022`，ruff 通过。
- [x] 新目录 S0：`/tmp/zf-s0-k442-closeout-mvNOce/pytest-testkit.json`，**11/11 passed**，
  exit=0、`verdict=pass`、`session_finished=true`；JSON 与 `git diff --check` 通过。
- [x] 初版“一次调度”的断言受到 app prewarm 影响，已修正为至少一次且 state_dir 一致；
  这是测试夹具时序假设，不是新增生产 Issue。
- [x] 日志：`/tmp/zf-k442-zf009-bounded-stale-20260922-rerun-2/new-contract.log`、
  同目录 `callers.log`；生产代码未修改。
- [ ] 本合同只证明受控 bounded-stale 空页是允许行为。历史 ALL 失败缺少响应元数据，且
  存在前置 fresh 请求，不能用本次通过证明其唯一根因；`ZF-TEST-009` 继续 open。

### 2026-09-22 K447 moving-watermark fresh route contract

- [x] 新增项目作用域 fresh-route 合同：在 fresh rebuild 完成后追加事件，首次响应只返回
  屏障前事件，并以响应最后一项的 seq 作为 `current_seq`；下一次 fresh 请求才返回追加事件。
- [x] 直接调用方联调：新增合同与 S6 projection、read-model freshness、single-flight 及
  Web channel caller 共 **41 passed、3 strict xfailed、115 deselected**；日志：
  `/tmp/zf-k447-zf009-1790079888148/callers.log`。
- [x] 完整 S6：**917 reported：909 passed、8 strict xfailed**，无普通失败、collection error、
  deselection 或 timeout；报告：`/tmp/zf-s6-k447-zf009/pytest-testkit.json`。
- [x] 新目录 S0：`/tmp/zf-s0-k447-closeout/pytest-testkit.json`，**11/11 passed**，
  `exit_code=0`、`verdict=pass`、`session_finished=true`。
- [ ] 本合同只固定响应与 fresh 屏障水位的绑定，不证明历史 ALL 空页时序的唯一根因；
  `ZF-TEST-009` 继续保持 `open`。

## ZF-TEST-010：S6 组合运行中 read-model SQLite WAL 初始化偶发 database locked

> 状态: open（未分类的间歇性观察）

### 发现阶段

S6 最新完整阶段 runner：

    /tmp/zf-testkit-s6-final-170420

发现测试：

    tests/test_web_server.py::TestApiChannels::test_project_channel_conversation_prefers_channel_read_model_slice

### 现象与期望

测试在隔离 `state_dir` 写入两个 channel 事件，并调用
`read_model.rebuild(state_dir)` 建立索引。期望 SQLite WAL 初始化和 rebuild
稳定完成，随后 API 使用 read-model slice。

### 当前结果

一次完整 S6 组合运行中，`read_model._connect()` 在执行
`PRAGMA journal_mode=WAL` 时抛出：

    sqlite3.OperationalError: database is locked

该 runner 共 200 个结果，其中 199 passed、1 failed；无 collection error、
deselection 或 timeout。单独连续 3 次运行该测试、以及整个 `TestApiChannels`
类的 22 个测试均通过，因此当前只能确认组合时序下的间歇性现象，不能断言
为已稳定复现的生产缺陷。

### 当前处理

- [x] 保留完整 runner 证据和失败节点；
- [x] 单测与测试类复跑通过，未用自动重试或 xfail 改写首次失败；
- [x] 第二次完整 S6 组合 runner `/tmp/zf-testkit-s6-rerun-170827` 的 200 个
  case 全部通过，未再次触发 WAL 锁；这降低了复现频率但不能证明问题已消失；
- [x] 未修改 ZaoFu 生产代码；
- [ ] 尚未确定是后台 catch-up 线程、SQLite 跨测试资源竞争还是其他时序原因；
- [ ] 尚未进入 `/issue → Fix Run → 回归 → merge` 流程。

### 2026-09-21 独立进程重复取证

- 对该 nodeid 启动 30 次相互独立的 pytest 进程，不使用自动重试或 xfail：
  `/tmp/zf-issue010-repeat-20260921/results.txt`。
- 结果为 **29 passed / 1 failed**；失败为第 29 次，日志保存在
  `/tmp/zf-issue010-repeat-20260921/29.log`。
- 失败仍发生在 `src/zf/web/projections/read_model.py::_connect` 的
  `PRAGMA journal_mode=WAL`，异常为 `sqlite3.OperationalError: database is locked`，
  单次失败耗时约 8.25 秒。
- 该批次证明问题可以在独立进程重复运行中低频复现，支持“SQLite WAL 初始化/读模型资源
  竞争时序”假设；尚不能仅凭此确定唯一根因，也没有把 29 次通过当作修复证据。
- 同批次 ZF-TEST-009 为 **30 passed / 0 failed**，因此没有把两个问题合并或互相外推。

### 2026-09-21 K246 根因分层取证（仍 open）

新增诊断测试：

    tests/test_zf_test010_root_cause_matrix.py

控制组和启动时序组均在隔离 `state_dir` 上执行。控制组同时启动两个显式
`read_model.rebuild()`，结果无异常，说明单纯并发 rebuild 会被现有 `locked_path` 串行化，
不能单独解释 WAL 初始化失败。启动时序组通过真实 `create_app()` 启动
`zf-web-prewarm`，在其进入 `read_model._connect()` 时短暂保持连接，再启动显式
`rebuild()`；结果确认两条路径同时作用于同一 `read_model.sqlite`：

    {"prewarm_connection_held": true,
     "main_rebuild_started_while_prewarm_blocked": true,
     "main_result": "ready"}

原始证据：

    /tmp/zf-issue010-rootcause-20260921/prewarm-overlap.json
    /tmp/zf-issue010-rootcause-20260921/rootcause-impact.txt

这把“后台 prewarm / read-model 连接生命周期竞争”从源码推断提升为可达时序证据，
但人工保持连接的诊断实验没有重新触发 `database is locked`，因此不能宣称唯一根因，
也不能替代独立进程批次中未插桩的真实失败。当前仍建议维护者检查：

1. `projection_status()` 未持有 read-model 文件锁时的 `_connect()` 生命周期；
2. `create_app()` 的 `zf-web-prewarm` 与显式 `rebuild()` 的交错窗口；
3. `PRAGMA journal_mode=WAL` 执行早于 `busy_timeout` 设置的错误边界；
4. 失败连接/后台线程是否在异常或测试清理路径中残留。

当前处理：

- [x] 完成控制组、启动时序组和 40-case 影响闭包；
- [x] 保留未插桩独立进程 `29 passed / 1 failed` 失败样本；
- [x] 未修改生产 `src/`，未通过重试、xfail 或 instrumentation 改写结果；
- [ ] 尚未完成唯一根因确认、维护者修复和 `--runxfail` 回归。

### 2026-09-21 K299 独立进程同步 WAL 初始化最小竞争面（仍 open）

在 K246 的人工 prewarm/rebuild 时序和 K247 的 prewarm 开关对照之外，新增一个不依赖
Web fixture 的最小跨进程探针：5 轮、每轮 8 个独立进程，用共享 barrier 同时首次调用
`read_model._connect()` 打开同一新建 `read_model.sqlite`，要求所有初始化连接成功并关闭。

检测入口：

    tests/test_zf_test010_root_cause_matrix.py::test_zf_test010_independent_wal_initializers_fail_closed_without_lock_errors

结果：

- strict 模式：该节点 **1 xfailed**；同文件完整矩阵为 **3 passed、1 xfailed**；
- `--runxfail`：同文件完整矩阵为 **3 passed、1 failed**；失败节点在 5 轮竞争中多轮收到
  `sqlite3.OperationalError: database is locked`（例如第 0/1/2/3/4 轮均有失败连接）；
- 失败发生于真实 `_connect()` 的 `PRAGMA journal_mode=WAL` 初始化边界，未执行 Web 请求、
  Provider、远端写入或生产修复；所有子进程均在测试 finally 中回收。

该证据把历史低频 S6 现象缩小为“多个独立进程首次初始化同一 WAL 数据库”的可重复竞争面，
但不能单独证明 Web prewarm 是唯一根因，也不能替代原始 29/30 独立进程生产形态取证。维护者
仍需检查 WAL 初始化前后的跨进程锁协议、失败连接生命周期以及修复后的原始 S6/独立进程回归。

当前处理：

- [x] 新增精确 strict-xfail 检测、独立进程 barrier、5 轮结果和子进程清理断言；
- [x] 已用 `--runxfail` 裸红确认 `database is locked`，并保留同文件 3 个绿色控制组；
- [x] 未修改生产 `src/`，未把诊断竞争面扩大为新的 Issue ID；
- [ ] 尚未完成唯一根因确认、维护者修复及修复后的 S6/原始独立进程回归。

### 2026-09-21 K342 当前 HEAD WAL 竞争矩阵复核

- [x] 完整 `tests/test_zf_test010_root_cause_matrix.py` strict 结果为 **3 passed、1 xfailed**；
  `--runxfail` 结果为 **3 passed、1 failed**，无 collection error 或 timeout。
- [x] 裸红节点仍在 5 轮 × 8 独立进程同步首次 WAL 初始化中捕获多个
  `sqlite3.OperationalError: database is locked`；并发 rebuild 串行控制组、prewarm overlap
  诊断和后台 catch-up 控制组均通过。
- [x] 证据表明跨进程 WAL 初始化竞争面在当前 HEAD 仍存在；未修改生产代码、未访问真实
  Provider/远端服务，保留既有测试和 Issue open 状态。
- [ ] 本阶段仍未证明唯一根因，也未进入 `/issue → Fix Run → 回归 → merge` 流程；维护者修复后
  必须用同一 `--runxfail` 矩阵验证无 XPASS/裸红。

### 2026-09-22 K391 当前 HEAD 裸红复核

- [x] 精确节点 strict **1 xfailed in 1.35s**；`--runxfail` **1 failed in 1.29s**，5 轮×8
  独立进程中第 2–4 轮再次出现多项 `database is locked`，所有子进程均清理。
- [x] root-cause/channel caller：组合 **1 passed、1 xfailed、123 deselected in 2.43s**；
  `TestApiChannels` **22 passed in 8.30s**，未把 API 绿灯当作竞争修复证据。
- [x] 证据日志：`/tmp/zf-k391-zf010-strict.log`、`/tmp/zf-k391-zf010-runxfail.log`、
  `/tmp/zf-k391-zf010-callers.log`、`/tmp/zf-k391-zf010-channel-callers.log`；未修改生产代码、
  未访问 Provider/网络或远端写入。

### 2026-09-21 K247 prewarm 开关与后台 catch-up 对照（仍 open）

为避免把 K246 的可达重叠窗口直接当成因果根因，固定原始 ZF-TEST-010 nodeid 做了两组
独立进程对照：

- 默认 `create_app()`（启用 `zf-web-prewarm`）：20 个独立进程，**20/20 passed**；
  证据 `/tmp/zf-issue010-rootcause-20260921/baseline-repeat.jsonl`。
- 测试插件仅禁用 `zf-web-prewarm`：20 个独立进程，**20/20 passed**；证据
  `/tmp/zf-issue010-rootcause-20260921/no-prewarm-repeat.jsonl`。

两组都没有重现 `database is locked`。因此当前证据不能支持“禁用 prewarm 即可修复”，
也不能把对照组全绿解释为历史失败已经消失。另有后台 `request_catch_up()` 与显式
`rebuild()` 控制组，和 Web/S6 影响闭包共 **41 passed**，输出为：

    /tmp/zf-issue010-rootcause-20260921/rootcause-impact-k247.txt

当前处理：

- [x] 完成默认/禁用 prewarm 的独立进程对照及逐次日志保存；
- [x] 完成后台 catch-up + 显式 rebuild 控制组及 41-case 影响闭包；
- [x] 未修改生产 `src/`，未启用自动重试或 `--runxfail` 改写结果；
- [ ] 唯一根因、维护者修复和修复后回归仍未完成。

### 2026-09-22 K438 当前 HEAD WAL 根因矩阵与独立重复复核

- [x] 根因矩阵 `tests/test_zf_test010_root_cause_matrix.py`：**3 passed、1 strict xfailed**；
  strict xfail 仍是五轮×八进程独立 WAL initializer 的 `database is locked` 检出，不改写
  为通过。
- [x] 原始 Web channel 节点使用 `--runxfail` 单次运行：**1 passed**；同一调用方与
  read-model freshness/根因矩阵联调为 **32 passed、2 strict xfailed、102 deselected**。
- [x] 同一原始节点再做 **20 个独立 pytest 进程：20 passed、0 failed**；每次单独保存日志，
  未使用自动重试或 xfail 掩盖结果。该批次未触发低频 API 复现，只能作为干净对照，不能证明
  缺陷已修复。
- [x] S0 收尾 `/tmp/zf-s0-k438-zf010-closeout/pytest-testkit.json`：**11/11 passed**，
  `exit_code=0`、`verdict=pass`、`session_finished=true`，无 collection error、deselection、
  timeout 或 evidence error。
- [x] 未修改 `src/`/`web/src/`，未启动 Provider/tmux、未触碰 8001、未写远端；Issue 继续
  `open`，仍需维护者确认唯一根因、修复并用同一 `--runxfail` 与影响闭包回归。

### 2026-09-22 K445 WAL 初始化失败阶段精确化

- [x] 新增 phase-aware 独立进程诊断：`connect → journal_mode=WAL → synchronous →
  busy_timeout`；3 轮×8 进程中失败均明确为 `phase=journal_mode_wal`，异常为
  `sqlite3.OperationalError: database is locked`。strict **1 xfailed**，`--runxfail`
  **1 failed**。
- [x] 原有 `tests/test_zf_test010_root_cause_matrix.py`：strict **3 passed、2 xfailed**；
  `--runxfail` **3 passed、2 failed**。Web/read-model/Channel 调用方：**39 passed、3
  strict xfailed**，无新增普通失败。
- [x] 失败阶段诊断未执行 Web/Provider/远端写入，不改生产代码；证据日志：
  `/tmp/zf-k445-zf010-phase-4lyCes/strict.log`、`runxfail.log`、`matrix-strict.log`、
  `matrix-runxfail.log`、`callers.log`。
- [x] 新目录 S0：`/tmp/zf-s0-k445-closeout-OsUnKV/pytest-testkit.json`，**11/11 passed**，
  `exit_code=0`、`verdict=pass`、`session_finished=true`。
- [ ] 该证据只把问题收窄到 WAL pragma 初始化阶段，不能证明唯一根因或修复；
  `ZF-TEST-010` 继续保持 `open`。

## ZF-TEST-008：TestKit 并发写 evidence 时 PID 临时文件名冲突（已解决）

> 状态: resolved

### 发现阶段

S1 TestKit 基础设施并发 evidence 写入测试。

发现测试：

    tests/test_testkit_s1.py::test_atomic_evidence_writes_are_collision_free_under_same_process_concurrency

复现命令：

    uv run pytest -q tests/test_testkit_s1.py::test_atomic_evidence_writes_are_collision_free_under_same_process_concurrency --no-cov

### 复现条件

1. 同一进程内启动两个线程；
2. 两个线程同时调用 `tests.testkit.evidence.write_json_atomic` 写入同一个 evidence 路径；
3. 在临时文件写入完成后用 barrier 同步两个线程，再执行原子替换。

### 期望结果

每个并发写入都应安全完成，最终 evidence 是完整 JSON，临时文件不会互相覆盖或导致
`FileNotFoundError`；调用方应能获得两个有效写入结果或明确的受控冲突。

### 发现时结果

发现时实现的临时文件名是 `.result.json.<pid>.tmp`，同一进程的两个线程得到同一个路径。
一个线程执行 `os.replace` 后，另一个线程找不到源临时文件而失败；该现象由原严格
xfail 稳定复现。

### 影响

- 并发 TestKit runner 或 evidence collector 可能丢失报告；
- 失败 bundle、receipt 或最终 `testkit-run.json` 可能出现写入错误；
- 该问题属于测试基础设施可靠性缺陷，不应被误判为 ZaoFu 产品行为失败。

### 建议修复方向（已采用）

- 临时文件名加入线程/随机唯一后缀，并保留目标路径的原子替换语义；
- 或对同一目标路径建立明确的进程内/跨进程写入协调；
- 增加多进程、同进程线程和不同目标路径的 evidence 写入矩阵。

### 当前处理

- [x] 已保留确定性 barrier 并发检出测试；
- [x] 已在 `tests/testkit/evidence.py` 使用每次写入唯一临时文件名，避免同进程
  多线程覆盖；该修复只涉及 TestKit 支撑层，不修改 ZaoFu 生产运行时代码；
- [x] 原严格 xfail 已转为正常并发断言，S1 runner 验证通过；
- [x] 已完成本地回归并记录在 `steps.md`，不执行远端 push/merge；
- [x] 不再作为当前开放缺陷计入 coverage matrix。

## ZF-TEST-007：派发事件写入前崩溃仍会提前推进 TaskStore 状态

> 状态: open（确定性复现；未修改生产代码）

### 发现阶段

S4 恢复与提交点故障测试。

发现测试：

    tests/conformance/test_zaofu_testkit_s4.py::test_crash_before_dispatch_event_leaves_task_for_one_retry

复现命令：

    uv run pytest -q tests/conformance/test_zaofu_testkit_s4.py::test_crash_before_dispatch_event_leaves_task_for_one_retry --no-cov

### 复现条件

1. 创建一个状态为 `backlog` 的任务；
2. 使用真实 `Orchestrator.run_once()`；
3. 在 `event_writer.append(task.dispatched)` 调用前注入进程故障；
4. 读取同一个 canonical `TaskStore`。

### 期望结果

事件尚未写入 EventLog 时，TaskStore 应仍保持 `backlog`；重启后应写入一次
`task.dispatched` 并将任务推进到 `in_progress`，不能出现没有事件依据的状态变化。

### 当前结果

在事件 append 前抛出故障后，TaskStore 已经变为 `in_progress`，但 EventLog 中没有
`task.dispatched`。因此状态与事件账本不一致；测试以严格 xfail 保留该检出。

### 影响

- 崩溃窗口可能产生无事件依据的派发状态；
- replay、恢复和审计无法从 EventLog 重建该状态；
- 重启后的 dispatch 判定可能与无故障 baseline 不一致。

### 建议修复方向（不在本测试任务中实施）

- 将 TaskStore 状态推进与 `task.dispatched` 事件写入放入可恢复的提交协议；
- 或在事件写入失败时回滚 canonical dispatch 字段并保留可重试标记；
- 修复后移除 xfail，并要求 pre-append crash、post-append crash 与 baseline
  三者的最终状态和事件序列符合契约。

### 当前处理

- [x] 已增加真实 Orchestrator 提交点测试；
- [x] 已保留严格 xfail 和复现命令；
- [x] 未修改生产代码；
- [ ] 尚未修复；
- [ ] 尚未进入 `/issue → Fix Run → 回归 → merge` 流程。

### 2026-09-21 K331 当前 HEAD 裸红复核

复核命令：

    uv run pytest -q --no-cov -o addopts='' tests/conformance/test_zaofu_testkit_s4.py -k 'real_orchestrator_pre_dispatch_crash_restarts_to_baseline or crash_before_dispatch_event_leaves_task_for_one_retry'
    uv run pytest -q --no-cov -o addopts='' --runxfail tests/conformance/test_zaofu_testkit_s4.py -k 'real_orchestrator_pre_dispatch_crash_restarts_to_baseline or crash_before_dispatch_event_leaves_task_for_one_retry'
    uv run pytest -q --no-cov -o addopts='' tests/conformance/test_zaofu_testkit_s4.py

结果：精确双节点 strict **2 xfailed in 4.94s**；`--runxfail` **2 failed in 4.89s**；完整
S4 **13 passed、5 xfailed in 78.70s**。`task.dispatched` append 前故障会使 TaskStore
先变为 `in_progress`，但 EventLog 没有对应事件；独立子进程恢复序列也与无故障 baseline
不等价。无 XPASS、collection error 或 timeout，未访问 Provider/网络，生产代码未修改，
Issue 继续保持 `open`。

### 2026-09-21 K348 当前 HEAD pre-dispatch crash 精确复核

- [x] 精确节点 strict **1 xfailed in 1.94s**；`--runxfail` **1 failed in 1.92s**。在
  `task.dispatched` append 前注入故障后，canonical TaskStore 已为 `in_progress`，而
  EventLog 没有该事件，仍违反状态/账本原子性。
- [x] 该节点与 K347 的完整 S4 结果联调：完整 S4 **13 passed、5 xfailed in 82.39s**，
  其它 recovery、TaskAttempt 和 child-process 控制通过；无 collection error 或 timeout。
- [x] 未修改生产代码、未访问 Provider/网络；Issue 继续 `open`，维护者修复后需以同一
  pre-append crash、重启和 baseline 矩阵回归。

## ZF-TEST-006：CandidateRebuilder 重放使用墙上时钟导致 candidate commit 漂移

> 状态: open（确定性复现；未修改生产代码）

### 发现阶段

S8 候选生成与恢复阶段的重启重放测试。

### 2026-09-21 K332 当前 HEAD 裸红复核

复核命令：

    uv run pytest -q --no-cov -o addopts='' tests/e2e/test_zaofu_testkit_s8_e2e.py -k 'candidate_rebuild_replay_after_restart_preserves_commit_lineage'
    uv run pytest -q --no-cov -o addopts='' --runxfail tests/e2e/test_zaofu_testkit_s8_e2e.py -k 'candidate_rebuild_replay_after_restart_preserves_commit_lineage'
    uv run pytest -q --no-cov -o addopts='' tests/e2e/test_zaofu_testkit_s8_e2e.py

结果：精确节点 strict **1 xfailed in 2.53s**；`--runxfail` **1 failed in 2.49s**；完整
S8 **8 passed、1 xfailed in 5.66s**。跨越 1.1 秒后，同一 candidate 的首次与重启重放
commit hash 不同，commit lineage 断言失败；无 XPASS、collection error 或 timeout，未访问
Provider/网络，生产代码未修改，Issue 继续保持 `open`。

发现测试：

    tests/e2e/test_zaofu_testkit_s8_e2e.py::test_candidate_rebuild_replay_after_restart_preserves_commit_lineage

复现命令：

    uv run pytest -q tests/e2e/test_zaofu_testkit_s8_e2e.py::test_candidate_rebuild_replay_after_restart_preserves_commit_lineage --no-cov

### 复现条件

1. 在隔离 git 仓库创建一个 approved task ref；
2. 第一次调用 `CandidateRebuilder.rebuild()` 创建 candidate branch；
3. 等待跨过至少一个 git commit timestamp 秒；
4. 用新 `EventLog`/`CandidateRebuilder` 实例、同一 state dir 再次重放。

### 期望结果

同一组 canonical task refs、base ref 和输入在重启重放后应保留相同的
candidate commit、commit lineage 和 manifest 身份。

### 当前结果

第二次 rebuild 仍返回 `updated`，但 candidate branch 的 commit hash 与第一次
不同（`first_commit != second_commit`），即使 source commit、文件内容和 task
lineage 相同。原因是候选工作树的 cherry-pick commit 使用当前墙上时间，导致
同一输入的 Git commit 身份随重放时间变化。测试用显式 1.1 秒边界并以严格
xfail 保留该检出；XPASS 也会使门禁失败，提示需要移除 xfail。

### 影响

- candidate ref 不能稳定地作为 Product Acceptance/evidence 的内容身份；
- 重启后 target commit 变化，可能使验收报告、digest 或远端候选绑定失效；
- 相同事件重放不再得到相同 candidate，削弱恢复、审计和回滚能力。

### 建议修复方向（不在本测试任务中实施）

- 让候选提交的 author/committer 时间和其他提交元数据来自 canonical 输入或
  固定策略，而不是重放时的墙上时钟；
- 或在已有相同 source commit/lineage 时复用已持久化 candidate commit；
- 修复后移除 xfail，并要求跨重启 candidate commit 与 lineage 精确一致。

### 当前处理

- [x] 已增加跨秒边界，确保缺陷可确定复现；
- [x] 已保留重启后 commit/lineage 精确断言；
- [x] 未修改生产代码；
- [ ] 尚未修复；
- [ ] 尚未进入 `/issue → Fix Run → 回归 → merge` 流程。

## ZF-TEST-005：Issue Fix Run 实现任务在运行时因缺失 base_commit 无法派发（测试夹具误报）

> 状态: verified-resolved（2026-09-12 复核确认：原用例缺少 Git 基线，非生产缺陷）

### 发现阶段

S9 Issue 回归—Fix Run—候选交付阶段的真实 Kernel 运行测试。

发现测试：

    tests/conformance/test_zaofu_testkit_s9_fixrun.py::test_runtime_issue_fix_run_dispatches_implementation_to_provider

复现命令：

    uv run pytest -q tests/conformance/test_zaofu_testkit_s9_fixrun.py::test_runtime_issue_fix_run_dispatches_implementation_to_provider --no-cov

### 复现条件

1. 通过 `ControlledActionService` 提交一个 `kind=issue` 的 workflow request；
2. 使用真实 `Orchestrator.run_once()` 消费 `workflow.invoke.requested`；
3. 让 Kernel 生成 Issue workflow 的 task map 和 implementation fanout；
4. 使用隔离 state dir 和 mock transport，不伪造 fanout 或 Provider 结果。

### 期望结果

Issue Fix Run 生成的实现子任务应完成派发准备，产生唯一的
`fanout.child.dispatched`，并调用配置的 mock Provider；不能把实现任务静默
留在 deferred 状态。

### 复核结果

原始用例没有在临时产品根目录建立 Git 仓库，因此 `base_commit` 缺失，触发了预期的
fail-closed preparation error。该环境不满足产品工作树的前置契约，不能据此认定为
ZaoFu 生产缺陷。

修正夹具后先创建隔离 Git 仓库并提交 baseline，再执行同一
`workflow-request` → `workflow-submit` → `Orchestrator.run_once()` 路径；测试稳定产生
唯一 `fanout.child.dispatched`，并记录 mock Provider 收到实现任务：

    tests/conformance/test_zaofu_testkit_s9_fixrun.py::test_runtime_issue_fix_run_dispatches_implementation_to_provider

因此该用例已移除 strict xfail；未修改 ZaoFu 生产代码。缺失 Git 基线仍由测试夹具和
环境前置检查负责报告为 harness/environment failure，而不是产品失败。

### 影响

- 若产品工作树确实缺失 Git 基线，运行时会正确 fail-closed，不能进入实现阶段；
- 产品工作树满足 Git 基线后，当前运行时路径可以派发实现任务并取得 Provider receipt。

### 建议修复方向（不在本测试任务中实施）

- 在 Issue task-map 生成或 dispatch preparation 前绑定并校验当前
  `base_commit`；
- 让 task contract snapshot 的身份字段与运行合同保持一致；
- 修复后移除本测试的 xfail，并要求真实 Provider dispatch 与唯一事件通过。

### 当前处理

- [x] 已确认原始失败来自测试夹具缺少 Git baseline，而非生产实现；
- [x] 已补充有效产品工作树前置条件并移除误报 xfail；
- [x] 修正后的真实 Kernel→mock Provider 派发测试通过；
- [x] 未修改 ZaoFu 生产代码；
- [x] 该 Issue 关闭为 verified-resolved，不进入生产修复流程。

## ZF-TEST-001：签名 EventLog 的 append-fold 缓存可能掩盖同前缀篡改

> 状态: open（确定性复现；未修改生产代码）

### 发现阶段

S2 EventLog/Schema/evidence conformance 测试。

发现 commit:

    bed9d732af7ddc1e5a11d97d6fa1edb7d0694b5e

测试 case:

    tests/conformance/test_zaofu_testkit_s2.py::test_signed_event_roundtrip_and_tamper_rejection
    tests/conformance/test_zaofu_testkit_s2.py::test_signed_same_size_tamper_rejection_after_fold_cache
    tests/conformance/test_zaofu_testkit_s2.py::test_signed_same_size_tamper_rejection_with_preserved_mtime
    tests/conformance/test_zaofu_testkit_s2_multiprocess.py::test_signed_reader_rejects_same_size_rewrite_while_writer_holds_lock

### 复现条件

1. 使用 EventLog(path, signer=EventSigner(...)) 写入一个已签名事件；
2. 调用 read_all()，使进程内 exact cache 和 append-fold cache 建立；
3. 在不改变已有文件前缀的情况下，重写 active events.jsonl 内容；
4. 再次调用 EventLog(path, signer=同一 signer).read_all()；
5. 当文件大小发生追加式变化时，append-fold 路径可能只读取尾部，而不重新验签被重写的前缀。

最小复现命令:

    uv run pytest -q tests/conformance/test_zaofu_testkit_s2.py::test_signed_event_roundtrip_and_tamper_rejection --no-cov

当前 case 使用严格 xfail 记录已知问题；生产代码未在本轮修复。

### 观察到的结果

期望：

    签名被篡改后，read_all() 不应返回原始有效事件；
    应返回空结果、event.malformed 或明确的签名校验失败。

当前实现：

    在缓存命中/append-fold 条件满足时，read_all() 可能返回缓存中的原始事件，
    从而把已被重写的事件继续当作有效事件提供给上层读取者。

### 影响

- 签名 EventLog 的 fail-closed 保护可能被进程内缓存绕过；
- 依赖 read_all() 的 projector、诊断、Web/API 或恢复逻辑可能读取过期/被篡改的事件；
- 事件账本的真实性和 replay 结论不再可靠。

### 证据

- 复现测试：tests/conformance/test_zaofu_testkit_s2.py
- 相关实现：src/zf/core/events/log.py 的 read_all()、_READ_ALL_CACHE、_READ_ALL_FOLD；
- 首次验证：临时修复版本 focused run 为 33 passed；撤回修复后该 case 按预期暴露为 xfail。

### 建议修复方向（不在本测试任务中实施）

后续由独立 Issue/Fix Run 评审以下方向之一：

- 为 append-fold 保存并校验已消费 active 前缀摘要，只对真正追加启用 tail decode；
- 或在签名日志读取时禁用不具备完整前缀完整性证明的 fold cache；
- 增加 same-size、size-growth、mtime 不变和跨进程读取的签名篡改回归矩阵；当前 TestKit 已覆盖这些形式，其中跨进程 live-reader/持锁重写 case 仍以严格 xfail 保留；
- 修复后移除该 case 的 xfail，并要求严格 PASS。

### 当前处理

- [x] 已保留确定性复现测试；
- [x] 已撤回本轮生产代码修复；
- [x] 已记录影响、证据和建议方向；
- [ ] 尚未修复；
- [ ] 尚未进入 /issue → Fix Run → 回归 → merge 流程。

### 2026-09-21 K333 当前 HEAD 裸红复核

复核命令：

    uv run pytest -q --no-cov -o addopts='' tests/conformance/test_zaofu_testkit_s2.py -k 'signed_event_roundtrip_and_tamper_rejection or signed_same_size_tamper_rejection_after_fold_cache or signed_same_size_tamper_rejection_with_preserved_mtime'
    uv run pytest -q --no-cov -o addopts='' --runxfail tests/conformance/test_zaofu_testkit_s2.py -k 'signed_event_roundtrip_and_tamper_rejection or signed_same_size_tamper_rejection_after_fold_cache or signed_same_size_tamper_rejection_with_preserved_mtime'
    uv run pytest -q --no-cov -o addopts='' tests/conformance/test_zaofu_testkit_s2_multiprocess.py -k 'signed_reader_rejects_same_size_rewrite_while_writer_holds_lock'
    uv run pytest -q --no-cov -o addopts='' --runxfail tests/conformance/test_zaofu_testkit_s2_multiprocess.py -k 'signed_reader_rejects_same_size_rewrite_while_writer_holds_lock'

结果：进程内三节点 strict **3 xfailed**、`--runxfail` **3 failed**；跨进程节点 strict
**1 xfailed**、`--runxfail` **1 failed**。同前缀、同长度、保留 mtime 及持锁跨进程重写后，
reader 均仍返回旧签名事件。S2 两文件组合为 **26 passed、4 xfailed in 5.15s**；无普通
失败、collection error 或 timeout，未访问 Provider/网络，生产代码未修改，Issue 继续保持
`open`。

## ZF-TEST-013：EventLog 归档碰撞在活动文件删除前崩溃会重复事件

> 状态: open（确定性复现；未修改生产代码）

### 发现阶段

S4 生产持久化崩溃边界测试：

### 2026-09-21 K334 当前 HEAD 裸红复核

复核命令：

    uv run pytest -q --no-cov -o addopts='' tests/conformance/test_zaofu_testkit_s4_persistence.py -k 'archive_collision_crash_recovers_without_duplicate_events'
    uv run pytest -q --no-cov -o addopts='' --runxfail tests/conformance/test_zaofu_testkit_s4_persistence.py -k 'archive_collision_crash_recovers_without_duplicate_events'
    uv run pytest -q --no-cov -o addopts='' tests/conformance/test_zaofu_testkit_s4_persistence.py

结果：精确节点 strict **1 xfailed in 0.90s**；`--runxfail` **1 failed in 0.90s**；完整持久化
模块 **2 passed、1 xfailed in 2.00s**。归档写入完成、活动文件删除前崩溃，重启后的事件 ID
为 `archive-seed, active-seed, active-seed, dispatch`，重复了 `active-seed`。无 collection
error 或 timeout，未访问 Provider/网络，生产代码未修改，Issue 继续保持 `open`。

    tests/conformance/test_zaofu_testkit_s4_persistence.py::test_archive_collision_crash_recovers_without_duplicate_events

复现命令：

    uv run pytest -q --no-cov tests/conformance/test_zaofu_testkit_s4_persistence.py

### 复现条件

1. 活动 `events.jsonl` 与同日期归档文件同时存在；
2. `EventLog.append()` 进入 `rotate_if_needed()` 的归档碰撞分支；
3. 归档拼接写入完成后、`active.unlink()` 前进程崩溃；
4. 使用同一 state dir 重启并再次追加事件。

### 期望结果

恢复后事件 ID 应为 `archive-seed`、`active-seed`、`dispatch` 各一次，且活动文件中的
旧内容不应再次拼接；重启结果应与无故障执行等价。

### 当前结果

测试以严格 xfail 检出：首次崩溃留下活动文件和已拼接归档，恢复时碰撞分支再次拼接
活动内容，导致 `active-seed` 重复。该结果违反 EventLog 追加账本的唯一事件和恢复等价
不变量。

### 影响

- 事件查询和基于事件的投影可能重复处理同一事实；
- 重复事件可能触发重复恢复、指标或下游副作用；
- 归档碰撞窗口中的崩溃不能保证幂等恢复。

### 当前处理

- [x] 已增加真实 `EventLog.append()` 子进程崩溃/重启测试；
- [x] 已保留严格 xfail，避免隐藏生产缺陷；
- [x] 未修改生产代码；
- [ ] 尚未进入 /issue → Fix Run → 回归 → merge 流程。

## ZF-TEST-004：动态 General Workflow 生成的 mock on-demand role 无法通过启动前检查

> 状态: open（确定性复现；未修改生产代码）

### 发现阶段

S6 隔离四流程 Kanban 浏览器 E2E 启动阶段。

发现测试：

    tests/e2e/scripts/run_four_flow_kanban_playwright_e2e.sh

复现命令（使用临时端口和脚本默认 fake provider）：

    timeout 300s tests/e2e/scripts/run_four_flow_kanban_playwright_e2e.sh --port 8002

### 复现条件

1. 脚本为临时项目执行 General Workflow synthesis/config apply；
2. 生成的 workflow 包含 `external-issue-triage` 和
   `external-issue-triage-verifier` 两个 `backend=mock` role；
3. 两个 role 的 lifecycle mode 为 `on_demand`；
4. 运行 `zf start`，尚未进入 Playwright 浏览器断言。

### 期望结果

用于 deterministic fake-provider E2E 的生成配置应能通过启动前检查并进入浏览器测试，或在生成阶段明确给出可执行的 provider/session 配置。

### 当前结果

`zf start` 在 runtime startup 阶段退出，静态 dispatch preflight 报告：

    role 'external-issue-triage' backend='mock' cannot use lifecycle.mode=on_demand without native session resume
    role 'external-issue-triage-verifier' backend='mock' cannot use lifecycle.mode=on_demand without native session resume

同时报告 `external-issue-triage-verify.completed` 无 consumer。浏览器测试没有开始，脚本以非零状态结束。

2026-09-13 恢复运行使用隔离根目录 `/tmp/zf-four-flow-zf-test-004-20260913-230000`
和端口 `8003` 再次稳定复现相同 preflight 拒绝；完整输出保存在
`/tmp/zf-zf-test-004-resume-20260913-230000.log`。脚本退出码为 `1`，退出时
临时 run root、tmux session、runtime/web 进程均已清理。

2026-09-13 再次执行：
`tests/e2e/scripts/run_four_flow_kanban_playwright_e2e.sh --run-root /tmp/zf-four-flow-resume-20260913T131837Z --port 8003`。
Web build/bundle 检查通过，但 runtime 在 install phase 启动前再次以退出码 `1`
终止，复现完全相同的两个 mock on-demand provider-session 错误及
`external-issue-triage-verify.completed` 无 consumer；脚本已清理该临时 run root、
tmux、runtime 和 Web 进程。

### 影响

- 预期用于验证 Kanban/Issue/Workflow 的隔离浏览器 E2E 无法启动；
- 动态 workflow synthesis 产出的配置与其声明的 mock provider 能力不闭合；
- 该问题会把实际产品流程阻断在测试前置阶段，无法取得 DOM、API/SSE 和状态收敛证据。

### 证据

- 复现测试：`tests/e2e/scripts/run_four_flow_kanban_playwright_e2e.sh`；
- 相关检查：`src/zf/runtime/preflight.py::_check_provider_session_configs`；
- 本次运行未触发真实 Provider、远端写入或生产仓库修改；临时运行目录由脚本清理。

### 建议修复方向（不在本测试任务中实施）

- 动态 workflow synthesis 应为 mock backend 选择支持的 lifecycle mode，或提供 native session resume 能力；
- 对 workflow success_event 的 consumer 做生成阶段闭合校验；
- 在启动浏览器前增加 deterministic fake-provider preflight case。

### 当前处理

- [x] 已保留可重复的隔离 E2E 检出入口；
- [x] 已记录完整 preflight 错误和影响；
- [x] TestKit fixture 已在 mock 角色上显式使用 `lifecycle.mode=eager`，使后续运行能够越过该
  启动前置条件；这不是生产修复，动态 workflow 生成配置的生产问题仍保持 open。
- [x] 未修改生产代码；
- [ ] 尚未修复；
- [ ] 尚未进入 /issue → Fix Run → 回归 → merge 流程。

## ZF-TEST-047：workflow synthesis operation 在跨进程消费时 request hash 漂移并被阻断

> 状态: open（2026-09-30，保留 feature 的执行项目归属；涉及 Self-Issue 旧根模型的 v7 修复不接入，整合目标闭环待确认）

此条是通用 synthesis 与 Self-Issue 根分离的混合问题。feature 已有当前项目根逻辑，
本次不声称它仍复现同一缺陷，也不为此修改 feature；原 K516 修复和测试结果仅保留历史。
重新开放表示不能用 v7 的 fixed 结论替代 feature 对本条的完整验证。

### K516 修复与证据（v7 历史）

检测入口：

    tests/conformance/test_zaofu_testkit_s8_workflow_operation_contract.py::test_locked_self_issue_runtime_keeps_synthesis_project_catalog_current

原始裸红 `/tmp/zf-k516-047-red-20260924.xml`：1 failed。升级为真实 tick caller 后，
`/tmp/zf-k516-047-caller-red-20260924.xml`：2 failed / 2 passed（锁定/未锁定 × 默认/嵌套
state_dir）。Orchestrator 保留 `workflow_project_root`，tick 的 synthesis consumer 使用
该项目根构建当前技能目录和 Provider cwd；Self-Issue 的源码/Fix 目标根不变。不修改
request hash/sidecar 校验、不吞掉真实目录漂移。原 strict xfail 已移除。

绿证据 `/tmp/zf-k516-047-green-20260924.xml`：62 passed/3.62s；
`/tmp/zf-k516-callers-20260924.xml`：48 passed/12.47s；flow smoke 10 passed/5.02s。
原 Docker 四流程入口 `/tmp/zf-four-flow-k516-20260924` 已确认 queued/started/settled
使用同一 request hash，并产出 admitted synthesis。随后暴露不同的 proposal 阻断：
自动注入的 External Issue Generic Workflow 与新 General Workflow 冲突；此失败不再
归于 047，也不把 047 fixed 写成四流程整链通过。历史复现记录以下保留。

### 发现阶段

S6 隔离四流程 Kanban Docker Playwright E2E，在修正 mock role lifecycle fixture、越过
`zf start` preflight 后发现。

发现测试：

    tests/e2e/scripts/run_four_flow_kanban_playwright_e2e.sh
    web/tests/four-flow-workflow-install.spec.ts

复现命令：

    timeout 900s env PATH=/home/uisee/zaofu_v7/.venv/bin:$PATH bash tests/e2e/scripts/run_four_flow_kanban_playwright_e2e.sh --run-root /tmp/zf-four-flow-20260917-current --keep

### 复现条件

1. 隔离项目以 `backend=mock` 启动，使用 fake provider 和 Docker Playwright；
2. Web 通过 `workflow-request` 提交 `REQ-FOUR-FLOW-20260916-193210`，
   `workflow.synthesis.queued` 记录 operation `wop-workflow-synthesis-select-flow-3ee32435be9e`；
3. 该 operation 的首次 `workflow.operation.requested`/sidecar request hash 为
   `0ccf2def96bae489cf4e4cdcf07d8a160a53d42690af7890e58094735e798f65`；
4. runtime 的跨进程 synthesis consumer 随后再次准备同一 operation。

### 期望结果

同一 `workflow_run_id + operation_id` 的语义 request 在 enqueue 与消费/恢复之间应保持
相同 canonical hash；若确实发生合法 revision 变化，应走明确的 supersede/revision 路径，
而不是把同一未变更请求标记为 divergence。测试应继续进入 synthesis result、proposal
和浏览器后续断言。

### 当前结果

runtime 写入：

    workflow.operation.blocked
    reason=request_hash_divergence
    expected_request_hash=0ccf2def96bae489cf4e4cdcf07d8a160a53d42690af7890e58094735e798f65
    actual_request_hash=a8dcbbfdb293a35eacaea7b9739f592f3cb462cb672b60c36b73a99b7730095d

Web 投影随后显示 `synthesis failed`，Playwright 在 `getByText("scope", {exact: true})`
处超时，浏览器流程未能进入 config apply 或后续四流程验证。复现 run root 的
`events.jsonl` 同时包含初始 operation request、queued、blocked 和 run-manager
diagnosis 事件；没有远端写入。

同一工作树、同一脚本在 `/tmp/zf-four-flow-20260917-repeat` 再次执行，仍在相同
`getByText("scope", {exact: true})` 断言前失败；Web build/bundle、fixture preflight
均通过，页面仍未出现 synthesis result。该重复运行结束后已清理临时 run root、tmux、
Web/runtime 进程，确认不是一次性 Docker 或 readiness 波动。

### 2026-09-21 K357 当前 HEAD 隔离四流程复核

复核命令：

    bash tests/e2e/scripts/run_four_flow_kanban_playwright_e2e.sh --run-root /tmp/zf-four-flow-k357-20260921-133902 --port 8003 --keep

结果：Web typecheck/build/bundle budget 通过（最大 chunk `487400/500000`）；动态 mock role
已由测试 fixture 设置为 `lifecycle.mode=eager`，因此本次没有把旧的
`ZF-TEST-004` on-demand preflight 拒绝误记为当前失败。Docker Playwright 阶段 1 仍在
`getByText('scope', exact=true)` 等待 120 秒后退出码 **1**，未进入 config apply。

同一 run root 的 EventLog 记录：

    workflow.operation.requested request_hash=f7cb1db76aaed80501aaa17245c00f906be48afb426e69c35063784c7d194371
    workflow.operation.blocked reason=request_hash_divergence
      expected=f7cb1db76aaed80501aaa17245c00f906be48afb426e69c35063784c7d194371
      actual=a07b5b42f2b1972776cd7dbfa163ddedd6ac46827d36732e801b7204bec13599

证据保留于 `/tmp/zf-four-flow-k357-20260921-133902`（Playwright trace/error-context、
runtime/web 日志、`.zf/events.jsonl` 与 operation request sidecar）。失败归类仍为
`ZF-TEST-047` 的跨进程 request hash 漂移；没有真实 Provider、远端写入或生产代码修改。
本次运行完成后脚本已停止 runtime/Web/tmux；Issue 继续保持 `open`。

对保留下来的 request sidecar 与当前源码重新计算 canonical hash 可得到一致的
`0ccf…`，说明失败发生在 runtime 消费时的跨进程 request/catalog 取值边界，而不是
Playwright 文本断言本身。两次现场都未能证明具体漂移字段，因此不虚构根因。

### 2026-09-21 K337 当前 HEAD 隔离 Playwright/账本复核

复核命令：

    timeout 300s bash tests/e2e/scripts/run_four_flow_kanban_playwright_e2e.sh --run-root <temporary> --port 8002
    timeout 300s bash tests/e2e/scripts/run_four_flow_kanban_playwright_e2e.sh --run-root <temporary> --port 8003 --keep

结果：首次隔离运行退出码 **1**；Web build/bundle 通过，Playwright 在
`getByText('scope', exact=true)` 等待 120s 后失败。`--keep` 复跑退出码 **1**，证据目录为
`/tmp/zf-four-flow-k337-keep-rveWWy`；账本中 `workflow.operation.requested`/queued 的
request hash 为 `75b89670319f...`，消费阶段写入 `workflow.operation.blocked`，
`reason=request_hash_divergence`，actual hash 为 `33a8e07e0f5a...`，随后进入 Run Manager/
Autoresearch diagnosis。两次运行均已越过 `ZF-TEST-004` 的 mock on-demand preflight，未进入
config apply 或后续浏览器断言；tmux、runtime、Web 进程已清理，未访问真实 Provider/远端。
Issue 继续保持 `open`。

### 2026-09-21 K365 当前 HEAD 四流程 Docker Playwright 复核

复核命令：

    ./tests/e2e/scripts/run_four_flow_kanban_playwright_e2e.sh \
      --run-root /tmp/zf-four-flow-k365 --port 18037 --keep

结果：当前生产 Web bundle/typecheck/bundle budget 通过，最大 chunk 为 `487400 < 500000`；
动态 mock role fixture 已越过启动前置，Docker Playwright 阶段 1 在
`getByText("scope", { exact: true })` 等待 120 秒后失败，未进入 config apply 或后续四流程
断言。

同一 run root 的 `.zf/events.jsonl` 明确记录：

    workflow.operation.requested request_hash=9793e8bdd9ecc8882fcb7fd3eb989e077a1272e6611e39b0cac9232beb94ff81
    workflow.operation.blocked reason=request_hash_divergence
      expected=9793e8bdd9ecc8882fcb7fd3eb989e077a1272e6611e39b0cac9232beb94ff81
      actual=e834ec55ca31872c1eb2ef55fda70a67cb82988906d87409fc4666aa22c17b76

因此本次失败仍归类为既有 `ZF-TEST-047` 的跨进程 request hash 漂移，而不是新的 selector、
Docker 或 readiness 问题。证据保留于 `/tmp/zf-four-flow-k365`（runtime/web 日志、
`.zf/events.jsonl`、Playwright trace 与 error-context）；runner 已清理 runtime/Web/tmux，
18037 未占用，既有 8001 未触碰。未访问真实 Provider、未执行远端写入、未修改生产代码。

Issue 继续保持 `open`；维护者修复后仍须用同一脚本验证 hash 稳定，并实际进入 synthesis
result、proposal、config apply 及完整四流程断言。

### 2026-09-22 K397 当前 HEAD 四流程 fake-provider 复核

- [x] 生产 Web build/typecheck/bundle budget 通过（最大 chunk `487400 < 500000`）；隔离 runtime、
  Web、tmux 和 mock role 均 ready。
- [x] `four-flow-workflow-install.spec.ts` 在 `scope` 断言处等待 120s 后失败，未进入 config apply；
  证据：`/tmp/zf-four-flow-k397/test-results-install`。
- [x] `.zf/events.jsonl` 证明不是 selector/harness 单独失败：
  `workflow.operation.requested` expected hash 为
  `410d4ac9d85e7f1f1d31614b55c0cb4efd184fddb3d09305001a8bd6255bd43b`，随后
  `workflow.operation.blocked` actual hash 为
  `b7da1dc9c30909b8eb01e088601ca74943701a4f40d8ce2ae784146e00b021a9`，reason=`request_hash_divergence`；Run Manager/Autoresearch diagnosis 事件链也已生成。
- [x] 临时 runtime/Web/tmux 已清理，run root 保留于 `/tmp/zf-four-flow-k397`；`ZF-TEST-047`
  继续保持 `open`，未修改生产代码或新增 Issue。修复后必须用同一脚本验证进入 config apply 和完整四流程断言。

### 2026-09-22 K437 当前 HEAD 四流程复核与 fail-fast 诊断补强

- [x] 以 `bash tests/e2e/scripts/run_four_flow_kanban_playwright_e2e.sh --run-root
  /tmp/zf-four-flow-k437c-20260922 --keep` 重跑；Web typecheck/build/bundle budget 通过，最大
  chunk 仍为 `487400/500000`，隔离 runtime/Web/tmux/mock provider 均启动成功。
- [x] 事件链继续复现 `workflow.operation.requested` hash
  `bf9d6f7c5aeb1537b87dbb30c0b455d2e6b170da7f40a4c63e1236b55e8bb335`，随后
  `workflow.operation.blocked reason=request_hash_divergence`，actual hash
  `d03bedc30af772bc0d4e1e47213c9060e14b3de011e5ea066c14617a14dd2638`；config apply 未开始。
- [x] 测试侧将等待逻辑改为先轮询 workflow operation：只有 operation 结算且 proposal 非空才
  继续 DOM 断言；`failed/blocked/superseded/cancelled` 立即抛出带 reason/hash 的诊断错误。
  复跑在约 10 秒内以 `status=blocked: request_hash_divergence` 失败，不再无意义等待 120 秒。
- [x] `npm --prefix web run typecheck`、workflow proposal/operation/synthesis backend focused
  **47 passed**；未修改生产 `src/`/`web/src/`，该测试夹具改动只增强诊断与 fail-closed 行为。
- [x] 证据保留于 `/tmp/zf-four-flow-k437c-20260922`；runtime/Web/tmux 已清理，8001、真实
  Provider 和远端均未触碰。

本轮仍是 `ZF-TEST-047` 的当前 HEAD 复现，不新增 Issue；维护者修复后必须用同一脚本确认
hash 稳定并真正进入 synthesis result、proposal、config apply 和后续四流程断言。

### 影响

- 动态 workflow synthesis 在未发生用户 revision 的情况下不能稳定完成；
- proposal/config apply 和所有依赖 synthesis result 的浏览器、API、SSE 证据无法产生；
- request hash divergence 会触发 run-manager/autoresearch 诊断链，掩盖原始用户旅程失败；
- 同类跨进程恢复可能把合法重试误判为请求篡改或重复操作。

### 建议修复方向（不在本测试任务中实施）

- 对 enqueue 与 consumer 使用同一不可变 operation request sidecar/hash 作为权威输入，避免
  从可能变化的运行时 catalog 重新拼装语义请求；
- 若 catalog/config 确实是 hash 的一部分，持久化其版本/digest，并在 divergence 诊断中
  输出具体差异字段；
- 增加跨进程 enqueue→tick consume、重启 resume 和无 revision 变化的 request hash
  稳定性回归测试。

### 当前处理

- [x] 已通过隔离 Docker Playwright E2E 稳定检出并保留 EventLog、页面快照和 trace；
- [x] 已记录初始/实际 hash、阻断事件和影响；
- [x] 未修改 ZaoFu 生产代码；
- [ ] 尚未修复；
- [ ] 尚未进入 /issue → Fix Run → 回归 → merge 流程。

## ZF-TEST-020：回归用例捕获写入崩溃会留下半写 artifact

> 状态: fixed（2026-09-23 K509；主干统一验收阻断，按用户授权修复，工作区未提交）

### K509 修复与验证（2026-09-23）

主干入口 `/tmp/zf-k509-mainline-20260923-bYOcL23t/testkit-run.json`：**91 passed / 1 strict
xfailed**，唯一未通过为 020；pytest exit=0，但 TestKit 正确保持 verdict=fail。
当前独立裸红 `/tmp/zf-k509-020-red-20260923.xml` **1 failed in 0.63s**，保留半写证据。

修复仅将 capture 的直接 `Path.write_text` 换为现有 `atomic_write_text`。原节点转普通回归，
注入点覆盖 final-path（防止退回旧实现）和实际 fdopen 临时文件部分写；新建/覆盖两种情况
都断言注入确实发生、最终证据无半写/旧字节不变及临时文件清理。补充实际 ControlledAction
调用的 replace 失败→无 captured/completed→重试发布→读回/replay，权限和事件合同不变。

`/tmp/zf-k509-020-direct-20260923.xml` **19 passed in 1.87s**；
`/tmp/zf-k509-regression-callers-20260923.xml` **128 passed in 3.07s**，含实际 S6 token-gated
API capture/replay 和共享 action router。实现说明：`docs/impl/regression-case-persistence.md`。
本项关闭受控部分写入/replace 失败，不宣称全盘断电、跨事件事务或 OS hard-crash 恢复。
修复后统一验收 `/tmp/zf-k509-mainline-fixed-20260923-wiyLK2tO/testkit-run.json`：
**93 passed in 105.81s**、verdict=pass、session_finished=true，无 skip/xfail、collection、
deselection、timeout 或 evidence error；不等同真实 Provider 中断恢复已完成。
以下为修复前历史，原始红/xfail 不改写；不实现 `/issue-merge`。

### 2026-09-21 K326 当前 HEAD 裸红复核

复核命令：

    uv run pytest -q --no-cov -o addopts='' tests/conformance/test_zaofu_testkit_s9.py::test_regression_case_capture_does_not_leave_partial_artifact_on_write_crash
    uv run pytest -q --no-cov -o addopts='' --runxfail tests/conformance/test_zaofu_testkit_s9.py::test_regression_case_capture_does_not_leave_partial_artifact_on_write_crash
    uv run pytest -q --no-cov -o addopts='' tests/conformance/test_zaofu_testkit_s9.py

结果：精确节点 strict **1 xfailed in 0.32s**；`--runxfail` **1 failed in 0.32s**；完整 S9
模块 **5 passed、1 xfailed in 1.63s**。注入最终路径写入异常后，
`state/artifacts/regression/atomic-case.json` 仍存在半写内容，断言失败；无 XPASS、collection
error 或 timeout。未修改 `src/`、未访问 Provider/网络，Issue 继续 open，维护者修复后须用同一
入口验证无 XPASS。

### 发现阶段

S9 回归证据持久化合同测试。

发现测试：

    tests/conformance/test_zaofu_testkit_s9.py::test_regression_case_capture_does_not_leave_partial_artifact_on_write_crash

复现命令：

    uv run pytest -q tests/conformance/test_zaofu_testkit_s9.py::test_regression_case_capture_does_not_leave_partial_artifact_on_write_crash --no-cov

### 复现条件

1. 调用 `capture_regression_case` 写入 `state/artifacts/regression/<case_id>.json`；
2. 在最终路径的 `Path.write_text` 已写入部分内容后注入进程/写入异常；
3. 检查目标 artifact 是否仍存在。

### 期望结果

回归证据应使用原子临时文件加替换协议。写入中途失败时，最终路径不能出现半写或
不可解析的 JSON；后续 Issue/回归流程不得读取不完整证据。

### 修复前结果

修复前实现直接向最终路径调用 `Path.write_text`。受控异常后目标文件仍存在且只有部分
JSON 内容，测试以严格 xfail 保留确定性检出；这不是测试跳过，也未修改生产实现。

### 影响

- 崩溃恢复后可能把半写 regression case 当作有效证据；
- Issue intake 的 ref/digest 可能绑定不可重放的损坏 artifact；
- 回归门禁可能出现错误的缺失/解析结果。

### 历史建议修复方向（K509 已按主干阻断授权实施原子发布）

- 使用现有原子 JSON writer 写入临时文件，再以同目录 `os.replace` 发布；
- 恢复/读取时拒绝残缺 JSON，并保留明确的 evidence 错误分类。

### 当前处理

- [x] 保留原始裸红/strict xfail 证据，修复后节点为普通回归；
- [x] 原子写入已修复，直接与 S6 API/共享 router 联调通过；
- [ ] `/issue → Fix Run → 回归 → merge` 新流程不在本阶段实现或验收范围。

## ZF-TEST-021：受控 regression case_id 可通过路径遍历逃出 artifact 根目录

> 状态: open（确定性复现；未修改生产代码）

### 2026-09-21 K327 当前 HEAD 裸红复核

复核命令：

    uv run pytest -q --no-cov -o addopts='' tests/conformance/test_zaofu_testkit_s7_untrusted.py::test_regression_case_id_cannot_escape_artifact_root
    uv run pytest -q --no-cov -o addopts='' --runxfail tests/conformance/test_zaofu_testkit_s7_untrusted.py::test_regression_case_id_cannot_escape_artifact_root
    uv run pytest -q --no-cov -o addopts='' tests/conformance/test_zaofu_testkit_s7_untrusted.py

结果：精确节点 strict **1 xfailed in 0.88s**；`--runxfail` **1 failed in 0.83s**；完整 S7
模块 **11 passed、1 xfailed in 1.60s**。`../escaped-case` 实际创建
`.zf/artifacts/escaped-case.json`，越出 `.zf/artifacts/regression` 根目录；无 XPASS、collection
error 或 timeout。未修改 `src/`、未访问 Provider/网络，Issue 继续 open，维护者修复后须用同一
入口验证无 XPASS。

### 发现阶段

S7 不可信输入到文件 sink 安全矩阵。

发现测试：

    tests/conformance/test_zaofu_testkit_s7_untrusted.py::test_regression_case_id_cannot_escape_artifact_root

复现命令：

    uv run pytest -q tests/conformance/test_zaofu_testkit_s7_untrusted.py::test_regression_case_id_cannot_escape_artifact_root --no-cov

### 复现条件

1. 通过 `ControlledActionService` 提交 `capture-regression-case`；
2. 在 payload 中提供 `case_id=../escaped-case`；
3. 检查 `state/artifacts/regression` 之外是否出现 JSON 文件。

### 期望结果

外部输入只能生成 artifact 根目录下的安全文件名；路径分隔符、`..`、绝对路径和
符号链接等越界值应在写入前拒绝，且不得产生 capture 成功事件或越界文件。

### 当前结果

当前实现直接将 `case_id` 插入 `artifacts/regression/<case_id>.json`，会在
`state/artifacts/escaped-case.json` 创建文件。测试以严格 xfail 保留确定性检出；未修改
生产代码。

### 影响

- 不可信的 Web/controlled action 输入可以把回归证据写到预期目录之外；
- 可能覆盖其他 artifact 或污染 canonical state 相邻目录；
- 后续 ref/digest 绑定的根目录假设失效。

### 建议修复方向（不在本测试任务中实施）

- 对 `case_id` 使用 filename-safe allowlist，并拒绝绝对路径、分隔符和 `..`；
- 在最终写入前使用 `PathGuard`/`resolve` 重新确认目标仍位于 regression artifact root；
- 越界输入返回明确的 4xx/controlled-action rejection，不写文件、不发成功事件。

### 当前处理

- [x] 已增加严格 xfail 检测和复现命令；
- [x] 已记录越界文件证据与安全影响；
- [x] 未修改 ZaoFu 生产代码；
- [ ] 尚未修复；
- [ ] 尚未进入 `/issue → Fix Run → 回归 → merge` 流程。

## ZF-TEST-003：独立 Kernel 并发唤醒同一任务可能重复派发

> 状态: open（确定性复现；未修改生产代码）

### 发现阶段

S4 故障、恢复和并发阶段的多进程 dispatch race 测试。

发现测试：

    tests/conformance/test_zaofu_testkit_s4_multiprocess.py::test_two_kernel_processes_accept_one_dispatch_for_shared_task

复现命令：

    uv run pytest -q tests/conformance/test_zaofu_testkit_s4_multiprocess.py --no-cov

### 复现条件

1. 在同一个 `state_dir` 中创建一个可派发的 backlog task；
2. 启动两个独立 Python 进程，各自构造一个 `Orchestrator`；
3. 两个进程同时执行 `run_once()`；
4. 读取共享 `events.jsonl` 和 `kanban.json`。

### 期望结果

同一个 task 在一次并发唤醒中最多产生一个 `task.dispatched`，最多执行一次派发副作用；Task 的 attempt、状态和事件 ID 保持一致。

### 当前结果

测试曾出现两个进程都返回 dispatch，并在共享 EventLog 中写入两条同 task 的 `task.dispatched`。该结果违反并发幂等边界；由于调度窗口受进程时序影响，当前测试以严格 xfail 保留检出能力，若实现修复后意外通过则以 XPASS 使套件失败。

### 影响

- 同一任务可能收到重复 worker 派发；
- attempt、session、外部 provider 副作用可能重复；
- 事件账本出现重复调度事实，后续恢复和诊断难以区分真实执行次数。

### 建议修复方向（不在本测试任务中实施）

- 在 task dispatch 的检查、状态更新和事件追加之间建立跨进程原子提交/锁协议；
- 以 canonical TaskStore 或专用 dispatch claim 记录作为唯一竞争裁决；
- 增加多进程重复唤醒、不同 task、fanout child 和重启交错矩阵，确认副作用 at-most-once。

### 当前处理

### 2026-09-21 K335 当前 HEAD 裸红复核

复核命令：

    uv run pytest -q --no-cov -o addopts='' tests/conformance/test_zaofu_testkit_s4_multiprocess.py -k 'kernel_processes_accept_one_dispatch_for_shared_task'
    uv run pytest -q --no-cov -o addopts='' --runxfail tests/conformance/test_zaofu_testkit_s4_multiprocess.py -k 'kernel_processes_accept_one_dispatch_for_shared_task'
    uv run pytest -q --no-cov -o addopts='' tests/conformance/test_zaofu_testkit_s4_multiprocess.py

结果：5 个参数 strict **5 xfailed in 35.98s**；`--runxfail` **5 failed in 40.96s**；完整
多进程模块 **5 xfailed in 42.73s**。2/4/8/16/32 个独立进程分别产生相同数量的重复
dispatch decision/event；无普通失败、collection error 或 timeout，未访问 Provider/网络，
生产代码未修改，Issue 继续保持 `open`。

- [x] 已保留多进程并发检出测试；
- [x] 已改为严格 xfail，避免偶发 race 污染普通回归结果；
- [x] 未修改生产代码；
- [ ] 尚未修复；
- [ ] 尚未进入 /issue → Fix Run → 回归 → merge 流程。

## ZF-TEST-002：派发事件写入后崩溃会丢失 dispatch context，恢复结果不等价

> 状态: open（确定性复现；未修改生产代码）

### 发现阶段

S4 故障/恢复阶段的 baseline 对照测试。

发现 commit:

    bed9d732af7ddc1e5a11d97d6fa1edb7d0694b5e

测试 case:

    tests/conformance/test_zaofu_testkit_s4.py::test_crash_after_dispatch_event_is_replay_safe

当前 case 使用严格 xfail；该 xfail 是故障检测，不是跳过测试。

### 复现条件

1. 创建 backlog Task 并运行 Orchestrator dispatch；
2. 让 `task.dispatched` 成功追加后、后续 dispatch 收尾事件写入前注入进程崩溃；
3. 使用相同 state dir 重启 Orchestrator 并运行一次恢复；
4. 将恢复后的 EventLog 与无故障 baseline 的语义事件序列比较。

最小复现命令:

    uv run pytest -q tests/conformance/test_zaofu_testkit_s4.py::test_crash_after_dispatch_event_is_replay_safe --no-cov

### 期望结果

恢复后的状态和事件序列应与无故障 baseline 等价，至少应补齐派发上下文所需的持久化事实，且不能重复派发。

### 当前结果

Task 状态保持 `in_progress` 且没有重复 `task.dispatched`，但恢复事件序列缺少 baseline 中的 `task.dispatch_context.bound`、`worker.state.changed` 和 `orchestrator.decision.recorded`，因此恢复结果不等价。测试稳定以严格 xfail 暴露该差异。

### 影响

- 重启后 Task 可能只有派发事实，没有完整 dispatch context 证据；
- 后续恢复、诊断或 worker 关联逻辑可能无法获得 task_doc/source_doc/progress_doc 等上下文；
- “重启后等价于无故障运行”的恢复不变量不成立。

### 建议修复方向（不在本测试任务中实施）

- 让派发上下文与 `task.dispatched` 具备明确的原子提交/恢复协议；或
- 启动恢复时检测缺失的 `task.dispatch_context.bound`，基于已持久化 dispatch 信息补写一次幂等上下文事件；
- 增加不同崩溃点、重复重启和 context sidecar 完整性矩阵。

### 当前处理

- [x] 已保留确定性 baseline/fault 对照测试；
- [x] 已记录稳定复现命令和缺失事件证据；
- [x] 未修改生产代码；
- [ ] 尚未修复；
- [ ] 尚未进入 /issue → Fix Run → 回归 → merge 流程。

### 2026-09-21 K336 当前 HEAD 裸红复核

复核命令：

    uv run pytest -q --no-cov -o addopts='' tests/conformance/test_zaofu_testkit_s4.py -k 'real_orchestrator_dispatch_crash_restarts_to_baseline or crash_after_dispatch_event_is_replay_safe'
    uv run pytest -q --no-cov -o addopts='' --runxfail tests/conformance/test_zaofu_testkit_s4.py -k 'real_orchestrator_dispatch_crash_restarts_to_baseline or crash_after_dispatch_event_is_replay_safe'
    uv run pytest -q --no-cov -o addopts='' tests/conformance/test_zaofu_testkit_s4.py

结果：精确双节点 strict **2 xfailed in 5.62s**；`--runxfail` **2 failed in 5.64s**；完整
S4 **13 passed、5 xfailed in 83.57s**。两种 `task.dispatched` 后故障均未重复派发，但
恢复事件序列缺少 baseline 的 `task.dispatch_context.bound`、`worker.state.changed` 和
`orchestrator.decision.recorded`。无普通失败、collection error 或 timeout，未访问
Provider/网络，生产代码未修改，Issue 继续保持 `open`。

## ZF-TEST-048：当前 Codex CLI 拒绝 restricted worker 生成的 `-a untrusted`

> 状态: fixed（2026-09-29 K595-B；受限启动兼容/配置准备阻断已修复并验证，未提交；非原生业务验收）

### 2026-09-29 K595-B 已修复

`CodexAdapter`移除退休参数；真实`SpawnCoordinator.spawn`在角色私有config中
移除显式policy并以CLI绑定实际cwd为untrusted，保留sandbox/allowed_paths；五hook
迁到角色user层并保持信任。启动前有界app-server只读校验实际合并配置和hook身份，
遇到策略覆盖、配置畸形、旧profiles、hook失配、超时明确拒绝，未改成on-request/never。
默认/bypass和既有显式override不变，不修改共享认证或operator config。

- 新安全/真实caller合同48 passed；原detector strict xfail已移除，精确节点通过。
- 真实CLI私有read-only/workspace-write配置＋原argv节点共3 passed/2.30s；审批为
  unless-trusted，五hook metadata均enabled/trusted；physical worker为Mock，无模型调用。
- 前序影响闭包196 passed/10 failed/136.335052s，source stable。十红为K593-C已登记
  静态hash基线；修复前/后10个输出及三个hash函数AST完全一致，测试文件与HEAD相同。
  原报告仍fail，未改期待值/版本基线，不能外推hash实际执行已验收。
- 正式结构门禁51 passed/9.72s；Ruff/diff通过。归档：
  `attachments/k595b-codex-restricted-repair-20260929/`，具体步骤见steps K595-B。

本Issue精确启动参数故障已解除；K547原生任务、实际审批交互/恢复和完整交付仍未
验收。只读登录复查exit1/Not logged in，与048修复分开记录；不重跑已绿浏览器，
不设计/执行`/issue-merge`或Feishu测试。下文所有“未修复/xfail”是修复前历史。

2026-09-29 K595-A：修复前资格验证已完成，**仍未修复**。官方迁移说明及安装CLI
私有探针证明，on-request不是等价审批策略；user层project untrusted且无显式policy
才派生unless-trusted，项目hooks会停用，user hooks可被发现但仍需信任/执行验证。
实际SpawnCoordinator私有样本联调证明：仅删-a会继承approval_policy=never，属于
权限放宽，禁止以此追绿。现有精确detector --runxfail仍1 failed/0.49s。资格5组
断言不计修复或native验收；见steps K595-A与
`attachments/k595-codex-permission-qualification-20260929/`。

2026-09-29 K594复核：`codex -a untrusted -s workspace-write --help`仍退出2，
当前允许值为on-request/never。新K547原生入口必须保留私有project写目录，不能改为
bypass；该Issue现为后续原生验收的明确阻断。入口已增加argv拒绝检查，生产修复尚未
进行。认证前置另为Not logged in，两种阻断不混同；详见本文件K594-F1与steps K594。

### 发现阶段

S5 真实 Provider/宿主机 canary 与 Doc156 Kanban 协作浏览器 E2E。

发现测试/脚本：

    tests/conformance/test_zaofu_testkit_s5_real_provider.py::test_codex_restricted_spawn_arguments_are_accepted_by_installed_cli
    tests/e2e/scripts/run_doc156_kanban_collaboration_e2e.sh

复现命令：

    ZF_RUN_REAL_CODEX_CANARY=1 uv run pytest -q --no-cov -m 'host and real_provider and serial' tests/conformance/test_zaofu_testkit_s5_real_provider.py -k restricted_spawn

或直接检查当前安装的 Codex CLI：

    codex --enable hooks --dangerously-bypass-hook-trust -a untrusted -s read-only --help

### 复现条件

1. `RoleConfig(permission_mode="restricted")` 进入 `src/zf/runtime/backend.py::CodexAdapter.build_command()`；
2. 当前实现生成 `codex --enable hooks --dangerously-bypass-hook-trust -a untrusted -s read-only`；
3. 当前宿主机 Codex CLI 版本为 `0.154.0`，其 `--ask-for-approval` 只接受 `on-request` 或 `never`。

### 期望结果

ZaoFu 生成的 restricted worker 启动参数应被当前支持的 Codex CLI 接受；若版本不兼容，
应在 preflight 阶段明确报告 provider/配置兼容性失败，而不是启动后静默留下 dead pane。

### 当前结果

当前 Codex CLI 对 `-a untrusted` 返回 **exit 2**：

    error: invalid value 'untrusted' for '--ask-for-approval <APPROVAL_POLICY>'
    possible values: on-request, never

Doc156 E2E 中 `delivery_worker` 因此未进入 ready，tmux session 随后消失；EventLog 记录
`worker.pane.dead_observed`，没有产生 delivery worker 的有效 provider 结果。严格 xfail
用例稳定保留该检出能力。

### 影响

- restricted Codex worker 无法启动，依赖该角色的 workflow 可能停在等待/超时；
- 浏览器或 Kanban 层只能看到未 ready/失败，无法得到真实任务结果；
- 如果没有启动前参数兼容性检查，问题容易被误判为 tmux、网络或 provider 随机故障。

### 建议修复方向（K595校正：已是目标阻断，允许最小修复）

- 按K595调研在角色私有配置中保留严格命令审批；不得简单改on-request/never或仅删-a。
  处理继承显式policy与项目trust优先级，保留sandbox/allowed_paths并迁移必要user hooks；
- 在 ZaoFu preflight 中执行生成 argv 的无副作用 CLI 参数探针，版本不兼容时 fail-closed；
- 修复后运行该严格 xfail 的 `--runxfail` 回归及 Doc156 浏览器 E2E。

### 处理状态（K595-B更新）

- [x] 保留可复制的原始CLI失败命令与历史严格xfail红报告；修复后去掉该xfail。
- [x] 已记录Doc156两次历史dead-pane证据；未冒充本次已重跑完整浏览器。
- [x] 作为主干阻断，已实施最小生产修复并完成精确detector/host/caller验证。
- [ ] 静态hash基线独立来源核验、K547原生业务及最终同源验收仍未闭合。
- `/issue-merge`/Feishu保持暂缓，不以进入新功能流程作为本次修复条件。

### 2026-09-21 K355 当前 HEAD restricted-worker CLI 兼容性复核

- [x] 当前安装版本为 `codex-cli 0.155.1`；由 `CodexAdapter.build_command(RoleConfig(permission_mode="restricted"))`
  生成的 argv 在无副作用 `--help` 探针中仍返回 **exit 2**，stderr 明确指出 `untrusted` 非法，
  当前只接受 `on-request`/`never`。
- [x] 精确节点 strict **1 xfailed in 0.46s**；`--runxfail` **1 failed in 0.45s**；完整确定性
  `tests/conformance/test_zaofu_testkit_s5.py` **8 passed in 1.55s**；real-provider 过滤集合为
  `4 deselected / 1 xfailed`，裸红为 `1 failed / 4 deselected`。
- [x] 探针只执行本机 CLI `--help`，未启动 app-server/任务、未访问 Provider/网络、未修改生产代码；
  Issue 保持 `open`，维护者修复后须以同一 strict/`--runxfail` 和 Doc156 worker E2E 回归。

## ZF-TEST-049：Run Manager repair 验证命令的未闭合引号逃逸结构化失败

> 状态: open（当前工作树 `142a62ac` 确定性复现；仅检测，不修改生产代码）

检测入口：

    tests/test_run_manager_repair_validation_mutation_contract.py::test_malformed_quoted_command_is_rejected_without_crashing

裸红重现：

    uv run pytest -q --no-cov -o addopts='' --runxfail tests/test_run_manager_repair_validation_mutation_contract.py::test_malformed_quoted_command_is_rejected_without_crashing

Given：隔离的真实 worktree 目录和一条 Run Manager repair verification plan；命令为
`uv run pytest '`，即来自计划数据、带未闭合单引号的畸形命令。没有执行 shell、Provider、
远端写入或真实修复命令。

When：调用生产
`zf.runtime.run_manager_repair_validation.execute_repair_verification_plan()`。该入口先调用
`_trusted_invocation()`，后者对普通命令执行 `shlex.split(command)`。

Then：畸形命令应像其他未列入 allowlist 的命令一样 fail-closed，返回
`ok=false`、`status=failed`、`reason=command_not_allowlisted`、`returncode=126`，使上层可以
生成可审计的 `run.manager.action.failed`，而不是让 repair closeout 调用栈异常退出。

Actual：`shlex.split()` 抛出：

    ValueError: No closing quotation

`execute_repair_verification_plan()` 只捕获执行阶段的 `TimeoutExpired` 和 `OSError`，没有在
allowlist 解析阶段把 `ValueError` 转为结构化拒绝；因此函数无 result，调用方无法按正常
验证失败协议记录 reason/results。该缺陷不会执行恶意命令，allowlist 没有被绕过；影响是
畸形或损坏的 repair 计划可使本次 Run Manager action 崩出既定失败边界并阻断可恢复审计。

边界与对照：新增测试同时证明精确 allowlist 的 git/npm/pytest/dev-verify/premerge/flow-smoke
命令可解析，近似命令与 shell 注入形式被拒绝，required/optional、manual review、非零退出、
timeout、spawn error 和 2000 字符输出截断均有独立普通绿色对照。未闭合引号是唯一 strict
xfail；不据此声称 shell 已执行、canonical state 已损坏或远端副作用已发生。

建议修复方向（不在本测试任务中实施）：在 `_trusted_invocation()` 内捕获 `shlex.split()`
的 `ValueError` 并返回 `None`，或在调用处统一映射为 `command_not_allowlisted`；修复后运行
本 strict-xfail 的 `--runxfail`、Run Manager repair closeout 调用方测试及 mutation 审计。

当前处理：

- [x] 已增加确定性 strict-xfail 检测和普通安全对照；
- [x] 已确认异常发生在 allowlist 解析前，未执行 subprocess；
- [x] 未修改生产代码；
- [ ] 尚未修复；
- [ ] 尚未进入 `/issue → Fix Run → 回归 → merge` 流程。

### 2026-09-21 K300 当前 HEAD 裸红复核

单节点复核命令：

    uv run pytest -q --no-cov -o addopts='' [--runxfail] tests/test_run_manager_repair_validation_mutation_contract.py::test_malformed_quoted_command_is_rejected_without_crashing

结果：strict 模式 **1 xfailed in 0.38s**；`--runxfail` 直接以
`ValueError: No closing quotation` 失败，异常仍从
`src/zf/runtime/run_manager_repair_validation.py::_trusted_invocation()` 的
`shlex.split()` 逃逸。完整文件联调为 strict **30 passed、2 xfailed**，`--runxfail`
**30 passed、2 failed**；另一个失败是既有 `ZF-TEST-050`，没有新的 collection error、
timeout 或未登记 Issue。普通 allowlist、shell 注入拒绝、timeout/spawn/manual-review、
输出截断和显式 worktree 对照继续通过；未执行恶意命令、Provider 或远端写入，未修改生产
代码。Issue 继续保持 `open`。

## ZF-TEST-050：空 repair worktree 静默解析为当前进程目录并通过验证

> 状态: open（当前工作树 `142a62ac` 确定性复现；仅检测，不修改生产代码）

检测入口：

    tests/test_run_manager_repair_validation_mutation_contract.py::test_empty_worktree_is_rejected_instead_of_using_process_cwd

裸红重现：

    uv run pytest -q --no-cov -o addopts='' --runxfail tests/test_run_manager_repair_validation_mutation_contract.py::test_empty_worktree_is_rejected_instead_of_using_process_cwd

Given：Run Manager repair verification plan 有一条安全的 `review docs` 人工审阅记录，但
`worktree=""`。测试不运行 subprocess，不修改当前 checkout、canonical state 或远端。

When：调用 `execute_repair_verification_plan()`。生产实现先执行：

    Path(str(worktree or "")).resolve()

Then：空 worktree 没有绑定任何 repair candidate，应以 `worktree_missing` fail-closed，且
results 为空。验证器不应把调用进程的 cwd 当成 repair worktree。

Actual：空字符串变成 `Path("")`，解析为当前进程目录 `/home/uisee/zaofu_v7`；因为目录
存在，验证继续执行并返回 `ok=true`、`status=passed`，receipt 中的 worktree 也被写成
主 checkout。当前复现只使用 manual-review 指令，因此没有证明生产命令已在主 checkout
执行；但同一入口对 allowlisted subprocess 使用该 root 作为 cwd，错误默认具有在错误树
执行验证命令并把结果绑定到 repair closeout 的风险。

边界与对照：不存在路径和“路径存在但不是目录”均由普通测试正确拒绝；显式 tmp worktree
下 allowlisted 命令、超时、spawn error 和输出 receipt 也有绿色对照。该 Issue 只针对空值
退化为 cwd，不声称任意路径可逃逸，也不修改生产实现。

建议修复方向（不在本测试任务中实施）：在构造 `Path` 前拒绝空/纯空白 worktree，随后再
执行 resolve、存在性与目录检查；修复后运行 strict-xfail 的 `--runxfail`、repair closeout
调用方测试及 mutation 审计，并确认主 checkout 未被隐式选择。

当前处理：

- [x] 已增加确定性 strict-xfail 和不存在/非目录正向拒绝对照；
- [x] 已确认复现未启动 subprocess、未修改主 checkout；
- [x] 未修改生产代码；
- [ ] 尚未修复；
- [ ] 尚未进入 `/issue → Fix Run → 回归 → merge` 流程。

### 2026-09-21 K301 当前 HEAD 裸红复核

精确节点复核命令：

    uv run pytest -q --no-cov -o addopts='' [--runxfail] tests/test_run_manager_repair_validation_mutation_contract.py::test_empty_worktree_is_rejected_instead_of_using_process_cwd

结果：strict 模式 **1 xfailed in 0.50s**；`--runxfail` 返回 `ok=true`、
`status=passed`，断言 `result["ok"] is False` 失败。生产实现仍将空字符串转换为
`Path("").resolve()`，即当前 checkout，而不是返回 `worktree_missing`；该节点没有启动
subprocess。完整 repair-validation 文件与 K300 一起联调时，普通安全对照保持通过，未产生
新的 collection error、timeout 或 Issue；未修改生产代码，Issue 继续保持 `open`。

## ZF-TEST-051：Resident restart 缺少 terminate 能力时仍声称旧槽位已终止

> 状态: open（当前工作树 `142a62ac` 确定性复现；仅检测，不修改生产代码）

### 2026-09-21 K353 当前 HEAD 联合边界复核

- [x] 精确节点 strict **1 xfailed in 1.63s**；`--runxfail` **1 failed in 1.33s**：transport
  缺少 terminate hook 时仍产生 `terminated_old_slot=true`。
- [x] 未启动 Provider/tmux、未修改生产代码；Issue 继续 `open`，修复后须确认 receipt 与
  实际终止能力一致。

检测入口：

    tests/test_run_manager_resident_restart_mutation_contract.py::test_restart_without_terminator_does_not_claim_old_slot_was_terminated

裸红重现：

    uv run pytest -q --no-cov -o addopts='' --runxfail tests/test_run_manager_resident_restart_mutation_contract.py::test_restart_without_terminator_does_not_claim_old_slot_was_terminated

Given：启用 dedicated Resident Run Manager，构造一个合法的
`run.manager.resident.restart.requested`，并提供一个可以 spawn、写 briefing 和完成 ready
流程、但没有 `terminate` 方法的 transport。测试只使用隔离临时目录和 spy，不启动真实
Provider、tmux 或远端副作用。

When：调用生产
`zf.runtime.run_manager_resident._apply_resident_restart_request()`。实现用可选
`getattr(transport, "terminate", None)` 查找终止器；缺失时不会执行任何旧槽位终止动作，
也不会写入错误。

Then：`run.manager.resident.spawned` 与 `run.manager.resident.restarted` 应如实记录
`terminated_old_slot=false`，或以明确状态表示无法证明旧槽位已终止。实际实现以
`bool(not terminate_error)` 计算该字段；因为“没有终止器”和“终止成功”都留下空
`terminate_error`，两个事件错误记录：

    terminated_old_slot: true

这会把未执行的生命周期副作用声明为已完成。新 Resident 仍可被创建，但旧槽位是否存在
未知；上层无法仅凭事件区分“成功终止”“没有终止能力”，可能据此错误判断没有双实例、
重复消费或资源泄漏风险。

边界与对照：普通绿色测试同时覆盖 terminator 成功、terminator 异常、transport 完全缺少
可选 lifecycle hooks、缺少 running probe 但有 initializer、running=false 但缺少 initializer、
spawn/ready/prompt 失败与完整三事件因果链。该 Issue 只针对 `terminated_old_slot` 的事实性，
不声称本测试已观察到真实双 tmux pane，也不把缺少可选 hook 本身定义为异常。

建议修复方向（不在本测试任务中实施）：独立记录 `terminate_attempted` 与
`terminate_succeeded`；只有终止器存在、被调用且未抛错时才设置
`terminated_old_slot=true`。缺少终止器时应为 false，并可增加结构化 reason。修复后运行
本 strict-xfail 的 `--runxfail`、Resident restart 调用方测试、真实 tmux pane restart/cleanup
专项及 K65 mutation 审计。

当前处理：

- [x] 已增加确定性 strict-xfail 和完整普通对照；
- [x] 已确认未调用终止器但两个事件均声称成功终止；
- [x] 已完成 Resident restart-request 目标变异审计；
- [x] 未修改生产代码；
- [ ] 尚未修复；
- [ ] 尚未进入 `/issue → Fix Run → 回归 → merge` 流程。

### 2026-09-21 K302 当前 HEAD 裸红复核

复核命令：

    uv run pytest -q --no-cov -o addopts='' [--runxfail] tests/test_run_manager_resident_restart_mutation_contract.py

结果：strict 模式 **20 passed、1 xfailed in 0.73s**；`--runxfail` 为
**20 passed、1 failed in 0.78s**。失败精确定位在
`test_restart_without_terminator_does_not_claim_old_slot_was_terminated`：测试 transport
没有 `terminate` 方法，生产实现没有执行终止动作，但 `run.manager.resident.spawned` 的
`terminated_old_slot` 仍为 `true`，期望为 `false`。完整文件的 terminator 成功/异常、可选
lifecycle hook、spawn/ready/prompt 失败与因果链对照均通过；未启动真实 Provider/tmux，未产生
远端副作用，未修改生产代码。Issue 继续保持 `open`。

## ZF-TEST-052：Run Manager action effect 可被缺少 fanout 身份的事件错误结算

> 状态: open（当前工作树 `142a62ac` 确定性复现；仅检测，不修改生产代码）

### 2026-09-21 K353 当前 HEAD 联合边界复核

- [x] 精确节点 strict **1 xfailed in 2.21s**；`--runxfail` **1 failed in 2.28s**：缺少
  `fanout_id` 的 `candidate.ready` 仍匹配 fanout-scoped effect 并被接受。
- [x] 未访问 Provider/网络、未修改生产代码；Issue 继续 `open`，修复后须保持缺失身份
  fail-closed 且不影响合法 fanout 对照。

检测入口：

    tests/test_run_manager_action_effect_mutation_contract.py::test_effect_scope_requires_present_fanout_identity

裸红重现：

    uv run pytest -q --no-cov -o addopts='' --runxfail tests/test_run_manager_action_effect_mutation_contract.py::test_effect_scope_requires_present_fanout_identity

Given：一个 `run.manager.action.effect.pending` 通过 `fanout_id=fanout-required` 绑定到
特定 fanout；随后出现同类型的预期业务事件，但该事件的 payload、correlation 和其他身份中
完全没有 `fanout_id`。测试只构造内存事件，不启动 Provider、tmux 或外部副作用。

When：调用生产 `zf.runtime.run_manager._effect_event_matches_scope()`。当前实现对
`workflow_run_id`、`run_id` 和 `fanout_id` 使用：

    if expected and actual and expected != actual:
        return False

Then：只要 pending 声明了 `fanout_id`，候选事件就必须携带相同 fanout 身份；缺失身份不是
“没有冲突”，而是无法证明该事件属于当前 effect，应 fail-closed 返回 `False`，继续保持
effect pending。

Actual：当 expected 非空而 actual 为空时，上述条件不会拒绝；若 pending 又没有 run 作用域，
函数最终返回 `True`。因此一个无 fanout 身份的同类型事件可结算本不属于它的 effect；
`_settle_pending_action_effects()` 随后可能铸造错误的
`run.manager.action.effect.passed/failed`，解除 goal completion 的
`pending_action_effect` blocker。

边界与对照：普通绿色测试已证明显式不同的 workflow/run/fanout 会被拒绝、相同身份会通过、
required sequence 必须按序且匹配作用域、终态 effect 不会重复结算；candidate retrigger
显式允许新 fanout，但仍要求同一 run。该 Issue 只针对“声明了普通 fanout 作用域、候选事件
却缺少 fanout 身份”的 fail-open，不声称已观察到真实错误 Goal 完成或远端副作用。

建议修复方向（不在本测试任务中实施）：对每个声明的 scope key，除明确的
`candidate-rework-apply/retrigger` fanout 例外外，要求候选事件存在相应身份且完全相等；
保留 workflow/run 使用 correlation 兼容历史事件的规则，但不要让缺失 fanout 静默匹配。
修复后运行本 strict-xfail 的 `--runxfail`、action effect settlement 调用方测试、Goal
completion gate 测试及 K67 mutation 审计。

当前处理：

- [x] 已增加确定性 strict-xfail 和完整作用域/顺序/幂等绿色对照；
- [x] 已以裸红命令确认缺少 fanout 身份时当前返回 `True`；
- [x] 未修改生产代码；
- [ ] 尚未修复；
- [ ] 尚未进入 `/issue → Fix Run → 回归 → merge` 流程。

### 2026-09-21 K303 当前 HEAD 裸红复核

复核命令：

    uv run pytest -q --no-cov -o addopts='' [--runxfail] tests/test_run_manager_action_effect_mutation_contract.py::test_effect_scope_requires_present_fanout_identity

结果：strict 模式 **1 xfailed in 3.00s**；`--runxfail` 以断言失败结束，
`_effect_event_matches_scope()` 对声明 `fanout_id=fanout-required`、但候选事件完全没有
fanout 身份的 `candidate.ready` 返回了 `True`，而 fail-closed 期望为 `False`。完整 action
effect contract 联调为 strict **47 passed、1 xfailed in 3.71s**，`--runxfail` **47 passed、
1 failed in 3.00s**；显式不同/相同 workflow、run、fanout、sequence、终态幂等和
candidate-retrigger 对照均通过。未启动 Provider/tmux，未产生远端副作用，未修改生产代码。
Issue 继续保持 `open`。

## ZF-TEST-053：Operator ControlledAction 的 blocked receipt 与 applied 返回值互相矛盾

> 状态: open（当前工作树 `142a62ac` 确定性复现；仅检测，不修改生产代码）

### 2026-09-21 K353 当前 HEAD 联合边界复核

- [x] 精确节点 strict **1 xfailed in 2.84s**；`--runxfail` **1 failed in 1.69s**：service
  返回 `{"ok": true, "status": "blocked"}` 时账本写入 blocked，但函数仍返回 `applied`。
- [x] 未启动 Provider/tmux、未产生远端副作用、未修改生产代码；Issue 继续 `open`，修复后
  须以 status/receipt 为唯一保守 oracle。

检测入口：

    tests/test_run_manager_operator_action_executor_mutation_contract.py::test_operator_action_executor_does_not_report_applied_for_blocked_receipt

裸红重现：

    uv run pytest -q --no-cov --runxfail tests/test_run_manager_operator_action_executor_mutation_contract.py::test_operator_action_executor_does_not_report_applied_for_blocked_receipt

Given：`ControlledActionService.execute()` 返回一个内部冲突但必须 fail-closed 的结果：
`{"ok": true, "status": "blocked"}`。此类结果不应发生在健康 service 中，但 Run Manager
作为确定性边界不能同时向两个消费者声明不同事实。

When：调用生产 `zf.runtime.run_manager._execute_operator_controlled_action()`。当前实现先根据
`status == "blocked"` 生成 `run.manager.action.blocked`，随后却用独立的 `ok` 布尔值决定函数
返回：

    return "applied" if ok else ...

Then：事件账本和同步返回值必须一致并采取更保守的 blocked；实际结果是 EventLog 记录
blocked，而函数返回 applied。上层 `run_manager_tick()` 可据返回值增加 `actions_applied`，
同时 ledger 又显示动作被阻塞，导致投影、指标和恢复判断分叉。

边界与对照：普通绿色测试覆盖 applied/no-op/failed/blocked/approval-required/缺 status、
service exception、完整 service 参数、emitted event IDs 和 post-verify 因果。该 Issue 只针对
冲突结果的 fail-closed 一致性，不声称当前 Service 正常路径会主动制造该组合。

建议修复方向（不在本测试任务中实施）：以最终 `event_type` 或结构化 status 作为唯一返回值
oracle；当 `status` 表示 blocked/approval-required 时，无论 `ok` 如何都返回 blocked；亦可在
Service result schema 层拒绝矛盾组合。修复后运行本 strict-xfail 的 `--runxfail`、operator
action executor、Run Manager tick 和 ControlledAction 调用方测试。

当前处理：

- [x] 已增加确定性 strict-xfail 和完整普通对照；
- [x] 裸红复现确认 blocked event 与 applied 返回值同时出现；
- [x] 未修改生产代码；
- [ ] 尚未修复；
- [ ] 尚未进入 `/issue → Fix Run → 回归 → merge` 流程。

### 2026-09-21 K304 当前 HEAD 裸红复核

复核命令：

    uv run pytest -q --no-cov -o addopts='' [--runxfail] tests/test_run_manager_operator_action_executor_mutation_contract.py

结果：strict 模式 **8 passed、1 xfailed in 1.45s**；`--runxfail` 为
**8 passed、1 failed in 1.47s**。冲突的 ControlledActionService 结果为
`{"ok": true, "status": "blocked"}` 时，账本已记录 blocked，但
`_execute_operator_controlled_action()` 仍返回 `applied`；精确断言期望返回 `blocked`。
其余 applied/no-op/failed/blocked/approval-required、异常、参数、event id 与 post-verify
对照均通过；未启动 Provider/tmux，未产生远端副作用，未修改生产代码。Issue 继续保持
`open`。

## ZF-TEST-054：终态 evidence 会把 null artifact ref 当作可回放引用

> 状态: open（当前工作树 `142a62a` 确定性复现；仅检测，不修改生产代码）

### 2026-09-21 K353 当前 HEAD 联合边界复核

- [x] 精确节点 strict **1 xfailed in 1.43s**；`--runxfail` **1 failed in 1.43s**：
  `artifact_refs=[null]` 被收集为 `['None']`，而不是空引用并触发 evidence gate 缺失。
- [x] 未读取或写入真实 artifact、未访问 Provider/网络、未修改生产代码；Issue 继续 `open`，
  修复后须保持字符串引用正向控制并拒绝 null/非字符串元素。

检测入口：

    tests/test_verification_evidence_mutation_contract.py::test_payload_refs_rejects_null_entries

裸红重现：

    uv run pytest -q --no-cov --runxfail tests/test_verification_evidence_mutation_contract.py::test_payload_refs_rejects_null_entries

Given：一个 judge 终态 payload 将 artifact 引用声明为 JSON null，例如
`{"artifact_refs": [null]}`。null 既不是路径，也不是 event/ref/digest，不能被读取或重放。

When：生产 `zf.core.verification.evidence._payload_refs()` 收集引用。当前列表分支先执行
`str(item).strip()`，再判断结果是否非空。

Then：null 应被丢弃，返回空引用列表，使 `_validate_judge_payload()` 报告缺少
`replayable artifact refs`。Actual：Python 的 `str(None)` 为 `"None"`，所以收集结果是
`["None"]`；若 payload 的其他字段齐全，终态 evidence gate 会把这个伪引用当作存在。

边界与对照：普通绿色测试已覆盖顶层、嵌套 evidence、check 内引用、字符串引用、空字符串、
顺序和去空白；本 Issue 只针对列表中的非字符串值被无条件字符串化，不声称已经读取到名为
`None` 的真实 artifact，也不修改当前 production gate。

建议修复方向（不在本测试任务中实施）：列表元素只接受非空字符串，或进一步使用统一的
artifact ref schema 校验 ref/digest；对 null、数字、布尔、对象和嵌套列表 fail-closed。修复后
运行本 strict-xfail 的 `--runxfail`、terminal evidence/judge 调用方测试及 K88 mutation 审计。

当前处理：

- [x] 已增加确定性 strict-xfail 和正常引用绿色对照；
- [x] 已以裸红命令确认 null 被收集为字符串 `"None"`；
- [x] 未修改生产代码；
- [ ] 尚未修复；
- [ ] 尚未进入 `/issue → Fix Run → 回归 → merge` 流程。

### 2026-09-21 K305 当前 HEAD 裸红复核

复核命令：

    uv run pytest -q --no-cov -o addopts='' [--runxfail] tests/test_verification_evidence_mutation_contract.py::test_payload_refs_rejects_null_entries

结果：strict 模式 **1 xfailed in 0.53s**；`--runxfail` 断言失败，生产
`_payload_refs({"artifact_refs": [None]}, ...)` 返回 `['None']`，而不是空列表。完整
verification-evidence contract 联调为 strict **44 passed、1 xfailed in 1.14s**，
`--runxfail` **44 passed、1 failed in 1.32s**；顶层/嵌套/字符串/空白/顺序/去重、tier
coverage 和其他 evidence 对照均通过。未读取真实名为 `None` 的 artifact，未执行 Provider
或远端写入，未修改生产代码。Issue 继续保持 `open`。

## ZF-TEST-055：Codex rollout 的非法 UTF-8 首行逃逸 fail-open 兼容策略

> 状态: open（当前工作树 `142a62a` 确定性复现；仅检测，不修改生产代码）

检测入口：

    tests/test_role_session_rollout_match_mutation_contract.py::test_rollout_match_accepts_invalid_utf8_as_unparseable_legacy_data

裸红重现：

    uv run pytest -q --no-cov --runxfail tests/test_role_session_rollout_match_mutation_contract.py::test_rollout_match_accepts_invalid_utf8_as_unparseable_legacy_data

Given：构造一个隔离的 Codex rollout JSONL，首行包含非法 UTF-8 字节 `0xff 0xfe`。该文件
无法解析出可信 `session_meta.payload.cwd`，因此不能证明它来自其他项目。

When：生产 `RoleSessionRegistry._rollout_matches_project()` 按显式 UTF-8 打开文件并读取首行。
函数文档规定 unreadable、unparseable 或缺少 session metadata 的旧数据采用“accept unless
clearly mismatched”兼容策略；实现只捕获 `OSError` 和 `json.JSONDecodeError`。

Then：非法编码应与其他不可解析旧 rollout 一样返回 `True`，或至少以结构化、可分类结果
结束；Actual：UTF-8 decoder 抛出的 `UnicodeDecodeError` 未被捕获并逃逸。调用方
`_glob_newest_since()` 也不捕获该异常，因此一个更新时间较新的损坏 rollout 可中断整个
Codex session 观察流程，阻止后续合法候选被检查。

边界与对照：普通绿色测试覆盖空行、无效 JSON、非 mapping 根、非 mapping payload、缺失/
空 cwd、项目根、当前 state workdir、legacy `.zf/workdirs`、外部项目、文件打开失败和 resolve
失败 fallback。本 Issue 只针对解码错误与既有 fail-open 文档契约不一致，不声称损坏文件应
被绑定为当前 session，也不评价 Provider 输出语义。

建议修复方向（不在本测试任务中实施）：在读取/解析兼容边界捕获 `UnicodeDecodeError`
（或更窄的 `UnicodeError`）并按无法证明 mismatch 处理；同时保留明确外部 cwd 的 fail-closed
拒绝。修复后运行本 strict-xfail 的 `--runxfail`、Codex observe/glob 调用方测试及 K92
mutation 审计。

当前处理：

- [x] 已增加确定性 strict-xfail 和完整 cwd/legacy/error 绿色对照；
- [x] 已以裸红命令确认 `UnicodeDecodeError` 逃逸；
- [x] 未修改生产代码；
- [ ] 尚未修复；
- [ ] 尚未进入 `/issue → Fix Run → 回归 → merge` 流程。

### 2026-09-21 K306 当前 HEAD 裸红复核

复核命令：

    uv run pytest -q --no-cov -o addopts='' [--runxfail] tests/test_role_session_rollout_match_mutation_contract.py::test_rollout_match_accepts_invalid_utf8_as_unparseable_legacy_data

结果：strict 模式 **1 xfailed in 0.42s**；`--runxfail` 在
`RoleSessionRegistry._rollout_matches_project()` 读取首行时抛出未捕获的
`UnicodeDecodeError`。完整 rollout-match contract 联调为 strict **16 passed、1 xfailed
in 1.01s**，`--runxfail` **16 passed、1 failed**；空行、无效 JSON、非 mapping、cwd/legacy
路径、外部项目、打开/resolve 失败 fallback 等对照均通过。未访问 Provider、未修改生产代码，
Issue 继续保持 `open`。

### 2026-09-21 K354 当前 HEAD 联合复核

- [x] 精确节点 strict **1 xfailed in 0.90s**；`--runxfail` **1 failed in 1.13s**，非法 UTF-8
  首行仍抛出未捕获 `UnicodeDecodeError`。
- [x] 完整调用方模块 strict **16 passed / 1 xfailed in 0.53s**；未修改生产代码、未访问
  Provider 或远端副作用；Issue 保持 `open`。

## ZF-TEST-056：Evolution malformed version 逃逸结构化 payload 拒绝

> 状态: open（当前工作树 `142a62a` 确定性复现；仅检测，不修改生产代码）

检测入口：

    tests/test_control_actions_evolution_policy_mutation_contract.py::test_malformed_evolution_version_is_structurally_rejected

裸红重现：

    uv run pytest -q --no-cov --runxfail tests/test_control_actions_evolution_policy_mutation_contract.py::test_malformed_evolution_version_is_structurally_rejected

Given：向受信 `evolution-asset-transition` ControlledAction 提交其他策略字段合法、但
`version="not-an-integer"` 的 payload。该值属于不合法输入，应由 action handler 的
`invalid_payload` 分支稳定拒绝，且不得写 receipt、推进 asset 或发出 completed event。

When：生产 `_evolution_asset_transition()` 在自身 `try/except (TypeError, ValueError)` 解析
version 之前，先调用 `_evolution_policy_error()`。后者直接在 registry key 中执行
`int(payload.get("version") or 0)`，没有捕获转换异常。

Then：预期得到 `_failed(..., status_code=422, status="invalid_payload")` 的结构化回执；
Actual：`ValueError: invalid literal for int()` 从 policy helper 逃逸，导致受控操作以未分类异常
中止。裸红命令稳定退出 1，trace 指向 `control_actions_evolution.py` 的 registry key 构造。

边界与对照：同一测试模块的普通绿色矩阵覆盖 authority、enablement、registry identity、mode、
asset kind、skill overlay lifecycle、policy digest 和 target state；本 Issue 只说明 malformed
version 的错误分类与 fail-closed 回执丢失，不声称合法 evolution transition/outcome 已失败。

建议修复方向（不在本测试任务中实施）：在进入 policy lookup 前统一解析并验证 version，或让
policy helper 对 `TypeError/ValueError` 返回确定性的 invalid-policy/payload reason；避免 handler
和 policy helper 各自采用不一致的转换规则。修复后运行本 strict-xfail 的 `--runxfail`、K100
policy mutation 套件、transition/outcome handler 测试及 ControlledAction router 联调。

### 2026-09-21 K307 当前 HEAD 裸红复核

复核命令：

    uv run pytest -q --no-cov -o addopts='' tests/test_control_actions_evolution_policy_mutation_contract.py::test_malformed_evolution_version_is_structurally_rejected
    uv run pytest -q --no-cov -o addopts='' --runxfail tests/test_control_actions_evolution_policy_mutation_contract.py::test_malformed_evolution_version_is_structurally_rejected

结果：精确节点 strict **1 xfailed in 0.47s**；`--runxfail` **1 failed in 0.53s**，
抛出 `ValueError: invalid literal for int()`，位置为
`control_actions_evolution.py::_evolution_policy_error()` 的 registry key 构造。完整模块
strict **34 passed、1 xfailed in 0.78s**；`--runxfail` **34 passed、1 failed in 0.73s**。
同模块的 authority、配置、asset identity、mode/kind、overlay lifecycle、digest 与 target
state 对照均通过；未访问 Provider、未修改生产代码，Issue 继续保持 `open`。

### 2026-09-21 K354 当前 HEAD 联合复核

- [x] 精确节点 strict **1 xfailed in 1.35s**；`--runxfail` **1 failed in 1.27s**，malformed
  version 仍抛出 `ValueError: invalid literal for int()`。
- [x] 完整调用方模块 strict **34 passed / 1 xfailed in 0.78s**；未修改生产代码、未访问
  Provider 或远端副作用；Issue 保持 `open`。

当前处理：

- [x] 已增加确定性 strict-xfail 和 34 个策略绿色对照；
- [x] 已以裸红命令确认 `ValueError` 在结构化 `_failed` 前逃逸；
- [x] 未修改生产代码；
- [ ] 尚未修复；
- [ ] 尚未进入 `/issue → Fix Run → 回归 → merge` 流程。

## ZF-TEST-057：real-E2E ControlledAction 的非法 timeout 逃逸结构化失败回执

> 状态: open（当前工作树 `142a62a` 确定性复现；仅检测，不修改生产代码）

检测入口：

    tests/test_control_actions_real_e2e_run_mutation_contract.py::test_invalid_timeout_is_classified_as_controlled_failure

裸红重现：

    uv run pytest -q --no-cov --runxfail tests/test_control_actions_real_e2e_run_mutation_contract.py::test_invalid_timeout_is_classified_as_controlled_failure

Given：向受信 `real-e2e-run` ControlledAction 提交有效 matrix ref，但令
`timeout_seconds="not-an-int"`。这是 action payload 的非法字段，应由 handler 稳定拒绝并生成
结构化失败回执；不得启动 runner，也不得写 completed event。

When：生产 `_real_e2e_run_action()` 在 `try/except (OSError, ValueError)` 之前构造 config，
并直接执行 `int(payload["timeout_seconds"])`。转换错误因此不在受控异常边界内。

Then：预期得到 `_failed(..., status_code=422, status="run_failed")` 的结构化回执；Actual：
`ValueError: invalid literal for int()` 从 handler 逃逸。裸红命令稳定退出 1，trace 指向
`control_actions_ops.py` 的 config 构造。

边界与对照：同一测试模块的 13 个绿色 case 覆盖 payload/run-contract refs、别名与去重、
project root、evidence/timeout 默认值、runner OSError/ValueError，以及 passed/failed 事件、
completion receipt 和同步响应。本 Issue 只说明 malformed timeout 的错误分类和失败回执丢失，
不声称合法 real-E2E runner 或 matrix 执行失败。

建议修复方向（不在本测试任务中实施）：将 timeout 解析纳入现有受控异常边界，或在构造
runner config 前采用统一的正整数验证并返回 `invalid_payload`；修复后执行本 strict-xfail 的
`--runxfail`、K129 mutation 合同、real-E2E matrix runner 测试及 Web action 联调。

当前处理：

- [x] 已增加确定性 strict-xfail 和 13 个绿色对照；
- [x] 已以裸红命令确认 `ValueError` 在结构化 `_failed` 前逃逸；
- [x] 未修改生产代码；
- [ ] 尚未修复；
- [ ] 尚未进入 `/issue → Fix Run → 回归 → merge` 流程。

### 2026-09-22 K385 当前 HEAD 裸红复核

- [x] 精确节点 strict **1 xfailed in 0.49s**；`--runxfail` **1 failed in 0.48s**，timeout
  `int()` 转换仍从 `control_actions_ops.py:760` 逃逸，未生成 `run_failed`/422 回执。
- [x] real-E2E mutation、matrix runner、ControlAction router 与 Web caller 联调：**19 passed、
  1 xfailed、246 deselected in 2.18s**；合法执行、缺 matrix、runner 异常及 completed receipt 对照通过。
- [x] 证据日志：`/tmp/zf-k385-zf057-strict.log`、`/tmp/zf-k385-zf057-runxfail.log`、
  `/tmp/zf-k385-zf057-callers.log`；未修改生产代码、未启动真实 runner/Provider、未访问网络或远端写入。

### 2026-09-21 K308 当前 HEAD 裸红复核

复核命令：

    uv run pytest -q --no-cov -o addopts='' tests/test_control_actions_real_e2e_run_mutation_contract.py::test_invalid_timeout_is_classified_as_controlled_failure
    uv run pytest -q --no-cov -o addopts='' --runxfail tests/test_control_actions_real_e2e_run_mutation_contract.py::test_invalid_timeout_is_classified_as_controlled_failure

结果：精确节点 strict **1 xfailed in 0.83s**；`--runxfail` **1 failed in 0.62s**，
抛出 `ValueError: invalid literal for int()`，位置为
`control_actions_ops.py::_real_e2e_run_action()` 的 config 构造。完整模块 strict
**13 passed、1 xfailed in 0.79s**；`--runxfail` **13 passed、1 failed in 0.82s**。
同模块的 matrix refs、run-contract fallback、去重、project root、默认/显式 timeout、
runner OSError/ValueError、passed/failed event 与 completion receipt 对照均通过；未访问
真实 Provider 或远端副作用，生产代码未修改，Issue 继续保持 `open`。

### 2026-09-21 K354 当前 HEAD 联合复核

- [x] 精确节点 strict **1 xfailed in 2.12s**；`--runxfail` **1 failed in 2.09s**，非法
  timeout 在 config 构造时抛出 `ValueError`。
- [x] 完整调用方模块 strict **13 passed / 1 xfailed in 0.83s**；未修改生产代码、未访问
  Provider 或远端副作用；Issue 保持 `open`。

### 2026-09-21 当前 HEAD 裸红复核（K293）

- 普通模式：固定探针 `test_post_dispatch_one_shot_iterable_preserves_all_event_triggers`
  **1 xfailed in 0.45s**；strict xfail 与 `ZF-TEST-058` 保持精确映射。
- `--runxfail`：**1 failed in 1.66s**，传入只含 `candidate.ready` 的一次性 iterator 后，
  `runtime.housekeeping` 实际为 `[]`，预期的 `['fanout_timeouts']` 未执行；无 XPASS、
  collection error 或 timeout。
- 这是当前 HEAD 对公开 `Iterable[ZfEvent]` 合同的确定性复现。测试使用真实
  `run_post_dispatch_sweep` 入口和隔离 fake runtime，未修改 `src/`、未访问 Provider/网络；
  list 形式及其它事件组合的正向对照仍通过。
- 维护者修复后须用同一 `--runxfail`、periodic-sweep mutation 合同与 Orchestrator 调用方
  联调验证无 XPASS；本 Issue 不外推为常规 list 调用必然漏 housekeeping。

## ZF-TEST-058：Orchestrator post-dispatch sweep 会漏掉一次性事件迭代器中的 candidate.ready

> 状态: open（当前工作树 `142a62a` 确定性复现；仅检测，不修改生产代码）

检测入口：

    tests/test_orchestrator_periodic_sweep_mutation_contract.py::test_post_dispatch_one_shot_iterable_preserves_all_event_triggers

裸红重现：

    uv run pytest -q --no-cov --runxfail tests/test_orchestrator_periodic_sweep_mutation_contract.py::test_post_dispatch_one_shot_iterable_preserves_all_event_triggers

Given：`run_post_dispatch_sweep()` 的公开参数类型为 `Iterable[ZfEvent] | None`，传入只包含
一个 `candidate.ready` 的一次性 iterator。该事件应触发 `fanout_timeouts` housekeeping，和
传入内容相同的 list 行为一致。

When：生产实现先用 `any(...)` 在同一个 iterable 上查找
`channel.agent.reply.failed`，随后再次用 `any(...)` 查找 `candidate.ready`。第一次扫描已把
iterator 消耗完，即使没有匹配事件，第二次扫描也看不到原事件。

Then：预期调用一次 `_safe_housekeeping("fanout_timeouts", ...)`；Actual：housekeeping 列表
为空。裸红命令稳定退出 1，断言显示 `[] != ["fanout_timeouts"]`。当前
`Orchestrator.run_once()` 的常规调用通常传入 list，因此这是一个由公开 Iterable 合同暴露的
潜在漏检，而不是声称已经观察到生产 list 路径漏跑。

边界与对照：同一测试模块的绿色 case 覆盖 None/空 batch、三种 ready event、
`provider.turn.closed`、reply failure、candidate ready、组合事件、periodic 全量 sweep、context
recycle/liveness finally 和精确执行顺序；list 形式的 `candidate.ready` 正确触发 timeout 检查。
本 Issue 只针对一次性 iterable 被重复扫描，不评价 fanout timeout 判定本身。

建议修复方向（不在本测试任务中实施）：像 `run_replay_sweep()` 一样在入口只物化一次
`event_batch = list(events or [])`，后续所有触发器都扫描该 batch；或将类型合同收窄为可重复
迭代的 `Sequence` 并在边界验证。修复后运行本 strict-xfail 的 `--runxfail`、periodic sweep
mutation 合同、fanout timeout 测试和 `Orchestrator.run_once()` 调用方联调。

当前处理：

- [x] 已增加确定性 strict-xfail 和 list/组合/顺序绿色对照；
- [x] 已以裸红命令确认一次性 iterator 会漏掉 `candidate.ready`；
- [x] 未修改生产代码；
- [ ] 尚未修复；
- [ ] 尚未进入 `/issue → Fix Run → 回归 → merge` 流程。
### 2026-09-21 K310 当前 HEAD 裸红复核

复核命令：

    uv run pytest -q --no-cov -o addopts='' tests/test_orchestrator_periodic_sweep_mutation_contract.py::test_post_dispatch_one_shot_iterable_preserves_all_event_triggers
    uv run pytest -q --no-cov -o addopts='' --runxfail tests/test_orchestrator_periodic_sweep_mutation_contract.py::test_post_dispatch_one_shot_iterable_preserves_all_event_triggers

结果：精确节点 strict **1 xfailed in 0.54s**；`--runxfail` **1 failed in 0.65s**，
`runtime.housekeeping` 仍为 `[]` 而非 `['fanout_timeouts']`。完整模块 strict
**25 passed、1 xfailed in 0.65s**；`--runxfail` **25 passed、1 failed in 0.65s**。
None/空 batch、list 形式、ready/reply/turn-close 组合、periodic 全量顺序和 context/liveness
finally 对照均通过；未修改生产代码，Issue 继续保持 `open`。

### 2026-09-21 K354 当前 HEAD 联合复核

- [x] 精确节点 strict **1 xfailed in 0.50s**；`--runxfail` **1 failed in 0.36s**，one-shot
  `candidate.ready` iterator 被前序扫描消费，未产生 `fanout_timeouts`。
- [x] 完整调用方模块 strict **25 passed / 1 xfailed in 0.65s**；未修改生产代码、未访问
  Provider 或远端副作用；Issue 保持 `open`。

### 2026-09-22 K386 当前 HEAD 裸红复核

- [x] 精确节点 strict **1 xfailed in 0.35s**；`--runxfail` **1 failed in 0.33s**，一次性
  `candidate.ready` iterator 被前序扫描消费，`runtime.housekeeping` 仍为 `[]`。
- [x] periodic-sweep 与 housekeeping-visible caller 联调：**38 passed、1 xfailed in 1.20s**；
  list/组合/periodic/顺序/finally 对照通过。tick-budget 的 19>18 两例属于既有 `ZF-TEST-029`，
  单独记录，未归因给本 Issue。
- [x] 证据日志：`/tmp/zf-k386-zf058-strict.log`、`/tmp/zf-k386-zf058-runxfail.log`、
  `/tmp/zf-k386-zf058-callers2.log`；未修改生产代码、未访问网络、未启动 Provider/tmux。

当前 HEAD 联合复核（2026-09-21，K282）：

    uv run pytest -q --no-cov -o addopts='' tests/conformance/test_zaofu_testkit_s7_toctou.py
    # 3 xfailed in 0.51s

    uv run pytest -q --no-cov -o addopts='' --runxfail tests/conformance/test_zaofu_testkit_s7_toctou.py
    # 3 failed in 0.50s

三条裸红分别为 `ZF-TEST-014`、`ZF-TEST-015`、`ZF-TEST-016`：二进制 sink、fanout
relocation 和目录 sink 均在 PathGuard 校验后接受被替换的 symlink；未修改 `src/`，未新增
Issue。该复核证据同时覆盖本节目录替换问题及前两节的对应文件 sink。
