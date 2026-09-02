import { fmtClock } from "@/lib/format";
import type { TranscriptSegment } from "@/lib/types";

/** Whisper's segments, timed against the meeting rather than against the audio
 *  they were produced from - so a line here can be found in the recording. */
export function SegmentsDrawer({ segments }: { segments: TranscriptSegment[] | null }) {
  if (!segments || !segments.length) return null;

  return (
    <details className="drawer">
      <summary>transcript with timings ({segments.length})</summary>
      <div className="segments">
        {segments.map((segment, index) => (
          <div className="seg" key={index}>
            <time>
              {fmtClock(segment.start)} → {fmtClock(segment.end)}
            </time>
            <span>{(segment.text || "").trim()}</span>
          </div>
        ))}
      </div>
    </details>
  );
}
