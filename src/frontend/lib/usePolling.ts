"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { messageOf } from "./api";

/**
 * Fetches something and keeps fetching it.
 *
 * A recording runs for an hour and says nothing until it is over, so every view
 * that watches work in flight polls: jobs every 3s, recordings every 10s, one
 * recording every 15s, the CAPTCHA banner every 15s. The old page owned one
 * interval in its router and cleared it on every route change; here each view
 * owns its own and React clears it on unmount.
 *
 * `data` holds the last successful answer, so a poll that fails mid-flight
 * leaves the table on screen and reports the failure beside it rather than
 * replacing a working page with an error.
 */
export function usePolling<T>(
  fetcher: () => Promise<T>,
  intervalMs: number | null,
  deps: unknown[] = []
) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // Kept in a ref so changing the callback identity on every render does not
  // tear down and rebuild the interval each time.
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  const refresh = useCallback(async () => {
    try {
      const result = await fetcherRef.current();
      setData(result);
      setError(null);
    } catch (err) {
      setError(messageOf(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    const tick = () => {
      if (!cancelled) void refresh();
    };

    setLoading(true);
    tick();
    if (intervalMs === null) return () => {
      cancelled = true;
    };

    const timer = setInterval(tick, intervalMs);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refresh, intervalMs, ...deps]);

  return { data, error, loading, refresh };
}
