"use client";

import { Button } from "@/components/ui/Button";
import { useEffect, useRef, useState } from "react";
import { Icon } from "./Icon";
import { useToast } from "./Toast";
import { fmtClock } from "@/lib/format";
import type { TranscriptSegment } from "@/lib/types";

export interface TranscriptRow extends TranscriptSegment {
  speakers?: string[];
}

export function TranscriptPlayer({ audioSrc, segments, transcriptText, copyText, emptyText, label, showSpeakers = false }: {
  audioSrc?: string;
  segments: TranscriptRow[];
  transcriptText?: string | null;
  copyText: string;
  emptyText: string;
  label: string;
  showSpeakers?: boolean;
}) {
  const audio = useRef<HTMLAudioElement>(null);
  const viewport = useRef<HTMLDivElement>(null);
  const rows = useRef(new Map<number, HTMLTableRowElement>());
  const pendingSeek = useRef<number | null>(null);
  const [currentTime, setCurrentTime] = useState(0);
  const [playing, setPlaying] = useState(false);
  const { attempt } = useToast();
  const activeIndex = segments.findIndex(segment => currentTime >= segment.start && currentTime < segment.end);

  // Move only the transcript viewport, leaving the player and page in place.
  useEffect(() => {
    if (!playing || activeIndex < 0) return;
    const container = viewport.current;
    const row = rows.current.get(activeIndex);
    if (!container || !row) return;
    const rowBounds = row.getBoundingClientRect();
    const bounds = container.getBoundingClientRect();
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    container.scrollTo({
      top: container.scrollTop + rowBounds.top - bounds.top - container.clientHeight / 2 + rowBounds.height / 2,
      behavior: reducedMotion ? "instant" : "smooth",
    });
  }, [activeIndex, playing]);

  function seek(time: number) {
    const player = audio.current;
    if (!player) return;
    if (player.readyState === 0) {
      pendingSeek.current = time;
      player.load();
      return;
    }
    player.currentTime = time;
    setCurrentTime(time);
  }

  return <div className="transcript-player">
    <div className="meeting-media-controls">
    {audioSrc && <audio
      ref={audio}
      controls
      preload="metadata"
      src={audioSrc}
      aria-label={`${label} audio`}
      onTimeUpdate={event => setCurrentTime(event.currentTarget.currentTime)}
      onSeeked={event => setCurrentTime(event.currentTarget.currentTime)}
      onPlay={() => setPlaying(true)}
      onPause={() => setPlaying(false)}
      onEnded={() => setPlaying(false)}
      onLoadedMetadata={event => {
        if (pendingSeek.current === null) return;
        event.currentTarget.currentTime = pendingSeek.current;
        setCurrentTime(pendingSeek.current);
        pendingSeek.current = null;
      }}
    />}
    <Button type="button" className="small copy-transcript" disabled={!copyText} title="Copy transcript in time order with timings and speaker names" onClick={() => void attempt(() => navigator.clipboard.writeText(copyText), "Transcript copied.")}>
      <Icon name="copy" /> Copy transcript
    </Button>
    </div>
    {segments.length ? <>
      {audioSrc && <p className="transcript-hint dim">Select a row to seek the audio. The transcript follows playback.</p>}
      <div className="meeting-transcript-scroll" ref={viewport} tabIndex={0} aria-label={`Timed ${label.toLowerCase()} transcript`}>
        <table className={`meeting-transcript-table ${audioSrc ? "seekable" : ""}`}>
          <colgroup><col className="transcript-start-column" />{showSpeakers && <col className="transcript-speaker-column" />}<col /></colgroup>
          <thead><tr><th scope="col">Start</th>{showSpeakers && <th scope="col" title="Speakers with recorded activity during this segment">Speaker</th>}<th scope="col">Transcript</th></tr></thead>
          <tbody>{segments.map((segment, index) => <tr
            key={`${segment.start}-${index}`}
            ref={row => { if (row) rows.current.set(index, row); else rows.current.delete(index); }}
            className={index === activeIndex && audioSrc ? "active-segment" : undefined}
            aria-current={index === activeIndex && audioSrc ? "true" : undefined}
            onClick={() => seek(segment.start)}
          >
            <td>{audioSrc ? <Button type="button" className="transcript-seek" aria-label={`Seek audio to ${fmtClock(segment.start)}`}><time>{fmtClock(segment.start)}</time></Button> : <time>{fmtClock(segment.start)}</time>}</td>
            {showSpeakers && <td>{segment.speakers?.length ? <div className="chips">{segment.speakers.map((name, speakerIndex) => <span className="chip" key={speakerIndex}>{name}</span>)}</div> : <span className="dim">Unknown</span>}</td>}
            <td>{segment.text?.trim() || "—"}</td>
          </tr>)}</tbody>
        </table>
      </div>
    </> : <div className={`transcript ${transcriptText ? "" : "empty-text"}`}>
      {transcriptText || emptyText}
    </div>}
  </div>;
}
