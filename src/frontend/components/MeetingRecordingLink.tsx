"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Job } from "@/lib/types";

import { ProviderBadge, providerOf } from "./ProviderBadge";
type Provider = "meet" | "teams";

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
      <ProviderBadge provider={provider} />
    </div>
  );
}
