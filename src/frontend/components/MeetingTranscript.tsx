"use client";

import { Panel, SectionHeader } from "@/components/ui/Panel";
import { useMemo } from "react";
import { audioUrl } from "@/lib/api";
import { speakersDuring, timedSegments, transcriptForClipboard } from "@/lib/transcript";
import type { Recording } from "@/lib/types";
import { TranscriptPlayer } from "./TranscriptPlayer";

export function MeetingTranscript({ recording, transcribing }: { recording: Recording; transcribing: boolean }) {
  const segments = useMemo(() => timedSegments(recording.meeting_transcript_segments).map(segment => ({
    ...segment,
    speakers: speakersDuring(recording.participants, segment).map(participant => participant.participant_name || participant.participant_id),
  })), [recording.meeting_transcript_segments, recording.participants]);
  const copyText = useMemo(() => transcriptForClipboard(recording), [recording]);

  return <Panel aria-labelledby="meeting-transcript-heading">
    <SectionHeader title="Meeting audio and transcript" titleId="meeting-transcript-heading" />
    <TranscriptPlayer
      audioSrc={recording.main_track_path ? audioUrl.meeting(recording.id) : undefined}
      segments={segments}
      transcriptText={recording.meeting_transcript_text}
      copyText={copyText}
      emptyText={transcribing ? "Transcribing..." : "Not transcribed yet."}
      label="Meeting"
      showSpeakers
    />
  </Panel>;
}
