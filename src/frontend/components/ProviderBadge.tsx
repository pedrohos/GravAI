import { Icon } from "./Icon";

export function providerOf(provider: unknown, meetingUrl?: string | null): "meet" | "teams" | null {
  if (provider === "meet" || provider === "teams") return provider;
  try {
    const host = new URL(meetingUrl ?? "").hostname;
    if (host === "meet.google.com") return "meet";
    if (host === "teams.microsoft.com" || host === "teams.live.com" || host.endsWith(".teams.microsoft.com")) return "teams";
  } catch { /* A directory is not a URL. */ }
  return null;
}

export function ProviderBadge({ provider, meetingUrl }: { provider?: unknown; meetingUrl?: string | null }) {
  const kind = providerOf(provider, meetingUrl);
  if (!kind) return null;
  return <span className={`provider-badge provider-${kind}`}><Icon name="video" />{kind === "meet" ? "Meet" : "Teams"}</span>;
}
