"use client";

import { DataTable } from "@/components/DataTable";
import { EmptyState } from "@/components/ui/Feedback";
import { Button } from "@/components/ui/Button";
import { TaskTypeBadge } from "./TaskTypeBadge";
import { JobResults } from "./JobResults";
import { DeleteButton } from "./DeleteButton";
import { useTableSort } from "./TableControls";
import { useColumnWidths } from "@/lib/useColumnWidths";
import { MeetingRecordingLink } from "./MeetingRecordingLink";
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
  const columns = ["JOB TYPE", "Status", "Created at", "Meeting recordings", "Actions"];
  const sizing = useColumnWidths("gravai.jobs.column-widths.v1", columns.length);

  const sorting = useTableSort(jobs, [job => job.type, job => job.status, job => job.created_at, job => job.params.meeting_url || job.session_dir || job.params.tracks_output_dir, job => ACTIVE_STATUSES.includes(job.status) ? "Cancel" : "Delete"], 2, true);

  if (!jobs.length) return <EmptyState>No jobs yet. Submit one above.</EmptyState>;

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
    <DataTable columns={columns} defaultWidths={[17, 23, 20, 28, 12]} sizing={sizing} sorting={sorting}>
      <tbody>
        {sorting.sorted.map((job) => {
          const active = ACTIVE_STATUSES.includes(job.status);
          // A transcription has no meeting to leave, and a recording that has
          // not reached a session directory yet has nothing to stop.
          const canStop = active && job.type !== "transcribe" && Boolean(job.session_dir);
          const target = job.params.meeting_url || job.params.tracks_output_dir || "";

          return (
            <tr key={job.id}>
              <td>
                <div className="job-type-cell">
                <TaskTypeBadge type={job.type} />
                {job.depends_on && <div className="faint job-dependency">After recording</div>}
                </div>
              </td>

              <td>
                <span className="job-status"><Pill status={job.status} /></span>
                {job.error && <div className="faint mono">{job.error}</div>}
                <JobLogDrawer jobId={job.id} status={job.status} />
                <JobResults result={job.result} />
              </td>

              <td className="dim"><time dateTime={job.created_at}>{fmtDateTime(job.created_at)}</time></td>

              <td>
                <MeetingRecordingLink job={job} />
                <div
                  className="faint mono"
                  title={target}
                  style={{ marginTop: 8 }}
                >
                  {target}
                </div>
                {job.session_dir && job.session_dir !== target && <div className="mono faint folder-path">{job.session_dir}</div>}
              </td>

              <td className="actions">
                {canStop && (
                  <Button
                    size="small"
                    onClick={() => void stop(job)}
                    title="Leave the meeting and keep the recording"
                  >
                    Stop
                  </Button>
                )}
                {active ? (
                  <Button
                    size="small" variant="danger"
                    onClick={() => void cancel(job)}
                    title="Kill it. Nothing is kept."
                  >
                    Cancel
                  </Button>
                ) : (
                  <DeleteButton onClick={() => void remove(job)}>
                    Delete
                  </DeleteButton>
                )}
              </td>
            </tr>
          );
        })}
      </tbody>
    </DataTable>
  );
}
