import { lazy, Suspense, useEffect, useRef, useState, type ReactNode } from "react";
import { ArrowLeft, PanelRightOpen, Pencil } from "lucide-react";
import type { ActionResponse, Task, TaskDetail } from "../../api/types";
import { asRecord, asStringArray, textValue } from "../../app/shared";
import { taskIsEffectivelyTerminal } from "../../lib/task-display";
import { TaskReadingBody } from "./TaskReadingBody";
import { TaskProperties } from "./TaskProperties";
import { TaskEditPanel } from "./TaskEditPanel";
import { latestInvalidContract, taskExecution } from "./taskExecution";

interface Props {
  projectId?: string;
  task: Task | null;
  selectedTaskId: string | null;
  detail: TaskDetail | null;
  loadError: string | null;
  actionReady: boolean;
  actionState: string;
  actionResult: ActionResponse | null;
  onAction: (action: string, payload?: Record<string, unknown>) => void;
  onRetry?: () => void;
  onBackToBoard: () => void;
  onOpenTask?: (id: string) => void;
  conversation?: ReactNode;
}

export function TaskReader(props: Props) {
  const { selectedTaskId, detail, loadError, projectId, onOpenTask } = props;
  const task = detail?.task ?? props.task;
  const back = useRef<HTMLButtonElement>(null);
  useEffect(() => { back.current?.focus({ preventScroll: true }); }, [selectedTaskId]);
  const toggle = useRef<HTMLButtonElement>(null);
  const drawer = useRef<HTMLDialogElement>(null);
  const wideQuery = "(min-width: 1200px)";
  const [wide, setWide] = useState(() => window.matchMedia(wideQuery).matches);
  const [expanded, setExpanded] = useState(() => {
    try { return localStorage.getItem("zf.taskProperties.expanded") !== "false"; } catch { return true; }
  });
  const [drawerOpen, setDrawerOpen] = useState(false);
  useEffect(() => {
    const media = window.matchMedia(wideQuery);
    const resize = () => { setWide(media.matches); setDrawerOpen(false); };
    media.addEventListener("change", resize);
    return () => media.removeEventListener("change", resize);
  }, []);
  useEffect(() => {
    if (drawerOpen && !wide) drawer.current?.showModal();
    else drawer.current?.close();
  }, [wide, drawerOpen]);
  function setProperties(open: boolean) {
    if (wide) {
      setExpanded(open);
      try { localStorage.setItem("zf.taskProperties.expanded", String(open)); } catch { /* Session preference still applies. */ }
    } else setDrawerOpen(open);
    if (!open) toggle.current?.focus();
  }
  const execution = task ? taskExecution(task, detail) : null;
  const properties = task && <TaskProperties task={task} detail={detail} projectId={projectId}
    worker={execution?.worker} onOpenTask={onOpenTask} onClose={() => setProperties(false)} />;
  const contract = detail?.contract ?? task?.contract ?? {};
  return <section className="task-detail task-reader" aria-label="任务详情">
    <header className="reader-toolbar">
      <button type="button" ref={back} onClick={props.onBackToBoard}><ArrowLeft size={16} />返回看板</button>
      <span className="mono">{selectedTaskId}</span>
      {task && <>
        <button type="button" className="reader-props-toggle" ref={toggle} aria-label="任务属性"
          aria-expanded={wide ? expanded : drawerOpen} aria-controls="task-properties"
          onClick={() => setProperties(!(wide ? expanded : drawerOpen))}><PanelRightOpen size={16} />属性</button>
        <TaskOptions key={task.id} {...props} task={task} />
      </>}
    </header>
    {loadError && <p role="alert">任务详情读取失败：{loadError} <button onClick={props.onRetry}>重试</button></p>}
    {selectedTaskId ? <div className="reader-layout">
      <div className="reader-main"><article className="reader-article" key={selectedTaskId}>
        {task && execution && <header className="reader-heading">
          <h1>{task.title || task.id}</h1>
          <TaskStatus execution={execution} task={task} detail={detail} projectId={projectId} onOpenTask={onOpenTask} />
        </header>}
        {task ? <>
          <TaskReadingBody contract={contract} title={task.title} objective={detail?.handoff_summary?.objective} />
          <TaskResults detail={detail} projectId={projectId} />
        </> : !loadError && <p role="status">正在读取任务…</p>}
        <TaskSkillEvaluationDisclosure key={JSON.stringify([projectId, selectedTaskId])} taskId={selectedTaskId} projectId={projectId} />
      </article>
      {props.conversation}</div>
      {task && (wide ? expanded && <aside id="task-properties" aria-label="任务属性" className="reader-properties">{properties}</aside>
        : <dialog id="task-properties" ref={drawer} aria-label="任务属性" className="reader-properties reader-drawer"
            onClose={() => setDrawerOpen(false)} onClick={event => {
              if (event.target === drawer.current && event.clientX < drawer.current.getBoundingClientRect().left) setProperties(false);
            }}>{drawerOpen && properties}</dialog>)}
    </div> : !loadError && <p role="status">请选择一张任务卡。</p>}
  </section>;
}

const TaskSkillEvaluations = lazy(() => import("./TaskSkillEvaluations").then(module => ({ default: module.TaskSkillEvaluations })));

function TaskSkillEvaluationDisclosure({ taskId, projectId }: { taskId: string; projectId?: string }) {
  const [open, setOpen] = useState(false);
  return <section>
    <details onToggle={event => setOpen(event.currentTarget.open)}>
      <summary>任务 Skill 评估</summary>
      {open && (projectId
        ? <Suspense fallback={<p role="status">正在加载任务评估…</p>}><TaskSkillEvaluations taskId={taskId} projectId={projectId} /></Suspense>
        : <p role="status">正在确认任务所属项目…</p>)}
    </details>
  </section>;
}

