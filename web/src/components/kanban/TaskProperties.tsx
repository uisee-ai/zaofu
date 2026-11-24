import { useEffect, useState } from "react";
import { ArrowUpRight, PanelRightClose } from "lucide-react";
import type { Task, TaskDetail, TaskRelation } from "../../api/types";
import { textValue } from "../../app/shared";
import { formatTime } from "../../lib/format";
import { taskPriority } from "../../lib/task-display";

interface Props {
  task: Task;
  detail: TaskDetail | null;
  projectId?: string;
  worker?: string;
  onOpenTask?: (id: string) => void;
  onClose: () => void;
}

const statusNames: Record<string, string> = {
  backlog: "待办", ready: "待调度", in_progress: "进行中", review: "评审中", testing: "测试中",
  blocked: "受阻", done: "已完成", cancelled: "已取消", superseded: "已被替代", archived: "已归档",
};

function MilestoneTime({ at }: { at: string }) {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 60_000);
    return () => window.clearInterval(timer);
  }, []);
  const minutes = Math.floor((now - Date.parse(at)) / 60_000);
  if (!Number.isFinite(minutes)) return null;
  const relative = minutes < 0 ? formatTime(at) : minutes === 0 ? "刚刚" : minutes < 60 ? `${minutes} 分钟前`
    : minutes < 1440 ? `${Math.floor(minutes / 60)} 小时前` : `${Math.floor(minutes / 1440)} 天前`;
  return <time dateTime={at} title={formatTime(at)}>{relative}</time>;
}

export function TaskProperties({ task, detail, worker, projectId, onOpenTask, onClose }: Props) {
  const info = detail?.properties;
  const contract = detail?.contract ?? task.contract ?? {};
  const sourceTitle = textValue(contract.source_title || task.source);
  const source = info?.source;
  const sourceRef = textValue(contract.source_ref);
  let sourceUrl: URL | undefined;
  if (/^https?:\/\//i.test(sourceRef) && URL.canParse(sourceRef)) {
    const url = new URL(sourceRef);
    if (!url.username && !url.password) sourceUrl = url;
  }
  const upstream: TaskRelation[] = info?.upstream ?? (task.blocked_by ?? []).map(id => ({ id, title: "", status: "" }));
  const downstream = info?.downstream ?? [];
  const children = info?.children ?? [];
  const fanout = detail?.fanout ?? task.fanout;
  const progress = fanout?.progress;
  const project = encodeURIComponent(projectId ?? "");
  const taskLink = (id: string, label: string) => <a href={`?project=${project}&page=task&task=${encodeURIComponent(id)}`}
    onClick={event => {
      if (onOpenTask && !event.ctrlKey && !event.metaKey && !event.shiftKey && !event.altKey) {
        event.preventDefault();
        onOpenTask(id);
      }
    }}>{label}</a>;
  const relationList = (rows: TaskRelation[]) => <ul className="reader-relations">{rows.map(row => <li key={row.id}>
    {taskLink(row.id, row.title || row.id)}
    <small>{[row.title && row.title !== row.id ? row.id : "", statusNames[row.status] || row.status, row.waiting ? "等待本任务" : ""].filter(Boolean).join(" · ")}</small>
  </li>)}</ul>;
  const sourceLabel = sourceTitle === "task_map_materialization" ? "任务计划" : sourceTitle;
  return <>
    <header className="reader-properties-head"><h2>任务属性</h2>
      <button type="button" aria-label="收起任务属性" onClick={onClose}><PanelRightClose size={16} /></button>
    </header>
    <dl className="reader-property-rows">
      <dt>优先级</dt><dd>P{taskPriority(task)}</dd>
      {(source || sourceLabel || sourceUrl) && <><dt>来源</dt><dd>
        {source?.kind === "task" ? taskLink(source.id, source.title || source.id)
          : source?.kind === "feature" ? <a href={`?page=delivery-trace&project=${project}&feature=${encodeURIComponent(source.id)}`}>{source.title || source.id}</a>
          : sourceUrl ? <a href={sourceUrl.href} target="_blank" rel="noreferrer">{sourceLabel || sourceUrl.hostname}</a>
          : sourceLabel}
      </dd></>}
      {worker && <><dt>当前执行者</dt><dd>{worker}</dd></>}
      {info?.milestone && <><dt>最近进展</dt><dd>{info.milestone.label}<small><MilestoneTime at={info.milestone.at} /></small></dd></>}
      {children.length ? <><dt>子任务</dt><dd><details><summary>{children.filter(row => row.status === "done").length} / {children.length} 已完成</summary>{relationList(children)}</details></dd></>
        : progress && progress.total > 0 && <><dt>并行进度</dt><dd>
          {fanout?.fanout_id ? <a href={`?page=fanouts&project=${project}&fanout_id=${encodeURIComponent(fanout.fanout_id)}`}>{progress.done} / {progress.total} 已结束</a>
            : <span>{progress.done} / {progress.total} 已结束</span>}
          {progress.failed > 0 && <small>{progress.failed} 项异常</small>}
        </dd></>}
    </dl>
    {(upstream.length > 0 || downstream.length > 0) && <section className="reader-related">
      <h2>关联任务</h2>
      {upstream.length > 0 && <><h3>依赖任务</h3>{relationList(upstream)}</>}
      {downstream.length > 0 && <><h3>下游任务</h3>{relationList(downstream)}</>}
    </section>}
    <a className="reader-events-link" href={`?page=events&project=${project}&task=${encodeURIComponent(task.id)}`}>查看任务事件 <ArrowUpRight size={14} /></a>
  </>;
}
