> 状态: active

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
