/**
 * The API, as functions.
 *
 * Every path is relative to `/api` on this page's own origin, which the Next
 * server rewrites to the API over the Docker network (see next.config.ts).
 * Nothing here needs to know where the API actually is - that was the point of
 * moving the call to the server side, and it is why there is no longer a
 * config.js, a `window.GRAVAI_API_BASE` or a CORS allowlist to keep in step.
 *
 * Errors come back from FastAPI as `{detail: ...}` and are turned into thrown
 * Errors carrying that detail, because the message the API wrote - "this is a
 * transcription, it has no meeting to leave" - is better than anything the page
 * could invent from a status code.
 */

import type {
  CaptchaChallenge,
  ConfigResponse,
  Job,
  JobLog,
  JobSubmission,
  Recording,
} from "./types";

/** The proxy prefix. The rewrite in next.config.ts strips it. */
const API_BASE = "/api";

/** An API path as a URL the browser can fetch. */
export const apiUrl = (path: string) => `${API_BASE}${path}`;

export class ApiError extends Error {
  status: number;
  detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

/** Whatever went wrong, as the sentence to put in front of somebody. */
export const messageOf = (error: unknown) =>
  error instanceof ApiError ? error.detail : String(error);

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(apiUrl(path), {
    headers: options.body ? { "Content-Type": "application/json" } : {},
    ...options,
  });

  if (response.status === 204) return null as T;

  const text = await response.text();
  let payload: unknown = null;
  try {
    payload = text ? JSON.parse(text) : null;
  } catch {
    payload = text;
  }

  if (!response.ok) {
    throw new ApiError(response.status, detailOf(payload) || response.statusText);
  }
  return payload as T;
}

/** FastAPI reports a bad body as a list of per-field errors; flatten it. */
function detailOf(payload: unknown): string | null {
  if (!payload) return null;
  if (typeof payload === "string") return payload;
  const detail = (payload as { detail?: unknown }).detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        const entry = item as { loc?: unknown[]; msg?: string };
        return `${(entry.loc || []).slice(1).join(".")}: ${entry.msg}`;
      })
      .join("; ");
  }
  return null;
}

const query = (params: Record<string, string | number | undefined>) => {
  const search = new URLSearchParams(
    Object.entries(params)
      .filter(([, value]) => value !== undefined && value !== "")
      .map(([key, value]) => [key, String(value)])
  ).toString();
  return search ? `?${search}` : "";
};

export const api = {
  submitJob: (submission: JobSubmission) =>
    request<Job>("/jobs", { method: "POST", body: JSON.stringify(submission) }),
  listJobs: (filters: { status?: string; type?: string } = {}) =>
    request<Job[]>(`/jobs${query(filters)}`),
  getJob: (id: string) => request<Job>(`/jobs/${encodeURIComponent(id)}`),
  stopJob: (id: string) =>
    request<Job>(`/jobs/${encodeURIComponent(id)}/stop`, { method: "POST" }),
  cancelJob: (id: string) =>
    request<Job>(`/jobs/${encodeURIComponent(id)}/cancel`, { method: "POST" }),
  deleteJob: (id: string) =>
    request<null>(`/jobs/${encodeURIComponent(id)}`, { method: "DELETE" }),
  jobLog: (id: string, lines?: number) =>
    request<JobLog>(`/jobs/${encodeURIComponent(id)}/log${query({ lines })}`),

  listRecordings: (filters: Record<string, string> = {}) =>
    request<Recording[]>(`/recordings${query(filters)}`),
  getRecording: (id: string) => request<Recording>(`/recordings/${encodeURIComponent(id)}`),
  recordingJobs: (id: string) => request<Job[]>(`/recordings/${encodeURIComponent(id)}/jobs`),
  deleteRecording: (id: string) =>
    request<null>(`/recordings/${encodeURIComponent(id)}`, { method: "DELETE" }),

  captchaChallenges: () => request<CaptchaChallenge[]>("/captcha_challenges"),

  getConfig: () => request<ConfigResponse>("/config"),
  updateConfig: (values: Record<string, string>) =>
    request<ConfigResponse>("/config", { method: "PUT", body: JSON.stringify({ values }) }),
};

/** Audio URLs, built rather than fetched - they go straight into an <audio src>.
 *  Same-origin paths now, so the browser's range requests go through the rewrite
 *  to the API's FileResponse and seeking works as it did. */
export const audioUrl = {
  meeting: (recordingId: string) =>
    apiUrl(`/recordings/${encodeURIComponent(recordingId)}/audio`),
  participant: (recordingId: string, participantId: string, speechOnly: boolean) =>
    apiUrl(
      `/recordings/${encodeURIComponent(recordingId)}/participants/` +
        `${encodeURIComponent(participantId)}/audio${speechOnly ? "?speech_only=true" : ""}`
    ),
};
