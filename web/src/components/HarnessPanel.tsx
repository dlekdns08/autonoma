"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  useHarnessPresets,
  type HarnessContent,
  type HarnessFieldSpec,
  type HarnessPipeline,
  type HarnessPipelineNode,
  type HarnessPreset,
  type HarnessSchema,
  type HarnessSectionContent,
} from "@/hooks/useHarnessPresets";

// ── Public types ──────────────────────────────────────────────────────

export interface HarnessStartPayload {
  preset_id?: string;
  overrides?: Record<string, HarnessSectionContent>;
}

interface Props {
  open: boolean;
  onClose: () => void;
  /** Called when user clicks "Apply" (saves settings, does NOT start).
   *  Parent stores the payload and uses it on the next start. */
  onApply: (payload: HarnessStartPayload) => void;
  /** Called when user clicks "Apply & Start". Parent starts immediately. */
  onApplyAndStart?: (payload: HarnessStartPayload) => void;
  activeFieldPaths?: ReadonlySet<string>;
}

const DEFAULT_SENTINEL = "__default__";

// ── Dialogue style previews (Feature 17) ─────────────────────────────

const STYLE_PREVIEWS: Record<string, string> = {
  casual: "hey so i just finished that thing, looks good tbh",
  formal: "I have completed the assigned task. The implementation is satisfactory.",
  poetic: "With careful hands I've woven code into being...",
  terse: "Done. Next?",
  enthusiastic: "YES! Got it working!! This is SO cool!!",
  technical: "Task complete. Exit code 0. No errors detected.",
  playful: "Ta-daa! Look what I made, hehe~ ✨",
};

// ── Section descriptions ──────────────────────────────────────────────

const SECTION_DESCRIPTIONS: Record<string, string> = {
  loop: "Round limits, timeouts, and exit/stall conditions",
  action: "Inbox size, sandbox limits, JSON extraction, error handling",
  decision: "LLM parse retries, failure behavior, message priority",
  memory: "Per-agent memory limits and context summarization",
  spawn: "Max agents, spawn cooldown, approval mode",
  routing: "Task and message routing strategy",
  safety: "Circuit breakers, code execution, enforcement level",
  mood: "Emotional state transitions and sentiment thresholds",
  social: "Relationship thresholds and social event cadences",
  system: "Agent persona and system prompt variant",
  cache: "Provider-side prompt-cache behavior",
  budget: "Token budget cap per run",
  checkpoint: "Session checkpoint event cadence",
};

// ── Diffing ───────────────────────────────────────────────────────────

function sectionEquals(a: HarnessSectionContent, b: HarnessSectionContent): boolean {
  const aKeys = Object.keys(a);
  const bKeys = Object.keys(b);
  if (aKeys.length !== bKeys.length) return false;
  for (const k of aKeys) {
    if (a[k] !== b[k]) return false;
  }
  return true;
}

function diffContent(base: HarnessContent, working: HarnessContent) {
  const out: Record<string, HarnessSectionContent> = {};
  for (const section of Object.keys(working)) {
    const baseSection = base[section] ?? {};
    if (!sectionEquals(baseSection, working[section])) {
      out[section] = working[section];
    }
  }
  return out;
}

// ── Field renderer ────────────────────────────────────────────────────

