"use client";

import { Button } from "@/components/ui/Button";
import { Drawer } from "./ui/Drawer";
import { useMemo } from "react";
import { Icon } from "@/components/Icon";
import { audioUrl } from "@/lib/api";
import { fmtDuration } from "@/lib/format";
import { timedSegments, transcriptForClipboard } from "@/lib/transcript";
import type { Participant, Recording } from "@/lib/types";
import { TranscriptPlayer } from "./TranscriptPlayer";

export function SpeakerPanel({ recording, participant, expanded, onExpandedChange, onTranscribe, transcribing, disabled }: {
  recording: Recording;
  participant: Participant;
  expanded: boolean;
  onExpandedChange: (expanded: boolean) => void;
  onTranscribe: () => void;
  transcribing: boolean;
  disabled: boolean;
}) {
  const name = participant.participant_name || participant.participant_id;
  const speaking = participant.segments.reduce((total, segment) => total + segment.end - segment.start, 0);
  const segments = useMemo(() => timedSegments(participant.transcript_segments), [participant.transcript_segments]);
  const copyText = useMemo(() => transcriptForClipboard({
    ...recording,
    participants: [participant],
    meeting_transcript_segments: null,
    meeting_transcript_text: null,
  }), [recording, participant]);

  return <details className="speaker speaker-details" open={expanded} onToggle={event => onExpandedChange(event.currentTarget.open)}>
    <summary>
      <Icon name="chevron-down" />
      <h3>{name}</h3>
      <span className="faint">{participant.segments.length} turn{participant.segments.length === 1 ? "" : "s"} · {fmtDuration(speaking)} speaking</span>
    </summary>
    {expanded && <div className="body">
      <div className="speaker-actions">
        <Button type="button" className="small" disabled={disabled || transcribing || !participant.track_path} onClick={onTranscribe}>
          {transcribing ? "Transcribing..." : participant.transcript_text ? "Transcribe this speaker again" : "Transcribe this speaker"}
        </Button>
      </div>
      <TranscriptPlayer
        audioSrc={participant.track_path ? audioUrl.participant(recording.id, participant.participant_id, false) : undefined}
        segments={segments}
        transcriptText={participant.transcript_text}
        copyText={copyText}
        emptyText={transcribing ? "Transcribing..." : "No transcript for this speaker."}
        label={name}
      />
      {participant.speech_track_path && <Drawer summary="Speech-only audio">
          <p className="dim">This speaker’s turns with silence removed.</p>
          <audio aria-label={`${name} speech-only audio`} controls preload="none" src={audioUrl.participant(recording.id, participant.participant_id, true)} />
      </Drawer>}
    </div>}
  </details>;
}
