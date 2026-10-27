import type { Snapshot } from "../../api/types";

export function ReadProjectionNotice({ snapshot, status = snapshot?.read_projection }: { snapshot?: Snapshot | null; status?: Snapshot["read_projection"] }) {
  if (status?.state !== "error") return null;
  return <div className="runtime-health-strip" role="status">
    数据更新失败。
  </div>;
}
