"use client";

import { Panel } from "@/components/ui/Panel";
import { Input, Select, FormField, Checkbox } from "@/components/ui/FormField";
import { Button } from "@/components/ui/Button";
import { useEffect, useRef, useState } from "react";
import { api, messageOf } from "@/lib/api";
import type { JobSubmission, JobType } from "@/lib/types";
import { useToast } from "./Toast";

export const JOB_TYPES: readonly (readonly [JobType, string, string])[] = [
  [
    "record_and_transcribe",
    "Record and transcribe",
    "",
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
  const [recordingDir, setRecordingDir] = useState("");
  const [directoryLoading, setDirectoryLoading] = useState(true);
  const [directoryError, setDirectoryError] = useState("");
  const submitting = useRef(false);
  const directoryKey = `${type}:${recordingDir.trim()}`;
  const currentDirectoryKey = useRef(directoryKey);
  currentDirectoryKey.current = directoryKey;
  const pendingValidation = useRef<{
    key: string;
    promise: Promise<{ path: string } | null>;
  } | null>(null);

  useEffect(() => {
    let active = true;
    void attempt(() => api.suggestRecordingDirectory()).then((result) => {
      if (active) {
        if (result) setRecordingDir((current) => current || result.path);
        setDirectoryLoading(false);
      }
    });
    return () => { active = false; };
  }, [attempt]);
  const [sliceTracks, setSliceTracks] = useState(true);
  const [groupByName, setGroupByName] = useState(true);
  const [busy, setBusy] = useState(false);
  const [hint, setHint] = useState("");

  const isTranscribe = type === "transcribe";
  const typeHint = JOB_TYPES.find(([value]) => value === type)?.[2] ?? "";

  function validateDirectory() {
    const key = directoryKey;
    if (pendingValidation.current?.key === key) return pendingValidation.current.promise;
    const promise = (async () => {
      try {
        const result = await api.validateRecordingDirectory(recordingDir.trim(), isTranscribe);
        if (currentDirectoryKey.current === key) setDirectoryError("");
        return result;
      } catch (error) {
        if (currentDirectoryKey.current === key) {
          const message = messageOf(error);
          setDirectoryError(message);
          toast(message, "bad");
        }
        return null;
      } finally {
        if (pendingValidation.current?.key === key) pendingValidation.current = null;
      }
    })();
    pendingValidation.current = { key, promise };
    return promise;
  }

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (submitting.current || directoryLoading) return;
    const value = (isTranscribe ? recordingDir : target).trim();
    if (!value) {
      toast(isTranscribe ? "Give a recording directory." : "Give a meeting URL.", "bad");
      return;
    }

    submitting.current = true;
    setBusy(true);
    try {
      // Await the same check started by blur before creating any job.
      const validation = await validateDirectory();
      if (!validation || currentDirectoryKey.current !== directoryKey) return;
      setRecordingDir(validation.path);
      const submission: JobSubmission = {
        tracks_output_dir: validation.path,
        type,
        slice_tracks: sliceTracks,
        group_slices_by_name: groupByName,
        ...(!isTranscribe ? { meeting_url: value } : {}),
      };

      const job = await attempt(() => api.submitJob(submission), "Job submitted.");
      if (job) {
        setTarget("");
        if (!isTranscribe) {
          const suggestion = await attempt(() => api.suggestRecordingDirectory());
          if (suggestion) setRecordingDir(suggestion.path);
        }
        setHint(`Started ${job.id.slice(0, 8)}…`);
        onSubmitted();
      }
    } finally {
      submitting.current = false;
      setBusy(false);
    }
  }

  return (
    <Panel title="New job">
      <form className="job-form" onSubmit={onSubmit}>
        <div className="row job-fields">
          <FormField id="job-type" label="Task type" hint={typeHint}>
            <Select
              id="job-type"
              value={type}
              disabled={busy}
              onChange={(event) => { setType(event.target.value as JobType); setDirectoryError(""); }}
            >
              {JOB_TYPES.map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </Select>
          </FormField>

          <div className="field">
            <FormField id="job-target" label="Meeting URL" hint="Google Meet and Microsoft Teams." className="">
              <Input
                type="text"
                id="job-target"
                disabled={isTranscribe || busy}
                value={target}
                onChange={(event) => setTarget(event.target.value)}
                placeholder="https://meet.google.com/… or https://teams.microsoft.com/…"
              />
            </FormField>
            <FormField
              id="job-recording-dir"
              label="Recording directory on the server"
              className=""
              error={directoryError}
              hint={directoryLoading ? "Loading recording directory…" : isTranscribe ? "Directory created by a previous recording." : "The recording will be saved in this exact directory."}
            >
              <Input
                id="job-recording-dir"
                type="text"
                value={recordingDir}
                disabled={busy}
                onChange={(event) => { setRecordingDir(event.target.value); setDirectoryError(""); }}
                aria-busy={directoryLoading}
                onBlur={async () => {
                  const entered = recordingDir;
                  if (entered.trim()) {
                    const result = await validateDirectory();
                    if (result) setRecordingDir((current) => current === entered ? result.path : current);
                  }
                }}
              />
            </FormField>
          </div>
        </div>

        <div className="row" style={{ marginBottom: 14 }}>
          {/* Slicing is only a choice for a record-only job: the full pipeline
              has to slice, because transcription reads what slicing produced. */}
          {type === "record" && (
            <Checkbox
              checked={sliceTracks}
              onChange={(event) => setSliceTracks(event.target.checked)}
            >
              Split the mix into one track per speaker
            </Checkbox>
          )}
          {type !== "record" && (
            <Checkbox
              checked={groupByName}
              onChange={(event) => setGroupByName(event.target.checked)}
            >
              Group a speaker&apos;s tiles by display name (if unsure, leave marked)
            </Checkbox>
          )}
        </div>

        <div className="row">
          <Button variant="primary" type="submit" disabled={busy || directoryLoading || !recordingDir.trim()}>
            Submit job
          </Button>
          <span className="faint">{hint}</span>
        </div>
      </form>
    </Panel>
  );
}
