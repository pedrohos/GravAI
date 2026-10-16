"use client";

import { EmptyState, ErrorMessage } from "@/components/ui/Feedback";
import { SettingsForm } from "@/components/SettingsForm";
import { api } from "@/lib/api";
import { usePolling } from "@/lib/usePolling";

export default function SettingsPage() {
  // No interval: settings do not change under the person editing them, and a
  // poll would overwrite what they had half-typed. Revert refetches by hand.
  const { data, error, loading, refresh } = usePolling(() => api.getConfig(), null);

  if (error && !data) return <ErrorMessage>{error}</ErrorMessage>;
  if (loading && !data) return <EmptyState>Loading…</EmptyState>;
  if (!data) return null;

  return (
    <>
      <h1 className="settings-heading">Settings</h1>
      <SettingsForm config={data} onSaved={refresh} />
    </>
  );
}
