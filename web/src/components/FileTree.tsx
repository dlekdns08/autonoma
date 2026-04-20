"use client";

import type { FileEntry } from "@/lib/types";
import { API_BASE_URL } from "@/hooks/useSwarm";

interface Props {
  files: FileEntry[];
  sessionId: number | null;
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

export default function FileTree({ files, sessionId }: Props) {
  const sessionQuery = sessionId !== null ? `&session=${sessionId}` : "";
  const zipSessionQuery = sessionId !== null ? `?session=${sessionId}` : "";
  const fileDownloadUrl = (path: string) =>
    `${API_BASE_URL}/api/files/download?path=${encodeURIComponent(path)}${sessionQuery}`;
  const zipUrl = `${API_BASE_URL}/api/files/zip${zipSessionQuery}`;

  return (
    <div className="flex flex-col gap-2 rounded-xl border border-fuchsia-500/20 bg-slate-900/50 p-3">
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-bold text-fuchsia-300 font-mono">♪ Files ♪</h3>
        {files.length > 0 && (
          <a
            href={zipUrl}
            className="rounded border border-fuchsia-500/40 bg-fuchsia-500/15 px-2 py-0.5 text-[10px] font-mono text-fuchsia-200 transition-colors hover:bg-fuchsia-500/25"
            title="Download all files as .zip"
          >
            ⬇ .zip
          </a>
        )}
      </div>

      {files.length === 0 ? (
        <p className="text-xs text-white/30 font-mono">(-_-) No files yet...</p>
      ) : (
        <div className="flex flex-col gap-0.5 max-h-48 overflow-y-auto scrollbar-thin">
          {files.map((file) => (
            <div
              key={file.path}
              className="flex items-center gap-2 rounded px-1 py-0.5 text-xs hover:bg-white/5 group"
            >
              <span className="text-cyan-400">♪</span>
              <span
                className="flex-1 text-white/60 font-mono truncate"
                title={file.description || file.path}
              >
                {file.path}
              </span>
              <span className="text-[9px] text-white/25 font-mono tabular-nums">
                {formatBytes(file.size)}
              </span>
              <a
                href={fileDownloadUrl(file.path)}
                download
                className="text-white/30 hover:text-fuchsia-300 text-[11px] opacity-0 group-hover:opacity-100 transition-opacity"
                title={`Download ${file.path}`}
                onClick={(e) => e.stopPropagation()}
              >
                ⬇
              </a>
            </div>
          ))}
        </div>
      )}

      <div className="mt-1 text-[10px] text-white/30 font-mono">
        {files.length} file{files.length !== 1 ? "s" : ""} created
      </div>
    </div>
  );
}
