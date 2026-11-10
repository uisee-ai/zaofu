> 状态: active — `e32d78ba` 交接撤回；保留既有提交与全部验证历史

# 有限 cp 写目标与任务工作区保护

授权：用户持续监工要求“遇到问题修复问题，完成后记录并恢复”；主监工将本次已复现 guard 缺陷明确交付本隔离 worktree 实施。仅本项，不修改 LS 产品或运行态。

现场：2026-11-10 `evt-50600bebfaa6` / `evt-9d2f7fc58882` 把文档只读源复制到当前 CORE 允许域的命令误拒。当前 dev `ced042cce117f0508ed7e54895e2365dbc4a5967` 与 Watcher `509a7a0f` 的 guard 相同；诊断见 LS `docs/records/2026-11-09-parallel-resume/workdir-copy-guard-review.md`，关联 `open_observations.workdir_guard`。

- 有限解析单条 `cp [--] literal-source-file literal-destination-file`，源只读、目标进入三重 guard -> verify: 两 provider 的实际 PreToolUse 入口接受文档源复制，仍拒绝 runtime 写入。
- 对所有可识别但不支持的 cp 参数/复合语法 fail-closed；有效 copy 拒绝外部目的地、合同外目标和 symlink -> verify: 外目标含 assigned 绝对源、路径穿越、旧代际/错误 cwd、目录/链接/复杂参数等负例。
- 保留原生工具、literal heredoc、result scratch 与读权限/secret 策略 -> verify: 当前 guard、直接调用者和共享状态边界回归，dev-verify plan/run、sentinel。
- 更新实施说明和范围限制，提交与归档 -> verify: 精确 pathspec 两次提交、干净 worktree 交主监工独立审查；不 merge/push。

非目标：通用 shell 解释器、Docker `:ro` 解析、OS 沙箱/TOCTOU 保证、扩展任何 task/role 权限。

## 实施与验证

已实现独立有限 copy intent 模块，接线 runtime/workdir/allowed_paths 三 guard；合法外源复制通过，外目标/合同越界/符号及硬链接/目录与未知语法拒绝，保留现有 scratch 和其它工具行为。

- 原始 `dev-verify.py plan --base dev` 选择 142 文件，超过 80 阈值原会扩大到全 tests；经主监工确认，保持原清单，四批 `dev-verify.py run --base dev --paths <原清单子集> --workers 2` 执行，341 + 511 + 533 + 295 = **1680 passed**。不是整库测试。
- `bash scripts/dev-premerge-gate.sh`：manual docs currentness ok，**53 passed**。
- 首轮 99 项中 1 个新测试用了错误终态事件名，已修正为正式 settled 合同；最终第三批已包含并通过。
- 完整原 plan、每批命令/结果、142 项并集无遗漏及测试源码 SHA 见 `docs/records/2026-11-10-worker-copy-guard-verification.json`。
- 未运行真实 provider/host，未修 Docker :ro 子问题；仅有限 ASCII 字面文件复制，不承诺通用 shell 或 TOCTOU 沙箱。

## 独立审查返工

主监工撤销 `04f5abed` merge-ready：普通命令参数中的 cp 文本被误判，copy 的 scope frame 沿用最后 `/project/` 截断导致嵌套路径漏检。原批准范围内继续修复，不扩大 shell 授权或权限语义。补两 provider 非 cp 数据回归、真实授权根相对范围正反例，先红后绿并重跑完整影响闭包。

09:20 主监工追加实证范围：native Write/Edit 两 provider 也存在嵌套 project scope 基线漏洞（独立12断言8pass/4fail）。本轮共享 frame 规范化覆盖所有已解析写目标，继续复用 Kernel workdir，不用任意 cwd 授权；先红后绿并保持 legacy/scratch 兼容。

## 返工实现与新验证

- cp 信号改为保留引用的命令位置扫描，普通 rg/printf/python/cat 数据不触发，已识别 wrapper/后续真正 cp 仍拒绝；literal 授权文法不扩展。
- 删除最后 `/project/` 猜根，所有已解析写目标共享当前 Kernel 工作区的规范相对 frame，一次 hook 内只解析一次绑定。覆盖 cp、Write/Edit、patch/heredoc，保持 legacy 相对路径与精确 scratch 例外。
- 先红后绿：初版新增21反例全部失败；native 扩围前20断言10失败/10通过；修后直接caller聚焦193通过。所有 hook 反例只判定 exit/events，不执行目标工具或越界写入。
- 新 `dev-verify.py plan --base dev` 选择143文件；保留原清单四批 run：341 + 511 + 548 + 333 = **1733 passed**，无遗漏；未跑整库/host/真实provider。新 sentinel **53 passed**，manual docs currentness ok。
- 新收据 `docs/records/2026-11-10-worker-copy-guard-rework-verification.json` 保留原plan、每批完整命令/结果、红例节点与新源码SHA。旧1680收据不变，不能当本次新diff的验证。

## 活动替换返工

独立复核确认双引号活动 `$(cp ...)` 在两个provider仍放行；基线已有逃逸，本轮有限cp承诺未闭合，相对04f5拒绝行为出现回退。仅补可识别活动替换正文的保守拒绝，literal cp授权文法不变，保留普通cp数据、单引号、转义与算术变量；所有hook反例仅判定不执行。

## 活动替换最终实现与验证

- 增加有界词法辅助：分离活动 `$()`/反引号/进程替换正文并屏蔽展开值；算术上下文只追踪嵌套活动替换，普通 cp 文本仍是数据。未闭合、超深/超数量上下文保守拒绝，不扩展 literal cp 授权文法。
- 补实证注释反例：词首未引用 `#` 后的 `)` 不再错误截断替换；词内、引用及转义 `#` 不作注释。此修复不执行 shell，也不提供通用 shell/动态命令名/TOCTOU 沙箱。
- 先红后绿：新活动替换回归在 e32d 源码上 **12 failed / 22 passed**；最终定向 **240 passed**。两个 provider 的真实 hook 只判定 exit/events，未执行被审工具或越界写。
- 中间144文件闭包 **1777 passed** 已保留；随后注释边界修复使它不再代表最终diff。最终重新执行完整144文件闭包，四批 **341 + 511 + 583 + 358 = 1793 passed**，文件并集无遗漏/重复；sentinel **53 passed**、manual docs currentness ok。不是全库验证，未运行真实provider/host。
- 最终源码SHA、原plan、逐批完整命令/结果、红例节点与边界见 `docs/records/2026-11-10-worker-copy-substitution-verification.json`；中间收据见 `docs/records/2026-11-10-worker-copy-substitution-intermediate-verification.json`。原1680/1733收据保持不变。
