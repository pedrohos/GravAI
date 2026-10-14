"use client";

import { Icon } from "./Icon";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { ConfigField, ConfigResponse } from "@/lib/types";
import { useToast } from "./Toast";

/** The settings worth grouping, in the order they are worth reading. Anything
 *  the API exposes that is not named here lands in "Other". */
const FIELD_GROUPS: readonly (readonly [string, readonly string[]])[] = [
  ["Transcription", ["WHISPER_HOST", "WHISPER_PORT", "WHISPER_LANGUAGE"]],
  ["Google account", ["GOOGLE_ACCOUNT_EMAIL", "GOOGLE_ACCOUNT_PASSWORD"]],
  [
    "CAPTCHA handoff over VNC",
    ["VNC_ENABLED", "VNC_HOST", "VNC_PORT", "VNC_PASSWORD", "VNC_CAPTCHA_TIMEOUT_S"],
  ],
  ["Storage", ["SAVE_DIR", "DATABASE_PATH", "WS_HOST"]],
  ["Diagnostics", ["LOG_LEVEL", "DEBUG_GRAVAI", "JOB_LOG_TAIL_LINES"]],
];

function group(config: ConfigResponse): [string, ConfigField[]][] {
  const byName = new Map(config.fields.map((field) => [field.name, field]));
  const grouped: [string, ConfigField[]][] = FIELD_GROUPS.map(([title, names]) => [
    title,
    names.map((name) => byName.get(name)).filter((field): field is ConfigField => Boolean(field)),
  ]);

  const listed = new Set(FIELD_GROUPS.flatMap(([, names]) => names));
  const rest = config.fields.filter((field) => !listed.has(field.name));
  if (rest.length) grouped.push(["Other", rest]);

  return grouped;
}

/** A boolean comes back as Python's str(bool). */
const isTrue = (value: string) => value === "True";

export function SettingsForm({ config, onSaved }: { config: ConfigResponse; onSaved: () => void }) {
  const { attempt } = useToast();
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(config.fields.map((field) => [field.name, field.value]))
  );
  const [baseline, setBaseline] = useState(values);
  const dirty = Object.keys(values).some((name) => values[name] !== baseline[name]);
  const [busy, setBusy] = useState(false);

  // Reset the form whenever a fresh config arrives - which is what Revert does,
  // by refetching.
  useEffect(() => {
    const next = Object.fromEntries(config.fields.map((field) => [field.name, field.value]));
    setValues(next);
    setBaseline(next);
  }, [config]);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!dirty || busy) return;
    setBusy(true);
    const changes = Object.fromEntries(
      Object.entries(values).filter(([name, value]) => value !== baseline[name])
    );
    const saved = await attempt(() => api.updateConfig(changes), "Settings saved.");
    setBusy(false);
    if (saved) {
      const next = Object.fromEntries(saved.fields.map((field) => [field.name, field.value]));
      setValues(next);
      setBaseline(next);
      onSaved();
    }
  }

  return (
    <form onSubmit={onSubmit}>
      {group(config).map(([title, fields]) => (
        <section className="panel" key={title}>
          <h2>{title}</h2>
          <div className="row" style={{ alignItems: "flex-start" }}>
            {fields.map((field) => (
              <Field
                key={field.name}
                field={field}
                disabled={busy}
                value={values[field.name] ?? ""}
                onChange={(value) => setValues((previous) => ({ ...previous, [field.name]: value }))}
              />
            ))}
          </div>
        </section>
      ))}

      <div className="row">
        <button className="primary" type="submit" disabled={busy || !dirty}>
          Save settings
        </button>
        <button type="button" disabled={busy || !dirty} onClick={onSaved}>
          Revert
        </button>
      </div>
    </form>
  );
}

function Field({
  field,
  value,
  onChange,
  disabled,
}: {
  field: ConfigField;
  value: string;
  onChange: (value: string) => void;
  disabled: boolean;
}) {
  const label = (
    <>
      {field.name}
      {field.required && <span className="faint"> required</span>}
    </>
  );
  const descriptionId = `help-${field.name}`;
  const fieldLabel = (
    <div className="setting-label">
      <label htmlFor={`set-${field.name}`}>{label}</label>
      {field.description && (
        <span className="info-tooltip">
          <button type="button" className="info-trigger" aria-label={`Information about ${field.name}`} aria-describedby={descriptionId}><Icon name="info" /></button>
          <span id={descriptionId} role="tooltip" className="info-content">{field.description}</span>
        </span>
      )}
    </div>
  );

  if (field.kind === "bool") {
    return (
      <div className="field">
        {fieldLabel}
        <label className="check">
          <input
            id={`set-${field.name}`}
            disabled={disabled}
            type="checkbox"
            checked={isTrue(value)}
            onChange={(event) => onChange(String(event.target.checked ? "True" : "False"))}
          />{" "}
          enabled
        </label>
      </div>
    );
  }

  return (
    <div className="field">
      {fieldLabel}
      <input
        disabled={disabled}
        type={field.secret ? "password" : "text"}
        id={`set-${field.name}`}
        name={field.name}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        // A secret reads back as a placeholder. Sending it back unchanged leaves
        // it alone; sending an empty string clears it.
        placeholder={field.secret && field.is_set ? "unchanged" : ""}
        autoComplete="off"
      />
    </div>
  );
}
