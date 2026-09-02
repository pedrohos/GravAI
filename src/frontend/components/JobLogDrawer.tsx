"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { api } from "@/lib/api";

const EMPTY = "Nothing logged yet. A recording writes its log once it has a session directory.";
const UNREADABLE = "The log could not be read.";

/**
 * A job's log, followed while it is open.
 *
 * Only polls while open - a page of finished jobs would otherwise ask for a
 * dozen logs every three seconds to show none of them. The open drawer keeps
 * itself scrolled to the bottom, but only if it was already there: somebody who
 * has scrolled up is reading, and yanking them back down on the next tick would
 * make the log unreadable exactly when it matters.
 */
export function JobLogDrawer({ jobId }: { jobId: string }) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("…");
  const body = useRef<HTMLPreElement>(null);
  const stuckToBottom = useRef(true);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;

    const refresh = async () => {
      try {
        const log = await api.jobLog(jobId);
        if (cancelled) return;
        const node = body.current;
        stuckToBottom.current = node
          ? node.scrollTop + node.clientHeight >= node.scrollHeight - 24
          : true;
        setText(log.lines.length ? log.lines.join("\n") : EMPTY);
      } catch {
        if (!cancelled) setText(UNREADABLE);
      }
    };

    void refresh();
    const timer = setInterval(() => void refresh(), 3000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [open, jobId]);

  // After the new lines are laid out, not before, or scrollHeight is the old one.
  useLayoutEffect(() => {
    if (open && stuckToBottom.current && body.current) {
      body.current.scrollTop = body.current.scrollHeight;
    }
  }, [text, open]);

  return (
    <details
      className="drawer"
      open={open}
      onToggle={(event) => setOpen((event.currentTarget as HTMLDetailsElement).open)}
    >
      <summary>log</summary>
      <div>
        <pre className="log" ref={body}>
          {text}
        </pre>
      </div>
    </details>
  );
}
