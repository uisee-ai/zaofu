import { statusTone } from "./goalCoverageModel";

export function GoalCoverageStatus({
  label,
  status,
}: {
  label: string;
  status?: string;
}) {
  return <span className={`goal-coverage-status tone-${statusTone(status)}`}>{label}</span>;
}
