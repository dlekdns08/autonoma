"use client";

import { useCallback, useEffect, useState } from "react";

export interface KeyNavOptions {
  onToggleTasks?: () => void;
  onToggleFiles?: () => void;
  onToggleChat?: () => void;
  onPauseResume?: () => void;
  isRunning?: boolean;
}

const SHORTCUTS = [
  { key: "T", description: "Toggle Tasks panel" },
  { key: "F", description: "Toggle Files panel" },
  { key: "C", description: "Toggle Chat panel" },
  { key: "Space", description: "Pause / resume swarm" },
  { key: "?", description: "Show this help" },
  { key: "Escape", description: "Close modal / panel" },
] as const;

export { SHORTCUTS };

export function useKeyNav(options: KeyNavOptions = {}) {
  const { onToggleTasks, onToggleFiles, onToggleChat, onPauseResume, isRunning } = options;
  const [showHelp, setShowHelp] = useState(false);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      // Don't capture shortcuts when user is typing in an input/textarea/select
      const target = e.target as HTMLElement;
      const tag = target.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || target.isContentEditable) {
        return;
      }

      switch (e.key) {
        case "t":
        case "T":
          e.preventDefault();
          onToggleTasks?.();
          break;
        case "f":
        case "F":
          e.preventDefault();
          onToggleFiles?.();
          break;
        case "c":
        case "C":
          e.preventDefault();
          onToggleChat?.();
          break;
        case " ":
          if (isRunning) {
            e.preventDefault();
            onPauseResume?.();
          }
          break;
        case "?":
          e.preventDefault();
          setShowHelp((v) => !v);
          break;
        case "Escape":
          setShowHelp(false);
          break;
      }
    },
    [onToggleTasks, onToggleFiles, onToggleChat, onPauseResume, isRunning],
  );

  useEffect(() => {
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [handleKeyDown]);

  return { showHelp, setShowHelp };
}
