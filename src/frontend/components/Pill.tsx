/** Recording lifecycle states share the job status labels and styling. */
export const normalizedStatus = (status: string) => ({ recording: "running", processing: "running", complete: "succeeded" }[status] ?? status);
export const statusLabel = (status: string) => normalizedStatus(status).toUpperCase();

export function Pill({ status }: { status: string }) {
  return <span className={`pill ${normalizedStatus(status)}`}>{statusLabel(status)}</span>;
}