function FieldRow({
  name,
  spec,
  value,
  onChange,
  sectionName,
}: {
  name: string;
  spec: HarnessFieldSpec;
  value: unknown;
  onChange: (next: unknown) => void;
  sectionName?: string;
}) {
  const label = (
    <label className="text-xs font-mono text-white/55 shrink-0 w-44 truncate" title={name}>
      {name}
    </label>
  );

  if (spec.type === "enum" && spec.options) {
    const currentVal = String(value ?? spec.default ?? "");
    const isSpeechStyle = sectionName === "social" && name === "speech_style";
    const stylePreview = isSpeechStyle ? STYLE_PREVIEWS[currentVal] : undefined;
    return (
      <div className="flex flex-col py-1">
        <div className="flex items-center gap-3">
          {label}
          <select
            value={currentVal}
            onChange={(e) => onChange(e.target.value)}
            className="flex-1 rounded border border-white/10 bg-slate-950/70 px-2 py-1 text-xs font-mono text-white outline-none focus:border-fuchsia-500/50"
          >
            {spec.options.map((opt) => (
              <option key={opt} value={opt}>{opt}</option>
            ))}
          </select>
        </div>
        {stylePreview && (
          <p className="text-xs italic text-white/40 mt-1 ml-[188px]">
            &ldquo;{stylePreview}&rdquo;
          </p>
        )}
      </div>
    );
  }

  if (spec.type === "bool") {
    return (
      <div className="flex items-center gap-3 py-1">
        {label}
        <button
          type="button"
          role="switch"
          aria-checked={Boolean(value ?? spec.default)}
          onClick={() => onChange(!Boolean(value ?? spec.default))}
          className={`relative h-5 w-9 rounded-full transition-colors ${
            Boolean(value ?? spec.default) ? "bg-fuchsia-600" : "bg-white/15"
          }`}
        >
          <span
            className={`absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-transform ${
              Boolean(value ?? spec.default) ? "translate-x-4" : "translate-x-0.5"
            }`}
          />
        </button>
        <span className="text-[10px] font-mono text-white/35">
          {Boolean(value ?? spec.default) ? "on" : "off"}
        </span>
      </div>
    );
  }

  if (spec.type === "int" || spec.type === "float") {
    const step = spec.type === "int" ? 1 : 0.01;
    return (
      <div className="flex items-center gap-3 py-1">
        {label}
        <input
          type="number"
          value={typeof value === "number" ? value : typeof spec.default === "number" ? spec.default : 0}
          min={spec.min}
          max={spec.max}
          step={step}
          onChange={(e) => {
            const raw = e.target.value;
            if (raw === "") return;
            const parsed = spec.type === "int" ? parseInt(raw, 10) : parseFloat(raw);
            if (Number.isFinite(parsed)) onChange(parsed);
          }}
          className="w-28 rounded border border-white/10 bg-slate-950/70 px-2 py-1 text-xs font-mono text-white outline-none focus:border-fuchsia-500/50"
        />
        {(spec.min !== undefined || spec.max !== undefined) && (
          <span className="text-[10px] font-mono text-white/25">
            [{spec.min ?? "−∞"} … {spec.max ?? "∞"}]
          </span>
        )}
      </div>
    );
  }

  return (
    <div className="flex items-center gap-3 py-1">
      {label}
      <span className="text-xs font-mono text-white/25 italic">unsupported type</span>
    </div>
  );
}

// ── Collapsible section block ─────────────────────────────────────────

