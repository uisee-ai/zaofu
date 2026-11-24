import type { HandoffSummaryProjection } from "../../api/types";
import { asRecord, textValue } from "../../app/shared";

export function HandoffBindingDetails({ summary, onEvidence, onActivity }: {
  summary?: HandoffSummaryProjection;
  onEvidence: () => void; onActivity: () => void;
}) {
  const pipeline = asRecord(summary?.pipeline);
  const workspace = asRecord(pipeline.workspace);
  const inputs = asRecord(summary?.input_recovery);
  const checkpoint = asRecord(inputs.checkpoint);
  if (!summary?.pipeline && !summary?.input_recovery) return null;
  const value = (row: Record<string, unknown>, key: string) => textValue(row[key]) || "未知";
  return <details className="subsection" data-testid="handoff-binding-details">
    <summary>交接依据与恢复条件</summary>
    {summary.pipeline ? <>
      {pipeline.status !== "current" ? <p role="status">当前执行依据不可用：{value(pipeline, "reason")}</p> : null}
      <dl className="detail-grid compact-detail-grid">
        <dt>当前流程</dt><dd>Task Pipeline · {value(pipeline, "pipeline_stage")}</dd>
        <dt>运行 / 执行</dt><dd className="mono">{value(pipeline, "workflow_run_id")} / {value(pipeline, "attempt_id")}</dd>
        <dt>代码目标</dt><dd className="mono">{value(pipeline, "target_ref")}</dd>
        <dt>任务现场</dt><dd>{workspace.status === "bound" ? <span className="mono">{value(workspace, "path")}</span>
          : <>不可用：{value(workspace, "reason")}</>}</dd>
      </dl>
    </> : null}
    <dl className="detail-grid compact-detail-grid">
      <dt>必要输入</dt><dd>{inputs.status === "bound" ? `${inputs.required_visible ?? "未知"} 项；历史读取缺口 ${inputs.historical_missing ?? "未知"} 项`
        : inputs.status === "rebind_required" ? "绑定已过期，等待受控重新绑定" : "当前输入依据不可用"}</dd>
      <dt>当前会话保留</dt><dd>未知；历史读取不能证明当前仍保留内容</dd>
      <dt>现场保存</dt><dd>{checkpoint.status === "bound" || checkpoint.status === "captured" ? "已有绑定快照" : "未取得当前有效快照"}
        {textValue(checkpoint.reason) ? <p>{textValue(checkpoint.reason)}</p> : null}</dd>
    </dl>
    <p className="muted">快照不包含忽略文件、Git 历史和运行时状态；旧执行是否已停止仍需单独确认。</p>
    <p className="muted">保存与读取记录不授予执行权限。恢复仍需核对身份、任务现场和执行权。</p>
    <div className="button-row">
      <button type="button" className="icon-button" onClick={onEvidence}>查看输入与成果</button>
      <button type="button" className="icon-button" onClick={onActivity}>查看交接历史</button>
    </div>
  </details>;
}
