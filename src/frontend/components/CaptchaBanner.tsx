"use client";

import { api } from "@/lib/api";
import { fmtDateTime } from "@/lib/format";
import { usePolling } from "@/lib/usePolling";

/**
 * A Google sign-in that hits a CAPTCHA parks the recording and waits for a
 * person; the wait expires. So this is checked from every page, not only from
 * the one that happens to be open, and it carries the port and the throwaway
 * password because those are what somebody needs in the next few minutes.
 */
export function CaptchaBanner() {
  const { data, error } = usePolling(() => api.captchaChallenges(), 15000);

  // The banner is an extra; a service that cannot answer this is a problem the
  // page the person is actually on will report.
  if (error || !data || data.length === 0) return null;

  return (
    <div id="captcha-banner">
      {data.map((challenge) => (
        <div className="captcha" key={challenge.session_dir}>
          <strong>A recording is waiting on a CAPTCHA.</strong>
          <span>
            Connect a VNC client to <span className="mono">{challenge.vnc_url}</span>
          </span>
          {challenge.vnc_password && (
            <span className="dim">
              password <span className="mono">{challenge.vnc_password}</span>
            </span>
          )}
          <span className="faint">expires {fmtDateTime(challenge.expires_at)}</span>
        </div>
      ))}
    </div>
  );
}
