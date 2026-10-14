"use client";

import { Icon } from "@/components/Icon";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useState } from "react";

import { ProviderBadge } from "@/components/ProviderBadge";
import { DeleteButton } from "@/components/DeleteButton";
import { JobsTable } from "@/components/JobsTable";
import { Pill } from "@/components/Pill";
import { MeetingTranscript } from "@/components/MeetingTranscript";
import { SpeakerPanel } from "@/components/SpeakerPanel";
import { useToast } from "@/components/Toast";
import { api } from "@/lib/api";
import { fmtDateTime, fmtDuration } from "@/lib/format";
import { usePolling } from "@/lib/usePolling";
import { ACTIVE_STATUSES } from "@/lib/types";

const FORGET_WARNING = "Forget this recording?\n\nThe audio and transcripts on disk are kept.";

export default function RecordingPage() {
  const { id } = useParams<{ id: string }>();
  const recordingId = decodeURIComponent(id);
  const router = useRouter();
  const { attempt } = useToast();
  const [expandedSpeakers, setExpandedSpeakers] = useState<Set<string>>(new Set());
  const [submittingSpeaker, setSubmittingSpeaker] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);

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

  async function transcribeSpeaker(participantId: string, sessionDir: string) {
    setSubmittingSpeaker(participantId);
    try {
      const job = await attempt(() => api.submitJob({
        type: "transcribe",
        tracks_output_dir: sessionDir,
        participant_id: participantId,
        slice_tracks: false,
        group_slices_by_name: true,
      }), "Speaker transcription started.");
      if (job) await refresh();
    } finally {
      setSubmittingSpeaker(null);
    }
  }

  async function forget() {
    if (!confirm(FORGET_WARNING)) return;
    const gone = await attempt(
      () => api.deleteRecording(recordingId).then(() => true),
      "Forgotten."
    );
    if (gone) router.push("/recordings");
  }

  async function deleteContent(both: boolean) {
    const warning = both
      ? "Delete this recording and all its transcriptions? Audio and transcript files will be permanently deleted."
      : "Delete all transcriptions for this recording? Audio will be kept.";
    if (!confirm(warning)) return;
    setDeleting(true);
    try {
      const deleted = await attempt(
        () => (both ? api.deleteRecordingAndTranscription(recordingId) : api.deleteTranscription(recordingId)).then(() => true),
        both ? "Recording and transcription deleted." : "Transcription deleted."
      );
      if (deleted) {
        if (both) router.push("/recordings");
        else await refresh();
      }
    } finally {
      setDeleting(false);
    }
  }

  if (error && !data) {
    return (
      <>
        <div className="error-box">{error}</div>
        <p>
          <Link href="/recordings"><Icon name="arrow-left" /> All recordings</Link>
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
  const allSpeakersExpanded = recording.participants.length > 0 && recording.participants.every(participant => expandedSpeakers.has(participant.participant_id));
  const meetingTranscribing = jobs.some(job => job.type !== "record" && !job.params.participant_id && ACTIVE_STATUSES.includes(job.status));
  const hasTranscript =
    Boolean(recording.meeting_transcript_text) ||
    recording.participants.some((participant) => participant.transcript_text);

  const transcribeButton = (
    <button
      className="small"
      disabled={transcribing || deleting}
      onClick={() => void transcribeAgain(recording.session_dir)}
    >
      {transcribing ? "Transcribing…" : hasTranscript ? "Transcribe again" : "Transcribe"}
    </button>
  );

  return (
    <>
      <p>
        <Link href="/recordings"><Icon name="arrow-left" /> All recordings</Link>
      </p>
      <h1 className="recording-heading">Meeting URL: {recording.meeting_url || "-"}</h1>
      <p className="subtitle mono recording-id">ID: {recording.id}</p>
      <p className="mono folder-path recording-disk-path">Disk path: {recording.session_dir}</p>

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
            <dd><ProviderBadge provider={recording.provider} meetingUrl={recording.meeting_url} />{!recording.provider && !recording.meeting_url && "—"}</dd>
          </div>
          <div>
            <dt>Speakers</dt>
            <dd>{recording.participants.length}</dd>
          </div>
        </dl>

        <details className="drawer" style={{ marginTop: 16 }}>
          <summary>Files and jobs</summary>
          <div>
            <p className="mono faint">{recording.session_dir}</p>
            <p className="dim" >
              Transcribe re-runs slicing and whisper over that directory.
            </p>
            {jobs.length > 0 && (
              <div className="jobs-table-scroll"><JobsTable jobs={jobs} onChanged={refresh} /></div>
            )}
          </div>
        </details>
      </section>

      <section className="panel" aria-labelledby="recording-actions-heading">
        <h2 id="recording-actions-heading">Actions</h2>
        <div className="row" style={{ marginBottom: 14 }}>
          {transcribeButton}
          <DeleteButton
            className="delete-transcription"
            disabled={deleting || transcribing || jobs.some((job) => ACTIVE_STATUSES.includes(job.status)) || ["recording", "processing"].includes(recording.status) || !hasTranscript}
            onClick={() => void deleteContent(false)}
          >
            Delete transcription
          </DeleteButton>
          <DeleteButton
            disabled={deleting || transcribing || jobs.some((job) => ACTIVE_STATUSES.includes(job.status)) || ["recording", "processing"].includes(recording.status)}
            onClick={() => void deleteContent(true)}
          >
            Delete recording and transcription
          </DeleteButton>
        </div>
        <div className="row" style={{ marginTop: 14 }}>
          <button className="small danger" onClick={() => void forget()}>
            Forget this recording
          </button>
          <span className="faint">
            Removes the catalogue entry. The audio on disk is left alone.
          </span>
        </div>
      </section>

      <MeetingTranscript key={recording.id} recording={recording} transcribing={meetingTranscribing} />

      <div className="section-head">
        <h2 style={{ margin: 0 }}>Speaker transcripts ({recording.participants.length})</h2>
        <button className="small" aria-expanded={allSpeakersExpanded} onClick={() => setExpandedSpeakers(new Set(allSpeakersExpanded ? [] : recording.participants.map(participant => participant.participant_id)))}>{allSpeakersExpanded ? "Collapse speakers" : "Show all speakers"}</button>
      </div>
      {recording.participants.length ? (
        recording.participants.map((participant) => (
          <SpeakerPanel
            key={participant.participant_id}
            recording={recording}
            participant={participant}
            expanded={expandedSpeakers.has(participant.participant_id)}
            onExpandedChange={expanded => setExpandedSpeakers(current => {
              if (current.has(participant.participant_id) === expanded) return current;
              const next = new Set(current);
              if (expanded) next.add(participant.participant_id);
              else next.delete(participant.participant_id);
              return next;
            })}
            onTranscribe={() => void transcribeSpeaker(participant.participant_id, recording.session_dir)}
            transcribing={submittingSpeaker === participant.participant_id || jobs.some(job => job.type === "transcribe" && ACTIVE_STATUSES.includes(job.status) && (!job.params.participant_id || job.params.participant_id === participant.participant_id))}
            disabled={deleting || submittingSpeaker !== null || ["recording", "processing"].includes(recording.status)}
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
