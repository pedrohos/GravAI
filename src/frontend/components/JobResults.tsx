import type { Job } from "@/lib/types";
import { Drawer } from "./ui/Drawer";

function labelOf(key: string) {
  return key.replace(/_/g, " ").replace(/^./, character => character.toUpperCase());
}

export function JobResults({ result }: { result: Job["result"] }) {
  if (!result || !Object.keys(result).length) {
    return <Drawer summary="Job results" summaryTitle="No job results available" unavailable />;
  }
  return <Drawer summary="Job results">
    <dl className="result-fields">
      {Object.entries(result).map(([key, value]) => <div key={key}>
        <dt>{labelOf(key)}</dt>
        <dd>{typeof value === "object" && value !== null ? <pre className="json">{JSON.stringify(value, null, 2)}</pre> : String(value ?? "—")}</dd>
      </div>)}
    </dl>
    <Drawer summary="Raw result JSON"><pre className="json">{JSON.stringify(result, null, 2)}</pre></Drawer>
  </Drawer>;
}
