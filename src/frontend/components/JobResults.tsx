import type { Job } from "@/lib/types";

function labelOf(key: string) {
  return key.replace(/_/g, " ").replace(/^./, character => character.toUpperCase());
}

export function JobResults({ result }: { result: Job["result"] }) {
  if (!result || !Object.keys(result).length) return <div className="drawer unavailable"><span className="unavailable-label" aria-disabled="true" title="No job results available">Job results</span></div>;
  return <details className="drawer"><summary>Job results</summary><div>
    <dl className="result-fields">{Object.entries(result).map(([key, value]) => <div key={key}><dt>{labelOf(key)}</dt><dd>{typeof value === "object" && value !== null ? <pre className="json">{JSON.stringify(value, null, 2)}</pre> : String(value ?? "—")}</dd></div>)}</dl>
    <details className="drawer"><summary>Raw result JSON</summary><div><pre className="json">{JSON.stringify(result, null, 2)}</pre></div></details>
  </div></details>;
}
