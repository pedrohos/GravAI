"use client";

import Link from "next/link";
import { api } from "@/lib/api";
import { fmtAge } from "@/lib/format";
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
          <th>Job</th>
          <th>Status</th>
          <th>Submitted</th>
          <th>Meeting</th>
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
                <div>{job.type.replaceAll("_", " ")}</div>
                <div className="faint mono">{job.id.slice(0, 8)}</div>
                {job.depends_on && (
                  <div className="faint mono" title={job.depends_on}>
                    after {job.depends_on.slice(0, 8)}
                  </div>
                )}
              </td>

              <td>
                <Pill status={job.status} />
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

              <td className="dim">{fmtAge(job.created_at)}</td>

              <td>
                {job.recording_id ? (
                  <Link href={`/recordings/${encodeURIComponent(job.recording_id)}`}>
                    open recording
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
                  <button className="small danger" onClick={() => void remove(job)}>
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
