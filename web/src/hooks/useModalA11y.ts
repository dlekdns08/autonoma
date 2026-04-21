"use client";

import { useEffect, useRef } from "react";

const FOCUSABLE =
  'a[href], area[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), iframe, object, embed, [tabindex]:not([tabindex="-1"]), [contenteditable="true"]';

type Options = {
  onEscape?: () => void;
  initialFocusRef?: React.RefObject<HTMLElement | null>;
};

/**
 * Modal accessibility glue: focus trap, initial focus, body-scroll lock,
 * and Escape handling — applied while the modal is mounted. Attach the
 * returned ref to the modal's dialog container (not the overlay).
 *
 * Why: every modal in the app previously re-implemented some subset of
 * this and missed the rest (e.g. Tab would leak to the background page,
 * scrolling the page while a modal was open was possible, Escape only
 * worked on one of four modals). Centralising keeps behaviour uniform.
 */
export function useModalA11y<T extends HTMLElement>({
  onEscape,
  initialFocusRef,
}: Options = {}) {
  const containerRef = useRef<T | null>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    // ── Body scroll lock (remember prior value so stacked modals restore cleanly)
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    // ── Remember previously focused element so we can restore on unmount
    const previouslyFocused =
      (document.activeElement as HTMLElement | null) ?? null;

    // ── Apply initial focus
    const focusFirst = () => {
      if (initialFocusRef?.current) {
        initialFocusRef.current.focus();
        return;
      }
      const first = container.querySelector<HTMLElement>(FOCUSABLE);
      if (first) {
        first.focus();
      } else {
        // Fallback so the container itself receives focus and Escape works
        container.tabIndex = -1;
        container.focus();
      }
    };
    // requestAnimationFrame gives React time to paint the modal contents
    const raf = requestAnimationFrame(focusFirst);

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape" && onEscape) {
        e.stopPropagation();
        onEscape();
        return;
      }
      if (e.key !== "Tab") return;
      const focusables = Array.from(
        container.querySelectorAll<HTMLElement>(FOCUSABLE),
      ).filter((el) => !el.hasAttribute("disabled") && el.offsetParent !== null);
      if (focusables.length === 0) {
        e.preventDefault();
        container.focus();
        return;
      }
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      const active = document.activeElement as HTMLElement | null;
      if (e.shiftKey) {
        if (active === first || !container.contains(active)) {
          e.preventDefault();
          last.focus();
        }
      } else {
        if (active === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };

    document.addEventListener("keydown", onKeyDown, true);

    return () => {
      cancelAnimationFrame(raf);
      document.removeEventListener("keydown", onKeyDown, true);
      document.body.style.overflow = previousOverflow;
      if (previouslyFocused && document.body.contains(previouslyFocused)) {
        previouslyFocused.focus();
      }
    };
  }, [onEscape, initialFocusRef]);

  return containerRef;
}
