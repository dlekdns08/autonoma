/**
 * Live-share — public share landing page (``/share/<code>``).
 *
 * Server component. Two responsibilities:
 *
 *  1. Generate Open Graph / Twitter Card metadata for social
 *     previews — title, description, og:url, twitter:card. The
 *     backend's ``GET /api/live-share/sessions/{code}`` is the source
 *     of truth; if it 404s (private/unknown room) we still emit a
 *     valid metadata object so the route never crashes.
 *
 *  2. Render a lightweight landing card with the title, description,
 *     host, agent emojis, and a big "▶ Watch now" CTA into
 *     ``/watch/<code>``. The kiosk-style viewer lives at /watch — this
 *     page exists purely so a link unfurled into a tweet/Slack/etc.
 *     shows a useful preview AND has a sane HTML body for crawlers
 *     that follow the link.
 *
 * Per Next.js 16 conventions (see ``web/AGENTS.md`` and
 * ``node_modules/next/dist/docs/01-app/01-getting-started/03-layouts-and-pages.md``),
 * dynamic-segment ``params`` is a Promise that must be awaited before
 * indexing. The same applies inside ``generateMetadata``.
 */

import type { Metadata } from "next";
import Link from "next/link";
import { fetchSessionByCode, type LiveSession } from "@/lib/liveShare";
import { formatTimeSince } from "@/components/LiveSessionCard";

interface SharePageProps {
  params: Promise<{ code: string }>;
}

// Deliberately fetch fresh on every request — a private room going
// public should reflect within seconds, and the data is small.
export const dynamic = "force-dynamic";
export const revalidate = 0;

async function safeFetch(code: string): Promise<LiveSession | null> {
  try {
    return await fetchSessionByCode(code);
  } catch {
    // Network/backend errors degrade to "no metadata" rather than
    // crashing the route. The page body falls back to a generic CTA.
    return null;
  }
}

// Resolve the public origin once for `metadataBase`. Crawlers need
// absolute URLs for og:image / twitter:image; relative paths get
// silently dropped by some unfurlers. Prefer the operator-set
// `NEXT_PUBLIC_API_URL`, fall back to the production hostname so the
// route is still useful in environments that haven't set the env var.
function resolveMetadataBase(): URL {
  const raw = process.env.NEXT_PUBLIC_API_URL?.trim();
  if (raw) {
    try {
      return new URL(raw);
    } catch {
      // fall through to default
    }
  }
  return new URL("https://autonoma.koala.ai.kr");
}

export async function generateMetadata({
  params,
}: SharePageProps): Promise<Metadata> {
  const { code } = await params;
  const session = await safeFetch(code);

  // Fall back to generic copy when the session 404s so the route
  // still produces a metadata-bearing page (no crash on unknown code).
  const title = session?.title?.trim() || "Autonoma live show";
  const description =
    session?.description?.trim() ||
    session?.goal?.trim() ||
    "Watch a self-organizing agent swarm";
  const url = `/share/${code}`;
  // Static OG image fallback — prevents the empty social-preview
  // thumbnail when `card: "summary_large_image"` is declared without
  // an image. A future PR can swap this for a per-session dynamic OG
  // image via Next.js' `ImageResponse` route handler.
  const ogImage = {
    url: "/og-default.svg",
    width: 1200,
    height: 630,
    alt: "Autonoma",
  };

  return {
    metadataBase: resolveMetadataBase(),
    title,
    description,
    openGraph: {
      title,
      description,
      type: "website",
      siteName: "Autonoma",
      url,
      images: [ogImage],
    },
    twitter: {
      card: "summary_large_image",
      title,
      description,
      images: ["/og-default.svg"],
    },
  };
}

export default async function SharePage({ params }: SharePageProps) {
  const { code } = await params;
  const session = await safeFetch(code);

  return (
    <div className="min-h-screen bg-[#0a0a12] text-white">
      <main className="mx-auto flex max-w-2xl flex-col gap-6 px-5 py-12">
        <Link
          href="/live"
          className="font-mono text-[11px] uppercase tracking-wider text-white/50 hover:text-fuchsia-300"
        >
          ← Live shows
        </Link>

        {session ? (
          <SessionLanding session={session} code={code} />
        ) : (
          <UnknownLanding code={code} />
        )}
      </main>
    </div>
  );
}

