import { useEffect, useState } from "react";

type Report = { actor: string; produced_body_bytes: number; repeated_exact_body_bytes: number; read_deliveries: number;
  host_read_ms: number | null; source_bytes: number | null; selected_bytes: number | null;
  read_bytes_by_reason: Record<string, number>; navigation: { observed_calls: number; output_bytes: number; host_ms: number };
  provider_usage: { input_tokens: number; output_tokens: number; cache_read_tokens: number;
    estimated_usd: number | null; provider_reported_usd: number | null; billed_usd: number | null } | null };
const value = (n: number | null) => n === null ? "未观测" : n.toLocaleString();
export function TaskInputCosts({ attemptId, projectId }: { attemptId: string; projectId?: string }) {
  const [open, setOpen] = useState(false);
  const [result, setResult] = useState<{ key: string; data?: Report; error?: string } | null>(null);
  const key = `${projectId}:${attemptId}`;
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    const prefix = projectId ? `/api/projects/${encodeURIComponent(projectId)}` : "/api";
    void fetch(`${prefix}/attempts/${encodeURIComponent(attemptId)}/input-costs`, { signal: controller.signal })
      .then(async response => { if (!response.ok) throw new Error(`HTTP ${response.status}`); return await response.json() as Report; })
      .then(data => { if (!controller.signal.aborted) setResult({ key, data }); })
      .catch(error => { if (!controller.signal.aborted) setResult({ key, error: String(error) }); });
    return () => controller.abort();
  }, [open, key, attemptId, projectId]);
  const current = result?.key === key ? result : null;
  return <div data-testid="task-input-costs">
    <button type="button" aria-expanded={open} onClick={() => { setOpen(!open); setResult(null); }}>查看输入成本</button>
    {open && <>
      {!current && <p role="status">正在读取成本证据…</p>}
      {current?.error && <p role="alert">成本证据不可用：{current.error}</p>}
      {current?.data && <>
        <p>消费者 {current.data.actor || "未绑定"} · 正文输出 {value(current.data.produced_body_bytes)} B · 相同正文重读 {value(current.data.repeated_exact_body_bytes)} B · {current.data.read_deliveries} 次交付</p>
        <p>已观测目录调用 {current.data.navigation.observed_calls} 次 / {current.data.navigation.output_bytes} B；未观测历史不计零成本。</p>
        <p>读取宿主耗时 {value(current.data.host_read_ms)} ms</p>
        <p>重读用途（调用者声明）：{Object.entries(current.data.read_bytes_by_reason).map(([reason, bytes]) => `${({ recovery: "恢复", retry: "重试", initial: "首次", unspecified: "未声明" } as Record<string,string>)[reason] ?? reason} ${bytes} B`).join("；") || "未观测"}</p>
        {current.data.provider_usage ? <>
          <p>整个 operation：新输入 {current.data.provider_usage.input_tokens} / 缓存输入 {current.data.provider_usage.cache_read_tokens} / 输出 {current.data.provider_usage.output_tokens} tokens</p>
          <p>估算 ${value(current.data.provider_usage.estimated_usd)} · Provider 上报 ${value(current.data.provider_usage.provider_reported_usd)} · 账单 ${value(current.data.provider_usage.billed_usd)}</p>
        </> : <p>Provider usage 未观测或无法准确归因。</p>}
        <p>operation 包含推理和其他工作；不能按正文字节推算 token 节约。独立 Verify 的消费单独记录。</p>
      </>}
    </>}
  </div>;
}
