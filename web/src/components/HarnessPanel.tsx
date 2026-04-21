"use client";

import { useCallback, useMemo, useState } from "react";
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

// ── Props ─────────────────────────────────────────────────────────────
//
// The panel is purely presentational from the parent's point of view —
// IdleScreen opens it, the panel lets the user shape a run, and on
// confirm it hands back {preset_id, overrides}. The parent forwards
// those to the WS `start` command.

export interface HarnessStartPayload {
  preset_id?: string;
  overrides?: Record<string, HarnessSectionContent>;
}

interface Props {
  open: boolean;
  onClose: () => void;
  onApply: (payload: HarnessStartPayload) => void;
  /** Field paths the swarm is currently exercising — receives a brief
   *  highlight in the Pipeline view so users can watch policy choices
   *  "light up" as the run progresses. Sourced from ``session.checkpoint``
   *  events upstream. */
  activeFieldPaths?: ReadonlySet<string>;
}

// Default preset sentinel — when the user picks "(default)" we send no
// preset_id, letting the backend fall through to `default_policy_content()`.
const DEFAULT_SENTINEL = "__default__";

// ── Diffing: per-section REPLACE ──────────────────────────────────────
//
// The server contract is "overrides REPLACE a whole section, they don't
// merge per-field". So the UI tracks a working-copy content, and on
// apply it compares each section to the base preset's section; any
// difference in any field sends the *entire* edited section as an
// override.

function sectionEquals(a: HarnessSectionContent, b: HarnessSectionContent): boolean {
  const aKeys = Object.keys(a);
  const bKeys = Object.keys(b);
  if (aKeys.length !== bKeys.length) return false;
  for (const k of aKeys) {
    if (a[k] !== b[k]) return false;
  }
  return true;
}

