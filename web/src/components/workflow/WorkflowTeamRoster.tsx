import { createContext, lazy, Suspense, useContext, useState, type ReactNode } from 'react';
import type { SessionTarget } from './WorkflowMemberSession';
const MemberSession = lazy(() => import('./WorkflowMemberSession'));
import type { SdlcNode } from './sdlcGraphModel';
import { durationLabel } from '../delivery-trace/loopTimelineModel';

export const TeamViewContext = createContext({clock: 0, historical: false, session: null as Pick<SessionTarget, 'scope' | 'generation'> | null, filters: {} as Record<string,string>,
  setFilter: (_key: string, _value: string) => {}});


type Message = {sender: string; recipient: string; source_event_id: string; posted: string; posted_seq?: number; seen_seq?: number; consumed_seq?: number; seen: string; consumed: string};
type Mailbox = {pending:number; unanswered:number; sent_unanswered:number; unknown:number; oldest_at:string; owner:string; deadline:string;
  last_claimed_at:string; last_answered_at:string; recovery_owner:string};
type Member = {mailbox?:Mailbox; instances?:string[]; selection_reason?:string; completion_condition?:string; admission_evidence_id?:string; judge_objectives?:string[]; source_seq?:number; attempt_count?:number; wait?:{reason:string; wake_event?:string; deadline?:string}; judge_questions?:{question_id:string;question:string}[]; integration_check_ids?:string[];
  obligation_id: string; stage: string; role: string; status: string; instance: string; task_ids: string[]; required?: boolean; reused?: boolean; source_operation_id?: string; source_result_event_id?: string; original_work_unit_id?: string; coverage_owner?: string; disposition?: {reason:string};
  recipient?: string; question?: string; expected_output?: string; owner: string; last_event_at: string; status_since: string;
  source_event_id: string; evidence_ref?: {ref?: string; sha256?: string}; waiting_for?: string[]; operation_ids: string[]; attempt_ids: string[]; verdict?: string;
  attempt_history?: {seq?:number; event_id:string; event_type:string; time:string; instance:string; reason:string; attempt_id:string}[];
  last_activity?: {kind?:string; time:string; event_type:string}; history: {operation_id: string; instance?:string; status: string; attempt_id: string; reason: string}[]; messages: Message[]};
type Team = {partial?:boolean; snapshot_at?:string; optional_count?:number; optional_admitted_count?:number; optional_verdicts?:Record<string,number>; plan_inputs?:{admitted:number;total:number}; unreported_acceptance?:{task_id:string;acceptance_id:string}[]; communications?: Message[]; authority: string; members: Member[]; required_total: number | null; admitted_count: number;
  verdicts: Record<string, number>; unbound_deliveries?: {event_id:string; event_type:string; ts:string}[]; decisions?: {event_id:string; event_type:string; ts:string; actor:string}[]; diagnostic?: string; unbound_operation_ids: string[]; task_progress?: Record<string,{admitted:number; total:number}>};
const labels: Record<string,string> = { reassigned: '已撤出且覆盖已转交', waiting_inputs: '等待上游输入', dispatched: '已派发待领取', ready: '获准待派发', proposed: '已提议待准入', requested: '排队中', reserved: '已预留',
  running: '执行中', admitted: '结果已准入', awaiting_admission: '结果待准入', blocked: '阻塞', failed: '尝试失败',
  suspended: '已中断待核实', superseded: '已替代', cancelled: '已撤销', settling: '等待结果', retrying: '重试中' };

function MemberHandoff({member, children}: {member: Member; children: ReactNode}) {
  const {session} = useContext(TeamViewContext);
  const latest = member.history?.at(-1);
  const target = session && ['running', 'retrying'].includes(member.status) && latest?.status === member.status
    && latest.operation_id && latest.attempt_id && latest.instance && latest.instance === member.instance
    ? {...session, operation_id:latest.operation_id, attempt_id:latest.attempt_id, instance:latest.instance,
       stage_id:member.stage, obligation_id:member.obligation_id} : null;
  return <HandoffContents key={JSON.stringify(target)} target={target}>{children}</HandoffContents>;
}

function HandoffContents({target, children}: {target: SessionTarget | null; children: ReactNode}) {
  const [open, setOpen] = useState(false);
  return <details onToggle={event => { if (event.target === event.currentTarget) setOpen(event.currentTarget.open); }}>
    {children}
    {open && target && <Suspense fallback={null}><MemberSession target={target}/></Suspense>}
  </details>;
}

