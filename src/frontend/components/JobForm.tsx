"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import type { JobSubmission, JobType } from "@/lib/types";
import { useToast } from "./Toast";

export const JOB_TYPES: readonly (readonly [JobType, string, string])[] = [
  [
    "record_and_transcribe",
    "Record and transcribe",
    "Two jobs: a recording, then a transcription that waits for it to finish.",
  ],
  ["record", "Record only", "Join and record. Transcribe it later."],
  ["transcribe", "Transcribe a recording", "Re-run slicing and whisper over a directory."],
];

/**
 * Submitting work.
 *
 * Its own component because the list beside it re-renders every 3 seconds and
 * this must not: replacing the form on a tick would take a half-typed meeting
 * URL with it. The old page mounted the form once by hand and rewrote only the
 * list; here the two are separate components and React does it.
 */
export function JobForm({ onSubmitted }: { onSubmitted: () => void }) {
  const { toast, attempt } = useToast();
  const [type, setType] = useState<JobType>("record_and_transcribe");
  const [target, setTarget] = useState("");
  const [sliceTracks, setSliceTracks] = useState(true);
  const [groupByName, setGroupByName] = useState(true);
  const [busy, setBusy] = useState(false);
  const [hint, setHint] = useState("");

  const isTranscribe = type === "transcribe";
  const typeHint = JOB_TYPES.find(([value]) => value === type)?.[2] ?? "";

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    const value = target.trim();
    if (!value) {
      toast(isTranscribe ? "Give a recording directory." : "Give a meeting URL.", "bad");
      return;
    }

    const submission: JobSubmission = {
      type,
      slice_tracks: sliceTracks,
      group_slices_by_name: groupByName,
      ...(isTranscribe ? { tracks_output_dir: value } : { meeting_url: value }),
    };

    setBusy(true);
    const job = await attempt(() => api.submitJob(submission), "Job submitted.");
    setBusy(false);
    if (job) {
      setTarget("");
      setHint(`Started ${job.id.slice(0, 8)}…`);
      onSubmitted();
    }
  }

  return (
    <section className="panel">
      <h2>New job</h2>
      <form onSubmit={onSubmit}>
        <div className="row">
          <div className="field">
            <label htmlFor="job-type">What to do</label>
            <select
              id="job-type"
              value={type}
              onChange={(event) => setType(event.target.value as JobType)}
            >
              {JOB_TYPES.map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
            <div className="hint">{typeHint}</div>
          </div>

          <div className="field">
            <label htmlFor="job-target">
              {isTranscribe ? "Recording directory on the server" : "Meeting URL"}
            </label>
            <input
              type="text"
              id="job-target"
              value={target}
              onChange={(event) => setTarget(event.target.value)}
              placeholder={
                isTranscribe
                  ? "/tmp/2026_08_26_…_tracks"
                  : "https://meet.google.com/… or https://teams.microsoft.com/…"
              }
            />
            <div className="hint">
              {isTranscribe
                ? "A directory a previous recording wrote. Its path is on every recording's page."
                : "Google Meet and Microsoft Teams."}
            </div>
          </div>
        </div>

        <div className="row" style={{ marginBottom: 14 }}>
          {/* Slicing is only a choice for a record-only job: the full pipeline
              has to slice, because transcription reads what slicing produced. */}
          {type === "record" && (
            <label className="check">
              <input
                type="checkbox"
                checked={sliceTracks}
                onChange={(event) => setSliceTracks(event.target.checked)}
              />
              Split the mix into one track per speaker
            </label>
          )}
          {type !== "record" && (
            <label className="check">
              <input
                type="checkbox"
                checked={groupByName}
                onChange={(event) => setGroupByName(event.target.checked)}
              />
              Group a speaker&apos;s tiles by display name
            </label>
          )}
        </div>

        <div className="row">
          <button className="primary" type="submit" disabled={busy}>
            Submit job
          </button>
          <span className="faint">{hint}</span>
        </div>
      </form>
    </section>
  );
}
