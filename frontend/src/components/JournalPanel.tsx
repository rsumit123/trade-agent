"use client";

export function JournalPanel({ content }: { content: string }) {
  return (
    <div className="bg-bg-card border border-border rounded-xl overflow-hidden">
      <div className="px-4 py-3 border-b border-border">
        <h3 className="text-xs uppercase tracking-wider font-semibold text-text-secondary">Learning Journal</h3>
      </div>
      <div className="p-4 max-h-[350px] overflow-y-auto text-sm text-text-secondary leading-relaxed whitespace-pre-wrap">
        {content || "No journal entries yet."}
      </div>
    </div>
  );
}