function TaskOptions({ task, detail, ...props }: Props & { task: Task }) {
  const menu = useRef<HTMLDetailsElement>(null);
  useEffect(() => {
    const dismiss = (event: PointerEvent) => {
      if (menu.current?.open && !menu.current.contains(event.target as Node)) menu.current.open = false;
    };
    document.addEventListener("pointerdown", dismiss);
    return () => document.removeEventListener("pointerdown", dismiss);
  }, []);
  return <details ref={menu} className="reader-options" onKeyDown={event => {
    if (event.key === "Escape" && menu.current?.open) {
      event.preventDefault();
      menu.current.open = false;
      menu.current.querySelector("summary")?.focus();
    }
  }}>
    <summary aria-label="编辑任务" title="编辑任务"><Pencil size={15} /></summary>
    <div className="reader-options-body">
        <TaskEditPanel actionReady={props.actionReady} actionState={props.actionState} detail={detail} task={task}
          onUpdate={p => props.onAction("update-task", p)} />
        {props.actionResult && <p role="status">{props.actionResult.status} {props.actionResult.reason}</p>}
    </div>
  </details>;
}

function Dependencies({ ids, onOpenTask }: { ids: string[]; onOpenTask?: (id: string) => void }) {
  return ids.map(id => onOpenTask ? <button type="button" key={id} onClick={() => onOpenTask(id)}>{id}</button> : <span key={id}>{id}</span>);
}

function TaskStatus({ execution, task, detail, projectId, onOpenTask }: Pick<Props, "detail" | "projectId" | "onOpenTask"> & { task: Task; execution: ReturnType<typeof taskExecution> }) {
  const terminal = taskIsEffectivelyTerminal(task);
  const pipeline = detail?.handoff_summary?.pipeline;
  const blockers = asStringArray(pipeline?.blockers);
  const dependencies = pipeline?.pipeline_stage === "dependency_blocked" ? blockers.filter(id => task.blocked_by?.includes(id)) : [];
  const attention = task.task_card?.attention ?? task.attention;
  const invalid = !terminal && latestInvalidContract(detail?.events ?? []);
  let reason = "";
  if (!terminal && (!pipeline || pipeline.status === "current")) {
    if (pipeline) {
      if (pipeline.pipeline_stage === "blocked" || execution.manualEvidence) reason = task.blocked_reason || "";
      if (!reason && !dependencies.length && !execution.manualEvidence) {
        const reasons: Record<string, string> = {
          verify_execution_failed: "验证执行失败", semantic_rework_exhausted: "已达到返工次数上限",
          verify_only_verification_rejected: "验证未通过", integration_admission_not_admitted: "集成尚未获准",
          integration_acceptance_replan_required: "验收要求重新规划",
        };
        reason = reasons[blockers[0]] || "";
      }
    } else {
      reason = task.workflow_projection?.rework_reason || (attention?.required !== false && task.blocked_reason) ||
        (detail?.verify?.state === "failed" && "验证失败") ||
        (["failed", "rejected"].includes(detail?.review?.state ?? "") && "评审未通过") || "";
    }
  }
  const required = asRecord((detail?.contract ?? task.contract)?.evidence_contract).required_manual_evidence;
  const refs = asRecord(required);
  const materials = typeof required === "string" ? [required] : asStringArray(Array.isArray(required) ? required : refs.refs || refs.required_refs || refs.artifacts);
  return <div className="reader-status" aria-label="任务状态">
    <span className={reason || invalid || execution.manualEvidence || dependencies.length ? "attention" : ""}>{invalid ? "待补充任务信息" : execution.label}</span>
    {reason && reason !== execution.label && <span> · {reason}</span>}
    {dependencies.length > 0 && <Dependencies ids={dependencies} onOpenTask={onOpenTask} />}
    {execution.manualEvidence && <>
      {materials.length > 0 && <span> · {materials.join("、")}</span>}
      <a href={`?page=runtime&project=${encodeURIComponent(projectId || "")}`}>查看运行处置请求</a>
    </>}
  </div>;
}

function TaskResults({ detail, projectId }: Pick<Props, "detail" | "projectId">) {
  if (!detail) return null;
  const links = new Map<string, string>();
  if (projectId) for (const item of detail.deliverables ?? []) {
    links.set(`/api/projects/${encodeURIComponent(projectId)}/tasks/${encodeURIComponent(detail.task.id)}/deliverables/${encodeURIComponent(item.sha256)}`, item.name);
  }
  const refs = Object.values(asRecord(detail.contract?.acceptance_evidence)).flatMap(asStringArray);
  for (const item of detail.handoff_summary?.evidence_refs ?? []) {
    if (item.kind === "accepted_artifact_refs" || ["accepted", "verified", "passed"].includes(textValue(item.status))) {
      refs.push(textValue(item.path));
    }
  }
  for (const ref of refs) {
    if (!/^https?:\/\//i.test(ref) || !URL.canParse(ref)) continue;
    const url = new URL(ref);
    if (url.username || url.password) continue;
    const pr = url.pathname.match(/\/(?:pull|merge_requests)\/(\d+)\/?$/);
    links.set(url.href, pr ? `PR #${pr[1]}` : url.pathname.split("/").filter(Boolean).pop() || url.hostname);
  }
  const results = Array.from(links, ([href, name]) => <a key={href} href={href} title={href} target="_blank" rel="noreferrer">{name}</a>);
  if (!results.length) return null;
  return <section className="reader-results"><h2>交付结果</h2>{results[0]}
    {results.length > 1 && <details><summary>另 {results.length - 1} 项</summary>{results.slice(1)}</details>}
  </section>;
}
