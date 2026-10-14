"use client";

import { useState } from "react";
import { Filters, FilterField } from "@/components/Filters";
import { Icon } from "@/components/Icon";
import { Dropdown } from "@/components/Dropdown";
import { Pill } from "@/components/Pill";
import { TaskTypeBadge } from "@/components/TaskTypeBadge";
import { JobForm } from "@/components/JobForm";
import { JobsTable } from "@/components/JobsTable";
import { api } from "@/lib/api";
import { usePolling } from "@/lib/usePolling";

const STATUSES = ["queued", "waiting", "running", "stopping", "succeeded", "failed", "cancelled"];

export default function JobsPage() {
  const [status, setStatus] = useState("");
  const [type, setType] = useState("");

  const { data, error, loading, refresh } = usePolling(
    () => api.listJobs({ status, type }),
    3000,
    [status, type]
  );

  return (
    <div className="jobs-page">
      <h1>Jobs</h1>
      <p className="subtitle">
        Record a meeting and transcribe it via a job manager.
      </p>

      <JobForm onSubmitted={refresh} />

      <section className="panel">
        <div className="section-head">
          <h2 style={{ margin: 0 }}>Submitted Jobs</h2>
          <Filters>
            <FilterField label="Status">
              <Dropdown label="Filter by status" value={status} onChange={setStatus} options={[
                { value: "", label: "All statuses", content: <span className="pill filter-all">All statuses</span> },
                ...STATUSES.map(value => ({ value, label: value, content: <Pill status={value} /> })),
              ]} />
            </FilterField>
            <FilterField label="Task type">
              <Dropdown label="Filter by task type" value={type} onChange={setType} options={[
                { value: "", label: "All task types", content: <span className="job-kind filter-all">All task types</span> },
                { value: "record", label: "Record", content: <TaskTypeBadge type="record" /> },
                { value: "transcribe", label: "Transcribe", content: <TaskTypeBadge type="transcribe" /> },
              ]} />
            </FilterField>
          </Filters>
        </div>

        {(status || type) && (
          <div className="active-filters" aria-label="Active filters">
            {status && (
              <span className={`pill filter-chip ${status}`}>
                Status: {status.toUpperCase()}
                <button aria-label="Clear status filter" onClick={() => setStatus("")}><Icon name="x" /></button>
              </span>
            )}
            {type && (
              <span className={`pill filter-chip job-kind-${type}`}>
                Task: {type.toUpperCase()}
                <button aria-label="Clear task type filter" onClick={() => setType("")}><Icon name="x" /></button>
              </span>
            )}
          </div>
        )}
        {error && <div className="error-box">{error}</div>}
        {loading && !data ? (
          <div className="empty">Loading…</div>
        ) : (
          <div className="jobs-table-scroll">
            <JobsTable jobs={data ?? []} onChanged={refresh} />
          </div>
        )}
      </section>
    </div>
  );
}
