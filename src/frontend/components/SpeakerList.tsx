"use client";
import { useState } from "react";

export function SpeakerList({ names }: { names: string[] }) {
  const [expanded, setExpanded] = useState(false);
  if (!names.length) return <span className="faint">—</span>;
  return <div><div className="chips">{(expanded ? names : names.slice(0, 4)).map((name, index) => <span className="chip" key={index}>{name}</span>)}</div>{names.length > 4 && <button className="small" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>{expanded ? "Collapse speakers" : `Show all ${names.length} speakers`}</button>}</div>;
}
