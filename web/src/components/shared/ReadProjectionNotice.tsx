import type { Snapshot } from "../../api/types";

export function ReadProjectionNotice({ snapshot, status = snapshot?.read_projection }: { snapshot?: Snapshot | null; status?: Snapshot["read_projection"] }) {
  if (!status || status.state === "ready") return null;
  const asOf = status.generated_at_epoch ? new Date(status.generated_at_epoch * 1000).toLocaleTimeString() : "";
  return <div className="runtime-health-strip" role="status">
    {status.state === "error" ? "数据更新失败，保留上次结果。" : "正在更新数据，当前显示上次结果。"}
    {asOf ? ` 数据截至 ${asOf}。` : ""}
  </div>;
}
