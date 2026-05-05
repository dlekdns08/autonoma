/**
 * Smoke + drift guard for the centralized UI string table.
 *
 * The convention in `strings.ts` is that any phrase used in 2+ files
 * lives here. These tests assert the keys callers depend on actually
 * exist — so a rename (or accidental delete) breaks the build instead
 * of breaking production silently.
 */
import { describe, it, expect } from "vitest";
import { STRINGS } from "@/lib/strings";

describe("STRINGS table", () => {
  it("exposes the required common section keys", () => {
    expect(STRINGS.common.reconnecting).toBeTruthy();
    expect(STRINGS.common.close).toBeTruthy();
    expect(STRINGS.common.loading).toBeTruthy();
    expect(STRINGS.common.refresh).toBeTruthy();
    expect(STRINGS.common.refreshing).toBeTruthy();
    expect(STRINGS.common.noData).toBeTruthy();
  });

  it("exposes admin gate messages", () => {
    expect(STRINGS.admin.onlyAdmin).toBeTruthy();
    expect(STRINGS.admin.adminRequired).toBeTruthy();
  });

  it("exposes the auth error vocabulary", () => {
    expect(STRINGS.auth.invalidCredentials).toBeTruthy();
    expect(STRINGS.auth.notActivated).toBeTruthy();
    expect(STRINGS.auth.usernameTaken).toBeTruthy();
    expect(STRINGS.auth.invalidInput).toBeTruthy();
    expect(STRINGS.auth.networkError).toBeTruthy();
  });

  it("model strings table has the settings title", () => {
    expect(STRINGS.model.settingsTitle).toBeTruthy();
  });
});
