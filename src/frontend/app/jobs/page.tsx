"use client";

import { useState } from "react";
import { JOB_TYPES, JobForm } from "@/components/JobForm";
import { JobsTable } from "@/components/JobsTable";
import { api } from "@/lib/api";
import { usePolling } from "@/lib/usePolling";

const STATUSES = ["queued", "running", "stopping", "succeeded", "failed", "cancelled"];

export default function JobsPage() {
  const [status, setStatus] = useState("");
  const [type, setType] = useState("");

  const { data, error, loading, refresh } = usePolling(
    () => api.listJobs({ status, type }),
    3000,
    [status, type]
  );

  return (
    <>
      <h1>Jobs</h1>
      <p className="subtitle">
        Recording a meeting and transcribing it both take longer than a request can wait, so each
        is a job with an id. Submit one here and it keeps running on the server whether or not
        this page is open.
      </p>

      <JobForm onSubmitted={refresh} />

      <section className="panel">
        <div className="section-head">
          <h2 style={{ margin: 0 }}>Submitted</h2>
          <div className="row row-end">
            <select
              style={{ width: "auto" }}
              value={status}
              onChange={(event) => setStatus(event.target.value)}
            >
              <option value="">Any status</option>
              {STATUSES.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
            <select
              style={{ width: "auto" }}
              value={type}
              onChange={(event) => setType(event.target.value)}
            >
              <option value="">Any type</option>
              {JOB_TYPES.map(([value]) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </div>
        </div>

        {error && <div className="error-box">{error}</div>}
        {loading && !data ? (
          <div className="empty">Loading…</div>
        ) : (
          <JobsTable jobs={data ?? []} onChanged={refresh} />
        )}
      </section>
    </>
  );
}
