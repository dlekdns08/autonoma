/**
 * Vitest setup — runs once before each test file.
 *
 * - `@testing-library/jest-dom` adds matchers like `toBeInTheDocument`.
 * - `cleanup` after each test prevents DOM leaks between renders.
 */
import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, beforeEach } from "vitest";

// jsdom 29 + Vitest 4 surfaces ``localStorage`` but the bundled Storage
// stub omits a few methods (``clear`` in particular). Tests that
// exercise persistence rely on a real Storage shape, so install a
// minimal in-memory implementation if jsdom's is incomplete.
function ensureStorage(prop: "localStorage" | "sessionStorage") {
  const existing = (globalThis as unknown as Record<string, Storage>)[prop];
  if (existing && typeof existing.clear === "function") return;
  const map = new Map<string, string>();
  const stub: Storage = {
    get length() {
      return map.size;
    },
    clear: () => map.clear(),
    getItem: (k) => (map.has(k) ? (map.get(k) as string) : null),
    setItem: (k, v) => void map.set(k, String(v)),
    removeItem: (k) => void map.delete(k),
    key: (i) => Array.from(map.keys())[i] ?? null,
  };
  Object.defineProperty(globalThis, prop, { value: stub, writable: true });
  Object.defineProperty(window, prop, { value: stub, writable: true });
}

ensureStorage("localStorage");
ensureStorage("sessionStorage");

beforeEach(() => {
  // Snapshots can't accidentally cross-pollinate even when a test
  // forgets its own ``beforeEach`` — flush per-test.
  localStorage.clear();
  sessionStorage.clear();
});

afterEach(() => {
  cleanup();
});
