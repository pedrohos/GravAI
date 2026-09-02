/** Dates, durations and offsets, formatted the way each is actually read. */

export const fmtDateTime = (iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "—";

export function fmtDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "—";
  const total = Math.round(seconds);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  if (hours) return `${hours}h ${minutes}m`;
  if (minutes) return `${minutes}m ${secs}s`;
  return `${secs}s`;
}

/** Offsets inside a meeting read as a clock, not as a count of seconds.
 *  Minutes are not wrapped into hours: this is a position in a recording, and
 *  "75:03.2" is easier to find than "1:15:03.2". */
export function fmtClock(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "—";
  const total = Math.max(0, seconds);
  const minutes = Math.floor(total / 60);
  const rest = (total % 60).toFixed(1).padStart(4, "0");
  return `${String(minutes).padStart(2, "0")}:${rest}`;
}

export const fmtAge = (iso: string | null | undefined): string => {
  if (!iso) return "—";
  const seconds = (Date.now() - new Date(iso).getTime()) / 1000;
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return fmtDateTime(iso);
};
