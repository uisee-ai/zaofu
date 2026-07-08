# ZaoFu 故障排查

> 适用对象: harness 启动失败、worker 不推进、Web 500、skills 不显示、真实 E2E 卡住时的第一轮处理。

## 1. 快速诊断顺序

先跑:

```bash
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main validate --path zf.yaml
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main validate --cold-start
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main doctor
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main skills doctor
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main kanban --board
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main events --last 50
```

如果是指定 task:

```bash
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main task trace <task_id>
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main kanban show <task_id>
```

## 2. `zf.yaml not found`

现象:

```text
Error: zf.yaml not found. To fix: run 'zf init'
```

处理:

```bash
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main init --preset safe-team
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main validate --path zf.yaml
```

如果这是已有项目,不要覆盖现有配置。先确认当前目录是否是项目根目录。

## 3. State Dir 不存在

现象:

```text
Error: .zf not found. To fix: run 'zf init'
```

处理:

```bash
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main init
```

如果 `project.state_dir` 不是 `.zf`,检查:

```bash
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main validate --path zf.yaml
```

## 4. Lock Held

现象:

```text
Error: Another harness is running (lock held). To fix: run 'zf stop' first
```

处理:

```bash
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main stop
```

如果确认没有 harness 进程仍在运行,再考虑:

```bash
tmux ls
ps -ef | rg "zf.cli.main start|tests.e2e.run_mixed|autoresearch"
```

只有确认是 stale lock 后,再手工处理 `project.state_dir/loop.lock`。

## 5. Worker 不推进

检查:

```bash
tmux ls
tmux attach -t <session>
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main events --last 100
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main status --workers
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main kanban --board
```

常见原因:

- 使用了 `zf start --no-watch` 或启动进程已退出,watcher 没有持续运行。
- role `triggers` 没包含上游事件。
- worker 完成事件缺 dispatch token 或 contract evidence。
- `max_rework_attempts` 已到上限。
- provider CLI 等待登录、hooks review 或交互 approval。
- context 过大,worker 正在 pending recycle。

处理:

```bash
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main validate --cold-start
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main task trace <task_id>
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main doctor
```

## 6. Codex Hooks 需要 Review

现象可能包含:

```text
hooks need review
```

处理:

- 进入对应 Codex pane。
- 按 Codex 提示打开 `/hooks` 并 review/trust 项目内 `.codex/hooks.json`。
- 重新启动 harness 或重新派发任务。

ZaoFu `start` 会写项目级 `.codex/hooks.json`,但 provider 是否允许执行仍受 Codex 自身 hook review 机制控制。

## 7. Skills 没有加载或 Web 中显示不全

先检查 CLI truth:

```bash
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main skills list
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main skills doctor
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main validate --strict-skills
```

常见原因:

- `skill_sources.path` 不存在。
- 同名 skill 存在多个候选。
- `runtime.skills.strict=false` 时只 warning,没有阻止启动。
- role 没有声明该 skill。
- Web 页面分页/滚动未展开,但 runtime truth 已在 `skills.lock.json`。

运行态文件:

```bash
jq . < .zf/skills.lock.json
find .zf/workdirs -path '*/runtime/skills-manifest.json' -print
```

## 8. Web 500 或 Degraded

先确认 Web 指向正确 state dir:

```bash
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main web \
  --state-dir "$(pwd)/.zf" \
  --host 0.0.0.0 \
  --port 5175
```

排查:

```bash
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main doctor
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main events --last 20
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main kanban --board
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main runs rebuild
```

常见原因:

- `kanban.json` 或 `events.jsonl` 不可读。
- Web 进程从错误目录启动,解析到错误 `.zf`。
- `--state-dir` 指向了已删除 worktree。
- API 端口冲突。
- runtime projection 旧版本字段与当前 Web 代码不匹配。

## 9. `kanban move done` 被拒绝

这是 gate 生效,不是 CLI bug。检查缺哪个前置事件:

```bash
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main task trace <task_id>
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main events --last 100
```

常见缺失:

- `review.approved`
- `test.passed`
- `judge.passed`
- `discriminator.passed`

正确处理是让任务回到缺失阶段,不要手工改 `kanban.json`。

## 10. Workdir 或 Ref 异常

检查:

```bash
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main doctor workdirs
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main refs verify
```

修复单个实例:

```bash
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main workdir repair dev-1
```

如果 multi-worktree 里不同 role 看到了不同 ref,优先检查 task evidence 和 candidate ref,不要让 review/test 直接基于 dev 的口头描述判定。

## 11. Pane-grid 绑定异常

当 pane title 全部显示为 `project`、role 与 pane 对应关系不清楚,或怀疑任务被发错
pane 时,先诊断:

```bash
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main doctor panes
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main panes doctor
```

如果 live tmux pane 仍在对应 `.zf/workdirs/<instance>/project` 中运行,可以非破坏性修复:

```bash
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main panes repair
```

该命令会设置 `@zf_instance_id`、尽力设置 tmux pane title,并重写 `.zf/pane_bindings.json`;
不会重启 session,不会 kill pane,也不会向 worker 发送任务内容。Codex TUI 可能继续覆盖
pane title,所以诊断以 `@zf_instance_id`、binding 文件和 cwd 为准。若缺少某个 configured
tmux role 的 live pane,命令会失败,避免写出不完整绑定。

## 12. 清理 Runtime State

先 dry run:

```bash
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main state clean --dry-run
```

确认已归档 evidence 后:

```bash
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main state clean --confirm --archive
```

`state clean` 只清理 rebuildable projection。不要手工删除 truth files,除非你明确要废弃这轮运行。

## 12. 真实 E2E 挂住

先定位是否已有 fatal/blocker:

```bash
PYTHONPATH=src python3 -m tests.e2e.mixed_phase_report --state-dir /tmp/zaofu-codex-smoke/.zf
PYTHONPATH="$(pwd)/src" python3 -m zf.cli.main events --state-dir /tmp/zaofu-codex-smoke/.zf --last 100
```

停止只关对应 session:

```bash
tmux ls
tmux kill-session -t <target-session>
```

不要关闭全部 tmux session。关闭前先确认 session 名来自 `zf.yaml` 的 `session.tmux_session` 或 E2E runner 输出。
