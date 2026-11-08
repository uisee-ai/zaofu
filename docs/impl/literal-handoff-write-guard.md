# 字面量交接正文与写入目标

LS-v1 监工发现：worker 使用引用 delimiter 的 heredoc 向分配工作区中的交接文档写入纯文本，正文引用项目根证据路径，旧 shell 字符串检查把正文中的路径误当作写入目标。原生编辑工具能够恢复，但 shell 写入会反复误拒。

`hook_workdir_guard.write_target_operations` 仅识别完整的单条 `cat > path <<'MARKER'`（含 `>>` 和双引号 delimiter）命令。目标只接受普通路径字符及简单引号路径，delimiter 必须显式引用，且其首次出现必须位于命令末尾。正文由 shell 当作字面量数据，不参与目标检查。

识别后，真实目标经现有 runtime、分配 workdir、合同 allowed_paths 三道检查。相对路径仍按真实 cwd 解析，穿越和符号链接仍检查解析后的路径。真实 runtime 写入、合同外写入不会因正文提及合法目录而被放行。

未引用 delimiter、命令替换、解释器、多个命令、提前结束及无法确定的形式继续走既有保守 shell 规则。该识别器不是通用 shell 解释器；原有复杂 shell 规则的完整性不由此保证。

回归证据：`tests/test_hook_literal_handoff.py` 经 Codex/Claude 真实 hook 入口验证合法正文、越界目标及歧义命令；任务记录见 `tasks/2026-11-08-0913-literal-handoff-guard.md`（完成后归档至 `tasks/done/`）。
