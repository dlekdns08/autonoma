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
// 16 nodes rendered as a clean three-lane diagram — each group is a
// horizontal row of numbered circles with a section label floating on
// the left. A low-contrast rail runs through the circle centers, and a
// short S-curve connects consecutive lanes to suggest flow. Clicking a
// node opens a per-field editor in the right drawer.

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

interface NodeCircleProps {
  number: number;
  label: string;
  valueText: string;
  selected: boolean;
  modified: boolean;
  active: boolean;
  adminSensitive: boolean;
  onClick: () => void;
}

function NodeCircle({
  number,
  label,
  valueText,
  selected,
  modified,
  active,
  adminSensitive,
  onClick,
}: NodeCircleProps) {
  // Single responsibility per ring — selection wins over active wins
  // over modified. Keeps the diagram legible: at most one accent per
  // circle at a time.
  const ringClass = selected
    ? "border-cyan-300/80 ring-2 ring-cyan-300/25"
    : active
      ? "border-amber-300/80 ring-2 ring-amber-300/25"
      : modified
        ? "border-fuchsia-400/70"
        : "border-white/15 group-hover:border-white/45";

  return (
    <button
      type="button"
      onClick={onClick}
      className="group relative z-10 flex w-[92px] flex-col items-center gap-2.5 focus:outline-none"
    >
      <div
        className={`relative flex h-12 w-12 items-center justify-center rounded-full border bg-slate-950 transition-all ${ringClass}`}
      >
        <span className="font-serif text-[18px] font-semibold tabular-nums text-white/85">
          {number}
        </span>
        {adminSensitive && (
          <span className="absolute -top-1 -right-1 text-[9px] leading-none text-amber-300/90">
            🔒
          </span>
        )}
        {modified && !selected && (
          <span
            aria-hidden
            className="absolute -bottom-0.5 -right-0.5 h-1.5 w-1.5 rounded-full bg-fuchsia-400 shadow-[0_0_6px_rgba(232,121,249,0.7)]"
          />
        )}
      </div>
      <span className="max-w-[88px] truncate text-center font-mono text-[11px] leading-tight text-white/65">
        {label}
      </span>
      <span className="-mt-1.5 max-w-[88px] truncate text-center font-mono text-[9px] leading-none text-white/30">
        {valueText}
      </span>
    </button>
  );
}

function LaneConnector() {
  // Thin S-curve from right-of-previous-lane down to left-of-next-lane.
  // Uses non-scaling stroke + preserveAspectRatio=none so the curve
  // stretches horizontally with the container without thickening.
  return (
    <div className="grid grid-cols-[72px_1fr] gap-5">
      <div />
      <div className="relative h-8 px-4">
        <svg
          aria-hidden
          className="absolute inset-0 h-full w-full text-white/15"
          viewBox="0 0 100 32"
          preserveAspectRatio="none"
          fill="none"
        >
          <path
            d="M 95 0 C 95 16, 5 16, 5 32"
            stroke="currentColor"
            strokeWidth="1"
            vectorEffect="non-scaling-stroke"
          />
        </svg>
      </div>
    </div>
  );
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
    for (const g of pipeline.groups) map.set(g.id, []);
    for (const n of pipeline.nodes) {
      const bucket = map.get(n.group);
      if (bucket) bucket.push(n);
    }
    return map;
  }, [pipeline.groups, pipeline.nodes]);

  // Global 1..N numbering follows group order, then intra-group order.
  const numbering = useMemo(() => {
    const m = new Map<string, number>();
    let i = 1;
    for (const g of pipeline.groups) {
      for (const n of nodesByGroup.get(g.id) ?? []) {
        m.set(n.id, i++);
      }
    }
    return m;
  }, [pipeline.groups, nodesByGroup]);

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
    <div className="flex flex-1 overflow-hidden bg-[#0a0a0f]">
      <div className="flex-1 overflow-y-auto px-8 py-10">
        <div className="mx-auto flex max-w-3xl flex-col">
          {pipeline.groups.map((group, gi) => {
            const groupNodes = nodesByGroup.get(group.id) ?? [];
            return (
              <div key={group.id}>
                {gi > 0 && <LaneConnector />}
                <div className="grid grid-cols-[72px_1fr] items-center gap-5">
                  <div className="pr-2 text-right font-mono">
                    <div className="text-base leading-none tracking-[0.2em] text-amber-200/75">
                      {group.id}
                    </div>
                    <div className="mt-1.5 whitespace-pre-line text-[10px] leading-tight text-white/35">
                      {group.description}
                    </div>
                  </div>
                  <div className="relative flex items-center justify-between px-2 py-7">
                    <div
                      aria-hidden
                      className="pointer-events-none absolute left-[60px] right-[60px] top-[calc(2.25rem+1px)] border-t border-white/10"
                    />
                    {groupNodes.map((node) => {
                      const path = node.field_path;
                      return (
                        <NodeCircle
                          key={node.id}
                          number={numbering.get(node.id) ?? 0}
                          label={node.label}
                          valueText={formatValue(valueAtPath(working, path))}
                          selected={selectedPath === path}
                          modified={isModified(path)}
                          active={activeFieldPaths?.has(path) ?? false}
                          adminSensitive={node.admin_sensitive}
                          onClick={() => setSelectedPath(path)}
                        />
                      );
                    })}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {selected && selectedSpec && (
        <aside className="w-80 shrink-0 overflow-y-auto border-l border-white/10 bg-slate-950/80 px-4 py-4">
          <div className="mb-3 flex items-start justify-between">
            <div>
              <h3 className="font-mono text-sm font-bold text-cyan-300">
                {numbering.get(selected.id) ?? "—"}. {selected.label}
              </h3>
              <p className="font-mono text-[10px] text-white/40">
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
            <div className="mb-3 rounded-md border border-amber-500/30 bg-amber-500/10 px-2 py-1.5 font-mono text-[10px] text-amber-200">
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
          <div className="mt-3 flex items-center justify-between font-mono text-[10px] text-white/40">
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
                  onFieldChange(
                    section,
                    field,
                    defaultForPath(schema, selected.field_path),
                  );
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
      <div className="w-full max-w-4xl max-h-[88vh] flex flex-col rounded-2xl border border-white/10 bg-[#0a0a0f]/95 shadow-2xl shadow-black/40">
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
