import { rows, text } from "./planReviewModel";
import type { Row } from "./planReviewModel";

/** Render the exact bound PAS. Planned assertions never imply execution. */
export function PlanBoundJourneys({ journeys }: { journeys: Row[] }) {
  if (!journeys.length) return null;
  return <section aria-label="已绑定产品验收旅程">
    <p>以下来自本版产品验收规格，属于计划要求，尚不代表执行通过。</p>
    {journeys.map((journey, index) => <article className="plan-review-card" key={text(journey.journey_id) || index}>
      <h4>{text(journey.journey_id)} · {text(journey.title)}</h4>
      <p>{text(journey.verification_tier)} · {journey.mandatory === true ? "必验" : "按规格适用"}</p>
      <ul>{rows(journey.assertions).map((assertion, i) => <li key={text(assertion.assertion_id) || i}>
        <strong>{text(assertion.assertion_id)}</strong> {text(assertion.statement)}
      </li>)}</ul>
    </article>)}
  </section>;
}
