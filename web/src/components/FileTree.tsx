"use client";

interface Props {
  files: string[];
}

export default function FileTree({ files }: Props) {
  return (
    <div className="flex flex-col gap-2 rounded-xl border border-fuchsia-500/20 bg-slate-900/50 p-3">
      <h3 className="text-xs font-bold text-fuchsia-300 font-mono">♪ Files ♪</h3>

      {files.length === 0 ? (
        <p className="text-xs text-white/30 font-mono">(-_-) No files yet...</p>
      ) : (
        <div className="flex flex-col gap-0.5 max-h-36 overflow-y-auto">
          {files.map((file) => (
            <div key={file} className="flex items-center gap-2 text-xs">
              <span className="text-cyan-400">♪</span>
              <span className="text-white/60 font-mono truncate">{file}</span>
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
