/**
 * ThemeToggle integration test.
 *
 * Renders the real component with the real ``useTheme`` hook so we
 * cover the full path:
 *   - initial render reflects localStorage (or the default)
 *   - clicking flips the theme
 *   - the ``data-theme`` attribute on <html> stays in sync (this is
 *     what every CSS rule keys off, so it is the contract that matters)
 */
import { describe, it, expect, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import ThemeToggle from "@/components/ThemeToggle";

beforeEach(() => {
  localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
  document.body.removeAttribute("data-theme");
});

describe("<ThemeToggle />", () => {
  it("starts in dark mode by default and shows the sun glyph", () => {
    render(<ThemeToggle />);
    const button = screen.getByRole("button", {
      name: /switch to light theme/i,
    });
    expect(button).toBeInTheDocument();
    expect(button.textContent).toContain("☀️");
  });

  it("flips to light mode on click and updates aria-label + glyph", async () => {
    const user = userEvent.setup();
    render(<ThemeToggle />);
    const button = screen.getByRole("button", {
      name: /switch to light theme/i,
    });

    await user.click(button);

    // Now the button advertises the inverse switch.
    expect(
      screen.getByRole("button", { name: /switch to dark theme/i }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button").textContent).toContain("🌙");
  });

  it("persists the chosen theme to localStorage", async () => {
    const user = userEvent.setup();
    render(<ThemeToggle />);
    await user.click(screen.getByRole("button"));
    expect(localStorage.getItem("autonoma_theme")).toBe("light");

    await user.click(screen.getByRole("button"));
    expect(localStorage.getItem("autonoma_theme")).toBe("dark");
  });

  it("syncs document data-theme attribute on toggle", async () => {
    const user = userEvent.setup();
    render(<ThemeToggle />);
    await user.click(screen.getByRole("button"));
    // Both <html> and <body> must agree — CSS may key off either.
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
    expect(document.body.getAttribute("data-theme")).toBe("light");
  });
});
