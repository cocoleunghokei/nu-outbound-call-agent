// Compile-time check of the generated contract types, run by `tsc -b` (npm run build).
// It has no runtime role: if the generated types stop narrowing, the build fails here.
import { parseCallEvent } from "./generated";
import type { KnownCallEvent } from "./generated";

/** Narrowing on `type` must give access to that event's payload. */
export function describeEvent(event: KnownCallEvent): string {
  switch (event.type) {
    case "transcript.final":
      return event.payload.text;
    case "status.changed":
      // @ts-expect-error a status.changed payload has no `text`
      return event.payload.text;
    default:
      return event.type;
  }
}

/** The decoder has three outcomes; the UI shows "Unknown" and drops invalid events. */
export function renderRaw(raw: unknown): string {
  const result = parseCallEvent(raw);
  if (result.kind === "known") return describeEvent(result.event);
  if (result.kind === "unknown") return "Unknown";
  console.warn("dropping invalid event", result.error);
  return "";
}
