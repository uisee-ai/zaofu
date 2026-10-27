import "../../styles/20-loop-timeline.css";
import { useMemo, useState } from "react";
import { Activity, ChevronDown, ChevronRight, Clock3, Layers3 } from "lucide-react";

import type { LoopViewProjection } from "../../api/types";
import { LoopAttemptDrawer } from "./LoopAttemptDrawer";
import { attemptRange, durationLabel, selectedAttemptIndices, timelineAttemptKey, timelineModel, timelineSegments, timelineTaskKey } from "./loopTimelineModel";

export function LoopTimeline({ view, openTask, onOpenTask }: {
  view: LoopViewProjection; openTask: string | null; onOpenTask: (key: string | null) => void;
}) {
  const [compressed, setCompressed] = useState(false);
  const [selection, setSelection] = useState<{ task: string; keys: string[] } | null>(null);
  const model = useMemo(() => timelineModel(view.tasks, view.run.last_ts, compressed), [view, compressed]);
  const rows = useMemo(() => model ? view.tasks.map((task) => timelineSegments(task, model)) : [], [view.tasks, model]);
  const attempts = view.tasks.reduce((count, task) => count + task.attempts.length, 0);
  const failures = view.tasks.reduce((count, task) => count + task.fails, 0);
  const timestamp = (value: number) => new Date(value).toISOString();
  const clock = (value: number) => timestamp(value).slice(11, 16);

  return <section className="loop-timeline" aria-label="Loop timeline" data-testid="loop-timeline">
    <header className="loop-timeline-header">
      <div className="loop-timeline-title"><Activity size={17} aria-hidden="true" /><div>
        <h3>Loop timeline</h3><p>Actual execution{view.run.run_id ? <span title={view.run.run_id}> · {view.run.run_id}</span> : null}</p>
      </div></div>
      <div className="loop-timeline-stats">
        {model ? <span title="Time between the first attempt and the last observed activity"><Clock3 size={14} />Observed span <strong>{durationLabel(model.observed - model.start)}</strong></span> : null}
        <span><Layers3 size={14} />Attempts <strong>{attempts}</strong></span>
        {failures > 0 ? <span className="loop-timeline-failures">Failed <strong>{failures}</strong></span> : null}
      </div>
    </header>
    {model ? <>
      <div className="loop-timeline-toolbar">
        <span>{timestamp(model.start).slice(0, 10)}{timestamp(model.observed).slice(0, 10) !== timestamp(model.start).slice(0, 10) ? ` – ${timestamp(model.observed).slice(0, 10)}` : ""} · UTC</span>
        <div className="loop-timeline-mode" role="group" aria-label="Time scale">
          <button type="button" aria-pressed={!compressed} onClick={() => setCompressed(false)}>Real time</button>
          <button type="button" aria-pressed={compressed} onClick={() => setCompressed(true)}>Compress gaps</button>
        </div>
      </div>
      <div className="loop-timeline-scroll" tabIndex={0} aria-label="Task execution timeline">
        <div className="loop-timeline-grid">
          <div className="loop-timeline-axis-row">
            <div className="loop-timeline-column-heading">Task / attempts</div>
            <div className="loop-v2-timeline-axis" data-testid="loop-time-axis">
              {model.ticks.map((tick) => <span key={tick.x} style={{ left: `${tick.x}%` }}
                data-edge={tick.x < 1 ? "start" : tick.x > 95 ? "end" : undefined} title={timestamp(tick.time)}>
                {model.end - model.start > 86400000 ? `${timestamp(tick.time).slice(8, 10)} ` : ""}{clock(tick.time)}
              </span>)}
            </div>
          </div>
          {view.tasks.map((task, index) => {
            const taskKey = timelineTaskKey(task);
            const opened = openTask === taskKey;
            const selectedIndices = opened && selection?.task === taskKey ? selectedAttemptIndices(task, selection.keys) : undefined;
            const row = rows[index];
            return <div className={`loop-v2-timeline-task ${opened ? "is-expanded" : ""}`}
              key={taskKey} id={`loop-row-${encodeURIComponent(taskKey)}`} data-testid="loop-task-row">
              <div className="loop-timeline-task-heading">
                <button type="button" className="loop-timeline-task-button" aria-expanded={opened}
                  onClick={() => { setSelection(null); onOpenTask(opened ? null : taskKey); }}>
                  {opened ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                  <span><span className="loop-v2-timeline-label" title={task.id}>{task.id}</span>
                    <span className="loop-v2-timeline-meta">{task.stage_id ? `${task.stage_id} · ` : ""}{task.counted} {task.counted === 1 ? "attempt" : "attempts"}{task.fails ? ` · ${task.fails} failed` : ""}</span>
                  </span>
                </button>
              </div>
              <div className="loop-v2-timeline-track" style={{ height: Math.max(48, row.lanes * 24 + 14) }}>
                {model.ticks.map((tick) => <span key={tick.x} className="loop-timeline-gridline" style={{ left: `${tick.x}%` }} />)}
                {model.gaps.map((gap) => <span key={gap.left} className="loop-timeline-gap"
                  style={{ left: `${gap.left}%`, width: `${gap.width}%` }} title={`${durationLabel(gap.duration)} gap compressed`} />)}
                <span className="loop-timeline-observed" style={{ left: `${model.at(model.observed)}%` }} aria-hidden="true" />
                {row.segments.map((segment) => {
                  const first = task.attempts[segment.indices[0]];
                  const range = attemptRange(first, model.observed);
                  const group = segment.indices.length > 1;
                  const label = group ? `${segment.indices.length} attempts · select to inspect`
                    : `Attempt ${segment.indices[0] + 1} · ${first.role || "Role unknown"} · ${range.known ? durationLabel(range.end - range.start) : "Duration unknown"} · ${first.terminal?.type || (first.counted ? "Open at last observation" : "Superseded")}`;
                  const point = segment.point || segment.right - segment.left < 0.8;
                  return <button key={segment.indices[0]} type="button" data-testid="loop-seg"
                    className={`loop-timeline-segment is-${segment.state} ${point ? "is-point" : ""} ${group ? "is-group" : ""}`}
                    style={{ left: `${segment.left}%`, width: point ? (group ? 40 : 16) : `${segment.right - segment.left}%`, top: 8 + segment.lane * 24 }}
                    aria-label={label} title={label} aria-pressed={Boolean(selectedIndices && selectedIndices.length === segment.indices.length && selectedIndices.every((value, offset) => value === segment.indices[offset]))}
                    onClick={() => { setSelection({ task: taskKey, keys: segment.indices.map((item) => timelineAttemptKey(task.attempts[item])) }); onOpenTask(taskKey); }}>
                    <span className="loop-timeline-segment-fill" />
                    <span className="loop-timeline-segment-text">{group ? `×${segment.indices.length}` : !point && segment.right - segment.left > 9 ? (first.terminal ? durationLabel(range.end - range.start) : "Open") : ""}</span>
                  </button>;
                })}
              </div>
              {opened ? <LoopAttemptDrawer key={`${index}:${selectedIndices?.join(",") ?? "all"}`} task={task}
                indices={selectedIndices} observed={model.observed} onShowAll={() => setSelection(null)} /> : null}
            </div>;
          })}
          <div className="loop-timeline-end-row"><span>{compressed ? "Compressed scale" : "Linear time scale"}</span>
            <span>{view.run.latched ? "End" : "Last observed"} · {clock(model.observed)} UTC</span>
          </div>
        </div>
      </div>
      <footer className="loop-timeline-footer">
        <div className="loop-timeline-legend"><span><i className="is-done" />Completed</span><span><i className="is-open" />Open</span>
          <span><i className="is-fail" />Failed</span><span><i className="is-uncounted" />Superseded</span><span>×N Grouped attempts</span></div>
        <details><summary>About this timeline</summary><p>Actual attempt records · {Array.from(new Set(view.tasks.map((task) => task.source))).join(", ")}. Open means no terminal result was observed; it does not confirm the worker is running. Points represent short events or unknown durations. Striped groups preserve individual records in their details.{compressed ? " Gaps over 30 minutes with no recorded execution are compressed." : " Waiting time remains visible on the linear scale."}</p></details>
      </footer>
    </> : <p className="loop-timeline-empty">No attempts recorded yet. The timeline appears with the first dispatch.</p>}
  </section>;
}
