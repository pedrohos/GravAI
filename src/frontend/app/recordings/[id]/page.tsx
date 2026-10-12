"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback } from "react";

import { Pill } from "@/components/Pill";
import { SegmentsDrawer } from "@/components/SegmentsDrawer";
import { SpeakerPanel } from "@/components/SpeakerPanel";
import { useToast } from "@/components/Toast";
import { api, audioUrl } from "@/lib/api";
import { fmtDateTime, fmtDuration } from "@/lib/format";
import { usePolling } from "@/lib/usePolling";
import { ACTIVE_STATUSES } from "@/lib/types";

const FORGET_WARNING = "Forget this recording?\n\nThe audio and transcripts on disk are kept.";

export default function RecordingPage() {
  const { id } = useParams<{ id: string }>();
  const recordingId = decodeURIComponent(id);
  const router = useRouter();
  const { attempt } = useToast();

  // Two calls, so one fetcher: the page has nothing to show until both land.
  const fetcher = useCallback(
    async () => ({
      recording: await api.getRecording(recordingId),
      jobs: (await api.recordingJobs(recordingId)) ?? [],
    }),
    [recordingId]
  );
  const { data, error, loading, refresh } = usePolling(fetcher, 15000, [recordingId]);

  /** Runs slicing and whisper over this recording's directory again.
   *
   *  Worth a button rather than a sentence telling somebody to copy a path into
   *  the jobs form: the common reason to want this is that whisper was down when
   *  the meeting ended, and at that point the recording is sitting right here
   *  with its audio playable and its transcript empty. */
  async function transcribeAgain(sessionDir: string) {
    const job = await attempt(
      () =>
        api.submitJob({
          type: "transcribe",
          tracks_output_dir: sessionDir,
          slice_tracks: true,
          group_slices_by_name: true,
        }),
      "Transcription started."
    );
    if (job) void refresh();
  }

  async function forget() {
    if (!confirm(FORGET_WARNING)) return;
    const gone = await attempt(
      () => api.deleteRecording(recordingId).then(() => true),
      "Forgotten."
    );
    if (gone) router.push("/recordings");
  }

  if (error && !data) {
    return (
      <>
        <div className="error-box">{error}</div>
        <p>
          <Link href="/recordings">← All recordings</Link>
        </p>
      </>
    );
  }

  if (loading && !data) return <div className="empty">Loading…</div>;
  if (!data) return null;

  const { recording, jobs } = data;
  // A transcription already in flight over this directory. Submitting a second
  // would have two whispers writing the same files.
  const transcribing = jobs.some(
    (job) => job.type !== "record" && ACTIVE_STATUSES.includes(job.status)
  );
  const hasTranscript =
    Boolean(recording.meeting_transcript_text) ||
    recording.participants.some((participant) => participant.transcript_text);

  const transcribeButton = (
    <button
      className="small"
      disabled={transcribing}
      onClick={() => void transcribeAgain(recording.session_dir)}
    >
      {transcribing ? "Transcribing…" : hasTranscript ? "Transcribe again" : "Transcribe"}
    </button>
  );

  return (
    <>
      <p>
        <Link href="/recordings">← All recordings</Link>
      </p>
      <h1>{recording.meeting_url || "Meeting"}</h1>
      <p className="subtitle mono">{recording.id}</p>

      <section className="panel">
        <dl className="meta-grid">
          <div>
            <dt>Status</dt>
            <dd>
              <Pill status={recording.status} />
            </dd>
          </div>
          <div>
            <dt>Started</dt>
            <dd>{fmtDateTime(recording.started_at)}</dd>
          </div>
          <div>
            <dt>Ended</dt>
            <dd>{fmtDateTime(recording.ended_at)}</dd>
          </div>
          <div>
            <dt>Length</dt>
            <dd>{fmtDuration(recording.duration_seconds)}</dd>
          </div>
          <div>
            <dt>Provider</dt>
            <dd>{recording.provider || "—"}</dd>
          </div>
          <div>
            <dt>Speakers</dt>
            <dd>{recording.participants.length}</dd>
          </div>
        </dl>

        <details className="drawer" style={{ marginTop: 16 }}>
          <summary>files and jobs</summary>
          <div>
            <p className="mono faint">{recording.session_dir}</p>
            <p className="dim" style={{ fontSize: 13 }}>
              Transcribe re-runs slicing and whisper over that directory.
            </p>
            {jobs.length > 0 && (
              <table>
                <tbody>
                  {jobs.map((job) => (
                    <tr key={job.id}>
                      <td>{job.type}</td>
                      <td>
                        <Pill status={job.status} />
                      </td>
                      <td className="dim">{fmtDateTime(job.created_at)}</td>
                      <td className="mono faint">{job.id.slice(0, 8)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            <div className="row" style={{ marginTop: 14 }}>
              <button className="small danger" onClick={() => void forget()}>
                Forget this recording
              </button>
              <span className="faint">
                Removes the catalogue entry. The audio on disk is left alone.
              </span>
            </div>
          </div>
        </details>
      </section>

      <section className="panel">
        <div className="section-head">
          <h2 style={{ margin: 0 }}>The whole meeting</h2>
          <span className="faint">
            one pass over the mixed track: overlaps intact, no speaker names
          </span>
          {transcribeButton}
        </div>
        {recording.main_track_path && (
          <audio controls preload="none" src={audioUrl.meeting(recording.id)} />
        )}
        <div className={`transcript ${recording.meeting_transcript_text ? "" : "empty-text"}`}>
          {recording.meeting_transcript_text ||
            (transcribing
              ? "Transcribing…"
              : "Not transcribed. The audio above is here either way — use Transcribe to run "
                + "whisper over it.")}
        </div>
        <SegmentsDrawer segments={recording.meeting_transcript_segments} />
      </section>

      <div className="section-head">
        <h2 style={{ margin: 0 }}>By speaker</h2>
      </div>
      {recording.participants.length ? (
        recording.participants.map((participant) => (
          <SpeakerPanel
            key={participant.participant_id}
            recording={recording}
            participant={participant}
          />
        ))
      ) : (
        <div className="empty">
          No speaker tracks. A recording job with slicing turned off, or a meeting where nobody
          was heard.
        </div>
      )}
    </>
  );
}