function SectionBlock({
  sectionName,
  fields,
  value,
  defaultValue,
  onChange,
  modified,
}: {
  sectionName: string;
  fields: Record<string, HarnessFieldSpec>;
  value: HarnessSectionContent;
  defaultValue: HarnessSectionContent;
  onChange: (next: HarnessSectionContent) => void;
  modified: boolean;
}) {
  const [open, setOpen] = useState(false);

  const summaryParts = useMemo(() => {
    return Object.entries(fields)
      .slice(0, 3)
      .map(([k]) => {
        const v = value[k];
        return v !== undefined ? `${k}: ${formatValue(v)}` : null;
      })
      .filter(Boolean)
      .join(" · ");
  }, [fields, value]);

  return (
    <div className={`rounded-lg border transition-colors ${
      modified ? "border-fuchsia-500/35" : "border-white/8"
    } bg-slate-900/30`}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center gap-3 px-4 py-2.5 text-left"
      >
        <span className="font-mono text-[10px] text-white/25 w-3">{open ? "▼" : "▶"}</span>
        <span className="font-mono text-sm font-semibold text-white/80 w-28 shrink-0 capitalize">
          {sectionName}
        </span>
        {modified && (
          <span className="h-1.5 w-1.5 rounded-full bg-fuchsia-400 shadow-[0_0_5px_rgba(232,121,249,0.7)] shrink-0" />
        )}
        <span className="text-[10px] font-mono text-white/30 truncate flex-1">{summaryParts}</span>
        <span className="text-[10px] font-mono text-white/20 shrink-0">
          {SECTION_DESCRIPTIONS[sectionName] ? "" : ""}
        </span>
        {modified && (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onChange({ ...defaultValue });
            }}
            className="shrink-0 rounded px-1.5 py-0.5 text-[10px] font-mono text-white/40 hover:bg-white/10 hover:text-white/70"
            title="Reset section to preset values"
          >
            reset
          </button>
        )}
      </button>

      {open && (
        <div className="border-t border-white/8 px-4 pb-3 pt-2">
          {SECTION_DESCRIPTIONS[sectionName] && (
            <p className="mb-2 text-[10px] font-mono text-white/30 italic">
              {SECTION_DESCRIPTIONS[sectionName]}
            </p>
          )}
          <div className="flex flex-col">
            {Object.entries(fields).map(([fieldName, spec]) => (
              <FieldRow
                key={fieldName}
                name={fieldName}
                spec={spec}
                value={value[fieldName]}
                onChange={(next) => onChange({ ...value, [fieldName]: next })}
                sectionName={sectionName}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ── Combo edges ──────────────────────────────────────────────────────
//
// Mirrors DANGEROUS_COMBOS in src/autonoma/harness/validation.py. When a
// combo's predicate trips, the Pipeline canvas paints both endpoint
// nodes red and the warnings panel lists the reason. Keep in sync with
// the server — the server is authoritative and rejects the payload, we
// just surface it ahead of the round-trip.

interface ComboEdge {
  from: string;
  to: string;
  reason: string;
  isConflict: (content: HarnessContent) => boolean;
}

const COMBO_EDGES: ComboEdge[] = [
  {
    from: "safety.code_execution",
    to: "action.harness_enforcement",
    reason:
      "code_execution=disabled + harness_enforcement=off disables both safety layers",
    isConflict: (c) =>
      c.safety?.code_execution === "disabled" &&
      c.action?.harness_enforcement === "off",
  },
  {
    from: "spawn.approval_mode",
    to: "safety.enforcement_level",
    reason:
      "automatic spawning + enforcement=off removes every gate on uncontrolled agent growth",
    isConflict: (c) =>
      c.spawn?.approval_mode === "automatic" &&
      c.safety?.enforcement_level === "off",
  },
];

// ── Policy validation (client-side preview of server rules) ──────────

function validatePolicy(working: HarnessContent): string[] {
  const warnings: string[] = [];
  const action = working.action ?? {};
  const safety = working.safety ?? {};
  const loop = working.loop ?? {};
  const spawn = working.spawn ?? {};

  for (const edge of COMBO_EDGES) {
    if (edge.isConflict(working)) {
      warnings.push(`🔴 ${edge.from} ↔ ${edge.to}: ${edge.reason}`);
    }
  }

  if (safety.enforcement_level === "off") {
    warnings.push(
      "⚠️ safety.enforcement_level=off is admin-only and removes every safety gate",
    );
  }
  if (action.harness_enforcement === "off") {
    warnings.push(
      "⚠️ action.harness_enforcement=off is admin-only — agents ignore role restrictions",
    );
  }
  const maxRounds = typeof loop.max_rounds === "number" ? loop.max_rounds : 0;
  if (maxRounds > 200) {
    warnings.push("⚠️ loop.max_rounds above 200 requires admin privileges");
  }
  const maxAgents = typeof spawn.max_agents === "number" ? spawn.max_agents : 0;
  if (maxAgents > 16) {
    warnings.push("⚠️ spawn.max_agents above 16 requires admin privileges");
  }

  return warnings;
}

// ── Pipeline canvas (editable graph) ──────────────────────────────────

function valueAtPath(content: HarnessContent, fieldPath: string): unknown {
  const [section, field] = fieldPath.split(".");
  if (!section || !field) return undefined;
  return content[section]?.[field];
}

function specAtPath(
  schema: HarnessSchema,
  fieldPath: string,
): HarnessFieldSpec | undefined {
  const [section, field] = fieldPath.split(".");
  if (!section || !field) return undefined;
  return schema.sections[section]?.[field];
}

function setValueAtPath(
  prev: HarnessContent,
  fieldPath: string,
  next: unknown,
): HarnessContent {
  const [section, field] = fieldPath.split(".");
  if (!section || !field) return prev;
  return {
    ...prev,
    [section]: { ...(prev[section] ?? {}), [field]: next },
  };
}

function CompactField({
  spec,
  value,
  onChange,
}: {
  spec: HarnessFieldSpec;
  value: unknown;
  onChange: (next: unknown) => void;
}) {
  if (spec.type === "enum" && spec.options) {
    const currentVal = String(value ?? spec.default ?? "");
    return (
      <select
        value={currentVal}
        onChange={(e) => onChange(e.target.value)}
        className="w-full rounded border border-white/15 bg-slate-950/80 px-1.5 py-1 text-[11px] font-mono font-semibold text-white outline-none focus:border-fuchsia-500/60"
      >
        {spec.options.map((opt) => (
          <option key={opt} value={opt}>
            {opt}
          </option>
        ))}
      </select>
    );
  }
  if (spec.type === "bool") {
    const on = Boolean(value ?? spec.default);
    return (
      <button
        type="button"
        role="switch"
        aria-checked={on}
        onClick={() => onChange(!on)}
        className={`w-full rounded px-2 py-1 text-[11px] font-mono font-bold transition-colors ${
          on
            ? "bg-fuchsia-600/80 text-white"
            : "bg-white/10 text-white/60 hover:bg-white/15"
        }`}
      >
        {on ? "ON" : "OFF"}
      </button>
    );
  }
  if (spec.type === "int" || spec.type === "float") {
    const step = spec.type === "int" ? 1 : 0.01;
    const num =
      typeof value === "number"
        ? value
        : typeof spec.default === "number"
          ? spec.default
          : 0;
    return (
      <input
        type="number"
        value={num}
        min={spec.min}
        max={spec.max}
        step={step}
        onChange={(e) => {
          const raw = e.target.value;
          if (raw === "") return;
          const parsed =
            spec.type === "int" ? parseInt(raw, 10) : parseFloat(raw);
          if (Number.isFinite(parsed)) onChange(parsed);
        }}
        className="w-full rounded border border-white/15 bg-slate-950/80 px-1.5 py-1 text-[11px] font-mono font-semibold text-white outline-none focus:border-fuchsia-500/60"
      />
    );
  }
  return <span className="text-[10px] font-mono italic text-white/30">—</span>;
}

function NodeCard({
  node,
  spec,
  value,
  onChange,
  active,
  modified,
  conflict,
}: {
  node: HarnessPipelineNode;
  spec: HarnessFieldSpec | undefined;
  value: unknown;
  onChange: (next: unknown) => void;
  active: boolean;
  modified: boolean;
  conflict: boolean;
}) {
  const borderClass = conflict
    ? "border-red-400/60 bg-red-500/10"
    : active
      ? "border-amber-400/55 bg-amber-500/10"
      : modified
        ? "border-fuchsia-500/40 bg-fuchsia-500/5"
        : "border-white/10 bg-slate-950/55";
  return (
    <div
      className={`flex min-w-[170px] flex-1 basis-[170px] flex-col rounded-md border px-2.5 py-2 ${borderClass}`}
    >
      <div className="mb-1.5 flex items-center gap-1.5">
        <span className="flex-1 truncate font-mono text-[10px] uppercase tracking-wide text-white/55">
          {node.label}
        </span>
        {node.admin_sensitive && (
          <span
            title="At least one value on this field is admin-only"
            className="text-[8px] text-amber-400/70"
          >
            🔒
          </span>
        )}
        {modified && (
          <span
            title="Modified from preset"
            className="h-1.5 w-1.5 rounded-full bg-fuchsia-400 shadow-[0_0_5px_rgba(232,121,249,0.6)]"
          />
        )}
      </div>
      {spec ? (
        <CompactField spec={spec} value={value} onChange={onChange} />
      ) : (
        <span className="text-[10px] font-mono italic text-white/30">
          schema missing
        </span>
      )}
      <div className="mt-1 font-mono text-[9px] text-white/25">
        {node.field_path}
      </div>
    </div>
  );
}

function PipelineCanvas({
  pipeline,
  schema,
  working,
  base,
  onChange,
  activeFieldPaths,
  comboConflicts,
}: {
  pipeline: HarnessPipeline;
  schema: HarnessSchema;
  working: HarnessContent;
  base: HarnessContent;
  onChange: (fieldPath: string, next: unknown) => void;
  activeFieldPaths?: ReadonlySet<string>;
  comboConflicts: ReadonlySet<string>;
}) {
  const nodesByGroup = useMemo(() => {
    const map = new Map<string, HarnessPipelineNode[]>();
    for (const g of pipeline.groups) map.set(g.id, []);
    for (const n of pipeline.nodes) {
      const bucket = map.get(n.group);
      if (bucket) bucket.push(n);
    }
    return map;
  }, [pipeline.groups, pipeline.nodes]);

  return (
    <div className="flex-1 overflow-y-auto px-5 py-4">
      <p className="mb-4 text-center text-[11px] font-mono text-white/30">
        Core knobs grouped by stage — edit inline. Open{" "}
        <span className="mx-0.5 rounded bg-white/5 px-1.5 py-0.5 text-[10px]">
          Advanced
        </span>{" "}
        for the full field list.
      </p>
      <div className="mx-auto flex max-w-3xl flex-col gap-3">
        {pipeline.groups.map((group) => {
          const nodes = nodesByGroup.get(group.id) ?? [];
          return (
            <div
              key={group.id}
              className="rounded-lg border border-white/10 bg-slate-900/35 px-4 py-3"
            >
              <div className="mb-2.5 flex items-baseline gap-2">
                <span className="font-mono text-xs font-bold tracking-widest text-amber-300/75">
                  {group.label}
                </span>
                <span className="truncate text-[10px] font-mono text-white/35">
                  {group.description}
                </span>
              </div>
              <div className="flex flex-wrap gap-2">
                {nodes.map((node) => {
                  const spec = specAtPath(schema, node.field_path);
                  const val = valueAtPath(working, node.field_path);
                  const baseVal = valueAtPath(base, node.field_path);
                  const modified = val !== baseVal;
                  const active =
                    activeFieldPaths?.has(node.field_path) ?? false;
                  const conflict = comboConflicts.has(node.field_path);
                  return (
                    <NodeCard
                      key={node.id}
                      node={node}
                      spec={spec}
                      value={val}
                      onChange={(next) => onChange(node.field_path, next)}
                      active={active}
                      modified={modified}
                      conflict={conflict}
                    />
                  );
                })}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── Preset bar with rename/delete ─────────────────────────────────────

function PresetBar({
  presets,
  selectedId,
  onSelect,
  onRename,
  onDelete,
  onDuplicate,
  modifiedCount,
}: {
  presets: HarnessPreset[];
  selectedId: string;
  onSelect: (id: string) => void;
  onRename: (id: string, name: string) => Promise<void>;
  onDelete: (id: string) => Promise<void>;
  onDuplicate: () => void;
  modifiedCount: number;
}) {
  const [renaming, setRenaming] = useState(false);
  const [renameValue, setRenameValue] = useState("");
  const [busy, setBusy] = useState(false);

  const selected = selectedId === DEFAULT_SENTINEL
    ? null
    : presets.find((p) => p.id === selectedId) ?? null;
  const canEdit = selected !== null && !selected.is_default;

  const startRename = () => {
    if (!canEdit || !selected) return;
    setRenameValue(selected.name);
    setRenaming(true);
  };

  const commitRename = async () => {
    if (!selected || !renameValue.trim()) return;
    setBusy(true);
    await onRename(selected.id, renameValue.trim());
    setBusy(false);
    setRenaming(false);
  };

  const handleDelete = async () => {
    if (!canEdit || !selected) return;
    if (!confirm(`Delete preset "${selected.name}"?`)) return;
    setBusy(true);
    await onDelete(selected.id);
    setBusy(false);
  };

  return (
    <div className="flex items-center gap-2 border-b border-white/10 px-5 py-3">
      <label className="shrink-0 text-xs font-mono text-white/50">Preset</label>

      {renaming ? (
        <input
          autoFocus
          value={renameValue}
          onChange={(e) => setRenameValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") void commitRename();
            if (e.key === "Escape") setRenaming(false);
          }}
          className="flex-1 rounded border border-fuchsia-500/50 bg-slate-900/80 px-2 py-1 text-xs font-mono text-white outline-none"
        />
      ) : (
        <select
          value={selectedId}
          onChange={(e) => onSelect(e.target.value)}
          className="flex-1 rounded border border-white/10 bg-slate-900/60 px-2 py-1 text-xs font-mono text-white outline-none focus:border-fuchsia-500/50"
        >
          <option value={DEFAULT_SENTINEL}>(default)</option>
          {presets.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}{p.is_default ? " · system" : ""}
            </option>
          ))}
        </select>
      )}

      {renaming ? (
        <>
          <button
            type="button"
            onClick={() => void commitRename()}
            disabled={busy || !renameValue.trim()}
            className="shrink-0 rounded border border-cyan-500/40 bg-cyan-500/10 px-2 py-1 text-[10px] font-mono text-cyan-200 hover:bg-cyan-500/20 disabled:opacity-40"
          >
            {busy ? "…" : "Save"}
          </button>
          <button
            type="button"
            onClick={() => setRenaming(false)}
            className="shrink-0 text-[10px] font-mono text-white/40 hover:text-white/70"
          >
            Cancel
          </button>
        </>
      ) : (
        <>
          {canEdit && (
            <button
              type="button"
              onClick={startRename}
              title="Rename preset"
              className="shrink-0 rounded border border-white/10 px-2 py-1 text-[10px] font-mono text-white/40 hover:border-white/30 hover:text-white/70"
            >
              ✏
            </button>
          )}
          <button
            type="button"
            onClick={onDuplicate}
            title="Duplicate as new preset"
            className="shrink-0 rounded border border-white/10 px-2 py-1 text-[10px] font-mono text-white/40 hover:border-white/30 hover:text-white/70"
          >
            ⊕
          </button>
          {canEdit && (
            <button
              type="button"
              onClick={() => void handleDelete()}
              disabled={busy}
              title="Delete preset"
              className="shrink-0 rounded border border-red-500/20 px-2 py-1 text-[10px] font-mono text-red-400/60 hover:border-red-500/50 hover:text-red-300 disabled:opacity-40"
            >
              🗑
            </button>
          )}
        </>
      )}

      {modifiedCount > 0 && (
        <span className="shrink-0 rounded bg-fuchsia-500/15 px-2 py-0.5 text-[10px] font-mono text-fuchsia-300">
          {modifiedCount} modified
        </span>
      )}
    </div>
  );
}

// ── Helpers ───────────────────────────────────────────────────────────

function formatValue(v: unknown): string {
  if (v === undefined || v === null) return "—";
  if (typeof v === "boolean") return v ? "on" : "off";
  if (typeof v === "number") return v.toLocaleString();
  return String(v);
}

function defaultContentFromSchema(schema: HarnessSchema): HarnessContent {
  const out: HarnessContent = {};
  for (const [section, fields] of Object.entries(schema.sections)) {
    const s: HarnessSectionContent = {};
    for (const [name, spec] of Object.entries(fields)) {
      s[name] = spec.default;
    }
    out[section] = s;
  }
  return out;
}

function basePresetContent(
  presets: HarnessPreset[],
  schema: HarnessSchema | null,
  presetId: string,
): HarnessContent {
  if (presetId !== DEFAULT_SENTINEL) {
    const p = presets.find((x) => x.id === presetId);
    if (p) return p.content;
  }
  const def = presets.find((p) => p.is_default);
  if (def) return def.content;
  return schema ? defaultContentFromSchema(schema) : {};
}

type ViewTab = "pipeline" | "advanced";

// ── Main panel ────────────────────────────────────────────────────────

export default function HarnessPanel({
  open,
  onClose,
  onApply,
  onApplyAndStart,
  activeFieldPaths,
}: Props) {
  const {
    presets,
    schema,
    pipeline,
    loading,
    error,
    createPreset,
    updatePreset,
    deletePreset,
  } = useHarnessPresets({ enabled: open });

  const [selectedId, setSelectedId] = useState<string>(DEFAULT_SENTINEL);
  const [working, setWorking] = useState<HarnessContent>({});
  const [saveName, setSaveName] = useState("");
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [tab, setTab] = useState<ViewTab>("pipeline");
  const [saveExpanded, setSaveExpanded] = useState(false);

  const base = useMemo(
    () => (schema ? basePresetContent(presets, schema, selectedId) : {}),
    [presets, schema, selectedId],
  );

  const [prevBase, setPrevBase] = useState<HarnessContent | null>(null);
  useEffect(() => {
    if (schema && base !== prevBase) {
      setPrevBase(base);
      setWorking(base);
    }
  }, [base, schema, prevBase]);

  const modifiedSections = useMemo(() => {
    const out = new Set<string>();
    for (const section of Object.keys(working)) {
      const b = base[section] ?? {};
      if (!sectionEquals(b, working[section])) out.add(section);
    }
    return out;
  }, [base, working]);

  const buildPayload = useCallback((): HarnessStartPayload => {
    const overrides = diffContent(base, working);
    const payload: HarnessStartPayload = {};
    if (selectedId !== DEFAULT_SENTINEL) payload.preset_id = selectedId;
    if (Object.keys(overrides).length > 0) payload.overrides = overrides;
    return payload;
  }, [base, working, selectedId]);

  const handleApply = useCallback(() => {
    onApply(buildPayload());
    onClose();
  }, [buildPayload, onApply, onClose]);

  const handleApplyAndStart = useCallback(() => {
    const payload = buildPayload();
    if (onApplyAndStart) {
      onApplyAndStart(payload);
    } else {
      onApply(payload);
    }
    onClose();
  }, [buildPayload, onApply, onApplyAndStart, onClose]);

  const handleReset = useCallback(() => setWorking(base), [base]);

  const handleSaveAsNew = useCallback(async () => {
    const name = saveName.trim();
    if (!name) { setSaveError("Enter a name."); return; }
    setSaving(true);
    setSaveError(null);
    const res = await createPreset(name, working);
    setSaving(false);
    if (!res.ok) { setSaveError(`Save failed: ${res.reason}`); return; }
    setSelectedId(res.preset.id);
    setSaveName("");
    setSaveExpanded(false);
  }, [saveName, working, createPreset]);

  const handleUpdatePreset = useCallback(async () => {
    if (selectedId === DEFAULT_SENTINEL) return;
    setSaving(true);
    setSaveError(null);
    const res = await updatePreset(selectedId, { content: working });
    setSaving(false);
    if (!res.ok) setSaveError(`Update failed: ${res.reason}`);
  }, [selectedId, working, updatePreset]);

  const handleRenamePreset = useCallback(async (id: string, name: string) => {
    await updatePreset(id, { name });
  }, [updatePreset]);

  const handleDeletePreset = useCallback(async (id: string) => {
    await deletePreset(id);
    setSelectedId(DEFAULT_SENTINEL);
  }, [deletePreset]);

  const handleDuplicate = useCallback(() => {
    const base_name = selectedId === DEFAULT_SENTINEL
      ? "Default"
      : presets.find((p) => p.id === selectedId)?.name ?? "Preset";
    setSaveName(`${base_name} copy`);
    setSaveExpanded(true);
  }, [selectedId, presets]);

  const canUpdateExisting =
    selectedId !== DEFAULT_SENTINEL &&
    !presets.find((p) => p.id === selectedId)?.is_default &&
    modifiedSections.size > 0;

  const policyWarnings = useMemo(() => validatePolicy(working), [working]);

  const comboConflicts = useMemo(() => {
    const paths = new Set<string>();
    for (const edge of COMBO_EDGES) {
      if (edge.isConflict(working)) {
        paths.add(edge.from);
        paths.add(edge.to);
      }
    }
    return paths;
  }, [working]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 backdrop-blur-sm">
      <div className="w-full max-w-3xl max-h-[90vh] flex flex-col rounded-2xl border border-white/10 bg-[#080810]/97 shadow-2xl shadow-black/60">

        {/* Header */}
        <div className="flex items-center justify-between border-b border-white/10 px-5 py-3.5">
          <div>
            <h2 className="text-base font-bold font-mono text-transparent bg-clip-text bg-gradient-to-r from-fuchsia-400 to-cyan-400">
              ⚙ Harness Settings
            </h2>
            <p className="text-[10px] font-mono text-white/35 mt-0.5">
              Configure swarm behavior — changes take effect on the next run
            </p>
          </div>
          <button
            onClick={onClose}
            className="rounded-md px-2 py-1 text-sm font-mono text-white/35 hover:bg-white/8 hover:text-white/70"
          >
            ✕
          </button>
        </div>

        {/* Loading / Error / Login required states */}
        {loading && !schema ? (
          <div className="flex-1 flex items-center justify-center text-sm font-mono text-white/35">
            Loading…
          </div>
        ) : error ? (
          <div className="flex-1 flex items-center justify-center text-sm font-mono text-red-400 px-8 text-center">
            {error}
          </div>
        ) : !schema ? (
          <div className="flex-1 flex items-center justify-center text-sm font-mono text-white/35">
            Login required to view harness settings.
          </div>
        ) : (
          <>
            {/* Preset bar */}
            <PresetBar
              presets={presets}
              selectedId={selectedId}
              onSelect={setSelectedId}
              onRename={handleRenamePreset}
              onDelete={handleDeletePreset}
              onDuplicate={handleDuplicate}
              modifiedCount={modifiedSections.size}
            />

            {/* Tabs */}
            <div className="flex gap-1 border-b border-white/10 px-5 pt-2">
              {(["pipeline", "advanced"] as ViewTab[]).map((t) => (
                <button
                  key={t}
                  type="button"
                  onClick={() => setTab(t)}
                  disabled={t === "pipeline" && !pipeline}
                  className={`rounded-t px-3 py-1.5 text-[11px] font-mono font-bold capitalize transition-colors ${
                    tab === t
                      ? "border-b-2 border-fuchsia-400 text-white"
                      : "text-white/35 hover:text-white/65"
                  } disabled:opacity-25 disabled:cursor-not-allowed`}
                >
                  {t === "pipeline" ? "Pipeline" : "Advanced"}
                </button>
              ))}
            </div>

            {/* Tab content */}
            {tab === "pipeline" && pipeline ? (
              <PipelineCanvas
                pipeline={pipeline}
                schema={schema}
                working={working}
                base={base}
                onChange={(path, next) =>
                  setWorking((prev) => setValueAtPath(prev, path, next))
                }
                activeFieldPaths={activeFieldPaths}
                comboConflicts={comboConflicts}
              />
            ) : (
              <div className="flex-1 overflow-y-auto px-5 py-3 flex flex-col gap-2">
                {Object.entries(schema.sections).map(([section, fields]) => (
                  <SectionBlock
                    key={section}
                    sectionName={section}
                    fields={fields}
                    value={working[section] ?? {}}
                    defaultValue={base[section] ?? {}}
                    onChange={(next) => setWorking((prev) => ({ ...prev, [section]: next }))}
                    modified={modifiedSections.has(section)}
                  />
                ))}
              </div>
            )}

            {/* Footer */}
            <div className="border-t border-white/10 px-5 py-3 flex flex-col gap-2">

              {/* Save as new / update row */}
              <div className="flex items-center gap-2">
                {saveExpanded ? (
                  <>
                    <input
                      autoFocus
                      type="text"
                      value={saveName}
                      onChange={(e) => setSaveName(e.target.value)}
                      onKeyDown={(e) => { if (e.key === "Enter") void handleSaveAsNew(); if (e.key === "Escape") setSaveExpanded(false); }}
                      placeholder="New preset name…"
                      className="flex-1 rounded border border-white/10 bg-slate-900/70 px-2 py-1 text-xs font-mono text-white placeholder:text-white/20 outline-none focus:border-fuchsia-500/50"
                    />
                    <button
                      type="button"
                      onClick={() => void handleSaveAsNew()}
                      disabled={saving || !saveName.trim()}
                      className="shrink-0 rounded border border-cyan-500/35 bg-cyan-500/10 px-3 py-1 text-xs font-mono text-cyan-200 hover:bg-cyan-500/20 disabled:opacity-40"
                    >
                      {saving ? "…" : "Save new"}
                    </button>
                    <button
                      type="button"
                      onClick={() => setSaveExpanded(false)}
                      className="text-[10px] font-mono text-white/35 hover:text-white/60"
                    >
                      Cancel
                    </button>
                  </>
                ) : (
                  <>
                    <button
                      type="button"
                      onClick={() => setSaveExpanded(true)}
                      className="rounded border border-white/10 px-3 py-1 text-xs font-mono text-white/45 hover:border-white/25 hover:text-white/65"
                    >
                      + Save as new preset
                    </button>
                    {canUpdateExisting && (
                      <button
                        type="button"
                        onClick={() => void handleUpdatePreset()}
                        disabled={saving}
                        className="rounded border border-cyan-500/30 bg-cyan-500/8 px-3 py-1 text-xs font-mono text-cyan-300/70 hover:bg-cyan-500/15 disabled:opacity-40"
                      >
                        {saving ? "…" : "Save changes"}
                      </button>
                    )}
                    {modifiedSections.size > 0 && (
                      <button
                        type="button"
                        onClick={handleReset}
                        className="rounded border border-white/10 px-3 py-1 text-xs font-mono text-white/35 hover:border-white/25 hover:text-white/55"
                      >
                        Reset all
                      </button>
                    )}
                  </>
                )}
              </div>

              {saveError && (
                <div className="text-[11px] font-mono text-red-400">{saveError}</div>
              )}

              {/* Policy validation warnings */}
              {policyWarnings.length > 0 && (
                <div className="flex flex-col gap-1 rounded-lg border border-yellow-500/30 bg-yellow-500/8 px-3 py-2">
                  {policyWarnings.map((w, i) => (
                    <p key={i} className="text-[11px] font-mono text-yellow-300/90">{w}</p>
                  ))}
                </div>
              )}

              {/* Action buttons */}
              <div className="flex items-center justify-end gap-2">
                <button
                  onClick={onClose}
                  className="rounded border border-white/10 bg-slate-900/60 px-4 py-2 text-xs font-mono text-white/55 hover:border-white/25"
                >
                  Cancel
                </button>
                <button
                  onClick={handleApply}
                  title="Save settings for the next run without starting"
                  className="rounded border border-fuchsia-500/35 bg-fuchsia-500/10 px-4 py-2 text-xs font-mono text-fuchsia-200 hover:bg-fuchsia-500/20"
                >
                  Apply
                </button>
                <button
                  onClick={handleApplyAndStart}
                  className="rounded bg-gradient-to-r from-fuchsia-600 to-cyan-600 px-4 py-2 text-xs font-bold font-mono text-white hover:from-fuchsia-500 hover:to-cyan-500"
                >
                  ▶ Apply & Start
                </button>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
