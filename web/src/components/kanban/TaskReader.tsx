import { useEffect, useRef, useState, type ReactNode } from "react";
import { ArrowLeft, PanelRight } from "lucide-react";
import type { ActionResponse, RecentEvent, SkillsSummary, Task, TaskDetail as Model, TaskDiff, TaskTimeline } from "../../api/types";
import { isTaskBusinessEvent } from "../../app/refreshEventPolicy";
import type { ProjectionKind } from "../../app/sharedTypes";
import { asRecord, asStringArray, textValue } from "../../app/shared";
import { formatDateTime } from "../../lib/uiPreferences";
import { taskPriority, taskIsEffectivelyTerminal } from "../../lib/task-display";
import { MarkdownText } from "../agent-session/MarkdownText";
import { HandoffBindingDetails } from "./HandoffBindingDetails";
import { mergeEventsByIdentity } from "../orchestrator/kanbanSessionEvents";
import { RouteSummaryStrip } from "./TaskCard";
import { taskOwner, taskPhase, taskStatus } from "./taskViewModel";
import {
  AssignmentIntentPanel, ContractInvalidBanner, HandoffSummaryPanel, TaskActivityPanel,
  TaskAdvancedPanel, TaskEditPanel, TaskEvidencePanel, TaskRunSummaryPanel,
} from "./TaskDetail";

interface Props {
  projectId?: string;
  task: Task | null;
  selectedTaskId: string | null;
  detail: Model | null;
  timeline: TaskTimeline | null;
  timelineError: string | null;
  timelineLoading: boolean;
  diff: TaskDiff | null;
  events: RecentEvent[];
  skillsSummary: SkillsSummary | null;
  loadError: string | null;
  actionReady: boolean;
  actionState: string;
  actionResult: ActionResponse | null;
  onAction: (action: string, payload?: Record<string, unknown>) => void;
  onRetry?: () => void;
  onBackToBoard: () => void;
  onOpenTask?: (id: string) => void;
  onOpenProjection: (kind: ProjectionKind, id: string) => void;
  conversation?: ReactNode;
}

export function TaskReader(props: Props) {
  const { task, selectedTaskId, loadError, onBackToBoard } = props;
  const back = useRef<HTMLButtonElement>(null);
  useEffect(() => { back.current?.focus({ preventScroll: true }); }, [selectedTaskId]);
  const [properties, setProperties] = useState(() => !window.matchMedia("(max-width: 800px)").matches);
  useEffect(() => {
    const query = window.matchMedia("(max-width: 800px)");
    const resize = () => setProperties(!query.matches);
    query.addEventListener("change", resize);
    return () => query.removeEventListener("change", resize);
  }, []);
  return <section className="task-detail task-reader" aria-label="任务详情">
    <header className="reader-toolbar">
      <button type="button" ref={back} onClick={onBackToBoard}><ArrowLeft size={16} />返回看板</button>
      <span className="mono">{selectedTaskId}</span>
      {task && <button type="button" className="reader-properties-toggle" aria-expanded={properties} aria-controls="task-properties" onClick={() => setProperties(!properties)}><PanelRight size={16} />属性</button>}
    </header>
    {loadError && <p role="alert">任务详情读取失败：{loadError} {props.onRetry && <button type="button" onClick={props.onRetry}>重试</button>}</p>}
    {task ? <TaskReading key={task.id} {...props} task={task} properties={properties} /> :
      !loadError && <p role="status">{selectedTaskId ? "正在读取任务…" : "请选择一张任务卡。"}</p>}
  </section>;
}

function stageVerdict(state?: string): string {
  if (!state || state === "empty") return "";
  const labels: Record<string, string> = { failed: "失败", passed: "通过", rejected: "未通过", approved: "通过", observed: "已有记录" };
  return labels[state] ?? state;
}

