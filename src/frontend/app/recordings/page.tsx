"use client";

import { Dropdown } from "@/components/Dropdown";
import { Icon } from "@/components/Icon";
import { Filters, FilterField } from "@/components/Filters";
import { normalizedStatus, statusLabel } from "@/components/Pill";
import { ProviderBadge } from "@/components/ProviderBadge";
import { DeleteButton } from "@/components/DeleteButton";
import { SpeakerList } from "@/components/SpeakerList";
import { TableHeader, useTableSort } from "@/components/TableControls";
import { useColumnWidths } from "@/lib/useColumnWidths";
import { useState } from "react";
import { providerOf } from "@/components/ProviderBadge";
import Link from "next/link";
import { Pill } from "@/components/Pill";
import { useToast } from "@/components/Toast";
import { api } from "@/lib/api";
import { fmtDateTime, fmtDuration } from "@/lib/format";
import { usePolling } from "@/lib/usePolling";
import type { Recording } from "@/lib/types";

function speakersOf(recording: Recording) {
  return recording.participants
    .map((participant) => participant.participant_name || participant.participant_id)
    .filter(Boolean);
}

export default function RecordingsPage() {
  const { data, error, loading, refresh } = usePolling(() => api.listRecordings(), 10000);
  const { attempt } = useToast();
  const [status, setStatus] = useState("");
  const [provider, setProvider] = useState("");
  const filtered = (data ?? []).filter(recording => (!status || normalizedStatus(recording.status) === status) && (!provider || providerOf(recording.provider, recording.meeting_url) === provider));
  const columns = ["Meeting recordings", "Status", "Created at", "Duration", "Speakers", "Actions"];
  const sizing = useColumnWidths("gravai.recordings.column-widths.v1", columns.length);
  const sorting = useTableSort(filtered, [r => r.meeting_url || r.id, r => r.status, r => r.created_at, r => r.duration_seconds, r => speakersOf(r).join(", "), r => Boolean(r.meeting_transcript_text) || r.participants.some(p => p.transcript_text) ? "Transcribe again" : "Transcribe"], 2, true);

  async function remove(recording: Recording) {
    if (!confirm("Delete this recording and all its transcriptions? Audio and transcript files will be permanently deleted.")) return;
    const deleted = await attempt(() => api.deleteRecordingAndTranscription(recording.id).then(() => true), "Recording and transcriptions deleted.");
    if (deleted) void refresh();
  }

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
    <div className="recordings-page">
      <h1>Recordings</h1>
      <p className="subtitle">
        Recording listings and transcriptions.
      </p>

      {error && <div className="error-box">{error}</div>}

      {loading && !data ? (
        <div className="empty">Loading…</div>
      ) : !data || data.length === 0 ? (
        <div className="empty">Nothing recorded yet. Submit a job from the Jobs page.</div>
      ) : (
        <section className="panel">
          <div className="section-head">
            <h2 style={{ margin: 0 }}>Recorded meetings</h2>
            <Filters>
              <FilterField label="Status">
                <Dropdown label="Filter recordings by status" value={status} onChange={setStatus} options={[
                  { value: "", label: "All statuses", content: <span className="pill filter-all">All statuses</span> },
                  ...Array.from(new Set([...data.map(r => normalizedStatus(r.status)), ...(status ? [status] : [])])).sort().map(value => ({ value, label: statusLabel(value), content: <Pill status={value} /> })),
                ]} />
              </FilterField>
              <FilterField label="Provider">
                <Dropdown label="Filter recordings by provider" value={provider} onChange={setProvider} options={[
                  { value: "", label: "All providers", content: <span className="provider-badge filter-all">All providers</span> },
                  { value: "meet", label: "Meet", content: <ProviderBadge provider="meet" /> },
                  { value: "teams", label: "Teams", content: <ProviderBadge provider="teams" /> },
                ]} />
              </FilterField>
            </Filters>
          </div>
          {(status || provider) && (
            <div className="active-filters" aria-label="Active filters">
              {status && <span className={`pill filter-chip ${status}`}>Status: {statusLabel(status)}<button aria-label="Clear status filter" onClick={() => setStatus("")}><Icon name="x" /></button></span>}
              {provider && <span className={`pill filter-chip provider-${provider}`}>Provider: {provider === "meet" ? "Meet" : "Teams"}<button aria-label="Clear provider filter" onClick={() => setProvider("")}><Icon name="x" /></button></span>}
            </div>
          )}
          {!filtered.length && <p className="empty">No recordings match these filters.</p>}
          <div className="jobs-table-scroll" tabIndex={0} aria-label="Recordings table">
          <table className="jobs-table" ref={sizing.tableRef} style={sizing.widths ? { width: sizing.widths.reduce((sum, width) => sum + width, 0) } : undefined}>
            <colgroup>{columns.map((label, index) => <col key={label} style={{ width: sizing.widths ? sizing.widths[index] : `${[30, 12, 16, 10, 18, 14][index]}%` }} />)}</colgroup>
            <TableHeader labels={columns} sizing={sizing} sorting={sorting} sortableCount={columns.length - 1} />
            <tbody>
              {sorting.sorted.map((recording) => {
                const speakers = speakersOf(recording);
                const transcribed = Boolean(recording.meeting_transcript_text) || recording.participants.some(
                  (participant) => participant.transcript_text
                );

                return (
                  <tr key={recording.id}>
                    <td>
                      <div className="recording-link-row"><Link className="button small open-recording" href={`/recordings/${encodeURIComponent(recording.id)}`}>OPEN RECORDING</Link><ProviderBadge provider={recording.provider} meetingUrl={recording.meeting_url} /></div>
                      <div className="meeting-title">{recording.meeting_url || recording.id}</div>
                      <div className="faint mono">
                        {recording.session_dir}
                      </div>
                    </td>
                    <td>
                      <Pill status={recording.status} />
                      {transcribed && <div className="faint">transcribed</div>}
                    </td>
                    <td className="dim">
                      {fmtDateTime(recording.created_at)}
                    </td>
                    <td className="dim">{fmtDuration(recording.duration_seconds)}</td>
                    <td>
                      <SpeakerList names={speakers} />
                    </td>
                    <td className="actions">
                      <button className="small" disabled={["recording", "processing"].includes(recording.status)} onClick={() => void transcribe(recording)}>
                        {transcribed ? "Transcribe again" : "Transcribe"}
                      </button>
                      <DeleteButton disabled={["recording", "processing"].includes(recording.status)} onClick={() => void remove(recording)} />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          </div>
        </section>
      )}
    </div>
  );
}
