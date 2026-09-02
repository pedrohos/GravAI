"use client";

import Link from "next/link";
import { Pill } from "@/components/Pill";
import { useToast } from "@/components/Toast";
import { api } from "@/lib/api";
import { fmtDateTime, fmtDuration } from "@/lib/format";
import { usePolling } from "@/lib/usePolling";
import type { Recording } from "@/lib/types";

/** Four names, then a count. A meeting of thirty would otherwise be a wall. */
const SHOWN = 4;

function speakersOf(recording: Recording) {
  return recording.participants
    .map((participant) => participant.participant_name || participant.participant_id)
    .filter(Boolean);
}

export default function RecordingsPage() {
  const { data, error, loading, refresh } = usePolling(() => api.listRecordings(), 10000);
  const { attempt } = useToast();

  /** Re-runs slicing and whisper over a recording without opening it, which is
   *  what a whisper that was down for a run of meetings leaves you wanting. */
  async function transcribe(recording: Recording) {
    const job = await attempt(
      () =>
        api.submitJob({
          type: "transcribe",
          tracks_output_dir: recording.session_dir,
          slice_tracks: true,
          group_slices_by_name: true,
        }),
      "Transcription started."
    );
    if (job) void refresh();
  }

  return (
    <>
      <h1>Recordings</h1>
      <p className="subtitle">
        Every meeting this service has recorded, newest first. One in progress is in here too,
        from the moment it has somewhere to write.
      </p>

      {error && <div className="error-box">{error}</div>}

      {loading && !data ? (
        <div className="empty">Loading…</div>
      ) : !data || data.length === 0 ? (
        <div className="empty">Nothing recorded yet. Submit a job from the Jobs page.</div>
      ) : (
        <section className="panel">
          <table>
            <thead>
              <tr>
                <th>Meeting</th>
                <th>Status</th>
                <th>When</th>
                <th>Length</th>
                <th>Speakers</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {data.map((recording) => {
                const speakers = speakersOf(recording);
                const transcribed = recording.participants.some(
                  (participant) => participant.transcript_text
                );

                return (
                  <tr key={recording.id}>
                    <td>
                      <Link href={`/recordings/${encodeURIComponent(recording.id)}`}>
                        {recording.meeting_url || recording.id}
                      </Link>
                      <div className="faint mono">
                        {recording.provider || ""} {recording.id.slice(0, 22)}…
                      </div>
                    </td>
                    <td>
                      <Pill status={recording.status} />
                      {transcribed && <div className="faint">transcribed</div>}
                    </td>
                    <td className="dim">
                      {fmtDateTime(recording.started_at || recording.created_at)}
                    </td>
                    <td className="dim">{fmtDuration(recording.duration_seconds)}</td>
                    <td>
                      {speakers.length ? (
                        <div className="chips">
                          {speakers.slice(0, SHOWN).map((name, index) => (
                            <span className="chip" key={`${name}-${index}`}>
                              {name}
                            </span>
                          ))}
                          {speakers.length > SHOWN && (
                            <span className="chip">+{speakers.length - SHOWN}</span>
                          )}
                        </div>
                      ) : (
                        <span className="faint">—</span>
                      )}
                    </td>
                    <td>
                      <button className="small" onClick={() => void transcribe(recording)}>
                        {transcribed ? "Transcribe again" : "Transcribe"}
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </section>
      )}
    </>
  );
}