function TaskReading({ task, detail, timeline, diff, events, projectId, properties, conversation, ...props }: Props & { task: Task; properties: boolean }) {
  const [evidenceOpen, setEvidenceOpen] = useState(false);
  const [activityOpen, setActivityOpen] = useState(false);
  const evidence = useRef<HTMLDetailsElement>(null);
  const activity = useRef<HTMLDetailsElement>(null);
  const showEvidence = () => { setEvidenceOpen(true); evidence.current?.scrollIntoView({ block: "start" }); };
  const showActivity = () => { setActivityOpen(true); activity.current?.scrollIntoView({ block: "start" }); };
  const handoff = detail?.handoff_summary;
  const handoffPanel = handoff && !handoff.empty ? <HandoffSummaryPanel detail={detail} /> : null;
  const activeHandoff = Boolean(handoff?.receipt?.delivery_status || handoff?.pipeline || (handoff?.receipt && handoff.receipt.status !== "unavailable"));
  const contract = detail?.contract ?? task.contract ?? {};
  const description = textValue(contract.behavior) || handoff?.objective;
  const progress = detail?.task_run_panel;
  const latest = asRecord(progress?.latest_progress);
  const progressMessage = textValue(latest.message || latest.current_subtask);
  const stage = handoff?.current_stage || progress?.current_stage_label || taskPhase(task);
  const owner = handoff?.pipeline ? textValue(handoff.owner?.instance_id || handoff.owner?.role) || "等待接手" : taskOwner(task);
  const attention = task.task_card?.attention ?? task.attention;
  const blocker = task.workflow_projection?.rework_reason || handoff?.blockers?.[0] || (attention?.required !== false && task.blocked_reason) || (attention?.required && attention.label);
  const verify = stageVerdict(detail?.verify?.state);
  const review = stageVerdict(detail?.review?.state);
  const files = diff?.files?.length ? diff.files : handoff?.changed_files ?? [];
  const artifacts = handoff?.evidence_refs ?? [];
  const source = [...new Set([textValue(contract.source_title || task.source), textValue(contract.spec_ref), textValue(contract.plan_ref)].filter(Boolean))];
  const wave = Number(contract.wave);
  if (wave > 0) source.push(`第 ${wave} 批`);
  const taskEvents = mergeEventsByIdentity(detail?.events ?? [], events.filter(e => e.task_id === task.id));
  const business = taskEvents.filter(event => isTaskBusinessEvent(event.type)).reverse();
  const trace = timeline?.links?.trace || timeline?.trace_id || detail?.links?.trace || detail?.trace_id || task.links?.trace || "";
  const terminal = taskIsEffectivelyTerminal(task);
  const issue = blocker || (detail?.verify?.state === "failed" && "验证失败") || (["failed", "rejected"].includes(detail?.review?.state ?? "") && "评审未通过");
  const next = handoff?.next_required_action;
  const showProgress = terminal || issue || progressMessage || (next && (handoff?.pipeline || progress?.active_operation || handoff?.receipt?.delivery_status));
  const fanout = task.fanout;
  const workflowRunId = textValue(handoff?.pipeline?.workflow_run_id || fanout?.workflow_run_id);
  return <div className="reader-layout" data-properties={properties}>
    <article className="reader-article">
      <h1>{task.title || task.id}</h1>
      {source.length > 0 && <p className="reader-source">来源 · {source.join(" / ")}</p>}
      <ContractInvalidBanner task={task} detail={detail} events={events} />
      {showProgress && <div className={issue && !terminal ? "reader-status attention" : "reader-status"}>
        <strong>{terminal ? taskStatus(task) : issue || stage}</strong>
        {next && !terminal && <p>下一步 · {next}</p>}
        {progressMessage && <p>{progressMessage}</p>}
        {(issue || terminal) && <button type="button" onClick={showEvidence}>查看{terminal ? "交付结果" : "验证依据"}</button>}
      </div>}
      {activeHandoff && handoffPanel}
      {description && description !== task.title && <section><h2>任务说明</h2><MarkdownText content={description} /></section>}
      <AcceptanceCriteria contract={contract} />
      {!activeHandoff && handoffPanel}
      <section><h2>本轮交付</h2>
        {(verify || review) && <p>{[verify && `验证 · ${verify}`, review && `评审 · ${review}`].filter(Boolean).join("　")}</p>}
        {files.length > 0 && <p>改动 {files.length} 个文件 · {files.slice(0, 3).join("、")}</p>}
        {artifacts.length > 0 && <ul>{artifacts.slice(0, 4).map((item, i) => <li key={i}>{textValue(item.title || item.path || item.ref || item.kind)}</li>)}</ul>}
        <details ref={evidence} open={evidenceOpen} onToggle={e => setEvidenceOpen(e.currentTarget.open)}>
          <summary>查看输入与交付证据</summary>
          {evidenceOpen && <TaskEvidencePanel detail={detail} diff={diff} projectId={projectId} />}
        </details>
      </section>
      <section><h2>动态</h2>
        {business.length ? <ol className="reader-events">{business.slice(0, 4).map((e, i) => <li key={e.id || i}>
          <time dateTime={e.ts} title={e.ts}>{e.ts && formatDateTime(e.ts, undefined, true)}</time><strong>{e.actor || "ZaoFu"}</strong><span>{textValue(e.payload?.summary || e.payload?.message || e.payload?.reason || e.type)}</span>
        </li>)}</ol> : <p className="muted">暂无任务动态</p>}
        <details ref={activity} open={activityOpen} onToggle={e => setActivityOpen(e.currentTarget.open)}>
          <summary>执行路线与更多动态</summary>
          {activityOpen && !timeline?.execution_route && !detail?.execution_route && task.route_summary && <RouteSummaryStrip route={task.route_summary} />}
          {activityOpen && <TaskActivityPanel detail={detail} events={business.slice(0, 20)} onOpenProjection={props.onOpenProjection}
            route={timeline?.execution_route ?? detail?.execution_route ?? null} timelineError={props.timelineError} timelineLoading={props.timelineLoading} traceId={trace} />}
        </details>
      </section>
      {conversation}
      <details><summary>执行现场</summary>
        <HandoffBindingDetails summary={handoff} onEvidence={showEvidence} onActivity={showActivity} />
        <TaskRunSummaryPanel task={task} detail={detail} onOpenProjection={props.onOpenProjection} />
        <TaskAdvancedPanel detail={detail} task={task} skillsSummary={props.skillsSummary} />
      </details>
    </article>
    <aside id="task-properties" className="reader-properties" hidden={!properties} aria-label="任务属性">
      <h2>任务属性</h2><dl><dt>状态</dt><dd>{taskStatus(task)}</dd><dt>阶段</dt><dd>{stage}</dd>
        <dt>负责人</dt><dd>{owner}</dd><dt>优先级</dt><dd>P{taskPriority(task)}</dd>
        {task.blocked_by?.length ? <><dt>依赖</dt><dd>{task.blocked_by.map(id => <button key={id} onClick={() => props.onOpenTask?.(id)}>{id}</button>)}</dd></> : null}
        {workflowRunId && <><dt>工作流运行</dt><dd>{workflowRunId}</dd></>}
        {fanout?.progress && <><dt>并行任务</dt><dd>{fanout.progress.done}/{fanout.progress.total} 完成 · {fanout.progress.failed} 失败</dd></>}
        {(["trace", "candidate", "fanout"] as const).map(kind => {
          const id = detail?.links?.[kind] || task.links?.[kind];
          return id && <dd key={kind}><button onClick={() => props.onOpenProjection(kind, id)}>{kind} · {id}</button></dd>;
        })}
      </dl>
      <details><summary>任务操作</summary>
        <TaskEditPanel actionReady={props.actionReady} actionState={props.actionState} detail={detail} task={task} onUpdate={p => props.onAction("update-task", p)} />
        <AssignmentIntentPanel actionReady={props.actionReady} actionState={props.actionState} task={task} onPropose={p => props.onAction("assignment-propose", p)} />
        {["dispatch-task","request-verify","request-review","ship-candidate"].map(action => <button key={action} disabled={!props.actionReady} onClick={() => props.onAction(action)}>{action}</button>)}
        {props.actionResult && <p role="status">{props.actionResult.status} {props.actionResult.reason}</p>}
      </details>
    </aside>
  </div>;
}

function AcceptanceCriteria({ contract }: { contract: Record<string, unknown> }) {
  const acceptance = textValue(contract.acceptance);
  const verification = textValue(contract.verification);
  const criteria = Array.isArray(contract.acceptance_criteria) ? contract.acceptance_criteria : [];
  const bindings = asRecord(contract.acceptance_evidence);
  if (!acceptance && !verification && !criteria.length) return null;

  return <section>
    <h2>验收条件</h2>
    {acceptance && <MarkdownText content={acceptance} />}
    {criteria.length > 0 && <ul>{criteria.map((item, index) => {
      const row = asRecord(item);
      const label = typeof item === "string" ? item : textValue(row.text || row.statement || row.criterion || row.description || row.acceptance);
      if (!label) return null;
      const keys = [textValue(row.id || row.acceptance_id), label, String(index)];
      const refs = [...new Set(keys.filter(Boolean).flatMap(key => asStringArray(bindings[key])))];
      return <li key={index}>
        {label}
        {refs.length > 0 && <details><summary>验收依据 · {refs.length}</summary>{refs.join("、")}</details>}
      </li>;
    })}</ul>}
    {verification && verification !== acceptance && <p>验证方式 · {verification}</p>}
  </section>;
}
