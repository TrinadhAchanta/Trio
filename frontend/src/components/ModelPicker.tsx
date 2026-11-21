"use client";

import type { ModelsResponse, Reasoning } from "@/lib/types";

export function modelLabel(models: ModelsResponse | null, id: string): string {
  if (id === "auto") return "Auto";
  for (const p of models?.providers ?? []) for (const m of p.models) if (m.id === id) return m.label;
  return id;
}

export function ModelPicker({
  models,
  value,
  onChange,
  allowAuto = true,
  label = "Model",
  id,
}: {
  models: ModelsResponse | null;
  value: string;
  onChange: (v: string) => void;
  allowAuto?: boolean;
  label?: string;
  id?: string;
}) {
  return (
    <label className="inline-flex items-center gap-1.5 text-xs">
      <span className="sr-only">{label}</span>
      <select
        id={id}
        data-testid={id}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="max-w-[14rem] rounded-md border border-line bg-panel px-2 py-1 text-xs"
      >
        {allowAuto && <option value="auto">Auto (best available)</option>}
        {models?.providers.map((p) => (
          <optgroup key={p.name} label={p.mock ? `${p.label}: local demo, no AI` : p.configured ? p.label : `${p.label}: not configured`}>
            {p.models.map((m) => (
              <option key={m.id} value={m.id} disabled={!p.configured}>
                {m.label}
                {!p.configured ? " (add API key)" : ""}
              </option>
            ))}
          </optgroup>
        ))}
      </select>
    </label>
  );
}

export function ReasoningPicker({ value, onChange }: { value: Reasoning; onChange: (v: Reasoning) => void }) {
  const opts: { v: Reasoning; label: string; title: string }[] = [
    { v: "fast", label: "Fast", title: "Quick answers from lighter models" },
    { v: "balanced", label: "Balanced", title: "Good default for most questions" },
    { v: "deep", label: "Deep", title: "More reasoning for hard problems" },
  ];
  return (
    <div role="radiogroup" aria-label="Reasoning mode" className="inline-flex overflow-hidden rounded-md border border-line text-xs">
      {opts.map((o) => (
        <button
          key={o.v}
          type="button"
          role="radio"
          aria-checked={value === o.v}
          title={o.title}
          onClick={() => onChange(o.v)}
          className={`px-2 py-1 ${value === o.v ? "bg-accent text-accent-contrast" : "bg-panel hover:bg-panel-2"}`}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}
