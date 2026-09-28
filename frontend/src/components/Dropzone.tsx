import { DragEvent, useRef, useState } from "react";

export function Dropzone({ label, hint, onFile, busy, compact }: {
  label: string; hint?: string; onFile: (f: File) => void; busy?: boolean; compact?: boolean;
}) {
  const [over, setOver] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const drop = (e: DragEvent) => {
    e.preventDefault(); setOver(false);
    const f = e.dataTransfer.files?.[0];
    if (f) onFile(f);
  };
  return (
    <div role="button" tabIndex={0} aria-label={label}
         onClick={() => !busy && input.current?.click()} onKeyDown={(e) => e.key === "Enter" && input.current?.click()}
         onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)} onDrop={drop}
         className={`rounded-lg text-center cursor-pointer transition-colors ${compact ? "p-3" : "p-6"}`}
         style={{ border: `2px dashed ${over ? "var(--accent)" : "var(--axis)"}`,
                  background: over ? "color-mix(in srgb, var(--accent) 8%, transparent)" : "var(--surface-2)" }}>
      <input ref={input} type="file" accept=".csv,text/csv" className="hidden"
             onChange={(e) => { const f = e.target.files?.[0]; if (f) onFile(f); e.target.value = ""; }} />
      <div className={`font-medium ${compact ? "text-xs" : "text-sm"}`}>{busy ? "Reading file…" : label}</div>
      {hint && !busy && <div className="text-[11px] muted mt-1">{hint}</div>}
    </div>
  );
}
