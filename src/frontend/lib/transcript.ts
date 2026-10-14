import { fmtClock } from "./format";
import type { Participant, Recording, TranscriptSegment } from "./types";

export function timedSegments(segments: TranscriptSegment[] | null) {
  return (segments ?? []).filter(segment =>
    Number.isFinite(segment.start) && Number.isFinite(segment.end) && segment.start >= 0 && segment.end >= segment.start
  ).slice().sort((a, b) => a.start - b.start);
}

export function speakersDuring(participants: Participant[], segment: TranscriptSegment) {
  return participants.filter(participant => participant.segments.some(speech =>
    segment.start === segment.end
      ? speech.start <= segment.start && speech.end > segment.start
      : speech.start < segment.end && speech.end > segment.start
  ));
}

/** Copy the meeting timeline once, in time order, with speaker labels on each line. */
export function transcriptForClipboard(recording: Recording): string {
  const meeting = timedSegments(recording.meeting_transcript_segments).filter(segment => segment.text?.trim());
  const entries = meeting.length ? meeting.map(segment => ({
    ...segment,
    speaker: speakersDuring(recording.participants, segment)
      .map(participant => participant.participant_name || participant.participant_id).join(", ") || "Unknown speaker",
  })) : recording.participants.flatMap(participant =>
    timedSegments(participant.transcript_segments).filter(segment => segment.text?.trim()).map(segment => ({
      ...segment,
      speaker: participant.participant_name || participant.participant_id,
    }))
  ).sort((a, b) => a.start - b.start);

  if (entries.length) return entries.map(segment =>
    `[${fmtClock(segment.start)} - ${fmtClock(segment.end)}] ${segment.speaker}: ${segment.text?.trim()}`
  ).join("\n");

  return recording.meeting_transcript_text?.trim() || recording.participants.flatMap(participant =>
    participant.transcript_text?.trim()
      ? [`${participant.participant_name || participant.participant_id}: ${participant.transcript_text.trim()}`]
      : []
  ).join("\n");
}