function diffContent(
  base: HarnessContent,
  working: HarnessContent,
): Record<string, HarnessSectionContent> {
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

interface FieldRowProps {
  name: string;
  spec: HarnessFieldSpec;
  value: unknown;
  onChange: (next: unknown) => void;
}

function FieldRow({ name, spec, value, onChange }: FieldRowProps) {
  const label = (
    <label className="text-xs font-mono text-white/60 shrink-0 w-48 truncate">
      {name}
    </label>
  );

  if (spec.type === "enum" && spec.options) {
    return (
      <div className="flex items-center gap-3 py-1.5">
        {label}
        <select
          value={String(value ?? spec.default ?? "")}
          onChange={(e) => onChange(e.target.value)}
          className="flex-1 rounded-md border border-white/10 bg-slate-950/60 px-2 py-1 text-xs font-mono text-white outline-none focus:border-fuchsia-500/60"
        >
          {spec.options.map((opt) => (
            <option key={opt} value={opt}>
              {opt}
            </option>
          ))}
        </select>
      </div>
    );
  }

  if (spec.type === "bool") {
    return (
      <div className="flex items-center gap-3 py-1.5">
        {label}
        <input
          type="checkbox"
          checked={Boolean(value ?? spec.default)}
          onChange={(e) => onChange(e.target.checked)}
          className="h-4 w-4 accent-fuchsia-500"
        />
      </div>
    );
  }

  if (spec.type === "int" || spec.type === "float") {
    const step = spec.type === "int" ? 1 : 0.01;
    return (
      <div className="flex items-center gap-3 py-1.5">
        {label}
        <input
          type="number"
          value={
            typeof value === "number"
              ? value
              : typeof spec.default === "number"
                ? spec.default
                : 0
          }
          min={spec.min}
          max={spec.max}
          step={step}
          onChange={(e) => {
            const raw = e.target.value;
            if (raw === "") return;
            const parsed = spec.type === "int" ? parseInt(raw, 10) : parseFloat(raw);
            if (Number.isFinite(parsed)) onChange(parsed);
          }}
          className="w-28 rounded-md border border-white/10 bg-slate-950/60 px-2 py-1 text-xs font-mono text-white outline-none focus:border-fuchsia-500/60"
        />
        {(spec.min !== undefined || spec.max !== undefined) && (
          <span className="text-[10px] font-mono text-white/30">
            [{spec.min ?? "−∞"} … {spec.max ?? "∞"}]
          </span>
        )}
      </div>
    );
  }

  return (
    <div className="flex items-center gap-3 py-1.5">
      {label}
      <span className="text-xs font-mono text-white/30 italic">
        unsupported field type
      </span>
    </div>
  );
}

// ── Section renderer ──────────────────────────────────────────────────

interface SectionBlockProps {
  sectionName: string;
  fields: Record<string, HarnessFieldSpec>;
  value: HarnessSectionContent;
  onChange: (next: HarnessSectionContent) => void;
  modified: boolean;
}

function SectionBlock({
  sectionName,
  fields,
  value,
  onChange,
  modified,
}: SectionBlockProps) {
  return (
    <div
      className={`rounded-xl border ${
        modified ? "border-fuchsia-500/40" : "border-white/10"
      } bg-slate-900/40 p-3`}
    >
      <div className="mb-2 flex items-center justify-between">
        <h3 className="text-sm font-mono font-bold text-fuchsia-300">
          {sectionName}
        </h3>
        {modified && (
          <span className="text-[10px] font-mono text-fuchsia-400">
            modified
          </span>
        )}
      </div>
      <div className="flex flex-col">
        {Object.entries(fields).map(([fieldName, spec]) => (
          <FieldRow
            key={fieldName}
            name={fieldName}
            spec={spec}
            value={value[fieldName]}
            onChange={(next) =>
              onChange({ ...value, [fieldName]: next })
            }
          />
        ))}
      </div>
    </div>
  );
}

// ── Pipeline view ─────────────────────────────────────────────────────
//
// 16 nodes rendered as three horizontal lanes (groups A/B/C). Clicking a
// node opens a focused drawer with just that field's editor. Edges are
// straight horizontal connectors within each lane — the grouping carries
// the real structural meaning, so a fancier layout would just add noise.

function valueAtPath(content: HarnessContent, fieldPath: string): unknown {
  const [section, field] = fieldPath.split(".");
  if (!section || !field) return undefined;
  return content[section]?.[field];
}

function specAtPath(
  schema: HarnessSchema,
  fieldPath: string,
): HarnessFieldSpec | null {
  const [section, field] = fieldPath.split(".");
  if (!section || !field) return null;
  return schema.sections[section]?.[field] ?? null;
}

function defaultForPath(
  schema: HarnessSchema,
  fieldPath: string,
): unknown {
  return specAtPath(schema, fieldPath)?.default;
}

interface PipelineViewProps {
  pipeline: HarnessPipeline;
  schema: HarnessSchema;
  base: HarnessContent;
  working: HarnessContent;
  onFieldChange: (section: string, field: string, next: unknown) => void;
  activeFieldPaths?: ReadonlySet<string>;
}

function PipelineView({
  pipeline,
  schema,
  base,
  working,
  onFieldChange,
  activeFieldPaths,
}: PipelineViewProps) {
  const [selectedPath, setSelectedPath] = useState<string | null>(null);

  const nodesByGroup = useMemo(() => {
    const map = new Map<string, HarnessPipelineNode[]>();
    for (const n of pipeline.nodes) {
      const bucket = map.get(n.group) ?? [];
      bucket.push(n);
      map.set(n.group, bucket);
    }
    return map;
  }, [pipeline.nodes]);

  const isModified = useCallback(
    (path: string): boolean => {
      const b = valueAtPath(base, path);
      const w = valueAtPath(working, path);
      return JSON.stringify(b) !== JSON.stringify(w);
    },
    [base, working],
  );

  const selected = selectedPath
    ? pipeline.nodes.find((n) => n.field_path === selectedPath) ?? null
    : null;
  const selectedSpec = selectedPath ? specAtPath(schema, selectedPath) : null;

  return (
    <div className="flex flex-1 overflow-hidden">
      <div className="flex-1 overflow-y-auto px-5 py-4 flex flex-col gap-4">
        {pipeline.groups.map((group) => {
          const groupNodes = nodesByGroup.get(group.id) ?? [];
          return (
            <div
              key={group.id}
              className="rounded-xl border border-white/10 bg-slate-900/40 p-3"
            >
              <div className="mb-2 flex items-baseline gap-3">
                <span className="text-sm font-mono font-bold text-fuchsia-300">
                  {group.id}. {group.label}
                </span>
                <span className="text-[10px] font-mono text-white/40">
                  {group.description}
                </span>
              </div>
              <div className="flex items-center gap-2 overflow-x-auto">
                {groupNodes.map((node, idx) => {
                  const isActive = activeFieldPaths?.has(node.field_path) ?? false;
                  const modified = isModified(node.field_path);
                  const current = valueAtPath(working, node.field_path);
                  return (
                    <div key={node.id} className="flex items-center gap-1">
                      <button
                        type="button"
                        onClick={() => setSelectedPath(node.field_path)}
                        className={`group relative flex min-w-[140px] flex-col items-start gap-0.5 rounded-lg border px-3 py-2 text-left transition-all ${
                          selectedPath === node.field_path
                            ? "border-cyan-400/70 bg-cyan-500/10"
                            : modified
                              ? "border-fuchsia-500/50 bg-fuchsia-500/10"
                              : "border-white/10 bg-slate-950/60 hover:border-white/30"
                        } ${isActive ? "ring-2 ring-amber-300/70 ring-offset-1 ring-offset-slate-950" : ""}`}
                      >
                        <span className="text-[11px] font-mono font-bold text-white/90 truncate w-full">
                          {node.label}
                        </span>
                        <span className="text-[10px] font-mono text-white/50 truncate w-full">
                          {formatValue(current)}
                        </span>
                        <div className="absolute top-1 right-1 flex gap-1">
                          {node.admin_sensitive && (
                            <span
                              title="admin-sensitive field"
                              className="text-[9px] text-amber-300"
                            >
                              🔒
                            </span>
                          )}
                          {modified && (
                            <span
                              title="modified from preset"
                              className="text-[9px] text-fuchsia-300"
                            >
                              ●
                            </span>
                          )}
                        </div>
                      </button>
                      {idx < groupNodes.length - 1 && (
                        <span className="text-white/20 text-xs">→</span>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          );
        })}
      </div>

      {selected && selectedSpec && (
        <aside className="w-80 shrink-0 border-l border-white/10 bg-slate-950/80 px-4 py-4 overflow-y-auto">
          <div className="mb-3 flex items-start justify-between">
            <div>
              <h3 className="text-sm font-mono font-bold text-cyan-300">
                {selected.label}
              </h3>
              <p className="text-[10px] font-mono text-white/40">
                {selected.field_path}
              </p>
            </div>
            <button
              type="button"
              onClick={() => setSelectedPath(null)}
              className="text-xs text-white/40 hover:text-white/80"
            >
              ✕
            </button>
          </div>
          {selected.admin_sensitive && (
            <div className="mb-3 rounded-md border border-amber-500/30 bg-amber-500/10 px-2 py-1.5 text-[10px] font-mono text-amber-200">
              🔒 일부 값은 관리자 권한이 필요합니다.
            </div>
          )}
          <FieldRow
            name={selected.field_path.split(".")[1] ?? selected.field_path}
            spec={selectedSpec}
            value={valueAtPath(working, selected.field_path)}
            onChange={(next) => {
              const [section, field] = selected.field_path.split(".");
              if (section && field) onFieldChange(section, field, next);
            }}
          />
          <div className="mt-3 flex items-center justify-between text-[10px] font-mono text-white/40">
            <span>
              default:{" "}
              <code className="text-white/60">
                {formatValue(defaultForPath(schema, selected.field_path))}
              </code>
            </span>
            <button
              type="button"
              onClick={() => {
                const [section, field] = selected.field_path.split(".");
                if (section && field) {
                  onFieldChange(section, field, defaultForPath(schema, selected.field_path));
                }
              }}
              className="rounded border border-white/10 bg-slate-900/60 px-2 py-0.5 text-[10px] hover:border-white/30"
            >
              reset
            </button>
          </div>
        </aside>
      )}
    </div>
  );
}

function formatValue(v: unknown): string {
  if (v === undefined || v === null) return "—";
  if (typeof v === "boolean") return v ? "true" : "false";
  if (typeof v === "number") return v.toLocaleString();
  return String(v);
}

function TabButton({
  active,
  onClick,
  disabled,
  children,
}: {
  active: boolean;
  onClick: () => void;
  disabled?: boolean;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={`rounded-t-md px-3 py-1.5 text-[11px] font-mono font-bold transition-colors ${
        active
          ? "border-b-2 border-fuchsia-400 text-white"
          : "text-white/40 hover:text-white/70"
      } disabled:opacity-30 disabled:cursor-not-allowed`}
    >
      {children}
    </button>
  );
}

// ── Main panel ────────────────────────────────────────────────────────

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
  // Fall back to the system default preset if it surfaces in the list;
  // otherwise synthesize from schema defaults (keeps the panel usable
  // even before the default row is seeded in the DB).
  const def = presets.find((p) => p.is_default);
  if (def) return def.content;
  return schema ? defaultContentFromSchema(schema) : {};
}

type ViewTab = "pipeline" | "sections";

export default function HarnessPanel({
  open,
  onClose,
  onApply,
  activeFieldPaths,
}: Props) {
  const {
    presets,
    schema,
    pipeline,
    loading,
    error,
    createPreset,
  } = useHarnessPresets({ enabled: open });

  const [selectedId, setSelectedId] = useState<string>(DEFAULT_SENTINEL);
  const [working, setWorking] = useState<HarnessContent>({});
  const [saveName, setSaveName] = useState("");
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [tab, setTab] = useState<ViewTab>("pipeline");

  const base = useMemo(
    () => (schema ? basePresetContent(presets, schema, selectedId) : {}),
    [presets, schema, selectedId],
  );

  // Re-seed the working copy whenever the selected preset changes (or
  // the backing schema/presets finish loading). Uses the "compare-prev
  // in render" pattern from the React docs to avoid a setState-in-effect
  // lint violation — the reset only fires when the base reference
  // actually changes.
  const [prevBase, setPrevBase] = useState<HarnessContent | null>(null);
  if (schema && base !== prevBase) {
    setPrevBase(base);
    setWorking(base);
  }

  const modifiedSections = useMemo(() => {
    const out = new Set<string>();
    for (const section of Object.keys(working)) {
      const b = base[section] ?? {};
      if (!sectionEquals(b, working[section])) out.add(section);
    }
    return out;
  }, [base, working]);

  const handleApply = useCallback(() => {
    const overrides = diffContent(base, working);
    const payload: HarnessStartPayload = {};
    if (selectedId !== DEFAULT_SENTINEL) payload.preset_id = selectedId;
    if (Object.keys(overrides).length > 0) payload.overrides = overrides;
    onApply(payload);
  }, [base, working, selectedId, onApply]);

  const handleReset = useCallback(() => {
    setWorking(base);
  }, [base]);

  const handleSaveAsNew = useCallback(async () => {
    const name = saveName.trim();
    if (name.length < 1) {
      setSaveError("이름을 입력해 주세요.");
      return;
    }
    setSaving(true);
    setSaveError(null);
    const res = await createPreset(name, working);
    setSaving(false);
    if (!res.ok) {
      setSaveError(`저장 실패: ${res.reason}`);
      return;
    }
    setSelectedId(res.preset.id);
    setSaveName("");
  }, [saveName, working, createPreset]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm">
      <div className="w-full max-w-3xl max-h-[88vh] flex flex-col rounded-2xl border border-fuchsia-500/30 bg-slate-950/95 shadow-2xl shadow-fuchsia-500/10">
        <div className="flex items-center justify-between border-b border-white/10 px-5 py-4">
          <div>
            <h2 className="text-lg font-bold font-mono text-transparent bg-clip-text bg-gradient-to-r from-fuchsia-400 to-cyan-400">
              Harness Settings
            </h2>
            <p className="text-[11px] font-mono text-white/40">
              프리셋을 고르거나 수정해서 실행하세요.
            </p>
          </div>
          <button
            onClick={onClose}
            className="rounded-md px-2 py-1 text-sm font-mono text-white/40 hover:bg-white/10 hover:text-white/80"
          >
            ✕
          </button>
        </div>

        {loading && !schema ? (
          <div className="flex-1 flex items-center justify-center text-sm font-mono text-white/40">
            loading…
          </div>
        ) : error ? (
          <div className="flex-1 flex items-center justify-center text-sm font-mono text-red-400">
            {error}
          </div>
        ) : !schema ? (
          <div className="flex-1 flex items-center justify-center text-sm font-mono text-white/40">
            로그인이 필요합니다.
          </div>
        ) : (
          <>
            <div className="border-b border-white/10 px-5 py-3 flex items-center gap-3">
              <label className="text-xs font-mono text-white/60">Preset</label>
              <select
                value={selectedId}
                onChange={(e) => setSelectedId(e.target.value)}
                className="flex-1 rounded-md border border-white/10 bg-slate-900/60 px-2 py-1 text-xs font-mono text-white outline-none focus:border-fuchsia-500/60"
              >
                <option value={DEFAULT_SENTINEL}>(default)</option>
                {presets.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                    {p.is_default ? " · system" : ""}
                  </option>
                ))}
              </select>
              <button
                onClick={handleReset}
                disabled={modifiedSections.size === 0}
                className="rounded-md border border-white/10 bg-slate-900/60 px-3 py-1 text-xs font-mono text-white/70 hover:border-white/30 disabled:opacity-40"
              >
                Reset
              </button>
            </div>

            <div className="border-b border-white/10 px-5 pt-1 flex gap-1">
              <TabButton
                active={tab === "pipeline"}
                onClick={() => setTab("pipeline")}
                disabled={!pipeline}
              >
                Pipeline
              </TabButton>
              <TabButton
                active={tab === "sections"}
                onClick={() => setTab("sections")}
              >
                Sections
              </TabButton>
            </div>

            {tab === "pipeline" && pipeline ? (
              <PipelineView
                pipeline={pipeline}
                schema={schema}
                base={base}
                working={working}
                onFieldChange={(section, field, next) =>
                  setWorking((prev) => ({
                    ...prev,
                    [section]: { ...(prev[section] ?? {}), [field]: next },
                  }))
                }
                activeFieldPaths={activeFieldPaths}
              />
            ) : (
              <div className="flex-1 overflow-y-auto px-5 py-4 flex flex-col gap-3">
                {Object.entries(schema.sections).map(([section, fields]) => (
                  <SectionBlock
                    key={section}
                    sectionName={section}
                    fields={fields}
                    value={working[section] ?? {}}
                    onChange={(next) =>
                      setWorking((prev) => ({ ...prev, [section]: next }))
                    }
                    modified={modifiedSections.has(section)}
                  />
                ))}
              </div>
            )}

            <div className="border-t border-white/10 px-5 py-3 flex flex-col gap-3">
              <div className="flex items-center gap-2">
                <input
                  type="text"
                  value={saveName}
                  onChange={(e) => setSaveName(e.target.value)}
                  placeholder="Save as new preset…"
                  className="flex-1 rounded-md border border-white/10 bg-slate-900/60 px-2 py-1 text-xs font-mono text-white placeholder:text-white/20 outline-none focus:border-fuchsia-500/60"
                />
                <button
                  onClick={handleSaveAsNew}
                  disabled={saving || saveName.trim().length === 0}
                  className="rounded-md border border-cyan-500/30 bg-cyan-500/10 px-3 py-1 text-xs font-mono text-cyan-200 hover:bg-cyan-500/20 disabled:opacity-40"
                >
                  {saving ? "…" : "Save"}
                </button>
              </div>
              {saveError && (
                <div className="text-[11px] font-mono text-red-400">
                  {saveError}
                </div>
              )}
              <div className="flex items-center justify-end gap-2">
                <button
                  onClick={onClose}
                  className="rounded-md border border-white/10 bg-slate-900/60 px-4 py-2 text-xs font-mono text-white/70 hover:border-white/30"
                >
                  Cancel
                </button>
                <button
                  onClick={handleApply}
                  className="rounded-md bg-gradient-to-r from-fuchsia-600 to-cyan-600 px-4 py-2 text-xs font-bold font-mono text-white hover:from-fuchsia-500 hover:to-cyan-500"
                >
                  Apply & Start
                </button>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
