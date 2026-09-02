"use client";

import { SettingsForm } from "@/components/SettingsForm";
import { api } from "@/lib/api";
import { usePolling } from "@/lib/usePolling";

export default function SettingsPage() {
  // No interval: settings do not change under the person editing them, and a
  // poll would overwrite what they had half-typed. Revert refetches by hand.
  const { data, error, loading, refresh } = usePolling(() => api.getConfig(), null);

  if (error && !data) return <div className="error-box">{error}</div>;
  if (loading && !data) return <div className="empty">Loading…</div>;
  if (!data) return null;

  return (
    <>
      <h1>Settings</h1>
      <p className="subtitle">
        Written to <span className="mono">{data.env_file}</span>. A change applies to every job
        started after it and survives a restart; a recording already in a meeting keeps the
        settings it began with.
      </p>
      <SettingsForm config={data} onSaved={refresh} />
    </>
  );
}
