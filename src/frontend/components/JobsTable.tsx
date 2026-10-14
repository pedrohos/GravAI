"use client";

import Link from "next/link";
import { api } from "@/lib/api";
import { fmtDateTime } from "@/lib/format";
import { ACTIVE_STATUSES, type Job } from "@/lib/types";
import { JobLogDrawer } from "./JobLogDrawer";
import { Pill } from "./Pill";
import { useToast } from "./Toast";

/** The wording matters: only one of these leaves a recording behind. */
const STOP_WARNING =
  "Leave the meeting now?\n\nThe recording keeps everything captured so far and " +
  "goes on to be sliced and transcribed.";
const CANCEL_WARNING =
  "Kill this job?\n\nNothing is finalized: a recording cancelled mid-meeting leaves " +
  "no usable audio. To end a meeting early and keep it, use Stop.";

export function JobsTable({ jobs, onChanged }: { jobs: Job[]; onChanged: () => void }) {
  const { attempt } = useToast();

  if (!jobs.length) return <div className="empty">No jobs yet. Submit one above.</div>;

  async function stop(job: Job) {
    if (!confirm(STOP_WARNING)) return;
    await attempt(() => api.stopJob(job.id), "Asked the recording to leave the meeting.");
    onChanged();
  }

  async function cancel(job: Job) {
    if (!confirm(CANCEL_WARNING)) return;
    await attempt(() => api.cancelJob(job.id), "Job cancelled.");
    onChanged();
  }

  async function remove(job: Job) {
    await attempt(() => api.deleteJob(job.id), "Job deleted.");
    onChanged();
  }

  return (
    <table>
      <thead>
        <tr>
          <th>JOB TYPE</th>
          <th>Status</th>
          <th>Submitted</th>
          <th>Meeting recordings</th>
          <th />
        </tr>
      </thead>
      <tbody>
        {jobs.map((job) => {
          const active = ACTIVE_STATUSES.includes(job.status);
          // A transcription has no meeting to leave, and a recording that has
          // not reached a session directory yet has nothing to stop.
          const canStop = active && job.type !== "transcribe" && Boolean(job.session_dir);
          const target = job.params.meeting_url || job.params.tracks_output_dir || "";

          return (
            <tr key={job.id}>
              <td>
                <span className={`job-kind job-kind-${job.type}`}>
                  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
                    {job.type === "transcribe" ? <><path d="M6 3h9l3 3v15H6z" /><path d="M9 10h6M9 14h6M9 18h4" /></> : <><rect x="3" y="6" width="13" height="12" rx="3" /><path d="m16 10 5-3v10l-5-3" /><circle cx="9.5" cy="12" r="2" fill="currentColor" /></>}
                  </svg>
                  {job.type === "transcribe" ? "Transcribe" : "Record"}
                </span>
                {job.depends_on && <div className="faint">After recording</div>}
              </td>

              <td>
                <span className="job-status"><Pill status={job.status} /></span>
                {job.error && <div className="faint mono">{job.error.slice(0, 240)}</div>}
                <JobLogDrawer jobId={job.id} />
                {job.result && (
                  <details className="drawer">
                    <summary>result</summary>
                    <div>
                      <pre className="json">{JSON.stringify(job.result, null, 2)}</pre>
                    </div>
                  </details>
                )}
              </td>

              <td className="dim"><time dateTime={job.created_at}>{fmtDateTime(job.created_at)}</time></td>

              <td>
                {job.recording_id ? (
                  <Link className="button small open-recording" href={`/recordings/${encodeURIComponent(job.recording_id)}`}>
                    OPEN RECORDING
                  </Link>
                ) : (
                  <span className="faint">—</span>
                )}
                <div
                  className="faint mono"
                  style={{ maxWidth: "34ch", overflow: "hidden", textOverflow: "ellipsis" }}
                >
                  {target}
                </div>
              </td>

              <td className="actions">
                {canStop && (
                  <button
                    className="small"
                    onClick={() => void stop(job)}
                    title="Leave the meeting and keep the recording"
                  >
                    Stop
                  </button>
                )}
                {active ? (
                  <button
                    className="small danger"
                    onClick={() => void cancel(job)}
                    title="Kill it. Nothing is kept."
                  >
                    Cancel
                  </button>
                ) : (
                  <button className="small delete-both" onClick={() => void remove(job)}>
                    Delete
                  </button>
                )}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
