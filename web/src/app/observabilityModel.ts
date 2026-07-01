import type { EventRecord } from "../api/types";

export interface ObservabilityEventWindow {
  visibleEvents: EventRecord[];
  renderedEvents: EventRecord[];
  hiddenNoiseCount: number;
  truncatedEventCount: number;
}

export function buildObservabilityEventWindow(
  events: EventRecord[],
  options: {
    foldNoise: boolean;
    maxRows?: number;
  },
): ObservabilityEventWindow {
  const { foldNoise, maxRows = 600 } = options;
  const visibleEvents = foldNoise ? events.filter((event) => !isObservabilityNoiseEvent(event)) : events;
  const renderedEvents = visibleEvents.slice(0, maxRows);
  return {
    visibleEvents,
    renderedEvents,
    hiddenNoiseCount: events.length - visibleEvents.length,
    truncatedEventCount: Math.max(0, visibleEvents.length - renderedEvents.length),
  };
}

export function isObservabilityNoiseEvent(event: Pick<EventRecord, "type">): boolean {
  const type = event.type.toLowerCase();
  return (
    type === "worker.heartbeat"
    || type.endsWith(".heartbeat")
    || type.includes(".typing")
    || type.includes(".progress")
    || type.includes(".stream.delta")
    || type.includes(".stream.chunk")
    || type === "runtime.tick"
  );
}