function SessionLanding({
  session,
  code,
}: {
  session: LiveSession;
  code: string;
}) {
  const headline =
    session.title?.trim() ||
    session.goal?.trim() ||
    "Autonoma live show";
  const description = session.description?.trim() || "";
  const agents = session.agents.slice(0, 12);

  return (
    <article className="flex flex-col gap-6 rounded-3xl border border-white/10 bg-slate-950/80 p-8 shadow-2xl">
      <div className="flex items-center gap-2">
        <span className="flex items-center gap-1.5 rounded-full border border-rose-400/40 bg-rose-500/15 px-2.5 py-0.5 font-mono text-[11px] uppercase tracking-wider text-rose-200">
          <span
            aria-hidden="true"
            className="inline-block h-1.5 w-1.5 rounded-full bg-rose-400 animate-pulse"
          />
          Live
        </span>
        <span className="font-mono text-[11px] tabular-nums text-white/40">
          {formatTimeSince(session.started_at)}
        </span>
      </div>

      <header className="flex flex-col gap-2">
        <h1 className="font-mono text-2xl font-bold leading-tight text-white">
          {headline}
        </h1>
        {description ? (
          <p className="font-mono text-sm leading-relaxed text-white/70">
            {description}
          </p>
        ) : null}
      </header>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 font-mono text-xs text-white/55">
        <span>🎙 {session.host_display_name || "anon host"}</span>
        <span className="text-white/20">·</span>
        <span className="tabular-nums">round {session.round_number}</span>
        <span className="text-white/20">·</span>
        <span className="tabular-nums">👁 {session.viewer_count}</span>
        <span className="text-white/20">·</span>
        <span className="tabular-nums">🤖 {session.agent_count}</span>
      </div>

      {agents.length > 0 ? (
        <div className="flex flex-wrap gap-1.5">
          {agents.map((a) => (
            <span
              key={a.name}
              title={`${a.name} (${a.role}, ${a.mood})`}
              className="rounded-full border border-white/10 bg-white/5 px-2 py-1 font-mono text-[11px] text-white/75"
            >
              <span aria-hidden="true">{a.emoji || "🤖"}</span>{" "}
              {a.name}
            </span>
          ))}
        </div>
      ) : null}

      <Link
        href={`/watch/${code}`}
        className="self-start rounded-2xl border border-fuchsia-400/50 bg-fuchsia-500/20 px-5 py-3 font-mono text-sm font-semibold text-fuchsia-100 transition-colors hover:bg-fuchsia-500/35"
      >
        ▶ Watch now
      </Link>
    </article>
  );
}

function UnknownLanding({ code }: { code: string }) {
  return (
    <article className="flex flex-col items-start gap-4 rounded-3xl border border-white/10 bg-slate-950/80 p-8">
      <h1 className="font-mono text-xl font-bold text-white/90">
        That show isn&apos;t public right now
      </h1>
      <p className="font-mono text-sm text-white/60">
        The host of room{" "}
        <span className="rounded bg-white/10 px-1.5 py-0.5 text-white/85">
          {code}
        </span>{" "}
        either ended the stream or set the room to private.
      </p>
      <div className="flex flex-wrap gap-2">
        <Link
          href="/live"
          className="rounded-2xl border border-fuchsia-400/40 bg-fuchsia-500/15 px-4 py-2 font-mono text-xs text-fuchsia-100 hover:bg-fuchsia-500/30"
        >
          Browse live shows
        </Link>
        <Link
          href="/"
          className="rounded-2xl border border-white/15 bg-white/5 px-4 py-2 font-mono text-xs text-white/70 hover:bg-white/10"
        >
          Back to dashboard
        </Link>
      </div>
    </article>
  );
}
