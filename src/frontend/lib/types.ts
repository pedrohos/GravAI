/**
 * The API's shapes, as TypeScript.
 *
 * Mirrors gravai/jobs/models.py and gravai/api/schemas.py. Written by hand
 * rather than generated: the surface is small and stable, and a generator would
 * need either a running API or a committed openapi.json to build against.
 */

export type JobType = "record" | "transcribe" | "record_and_transcribe";

export type JobStatus =
  | "queued"
  | "waiting"
  | "running"
  | "stopping"
  | "succeeded"
  | "failed"
  | "cancelled";

/** The statuses a job can still be acted on from. */
export const ACTIVE_STATUSES: readonly JobStatus[] = [
  "queued",
  "waiting",
  "running",
  "stopping",
];

export interface Job {
  id: string;
  type: JobType;
  status: JobStatus;
  /** Exactly what the caller asked for, kept verbatim. */
  params: { meeting_url?: string; tracks_output_dir?: string; [key: string]: unknown };
  result: Record<string, unknown> | null;
  error: string | null;
  /** Both are filled in as soon as the recorder has them, minutes before a result. */
  recording_id: string | null;
  session_dir: string | null;
  /** The job that has to finish first. Set on the transcription half of a
   *  record-and-transcribe, which waits for its recording. */
  depends_on: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface JobSubmission {
  type: JobType;
  meeting_url?: string;
  tracks_output_dir?: string;
  slice_tracks: boolean;
  group_slices_by_name: boolean;
}

export interface JobLog {
  job_id: string;
  session_dir: string | null;
  log_path: string | null;
  lines: string[];
}

export interface SpeechSegment {
  start: number;
  end: number;
}

/** Whisper's own segments, timed against the meeting rather than the track. */
export interface TranscriptSegment {
  start: number;
  end: number;
  text?: string;
}

export interface Participant {
  participant_id: string;
  participant_name: string | null;
  track_path: string | null;
  speech_track_path: string | null;
  segments: SpeechSegment[];
  transcript_text: string | null;
  transcript_segments: TranscriptSegment[] | null;
}

export interface Recording {
  id: string;
  meeting_url: string | null;
  provider: string | null;
  session_dir: string;
  status: string;
  started_at: string | null;
  ended_at: string | null;
  duration_seconds: number | null;
  main_track_path: string | null;
  meeting_transcript_text: string | null;
  meeting_transcript_segments: TranscriptSegment[] | null;
  participants: Participant[];
  created_at: string | null;
  updated_at: string | null;
}

export interface ConfigField {
  name: string;
  value: string;
  secret: boolean;
  is_set: boolean;
  kind: "string" | "int" | "float" | "bool";
  required: boolean;
  description: string;
}

export interface ConfigResponse {
  env_file: string;
  fields: ConfigField[];
}

export interface CaptchaChallenge {
  state: string;
  session_dir: string;
  meeting_url: string | null;
  vnc_url: string;
  vnc_host: string;
  vnc_port: number;
  vnc_password: string | null;
  screenshot_path: string | null;
  expires_at: string;
  updated_at: string;
}
