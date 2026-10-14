"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Job } from "@/lib/types";

type Provider = "meet" | "teams";

function providerOf(provider: unknown, meetingUrl?: string | null): Provider | null {
  if (provider === "meet" || provider === "teams") return provider;
  try {
    const host = new URL(meetingUrl ?? "").hostname;
    if (host === "meet.google.com") return "meet";
    if (host === "teams.microsoft.com" || host === "teams.live.com" || host.endsWith(".teams.microsoft.com")) return "teams";
  } catch { /* A recording directory is not a meeting URL. */ }
  return null;
}

export function MeetingRecordingLink({ job }: { job: Job }) {
  const knownProvider = providerOf(job.params.provider, job.params.meeting_url);
  const [recordingProvider, setRecordingProvider] = useState<Provider | null>(null);
  useEffect(() => {
    setRecordingProvider(null);
    if (knownProvider || !job.recording_id) return;
    let active = true;
    api.getRecording(job.recording_id).then((recording) => {
      if (active) setRecordingProvider(providerOf(recording.provider, recording.meeting_url));
    }).catch(() => { /* The recording may have been deleted. */ });
    return () => { active = false; };
  }, [job.recording_id, knownProvider]);

  if (!job.recording_id) return <span className="faint">—</span>;
  const provider = knownProvider ?? recordingProvider;
  return (
    <div className="recording-link-row">
      <Link className="button small open-recording" href={`/recordings/${encodeURIComponent(job.recording_id)}`}>
        OPEN RECORDING
      </Link>
      {provider && (
        <span className={`provider-badge provider-${provider}`}>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
            <rect x="3" y="6" width="12" height="12" rx="2" /><path d="m15 10 6-3v10l-6-3" />
          </svg>
          {provider === "meet" ? "Meet" : "Teams"}
        </span>
      )}
    </div>
  );
}
