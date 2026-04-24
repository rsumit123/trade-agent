"use client";
import { cn } from "@/lib/api";

export interface TabItem {
  id: string;
  label: string;
  badge?: string | number;
}

export function TabStrip({
  tabs, active, onChange, sticky = true,
}: {
  tabs: TabItem[];
  active: string;
  onChange: (id: string) => void;
  sticky?: boolean;
}) {
  return (
    <div
      className={cn(
        "mb-4 md:mb-5 -mx-4 md:mx-0 px-4 md:px-0",
        sticky && "sticky z-20",
        sticky && "md:top-0 top-[54px]" // below MobileTopBar on mobile
      )}
      style={sticky ? {
        background: "linear-gradient(to bottom, rgba(10,14,23,0.97) 0%, rgba(10,14,23,0.97) 85%, transparent 100%)",
        backdropFilter: "blur(8px)",
        WebkitBackdropFilter: "blur(8px)",
        paddingTop: 8,
        paddingBottom: 8,
      } : undefined}
    >
      <div
        className="flex items-center gap-1 p-1 rounded-xl border border-border bg-bg-card overflow-x-auto"
        style={{ scrollbarWidth: "none" }}
      >
        {tabs.map((tab) => {
          const isActive = active === tab.id;
          return (
            <button
              key={tab.id}
              onClick={() => onChange(tab.id)}
              className={cn(
                "px-3 md:px-4 rounded-lg text-xs md:text-sm font-semibold transition-all inline-flex items-center gap-1.5 whitespace-nowrap shrink-0",
                isActive
                  ? "bg-bg-secondary text-text-primary shadow-sm"
                  : "text-text-muted hover:text-text-primary"
              )}
              style={{ minHeight: 36 }}
            >
              <span>{tab.label}</span>
              {tab.badge != null && tab.badge !== "" && (
                <span
                  className={cn(
                    "font-mono text-[10px] px-1.5 py-0.5 rounded",
                    isActive ? "bg-bg-primary/60 text-text-secondary" : "bg-bg-secondary/60 text-text-muted"
                  )}
                >
                  {tab.badge}
                </span>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}
