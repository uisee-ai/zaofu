import { useEffect, useState } from 'react';
import type { SdlcNode } from './sdlcGraphModel';

type Message = {sender: string; recipient: string; source_event_id: string; posted: string; seen: string; consumed: string};
type Member = {obligation_id: string; stage: string; role: string; status: string; instance: string; task_ids: string[]; required?: boolean; reused?: boolean; source_operation_id?: string; source_result_event_id?: string; original_work_unit_id?: string; coverage_owner?: string; disposition?: {reason:string};
  recipient?: string; question?: string; expected_output?: string; owner: string; last_event_at: string; status_since: string;
  source_event_id: string; evidence_ref?: {ref?: string; sha256?: string}; waiting_for?: string[]; operation_ids: string[]; attempt_ids: string[]; verdict?: string;
  attempt_history?: {event_id:string; event_type:string; time:string; instance:string; reason:string; attempt_id:string}[];
  last_activity?: {time:string; event_type:string}; history: {operation_id: string; status: string; attempt_id: string; reason: string}[]; messages: Message[]};
type Team = {optional_count?:number; optional_admitted_count?:number; optional_verdicts?:Record<string,number>; plan_inputs?:{admitted:number;total:number}; unreported_acceptance?:{task_id:string;acceptance_id:string}[]; communications?: Message[]; authority: string; members: Member[]; required_total: number | null; admitted_count: number;
  verdicts: Record<string, number>; unbound_deliveries?: {event_id:string; event_type:string; ts:string}[]; decisions?: {event_id:string; event_type:string; ts:string; actor:string}[]; diagnostic?: string; unbound_operation_ids: string[]; task_progress?: Record<string,{admitted:number; total:number}>};
const labels: Record<string,string> = { reassigned: '已撤出且覆盖已转交', waiting_inputs: '等待上游输入', dispatched: '已派发待领取', ready: '获准待派发', proposed: '已提议待准入', requested: '排队中', reserved: '已预留',
  running: '执行中', admitted: '结果已准入', awaiting_admission: '结果待准入', blocked: '阻塞', failed: '尝试失败',
  suspended: '待核实', superseded: '已替代', cancelled: '已撤销', settling: '等待结果', retrying: '重试中' };

export function WorkflowTeamRoster({node, details = false, task, onSelect}: {
  node: SdlcNode; details?: boolean; task?: string; onSelect?: (id: string) => void;
}) {
  const [filter, setFilter] = useState('');
  useEffect(() => setFilter(''), [node.id, task]);
  const team = node.data.team as Team | undefined;
  if (!team) return null;
  const members = team.members.filter(m => !task || m.task_ids.includes(task));
  if (task && !members.length) return null;

  const visible = details && filter ? members.filter(m => m.role === filter || m.instance === filter) : members;
  const links = (team.communications || []).filter(m => !filter || m.sender === filter || m.recipient === filter);
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
    {details && members.length > 1 && <label>筛选成员 <select value={filter} onChange={event => setFilter(event.target.value)}>
      <option value="">全部成员</option>{[...new Set(members.map(m => m.role))].map(role => <option key={role}>{role}</option>)}
    </select></label>}
    {details && links.length > 0 && <details><summary>沟通关系 · {links.length} 条交接</summary>
      <ul>{links.map(msg => <li key={msg.source_event_id + msg.recipient}><b>{msg.sender}</b> → <b>{msg.recipient}</b>
        <small> 发布 {msg.posted} · 已读 {msg.seen || '未观测'} · 接手 {msg.consumed || '未观测'}</small>
      </li>)}</ul></details>}
    <div className={details ? '' : 'stage-team-members nowheel'}>{visible.map(m => <div key={JSON.stringify([m.stage,m.obligation_id,m.task_ids])}>
      <b>{m.role}</b>{m.required === false && ' · 可选'}{m.reused && ' · 复用原意见'} · {labels[m.status] || m.status} · {m.instance || '实例待绑定'}
      {details && <details><summary>{m.obligation_id} · {m.task_ids.join('、') || m.stage} · 查看交接</summary>
        {m.disposition && <p>变更原因：{m.disposition.reason} · 覆盖责任人：{m.coverage_owner}</p>}
        {m.question && <p>{m.question}</p>}{m.expected_output && <p>交付物：{m.expected_output}</p>}
        <p>当前责任人：{m.owner} {m.recipient && `· 预定接收人：${m.recipient}`}</p>
        <p>状态起点：{m.status_since || '未知'} · 最近观测：{m.last_event_at || '未知'}</p>
        {m.last_activity && <p>最近活动：{m.last_activity.event_type} · {m.last_activity.time}</p>}
        <p>来源：{m.source_event_id || '未知'} {m.verdict && `· 判词：${m.verdict}`}</p>
        {m.reused && <p>原工作单元：{m.original_work_unit_id} · 原调用：{m.source_operation_id} · 原准入回执：{m.source_result_event_id}</p>}
        {m.evidence_ref?.ref && <p>准入产物：{m.evidence_ref.ref} · {m.evidence_ref.sha256}</p>}
        {!!m.waiting_for?.length && <p>等待义务：{m.waiting_for.join('、')}</p>}
        {!!m.attempt_ids.length && <p>尝试：{m.attempt_ids.join('、')}</p>}
        {!!m.attempt_history?.length && <details><summary>尝试历史</summary><ol>
          {m.attempt_history.map(h => <li key={h.event_id}>{h.time} · {h.event_type} · {h.attempt_id} {h.instance} {h.reason}</li>)}
        </ol></details>}
        {m.history.map(h => <button type="button" key={h.operation_id} onClick={() => onSelect?.('operation:' + h.operation_id)}>
          {h.operation_id} · {labels[h.status] || h.status} {h.reason}</button>)}
        {m.messages.map(msg => <p key={msg.source_event_id + msg.recipient}>{msg.sender} → {msg.recipient} · {msg.posted}
          {msg.seen && ` · 已读 ${msg.seen}`}{msg.consumed && ` · 接手 ${msg.consumed}`}</p>)}
      </details>}
    </div>)}</div>
    {details && team.unbound_operation_ids?.length > 0 && <p>归属待核实：{team.unbound_operation_ids.join('、')}</p>}
  </section>;
}


export function WorkflowTaskMembers({node}: {node:SdlcNode}) {
  const members = node.data.team_members as {stage:string; obligation_id:string; instance:string; status:string; owner:string}[] | undefined;
  return members?.length ? <div className="stage-team-members nowheel">{members.map(m =>
    <small key={m.stage + m.obligation_id}>{m.stage} · {m.instance || '待分配'} · {labels[m.status] || m.status}</small>
  )}</div> : null;
}
