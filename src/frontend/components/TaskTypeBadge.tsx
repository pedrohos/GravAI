import type { JobType } from "@/lib/types";
import { Icon } from "./Icon";

export function TaskTypeBadge({ type }: { type: JobType }) {
  return <span className={`job-kind job-kind-${type === "transcribe" ? "transcribe" : "record"}`}>
    <Icon name={type === "transcribe" ? "file-text" : "video"} />
    {type === "transcribe" ? "Transcribe" : type === "record_and_transcribe" ? "Record + transcribe" : "Record"}
  </span>;
}