export function WorkflowTeamRoster({node, details = false, task, onSelect, onReplay}: {
  node: SdlcNode; details?: boolean; task?: string; onSelect?: (id: string) => void; onReplay?: (seq:number) => void;
}) {
  const view = useContext(TeamViewContext);
  const key = JSON.stringify([node.id,task]);
  const filter = view.filters[key] || '';
  const [localFilter, setLocalFilter] = useState('');
  const selected = view.clock ? filter : localFilter;
  const changeFilter = (value:string) => {setLocalFilter(value); view.setFilter(key,value);};
  const team = node.data.team as Team | undefined;
  if (!team) return null;
  const members = team.members.filter(m => !task || m.task_ids.includes(task));
  if (task && !members.length) return null;

  const options = new Map<string,string>();
  for (const m of members) {
    if (!m.instance) options.set('obligation:' + m.obligation_id, m.obligation_id + ' · 待绑定');
    for (const instance of [m.instance, ...(m.instances || [])].filter(Boolean)) options.set('instance:' + instance,instance);
  }
  const instance = selected.startsWith('instance:') ? selected.slice('instance:'.length) : '';
  const visible = !details || !selected ? members : members.filter(m => instance
    ? m.instance === instance || m.instances?.includes(instance) : 'obligation:' + m.obligation_id === selected);
  const memberMessages = (m:Member) => (m.messages || []).filter(msg => !instance || msg.sender === instance || msg.recipient === instance);
  const links = task || selected ? [...new Map(visible.flatMap(memberMessages).map(msg =>
    [JSON.stringify([msg.source_event_id,msg.recipient]),msg])).values()] : team.communications || [];
  const clock = view.historical ? Date.parse(team.snapshot_at || "") : view.clock || Date.now();
  const replay = (seq:number | undefined, label:string) => onReplay && seq && seq > 0
    ? <button type="button" onClick={() => onReplay(seq)}>{label} · #{seq}</button> : null;
  return <section className="stage-team-roster" aria-label="阶段分工">
    <strong>{task ? String(node.data.stage_id) : '阶段分工'}</strong>
    <p>{team.required_total === null ? (members.length ? `已提议 ${members.length} 项 · 未计入准入总数` : team.authority === 'waiting_freeze' ? '等待冻结 Reader 名单' : team.authority === 'waiting_oa' ? '等待 OA 提交本代分工' : '历史名单未记录 · 总数未知')
      : `已准入 ${team.admitted_count}/${team.required_total} 项义务`}</p>
    {!!team.optional_count && <p>可选意见：{team.optional_admitted_count || 0}/{team.optional_count} 已准入
      {Object.entries(team.optional_verdicts || {}).map(([v,n]) => ` · ${v} × ${n}`).join('')}</p>}
    {team.plan_inputs && <p>必需专家输入 {team.plan_inputs.admitted}/{team.plan_inputs.total} · 正式 Plan 决定 {team.decisions?.length || 0} 条</p>}
    {!!team.unreported_acceptance?.length && <p>尚无判词的验收项：{team.unreported_acceptance.map(a => a.task_id + ' / ' + a.acceptance_id).join('、')}</p>}
    {!!Object.keys(team.verdicts || {}).length && <p>{Object.entries(team.verdicts).map(([v, n]) => `${v} × ${n}`).join('、')}</p>}
    {team.decisions?.map(d => <p key={d.event_id}>{d.event_type} · {d.actor} · {d.ts}</p>)}
    {team.unbound_deliveries?.map(d => <p key={d.event_id}>其他或待核实版本的交付：{d.event_type} · {d.ts} · {d.event_id}</p>)}
    {team.diagnostic && <p>{team.diagnostic}</p>}
    {Object.entries(team.task_progress || {}).map(([stage, progress]) => <p key={stage}>{stage} · 业务任务 {progress.admitted}/{progress.total}</p>)}
    {details && options.size > 1 && <label>筛选成员 <select value={selected} onChange={event => changeFilter(event.target.value)}>
      <option value="">全部成员</option>{[...options].map(([id,label]) => <option key={id} value={id}>{label}</option>)}
    </select></label>}
    {details && view.historical && <button type="button" onClick={() => onReplay?.(0)}>返回实时分工</button>}
    {details && links.length > 0 && <details><summary>沟通关系 · {links.length} 条交接</summary>
      <ul>{links.map(msg => <li key={msg.source_event_id + msg.recipient}><b>{msg.sender}</b> → <b>{msg.recipient}</b>
        <small> 发布 {msg.posted} · 已读 {msg.seen || '未观测'} · 已消费 {msg.consumed || '未观测'}</small>
        {replay(msg.posted_seq, '回看发布')}{replay(msg.seen_seq, '回看已读')}{replay(msg.consumed_seq, '回看消费')}
      </li>)}</ul></details>}
    <div className={details ? '' : 'stage-team-members nowheel'}>{visible.map(m => <div key={JSON.stringify([m.stage,m.obligation_id,m.task_ids])}>
      <b>{m.role}</b>{m.required === false && ' · 可选'}{m.reused && ' · 复用原意见'} · {labels[m.status] || m.status} · {m.instance || '实例待绑定'}
      <small className="team-member-facts">
        {m.task_ids.length ? m.task_ids.join('、') + ' · ' : ''}{m.question || m.expected_output || m.obligation_id}
        {m.attempt_count ? ` · 第 ${m.attempt_count} 次尝试` : ''}
        {Number.isFinite(clock) && Number.isFinite(Date.parse(m.status_since)) && <span title={m.status_since}> · 当前状态 {durationLabel(clock - Date.parse(m.status_since))}</span>}
        {m.last_activity ? ` · ${m.last_activity.kind === 'heartbeat' ? '心跳' : m.last_activity.event_type} ${m.last_activity.time}`
                         : m.last_event_at && ` · 最近观测 ${m.last_event_at}`}
        {m.recipient && ` · 交给 ${m.recipient}`}{m.owner && ` · 责任人 ${m.owner}`}
        {m.mailbox && <span> · 待接手 {m.mailbox.pending} · 已接手待答 {m.mailbox.unanswered} · 已发待答 {m.mailbox.sent_unanswered} · 交付待核实 {m.mailbox.unknown}
          {m.mailbox.oldest_at && ` · 最早等待 ${m.mailbox.oldest_at}`}{m.mailbox.owner && ` · 邮件责任人 ${m.mailbox.owner}`}
          {m.mailbox.deadline && ` · 邮件截止 ${m.mailbox.deadline}`}{m.mailbox.last_claimed_at && ` · 最近接手 ${m.mailbox.last_claimed_at}`}
          {m.mailbox.last_answered_at && ` · 最近回答 ${m.mailbox.last_answered_at}`}{m.mailbox.recovery_owner && ` · 恢复责任人 ${m.mailbox.recovery_owner}`}</span>}
        {m.wait && ` · ${m.wait.reason} · 唤醒 ${m.wait.wake_event || '待核实'}`}{m.wait?.deadline && ` · 截止 ${m.wait.deadline}`}
      </small>
      {details && !team.partial && <MemberHandoff member={m}><summary>{m.obligation_id} · {m.task_ids.join('、') || m.stage} · 查看交接</summary>
        {m.disposition && <p>变更原因：{m.disposition.reason} · 覆盖责任人：{m.coverage_owner}</p>}
        {m.selection_reason && <p>选人依据：{m.selection_reason}</p>}
        {m.completion_condition && <p>完成条件：{m.completion_condition}</p>}
        {m.admission_evidence_id && <p>增员依据：{m.admission_evidence_id}</p>}
        {m.judge_objectives?.map(goal => <p key={goal}>裁决目标：{goal}</p>)}
        {m.judge_questions?.map(q => <p key={q.question_id}>{q.question_id} · {q.question}</p>)}
        {!!m.integration_check_ids?.length && <p>跨任务检查：{m.integration_check_ids.join('、')}</p>}
        {m.question && <p>{m.question}</p>}{m.expected_output && <p>交付物：{m.expected_output}</p>}
        <p>当前责任人：{m.owner} {m.recipient && `· 预定接收人：${m.recipient}`}</p>
        <p>状态起点：{m.status_since || '未知'} · 最近观测：{m.last_event_at || '未知'}</p>
        {m.last_activity && <p>最近活动：{m.last_activity.event_type} · {m.last_activity.time}</p>}
        <p>来源：{m.source_event_id || '未知'} {m.verdict && `· 判词：${m.verdict}`}</p>
        {m.source_seq && <p>{replay(m.source_seq - 1, '回看状态之前')}{replay(m.source_seq, '回看此状态')}</p>}
        {m.reused && <p>原工作单元：{m.original_work_unit_id} · 原调用：{m.source_operation_id} · 原准入回执：{m.source_result_event_id}</p>}
        {m.evidence_ref?.ref && <p>准入产物：{m.evidence_ref.ref} · {m.evidence_ref.sha256}</p>}
        {!!m.waiting_for?.length && <p>等待义务：{m.waiting_for.join('、')}</p>}
        {!!m.attempt_ids?.length && <p>尝试：{m.attempt_ids.join('、')}</p>}
        {!!m.attempt_history?.length && <details><summary>尝试历史</summary><ol>
          {m.attempt_history.map(h => <li key={h.event_id}>{h.time} · {h.event_type} · {h.attempt_id} {h.instance} {h.reason} {replay(h.seq, '回看尝试')}</li>)}
        </ol></details>}
        {m.history?.map(h => <button type="button" key={h.operation_id} onClick={() => onSelect?.('operation:' + h.operation_id)}>
          {h.operation_id} · {labels[h.status] || h.status} {h.reason}</button>)}
        {memberMessages(m).map(msg => <p key={msg.source_event_id + msg.recipient}>{msg.sender} → {msg.recipient} · {msg.posted}
          {msg.seen && ` · 已读 ${msg.seen}`}{msg.consumed && ` · 已消费 ${msg.consumed}`}</p>)}
      </MemberHandoff>}
    </div>)}</div>
    {details && team.unbound_operation_ids?.length > 0 && <p>归属待核实：{team.unbound_operation_ids.join('、')}</p>}
  </section>;
}


export function WorkflowTaskMembers({node}: {node:SdlcNode}) {
  const members = node.data.team_members as {stage:string; obligation_id:string; instance:string; status:string; owner:string; attempt_count?:number}[] | undefined;
  return members?.length ? <div className="stage-team-members nowheel">{members.map(m =>
    <small key={m.stage + m.obligation_id}>{m.stage} · {m.instance || '待分配'} · {labels[m.status] || m.status}{m.attempt_count ? ` · 第 ${m.attempt_count} 次尝试` : ''}{!m.instance && m.owner && ` · 责任人 ${m.owner}`}</small>
  )}</div> : null;
}
