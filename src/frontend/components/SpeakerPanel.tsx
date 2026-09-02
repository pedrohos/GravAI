import { audioUrl } from "@/lib/api";
import { fmtClock, fmtDuration } from "@/lib/format";
import type { Participant, Recording } from "@/lib/types";
import { SegmentsDrawer } from "./SegmentsDrawer";

export function SpeakerPanel({
  recording,
  participant,
}: {
  recording: Recording;
  participant: Participant;
}) {
  const name = participant.participant_name || participant.participant_id;
  const speaking = participant.segments.reduce(
    (total, segment) => total + (segment.end - segment.start),
    0
  );

  return (
    <section className="speaker">
      <header>
        <h3>{name}</h3>
        <span className="faint">
          {participant.segments.length} turn{participant.segments.length === 1 ? "" : "s"} ·{" "}
          {fmtDuration(speaking)} speaking
        </span>
      </header>

      <div className="body">
        <div className={`transcript ${participant.transcript_text ? "" : "empty-text"}`}>
          {participant.transcript_text || "No transcript for this speaker."}
        </div>

        <details className="drawer">
          <summary>audio</summary>
          <div>
            <label>
              Aligned to the meeting — everyone else silenced, so it plays against the others
            </label>
            <audio
              controls
              preload="none"
              src={audioUrl.participant(recording.id, participant.participant_id, false)}
            />
            {/* The speech-only track is what whisper was actually given: the
                aligned one is mostly silence, and whisper answers silence with
                sentences nobody said. */}
            {participant.speech_track_path && (
              <>
                <label style={{ marginTop: 12 }}>
                  Their turns only — the audio whisper was given
                </label>
                <audio
                  controls
                  preload="none"
                  src={audioUrl.participant(recording.id, participant.participant_id, true)}
                />
              </>
            )}
          </div>
        </details>

        <details className="drawer">
          <summary>when they were speaking ({participant.segments.length})</summary>
          <div className="segments">
            {participant.segments.length ? (
              participant.segments.map((segment, index) => (
                <div className="seg" key={index}>
                  <time>
                    {fmtClock(segment.start)} → {fmtClock(segment.end)}
                  </time>
                  <span className="faint">{fmtDuration(segment.end - segment.start)}</span>
                </div>
              ))
            ) : (
              <p className="faint">None.</p>
            )}
          </div>
        </details>

        <SegmentsDrawer segments={participant.transcript_segments} />
      </div>
    </section>
  );
}
