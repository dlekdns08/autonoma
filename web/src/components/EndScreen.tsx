"use client";

import { useState } from "react";
import { API_BASE_URL } from "@/hooks/useSwarm";
import type { FileEntry } from "@/lib/types";

interface Props {
  finalAnswer: string;
  epilogue: string;
  leaderboard: string;
  multiverse: string;
  graveyard: string;
  files: FileEntry[];
  projectName: string;
}

type Tab = "final" | "epilogue" | "leaderboard" | "multiverse" | "graveyard";

export default function EndScreen({
  finalAnswer,
  epilogue,
  leaderboard,
  multiverse,
  graveyard,
  files,
  projectName,
}: Props) {
  const [tab, setTab] = useState<Tab>("final");

  const allTabs: { id: Tab; label: string; icon: string; content: string }[] = [
    { id: "final", label: "최종 답변", icon: "★", content: finalAnswer },
    { id: "epilogue", label: "이야기", icon: "♪", content: epilogue },
    { id: "leaderboard", label: "랭킹", icon: "👑", content: leaderboard },
    { id: "multiverse", label: "What If", icon: "🌀", content: multiverse },
    { id: "graveyard", label: "Graveyard", icon: "👻", content: graveyard },
  ];
  const tabs = allTabs.filter(
    (t) => t.content && t.content.trim().length > 0 && !t.content.includes("No "),
  );

  const activeContent = tabs.find((t) => t.id === tab)?.content || finalAnswer || epilogue;
  const zipUrl = `${API_BASE_URL}/api/files/zip`;

  return (
    <div className="flex flex-col items-center justify-start h-full gap-5 p-6 overflow-y-auto scrollbar-thin">
      {/* Celebration Header */}
      <div className="text-center animate-fade-in">
        <h1 className="text-3xl font-bold text-transparent bg-clip-text bg-gradient-to-r from-fuchsia-400 via-cyan-400 to-yellow-400 font-mono">
          ~*~ 프로젝트 완료! ~*~
        </h1>
        <p className="mt-2 text-white/50 text-sm">
          {projectName ? `'${projectName}' — ` : ""}모험이 막을 내렸습니다.
        </p>
      </div>

      {/* Tab Selector */}
      {tabs.length > 1 && (
        <div className="flex flex-wrap justify-center gap-2">
          {tabs.map((t) => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={`rounded-lg px-4 py-2 text-xs font-mono transition-all ${
                tab === t.id
                  ? "bg-fuchsia-500/30 text-fuchsia-300 border border-fuchsia-500/50"
                  : "bg-white/5 text-white/40 hover:bg-white/10 border border-transparent"
              }`}
            >
              {t.icon} {t.label}
            </button>
          ))}
        </div>
      )}

      {/* Content */}
      <div className="max-w-3xl w-full rounded-xl border border-fuchsia-500/20 bg-slate-900/80 p-6 backdrop-blur-sm">
        {tab === "final" ? (
          <MarkdownLite text={activeContent} />
        ) : (
          <pre className="whitespace-pre-wrap text-xs font-mono text-white/70 leading-relaxed">
            {activeContent}
          </pre>
        )}
      </div>

      {/* Generated Files */}
      {files.length > 0 && (
        <div className="max-w-3xl w-full rounded-xl border border-cyan-500/20 bg-slate-900/60 p-5 backdrop-blur-sm">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-bold text-cyan-300 font-mono">
              ♪ 생성된 결과물 ({files.length})
            </h2>
            <a
              href={zipUrl}
              className="rounded border border-cyan-500/40 bg-cyan-500/15 px-3 py-1 text-xs font-mono text-cyan-200 transition-colors hover:bg-cyan-500/25"
            >
              ⬇ 전체 .zip 다운로드
            </a>
          </div>
          <div className="flex flex-col gap-1 max-h-64 overflow-y-auto scrollbar-thin">
            {files.map((f) => (
              <div
                key={f.path}
                className="flex items-center gap-2 rounded px-2 py-1 text-xs hover:bg-white/5 group"
              >
                <span className="text-cyan-400">♪</span>
                <span
                  className="flex-1 text-white/70 font-mono truncate"
                  title={f.description || f.path}
                >
                  {f.path}
                </span>
                {f.description && (
                  <span className="hidden md:inline text-[10px] text-white/30 truncate max-w-[30%]">
                    {f.description}
                  </span>
                )}
                <a
                  href={`${API_BASE_URL}/api/files/download?path=${encodeURIComponent(f.path)}`}
                  download
                  className="text-white/40 hover:text-cyan-300 text-xs"
                  title={`Download ${f.path}`}
                >
                  ⬇
                </a>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// Minimal markdown renderer for the final answer: headings, bold, lists.
// We intentionally keep this tiny — pulling react-markdown would be overkill
// for what the Director produces.
function MarkdownLite({ text }: { text: string }) {
  if (!text) {
    return (
      <p className="text-sm text-white/40 font-mono italic">
        (최종 답변이 아직 생성되지 않았습니다)
      </p>
    );
  }

  const lines = text.split("\n");
  const elements: React.ReactNode[] = [];
  let listBuf: string[] = [];

  const flushList = () => {
    if (listBuf.length === 0) return;
    elements.push(
      <ul
        key={`ul-${elements.length}`}
        className="ml-4 my-2 list-disc text-sm text-white/80 space-y-1"
      >
        {listBuf.map((item, i) => (
          <li key={i}>{renderInline(item)}</li>
        ))}
      </ul>,
    );
    listBuf = [];
  };

  lines.forEach((raw, i) => {
    const line = raw.trimEnd();
    if (line.startsWith("### ")) {
      flushList();
      elements.push(
        <h3 key={i} className="mt-3 text-sm font-bold text-cyan-300 font-mono">
          {line.slice(4)}
        </h3>,
      );
    } else if (line.startsWith("## ")) {
      flushList();
      elements.push(
        <h2 key={i} className="mt-4 text-base font-bold text-fuchsia-300 font-mono">
          {line.slice(3)}
        </h2>,
      );
    } else if (line.startsWith("# ")) {
      flushList();
      elements.push(
        <h1 key={i} className="mt-4 text-lg font-bold text-yellow-300 font-mono">
          {line.slice(2)}
        </h1>,
      );
    } else if (/^\s*[-*]\s+/.test(line)) {
      listBuf.push(line.replace(/^\s*[-*]\s+/, ""));
    } else if (line.trim() === "") {
      flushList();
      elements.push(<div key={i} className="h-2" />);
    } else {
      flushList();
      elements.push(
        <p key={i} className="text-sm text-white/80 leading-relaxed">
          {renderInline(line)}
        </p>,
      );
    }
  });
  flushList();

  return <div className="font-sans">{elements}</div>;
}

function renderInline(text: string): React.ReactNode {
  // bold: **text**
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g);
  return parts.map((p, i) => {
    if (p.startsWith("**") && p.endsWith("**")) {
      return (
        <strong key={i} className="font-bold text-white">
          {p.slice(2, -2)}
        </strong>
      );
    }
    if (p.startsWith("`") && p.endsWith("`")) {
      return (
        <code
          key={i}
          className="rounded bg-white/10 px-1 py-0.5 text-[12px] font-mono text-cyan-200"
        >
          {p.slice(1, -1)}
        </code>
      );
    }
    return <span key={i}>{p}</span>;
  });
}
