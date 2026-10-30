"use client";

import { usePathname } from "next/navigation";
import BGMToggle from "@/components/BGMToggle";
import ThemeToggle from "@/components/ThemeToggle";

/** Routes that are composited into a broadcast (OBS browser source).
 *  Anything fixed-positioned here would be captured on stream. */
const BROADCAST_ROUTES = ["/obs"];

/**
 * Site-wide floating controls (theme switch, BGM mute). Hidden on
 * broadcast routes: on ``/obs`` they were burned into the stream and the
 * BGM button sat on top of the OBS record button.
 */
export default function GlobalChrome() {
  const pathname = usePathname() ?? "";
  if (BROADCAST_ROUTES.some((r) => pathname === r || pathname.startsWith(`${r}/`))) {
    return null;
  }
  return (
    <>
      <ThemeToggle />
      {/* Site-wide procedural BGM. Defaults muted; the user opts in
          (browser autoplay policy demands a gesture anyway — see
          BGMToggle.tsx) and self-persists settings to localStorage. */}
      <BGMToggle mood="calm" />
    </>
  );
}
