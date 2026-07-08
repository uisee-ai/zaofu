// Project init onboarding 结果的可读展示(与 CLI init 输出对齐):
// git hook 安装状态 + project.scripts.setup 声明建议。原始 JSON 仍由
// 旁边的 PreBlock 全量展示,这里只把两条 operator 需要行动的信息抬高。
export function ProjectInitOnboarding({ result }: { result: Record<string, unknown> | null }) {
  if (!result || result.ok === false) return null;
  const gitHook = typeof result.git_hook === "string" ? result.git_hook : "";
  const suggestion = typeof result.setup_suggestion === "string" ? result.setup_suggestion : "";
  if (!gitHook && !suggestion) return null;
  const hookLine =
    gitHook === "installed"
      ? "git pre-commit hook 已安装(运行时真相守卫 + 大暂存集熔断)"
      : gitHook === "exists"
        ? "git pre-commit hook 已存在,保持不动"
        : gitHook === "no-git"
          ? "非 git 仓库,跳过 pre-commit hook 安装"
          : "";
  return (
    <div className="hint-block" style={{ fontSize: 12, lineHeight: 1.6, opacity: 0.9 }}>
      {hookLine ? <div>+ {hookLine}</div> : null}
      {suggestion ? (
        <div>
          + 未声明 project.scripts.setup;检测到依赖清单 → 建议在 zf.yaml 加:
          <pre style={{ margin: "4px 0 0 12px" }}>
            {`project:\n  scripts:\n    setup: ${suggestion}`}
          </pre>
          (worktree 铸造时自动执行,使 worker 的新 worktree 开箱可运行)
        </div>
      ) : null}
    </div>
  );
}
