import { useEffect, useState } from "react";
import { getChannelPrdDocument, type ChannelPrdDocument, type ChannelPrdSelection } from "../../api/channelDelivery";

const bareDigest = (value: unknown) => String(value || "").replace(/^sha256:/, "");
const describe = (value: unknown) => {
  if (typeof value === "string") return value;
  const row = value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
  const label = [row.description, row.statement, row.reason, row.summary, row.title]
    .find(item => typeof item === "string" && item.trim()) as string | undefined;
  return <><span>{label || "查看此项的详细信息"}</span>
    <details><summary>查看原始数据</summary><pre>{JSON.stringify(value, null, 2)}</pre></details></>;
};

/** Remounted for each project/thread/PRD/readiness identity by ChannelPage. */
export function ChannelConsensusControl({ selection, artifactRef, consensus, actionReady, busy, onDecision }: {
  selection: ChannelPrdSelection; artifactRef: string; consensus: Record<string, unknown>;
  actionReady: boolean; busy: boolean;
  onDecision: (decision: "confirm" | "block", blocker: string, acceptRisk: boolean) => Promise<void>;
}) {
  const [blocker, setBlocker] = useState("");
  const [accepted, setAccepted] = useState(false);
  const [readiness, setReadiness] = useState<ChannelPrdDocument["readiness"]>();
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const needsRisk = ["needs_owner", "needs_multi_lens"].includes(String(consensus.readiness_verdict));
  const planningOnly = consensus.readiness_verdict === "ready" && consensus.implementation_start === false;
  const reviewBlocked = Array.isArray(consensus.blocked) && consensus.blocked.length > 0;
  useEffect(() => {
    if (!needsRisk && !planningOnly) return;
    const controller = new AbortController();
    setReadiness(undefined); setAccepted(false); setError("");
    getChannelPrdDocument(selection, controller.signal).then(doc => {
      if (controller.signal.aborted) return;
      if (doc.artifact_ref !== artifactRef || bareDigest(doc.artifact_digest) !== bareDigest(selection.digest)
        || doc.revision !== selection.revision || doc.channel_id !== selection.channelId || doc.thread_id !== selection.threadId
        || !doc.readiness || doc.readiness.ref !== consensus.readiness_ref
        || bareDigest(doc.readiness.digest) !== bareDigest(consensus.readiness_digest)
        || doc.readiness.verdict !== consensus.readiness_verdict
        || (planningOnly && doc.readiness.implementation_start !== false)
        || !Array.isArray(doc.readiness.gaps) || !Array.isArray(doc.readiness.risks)) {
        throw new Error("PRD 风险版本不一致，请重新打开当前版本。");
      }
      setReadiness(doc.readiness);
    }).catch(reason => { if (!controller.signal.aborted) setError(String(reason)); });
    return () => controller.abort();
  }, [attempt]); // Identity changes remount this component.
  return <div className="channel-consensus-control" data-testid="channel-consensus-control">

    {(needsRisk || planningOnly) && !consensus.human_confirmed ? <section className="channel-readiness-risks" data-testid="channel-readiness-risks">
      <strong>{planningOnly ? "当前版本仅允许规划" : "当前 PRD 仍有未解决项或风险"}</strong>
      {planningOnly ? <p>可以只确认文档。如需继续执行，请明确接受本版本风险，再分别批准 Task 和 Workflow。</p> : null}
      {error ? <><p role="alert">{error}</p><button className="icon-button" type="button" onClick={() => setAttempt(attempt + 1)}>Retry</button></>
        : !readiness ? <p role="status">正在读取当前版本的完整风险说明…</p> : <>
          <p>{readiness.reason}</p>
          {readiness.gaps.length ? <><p>未解决项</p><ul>{readiness.gaps.map((item, index) => <li key={index}>{describe(item)}</li>)}</ul></> : null}
          {readiness.risks.length ? <><p>风险</p><ul>{readiness.risks.map((item, index) => <li key={index}>{describe(item)}</li>)}</ul></> : null}
          {!reviewBlocked ? <label><input type="checkbox" checked={accepted} disabled={!actionReady || busy}
            onChange={event => setAccepted(event.target.checked)} />{planningOnly ? "我允许在分别批准 Task 和 Workflow 后执行，并接受本版本列明的风险。" : "我接受本版本上列未解决项和风险；Task 创建及 Workflow 启动仍需分别批准。"}</label> : null}
        </>}
    </section> : null}
    <span className="channel-discussion-qtext">{reviewBlocked
      ? "Review blockers require another PRD revision"
      : consensus.human_confirmed
        ? "Owner confirmed; waiting for required signatures" : "Canonical PRD awaiting owner decision"}</span>
    {!reviewBlocked ? <>
      <input className="filter-input" placeholder="Blocker" value={blocker} onChange={event => setBlocker(event.target.value)} />
      <button className="icon-button primary" type="button"
        disabled={!actionReady || busy || Boolean(consensus.human_confirmed) || (needsRisk && (!readiness || !accepted || Boolean(error))) || (planningOnly && accepted && (!readiness || Boolean(error)))}
        onClick={() => void onDecision("confirm", "", (needsRisk || planningOnly) && accepted)}>{needsRisk || (planningOnly && accepted) ? "接受风险并确认" : "Confirm"}</button>
      <button className="icon-button" type="button" disabled={!actionReady || busy || !blocker.trim()}
        onClick={() => void onDecision("block", blocker.trim(), false).then(() => setBlocker(""))}>Block</button>
    </> : null}
  </div>;
}
