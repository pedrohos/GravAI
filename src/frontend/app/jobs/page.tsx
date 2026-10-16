"use client";

import { TableScroll } from "@/components/ui/TableScroll";
import { EmptyState, ErrorMessage } from "@/components/ui/Feedback";
import { Panel, SectionHeader, PageHeader } from "@/components/ui/Panel";
import { useState } from "react";
import { Filters, FilterField, FilterChip } from "@/components/Filters";
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
      <PageHeader title="Jobs" subtitle="Record a meeting and transcribe it via a job manager." />

      <JobForm onSubmitted={refresh} />

      <Panel>
        <SectionHeader title="Submitted Jobs">
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
        </SectionHeader>

        {(status || type) && (
          <div className="active-filters" aria-label="Active filters">
            {status && (
              <FilterChip label="status" className={`pill ${status}`} onClear={() => setStatus("")}>Status: {status.toUpperCase()}</FilterChip>
            )}
            {type && (
              <FilterChip label="task type" className={`pill job-kind-${type}`} onClear={() => setType("")}>Task: {type.toUpperCase()}</FilterChip>
            )}
          </div>
        )}
        {error && <ErrorMessage>{error}</ErrorMessage>}
        {loading && !data ? (
          <EmptyState>Loading…</EmptyState>
        ) : (
          <TableScroll>
            <JobsTable jobs={data ?? []} onChanged={refresh} />
          </TableScroll>
        )}
      </Panel>
    </div>
  );
}
