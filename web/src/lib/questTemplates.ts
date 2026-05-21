/**
 * Quest templates — thin client for the per-user template bank.
 *
 *   GET    /api/quest-templates           -> { templates: [...] } | [...]
 *   POST   /api/quest-templates  {text}   -> { template: {...} }
 *   DELETE /api/quest-templates/{id}      -> { template_id }
 *
 * The shape parsing is intentionally defensive: the GET handler may
 * return either ``{templates: [...]}`` or a raw array depending on
 * future router refactors, so every list-returning call funnels through
 * a single normalizer that always produces an ``Array.isArray``-safe
 * value.
 *
 * Auth failures (401/403) propagate as a thrown ``QuestTemplateApiError``
 * so the UI hook can map them to "empty list" without crashing the
 * panel that called us.
 */

import { API_BASE_URL } from "@/hooks/useSwarm";

export interface QuestTemplate {
  id: number;
  user_id: string;
  text: string;
  created_at: string | null;
}

export class QuestTemplateApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
    this.name = "QuestTemplateApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    credentials: "include",
    headers: init?.body
      ? { "Content-Type": "application/json", ...(init?.headers ?? {}) }
      : init?.headers,
    ...init,
  });
  if (!res.ok) {
    let detail: unknown = null;
    try {
      detail = await res.json();
    } catch {
      // Body wasn't JSON — fall back to status code below.
    }
    let message = `HTTP ${res.status}`;
    if (
      detail &&
      typeof detail === "object" &&
      "detail" in detail &&
      typeof (detail as { detail?: unknown }).detail === "string"
    ) {
      message = (detail as { detail: string }).detail;
    }
    throw new QuestTemplateApiError(message, res.status);
  }
  if (res.status === 204) return undefined as unknown as T;
  return (await res.json()) as T;
}

/**
 * Normalize the GET response. Accepts:
 *   - ``{templates: [...]}``  (current contract)
 *   - ``[...]``               (defensive, in case the router is simplified later)
 *   - anything else → empty array (don't crash the panel on a stray 200 + html)
 */
function coerceTemplates(body: unknown): QuestTemplate[] {
  if (Array.isArray(body)) {
    return body.filter(isTemplateLike);
  }
  if (
    body &&
    typeof body === "object" &&
    "templates" in body &&
    Array.isArray((body as { templates?: unknown }).templates)
  ) {
    return (body as { templates: unknown[] }).templates.filter(isTemplateLike);
  }
  return [];
}

function isTemplateLike(value: unknown): value is QuestTemplate {
  if (!value || typeof value !== "object") return false;
  const v = value as Record<string, unknown>;
  return (
    typeof v.id === "number" &&
    typeof v.text === "string" &&
    typeof v.user_id === "string"
  );
}

export async function listTemplates(): Promise<QuestTemplate[]> {
  const body = await request<unknown>(`/api/quest-templates`);
  return coerceTemplates(body);
}

export async function saveTemplate(text: string): Promise<QuestTemplate | null> {
  const body = await request<unknown>(`/api/quest-templates`, {
    method: "POST",
    body: JSON.stringify({ text }),
  });
  // The backend returns ``{template: {...}}``; tolerate either shape so a
  // future inline-row response doesn't break callers.
  if (Array.isArray(body)) {
    const first = body.find(isTemplateLike);
    return first ?? null;
  }
  if (body && typeof body === "object") {
    const obj = body as Record<string, unknown>;
    if (isTemplateLike(obj.template)) return obj.template;
    if (isTemplateLike(obj)) return obj;
  }
  return null;
}

export async function deleteTemplate(id: number): Promise<void> {
  await request<void>(`/api/quest-templates/${id}`, { method: "DELETE" });
}
