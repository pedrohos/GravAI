"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { ACTIVE_STATUSES, type JobStatus } from "@/lib/types";
import { api } from "@/lib/api";

const EMPTY = "No execution log is available yet.";
const UNREADABLE = "The log could not be read.";

/** Check availability while collapsed; follow the full log while open. */
export function JobLogDrawer({ jobId, status }: { jobId: string; status: JobStatus }) {
  const [available, setAvailable] = useState(false);
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("…");
  const body = useRef<HTMLPreElement>(null);
  const stuckToBottom = useRef(true);

  useEffect(() => {
    let cancelled = false;

    const refresh = async () => {
      try {
        const log = await api.jobLog(jobId, open ? undefined : 1);
        if (cancelled) return;
        setAvailable(Boolean(log.log_path) || log.lines.length > 0);
        const node = body.current;
        stuckToBottom.current = node
          ? node.scrollTop + node.clientHeight >= node.scrollHeight - 24
          : true;
        setText(log.lines.length ? log.lines.join("\n") : EMPTY);
      } catch {
        if (!cancelled) { setAvailable(false); setText(UNREADABLE); }
      }
    };

    void refresh();
    const timer = open || ACTIVE_STATUSES.includes(status) ? setInterval(() => void refresh(), open ? 3000 : 10000) : undefined;
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [open, jobId, status]);

  // After the new lines are laid out, not before, or scrollHeight is the old one.
  useLayoutEffect(() => {
    if (open && stuckToBottom.current && body.current) {
      body.current.scrollTop = body.current.scrollHeight;
    }
  }, [text, open]);

  return (
    <details
      className={`drawer ${available ? "" : "unavailable"}`}
      open={open}
      onToggle={(event) => setOpen((event.currentTarget as HTMLDetailsElement).open)}
    >
      <summary title={available ? "View execution log" : "Open to check for an execution log"}>Execution log</summary>
      <div>
        <pre className="log" ref={body}>
          {text}
        </pre>
      </div>
    </details>
  );
}
