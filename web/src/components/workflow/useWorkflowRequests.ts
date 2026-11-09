import { useCallback, useEffect, useRef, useState } from "react";
import type { MutableRefObject } from "react";
import { getWorkflowRequestDetail, getWorkflowRequests } from "../../api/client";
import type { WorkflowRequestDetail } from "../../api/types";
import { LatestRequestGate } from "../../app/latestRequestGate";
import { textValue } from "../../app/shared";
import type { WorkflowRequest } from "./workflowProposalModel";

// Owned by a project-keyed workbench. Selection changes invalidate detail reads
// immediately; polling shares one timer and never stacks the same read.
export function useWorkflowRequests(projectId: string, reviewBusy: MutableRefObject<boolean>) {
  const [requests, setRequests] = useState<WorkflowRequest[]>([]);
  const [selectedId, setSelection] = useState(() => new URLSearchParams(window.location.search).get("request") || "");
  const selection = useRef(selectedId);
  const [detail, setDetail] = useState<WorkflowRequestDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [listError, setListError] = useState("");
  const [detailError, setDetailError] = useState("");
  const listGate = useRef(new LatestRequestGate());
  const detailGate = useRef(new LatestRequestGate());
  const listFlight = useRef<AbortController | null>(null);
  const detailFlight = useRef<AbortController | null>(null);

  const setSelectedId = useCallback((id: string) => {
    if (selection.current === id) return;
    selection.current = id;
    detailGate.current.invalidate();
    detailFlight.current?.abort();
    detailFlight.current = null;
    reviewBusy.current = false;
    setDetail(null);
    setDetailError("");
    setSelection(id);
  }, [reviewBusy]);

  const loadRequests = useCallback(async (force = true) => {
    if (!projectId) { setLoading(false); return; }
    if (listFlight.current && !force) return;
    listFlight.current?.abort();
    const controller = new AbortController();
    listFlight.current = controller;
    const ticket = listGate.current.issue();
    try {
      const page = await getWorkflowRequests(projectId, controller.signal);
      if (!listGate.current.isCurrent(ticket) || controller.signal.aborted) return;
      setRequests(page.items);
      if (!page.items.some((item) => textValue(item.request_id) === selection.current)) {
        const taskId = new URLSearchParams(window.location.search).get("task") || "";
        const matches = taskId ? page.items.filter(item => textValue(item.task_id) === taskId) : [];
        const chosen = matches.find(item => ["running", "submitted"].includes(textValue(item.status))) ?? matches[0] ?? page.items[0];
        setSelectedId(textValue(chosen?.request_id));
      }
      setListError("");
    } catch (reason) {
      if (listGate.current.isCurrent(ticket) && !controller.signal.aborted) setListError(String(reason));
    } finally {
      if (listGate.current.isCurrent(ticket)) { listFlight.current = null; setLoading(false); }
    }
  }, [projectId, setSelectedId]);

  const loadDetail = useCallback(async (force = true) => {
    if (reviewBusy.current || !projectId || !selectedId || selection.current !== selectedId) return;
    if (detailFlight.current && !force) return;
    detailFlight.current?.abort();
    const controller = new AbortController();
    detailFlight.current = controller;
    const ticket = detailGate.current.issue();
    const current = () => detailGate.current.isCurrent(ticket)
      && !controller.signal.aborted && selection.current === selectedId && !reviewBusy.current;
    try {
      const next = await getWorkflowRequestDetail(selectedId, projectId, controller.signal);
      if (!current()) return;
      if (textValue(next.result.request_id) !== selectedId) throw new Error("Workflow detail identity mismatch");
      setDetail(next);
      setDetailError("");
    } catch (reason) {
      if (current()) { setDetail(null); setDetailError(String(reason)); }
    } finally {
      if (detailGate.current.isCurrent(ticket)) detailFlight.current = null;
    }
  }, [projectId, selectedId, reviewBusy]);

  useEffect(() => {
    void loadRequests(false);
    return () => {
      listGate.current.invalidate();
      detailGate.current.invalidate();
      listFlight.current?.abort();
      detailFlight.current?.abort();
      listFlight.current = null;
      detailFlight.current = null;
    };
  }, [loadRequests]);
  useEffect(() => { void loadDetail(false); }, [loadDetail]);

  const currentDetail = textValue(detail?.result.request_id) === selectedId ? detail : null;
  const preparing = ["queued", "running"].includes(textValue(currentDetail?.operation?.queue_status));
  useEffect(() => {
    const timer = window.setInterval(() => {
      void loadRequests(false);
      void loadDetail(false);
    }, preparing ? 1500 : 8000);
    return () => window.clearInterval(timer);
  }, [preparing, loadRequests, loadDetail]);

  return { requests, selectedId, setSelectedId, detail: currentDetail, loading,
    error: detailError || listError, loadRequests, loadDetail };
}
