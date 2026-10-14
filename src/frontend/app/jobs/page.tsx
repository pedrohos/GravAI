"use client";

import { useState } from "react";
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
          <div className="row row-end job-filters">
            <select
              aria-label="Filter by status"
              value={status}
              onChange={(event) => setStatus(event.target.value)}
            >
              <option value="">Filter status</option>
              {STATUSES.map((value) => (
                <option key={value} value={value}>
                  {value.toUpperCase()}
                </option>
              ))}
            </select>
            <select
              aria-label="Filter by task type"
              value={type}
              onChange={(event) => setType(event.target.value)}
            >
              <option value="">Filter task type</option>
              {["record", "transcribe"].map((value) => (
                <option key={value} value={value}>
                  {value.toUpperCase()}
                </option>
              ))}
            </select>
          </div>
        </div>

        {(status || type) && (
          <div className="active-filters" aria-label="Active filters">
            {status && (
              <span className={`pill filter-chip ${status}`}>
                Status: {status.toUpperCase()}
                <button aria-label="Clear status filter" onClick={() => setStatus("")}>×</button>
              </span>
            )}
            {type && (
              <span className={`pill filter-chip job-kind-${type}`}>
                Task: {type.toUpperCase()}
                <button aria-label="Clear task type filter" onClick={() => setType("")}>×</button>
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
