"use client";
import { useState } from "react";
import { cn } from "@/lib/api";

export function Collapsible({
  title, subtitle, icon, children, defaultOpen = false, badge,
}: {
  title: string;
  subtitle?: string;
  icon?: string;
  children: React.ReactNode;
  defaultOpen?: boolean;
  badge?: string | number;
}) {
  const [open, setOpen] = useState(defaultOpen);

  return (
    <div className="rounded-2xl border border-border bg-bg-card overflow-hidden">
      <button
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-bg-card-hover transition-colors"
        style={{ minHeight: 52 }}
      >
        {icon && (
          <span
            className="flex items-center justify-center rounded-lg shrink-0"
            style={{ width: 32, height: 32, background: "rgba(59,130,246,0.12)", color: "#3b82f6", fontSize: 16 }}
          >
            {icon}
          </span>
        )}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-semibold text-text-primary truncate">{title}</span>
            {badge != null && (
              <span className="font-mono text-[10px] px-1.5 py-0.5 rounded bg-accent-blue/15 text-accent-blue">
                {badge}
              </span>
            )}
          </div>
          {subtitle && (
            <div className="text-xs text-text-muted mt-0.5 truncate">{subtitle}</div>
          )}
        </div>
        <span
          className={cn("text-text-muted transition-transform duration-200 shrink-0", open && "rotate-180")}
          style={{ fontSize: 14 }}
        >
          ▾
        </span>
      </button>
      {open && (
        <div className="border-t border-border/60">
          {children}
        </div>
      )}
    </div>
  );
}
