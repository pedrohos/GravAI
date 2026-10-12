"use client";

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
  const [values, setValues] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);

  // Reset the form whenever a fresh config arrives - which is what Revert does,
  // by refetching.
  useEffect(() => {
    setValues(Object.fromEntries(config.fields.map((field) => [field.name, field.value])));
  }, [config]);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    const saved = await attempt(() => api.updateConfig(values), "Settings saved.");
    setBusy(false);
    if (saved) onSaved();
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
                value={values[field.name] ?? ""}
                onChange={(value) => setValues((previous) => ({ ...previous, [field.name]: value }))}
              />
            ))}
          </div>
        </section>
      ))}

      <div className="row">
        <button className="primary" type="submit" disabled={busy}>
          Save settings
        </button>
        <button type="button" onClick={onSaved}>
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
}: {
  field: ConfigField;
  value: string;
  onChange: (value: string) => void;
}) {
  const label = (
    <>
      {field.name}
      {field.required && <span className="faint"> required</span>}
    </>
  );
  const hint = field.description ? <div className="hint">{field.description}</div> : null;

  if (field.kind === "bool") {
    return (
      <div className="field">
        <label>{label}</label>
        <label className="check">
          <input
            type="checkbox"
            checked={isTrue(value)}
            onChange={(event) => onChange(String(event.target.checked ? "True" : "False"))}
          />{" "}
          enabled
        </label>
        {hint}
      </div>
    );
  }

  return (
    <div className="field">
      <label htmlFor={`set-${field.name}`}>{label}</label>
      <input
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
      {hint}
    </div>
  );
}
