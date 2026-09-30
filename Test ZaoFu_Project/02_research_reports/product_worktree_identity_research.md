# 产品 TestKit 工作树身份调研

> 文档定位（2026-09-30）：历史调研，正文“当前/建议/尚未实现”仅适用于各段所记时点，不是最新执行队列。现行方案见 [ZaoFu_Product_TestKit.md](../03_plans_and_steps/ZaoFu_Product_TestKit.md)，执行仍按既有暂停/跳过边界；本次不重跑或改写历史结果。
>
> 当前对齐入口：`tests/product_testkit/executor.py`。当前已补 target 规范化、tracked diff、执行前未跟踪白名单及 cleanup 后身份复核；ignored/沙箱边界仍需单独证明。

调研日期：2026-09-16。依据仅含 Git 官方文档与本机 Git 2.43.0 行为实验。

## 目标

机械证明 TestKit 实际执行目录的 `HEAD` 与 `target_commit` 是同一提交，且 index/working tree 中所有 tracked 字节仍属于该提交；同时允许本次 manifest 和 evidence 作为未跟踪 sidecar 存在。

## 官方事实

- `git rev-parse HEAD` 只解析当前 HEAD，不检查 index、working tree 或 untracked。应使用 `git rev-parse --verify HEAD^{commit}` 明确要求 commit；对外部输入先用 `--verify --end-of-options <target>^{commit}` 解析为规范化完整 OID。[git-rev-parse](https://git-scm.com/docs/git-rev-parse)
- 单 commit 形式的 `git diff` 比较 working tree 与指定 commit；`--quiet` 隐含 `--exit-code`：0 无差异、1 有差异。它能发现 staged 和 unstaged tracked 漂移，但不枚举 untracked/ignored。[git-diff](https://git-scm.com/docs/git-diff)
- `git status --porcelain=v1` 是稳定的脚本格式；`-z` 用 NUL 分隔文件名，`--untracked-files=all` 展开目录内文件；`??` 表示 untracked，`!!` 表示 ignored（需显式 `--ignored`）。[git-status](https://git-scm.com/docs/git-status)
- `git worktree add --detach <path> <commit>` 在指定提交创建 linked worktree；HEAD/index 为各 worktree 独有，但 objects、refs 和默认 config 仍共享。它固定干净起点，不是安全沙箱。[git-worktree](https://git-scm.com/docs/git-worktree)

本机实验吻合：仅增加未跟踪 manifest/evidence 时 HEAD 不变且 diff 返回 0、status 报 `??`；tracked 文件无论暂存与否，HEAD 仍不变但 diff 返回 1；ignored evidence 默认不出现在 status；显式 target 创建的 detached worktree 初始 HEAD 精确等于该 target。

## 方案选择

当前 [`verify_target_commit`](../../../../tests/product_testkit/executor.py) 的 `rev-parse HEAD + git diff --quiet HEAD --` 已覆盖核心 tracked 门禁，并由 [`test_executor.py`](../../../../tests/product_testkit/test_executor.py) 验证 tracked 修改拒绝、未跟踪 manifest 放行。产品级最小安全方案建议：

1. 解析 `target_commit^{commit}` 得到 `resolved_target`，以对象身份比较，避免 manifest 允许大写 SHA 而 Git 输出小写造成伪 mismatch。
2. `git worktree add --detach <test_worktree> <resolved_target>`；所有检查和测试均以该目录为 cwd。
3. 在测试树内要求 `git rev-parse --verify HEAD^{commit}` 等于 `resolved_target`，并要求 `git diff --quiet --no-ext-diff <resolved_target> --` 返回 0。用固定 target 而非再次引用可变化的 HEAD；返回 1 判 tracked 漂移，其他非零判 harness inspection failure。
4. 若契约是“只允许两类 sidecar”，解析 `git status --porcelain=v1 -z --untracked-files=all --ignore-submodules=none`：任何非 `??` 拒绝；`??` 仅放行精确 manifest 路径和本次 evidence 根下的路径，路径须按组件判断。若保持当前“所有 untracked 均放行”契约，则 status 只记录诊断，不能宣称已排除其他 untracked。
5. manifest/evidence 优先放到测试树外。至少在任何项目命令前、setup 后测试前、全部命令后复核；若要求每条命令都测试相同字节，则每条命令前复核或使用只读文件系统。

## 边界

- Git 检查证明提交与 tracked 工作树观察值，不证明断言充分、依赖可复现、evidence 未伪造，也不阻止进程写文件。
- 前后检查存在 TOCTOU；detached worktree 只能减少开发树污染和 branch 推进竞争。
- ignored 默认不可见；需 `--ignored=matching` 后拒绝 `!!`，或明确将其排除在证明范围外。
- submodule 内 dirty/untracked 不是顶层 tree 身份的自然等价物；含 submodule 时须另定递归政策，不能把顶层通过泛化为嵌套仓库全洁净。

## 测试清单

- 正常 detached target、未跟踪 manifest、未跟踪 evidence 均通过。
- HEAD mismatch、tracked 未暂存修改、tracked 已暂存修改分别失败。
- 其他 untracked、ignored、奇异文件名、`evidence-evil/` 前缀混淆按声明政策处理。
- diff 返回大于 1、非 Git 目录、unborn HEAD 均归类为 harness failure。
- setup 或前一测试修改 tracked 文件时，后续复核失败；主工作树移动 branch 不影响 detached 测试树 target。
