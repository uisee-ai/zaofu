import { useEffect, useRef, useState } from 'react';
import { localWebActionToken } from '../../api/dashboardLaunch';
import type { SdlcGraph } from './sdlcGraphModel';

export type SessionTarget = {
  scope: SdlcGraph['scope']; generation: string; stage_id: string; obligation_id: string;
  operation_id: string; attempt_id: string; instance: string;
};
type Reply = { available: boolean; text?: string; at?: string; event_id?: string; excerpted?: boolean };

async function fetchReply(target: SessionTarget, read: boolean, signal: AbortSignal): Promise<Reply | null> {
  const token = localWebActionToken();
  const { project_id, request_id } = target.scope;
  if (!project_id) return null;
  const url = `/api/projects/${encodeURIComponent(project_id)}/workflow-requests/${encodeURIComponent(request_id)}/member-session`;
  const response = await fetch(url, {
    method: 'POST', cache: 'no-store', credentials: 'same-origin', signal,
    headers: { 'Content-Type': 'application/json', ...(token ? { 'X-ZF-Web-Token': token } : {}) },
    body: JSON.stringify({ ...target, read }),
  });
  return response.ok ? await response.json() : null;
}

export default function WorkflowMemberSession({target}: {target: SessionTarget}) {
  const [available, setAvailable] = useState(false);
  const [reply, setReply] = useState<Reply | null>(null);
  const [loading, setLoading] = useState(false);
  const binding = JSON.stringify(target);
  const request = useRef<AbortController | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    request.current = controller;
    const check = window.setTimeout(() => fetchReply(target, false, controller.signal).then(result => {
      if (!controller.signal.aborted) setAvailable(result?.available === true);
    }).catch(() => {}), 0);
    return () => { window.clearTimeout(check); controller.abort(); };
  }, [binding]);
  async function open() {
    const controller = request.current;
    if (!controller || loading) return;
    setLoading(true);
    try {
      const result = await fetchReply(target, true, controller.signal);
      if (!controller.signal.aborted) {
        if (result?.available && typeof result.text === 'string') setReply(result);
        else setAvailable(false);
      }
    } catch { if (!controller.signal.aborted) setAvailable(false); }
    finally { if (!controller.signal.aborted) setLoading(false); }
  }
  if (!available) return null;
  if (!reply) return <button type="button" onClick={open} disabled={loading}>查看会话摘录</button>;
  return <section aria-label="会话摘录"><strong>会话摘录</strong><p>{reply.text}</p>
    <small>{reply.at} · {reply.event_id}{reply.excerpted && ' · 已截取'}</small></section>;
}
