import { useMemo, useState } from "react";
import { statusLabels, workflowNodeLabel, type SdlcGraph, type SdlcNode } from "./sdlcGraphModel";
import { durationLabel, timelineModel } from "../delivery-trace/loopTimelineModel";

type Interval = SdlcGraph["analysis"]["intervals"][number];
type Entry = { interval: Interval; node?: SdlcNode };
type Group = { id: string; node?: SdlcNode; entries: Entry[] };
const time = (value: number) => new Date(value).toISOString().replace("T", " ").slice(0, 16) + " UTC";
const validTime = (value: number | null): value is number => typeof value === "number" && Number.isFinite(value) && Math.abs(value) <= 8640000000000000;

export default function WorkflowExecutionTimeline({ graph, onSelect }: { graph: SdlcGraph; onSelect: (id: string) => void }) {
  const [limit, setLimit] = useState(50);
  const [compressed, setCompressed] = useState(true);
  const { groups, start, end, count } = useMemo(() => {
    const operations = new Map(graph.nodes.filter(node => node.kind === "operation").map(node => [node.id, node]));
    const stages = graph.nodes.filter(node => node.id.startsWith("workflow:stage:"));
    const grouped = new Map<string, Group>(stages.map(node => [String(node.data.stage_id || node.id.slice("workflow:stage:".length)), { id: String(node.data.stage_id || node.id.slice("workflow:stage:".length)), node, entries: [] }]));
    const intervals = graph.analysis?.intervals ?? [];
    const times: number[] = [];
    for (const interval of intervals) {
      const node = operations.get(`operation:${interval.operation_id}`);
      const id = String(node?.data.parent_stage_id || "未归属阶段");
      if (!grouped.has(id)) grouped.set(id, { id, entries: [] });
      grouped.get(id)!.entries.push({ interval, node });
      if (validTime(interval.start_ms)) {
        times.push(interval.start_ms);
        if (validTime(interval.end_ms) && interval.end_ms >= interval.start_ms) times.push(interval.end_ms);
      }
    }
    for (const group of grouped.values()) group.entries.sort((a, b) => (a.interval.start_ms ?? Infinity) - (b.interval.start_ms ?? Infinity));
    const start = times.length ? Math.min(...times) : null;
    const end = times.length ? Math.max(...times) : null;
    return { groups: [...grouped.values()], start, end, count: intervals.length };
  }, [graph]);
  const scale = useMemo(() => timelineModel([{ id: "operations", source: "observed operations", counted: 0, fails: 0,
    attempts: (graph.analysis?.intervals ?? []).flatMap(interval => {
      if (!validTime(interval.start_ms)) return [];
      const finished = validTime(interval.end_ms) && interval.end_ms >= interval.start_ms;
      return [{ started_ts: new Date(interval.start_ms).toISOString(), role: "", open: !finished, counted: finished,
        terminal: finished ? { type: "observed", ts: new Date(interval.end_ms!).toISOString() } : null }];
    }) }], "", compressed), [graph, compressed]);
  let remaining = limit;
  const visible = groups.map(group => {
    const entries = group.entries.slice(0, remaining);
    remaining -= entries.length;
    return { ...group, entries };
  });
  const position = (value: number) => scale?.at(value) ?? 0;
  return <section className="sdlc-execution-timeline" aria-label="Workflow 执行时间线" data-testid="workflow-execution-timeline">
    <header><div><strong>执行时间线</strong><p>按阶段查看 Task 和 Agent 的实际执行；未观测到的时间不会估算。</p></div><span>{count} 个操作</span></header>
    <div className="sdlc-execution-controls" role="group" aria-label="时间比例"><button type="button" aria-pressed={!compressed} onClick={() => setCompressed(false)}>真实时间</button><button type="button" aria-pressed={compressed} onClick={() => setCompressed(true)}>压缩空档</button><span>{compressed ? scale?.gaps.length ? "长空档已压缩，横轴不等比例" : "没有超过 30 分钟的空档" : "横轴按真实时间比例"}</span></div>
    {start !== null && end !== null && <div className="sdlc-execution-axis"><span>{time(start)}</span><span>{time(end)}</span></div>}
    {!groups.length && <p className="sdlc-execution-empty">尚无阶段或执行记录。</p>}
    {visible.map(group => <section className="sdlc-execution-group" key={group.id}>
      <div className="sdlc-execution-stage"><strong>{group.node ? workflowNodeLabel(group.node) : group.id}</strong><span>{group.node ? statusLabels[group.node.status] || group.node.status : "阶段未绑定"} · {groups.find(item => item.id === group.id)?.entries.length || 0} 个操作</span>
        {group.node && <button type="button" onClick={() => onSelect(group.node!.id)}>阶段详情</button>}</div>
      {group.entries.map(({ interval, node }) => {
        const startTime = validTime(interval.start_ms) ? interval.start_ms : null;
        const endTime = startTime !== null && validTime(interval.end_ms) && interval.end_ms >= startTime ? interval.end_ms : null;
        const left = startTime !== null ? position(startTime) : 0;
        const width = endTime !== null ? Math.max(0, position(endTime) - left) : 0;
        const label = `${interval.task_id || "未关联 Task"} · ${interval.role || node?.label || "执行者未记录"}`;
        return <div className="sdlc-execution-row" key={interval.operation_id} data-testid="workflow-execution-row">
          <div className="sdlc-execution-label"><strong title={label}>{label}</strong><small>{statusLabels[interval.status] || interval.status} · {endTime !== null && startTime !== null ? durationLabel(endTime - startTime) : startTime !== null ? "结束时间未观测" : "开始时间未观测"}</small></div>
          <div className="sdlc-execution-track" aria-label={label}>
            {compressed && scale?.gaps.map(gap => <span key={gap.left} className="sdlc-execution-gap" style={{ left: `${gap.left}%`, width: `${gap.width}%` }} title={`压缩空档 ${durationLabel(gap.duration)}`} />)}
            {startTime !== null ? <button type="button" className={`sdlc-execution-bar ${endTime !== null ? "" : "is-point"} status-${interval.status}`} style={{ left: `${left}%`, width: endTime !== null ? `max(8px, ${width}%)` : "12px" }} disabled={!node} onClick={() => node && onSelect(node.id)} title={`${time(startTime)} · ${endTime !== null ? time(endTime) : "结束时间未观测"}`} aria-label={`${label} · ${endTime !== null ? durationLabel(endTime - startTime) : "结束时间未观测"}`} /> : <span className="sdlc-execution-unknown">时间未观测</span>}
          </div>
          {node && <button type="button" onClick={() => onSelect(node.id)}>详情</button>}
        </div>;
      })}
      {!group.entries.length && !groups.find(item => item.id === group.id)?.entries.length && <p className="sdlc-execution-empty">尚无已观测操作</p>}
    </section>)}
    {limit < count && <button type="button" onClick={() => setLimit(value => value + 50)}>加载更多操作</button>}
  </section>;
}
