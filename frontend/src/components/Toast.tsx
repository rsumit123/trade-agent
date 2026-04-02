"use client";
import { createContext, useContext, useState, useCallback, useRef, useEffect } from "react";
import { cn } from "@/lib/api";

type ToastType = "success" | "error" | "info";

interface ToastItem {
  id: number;
  message: string;
  type: ToastType;
  dismissing: boolean;
}

interface ToastContextValue {
  toast: {
    success: (msg: string) => void;
    error: (msg: string) => void;
    info: (msg: string) => void;
  };
}

const ToastContext = createContext<ToastContextValue | null>(null);

export function useToast(): ToastContextValue["toast"] {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used within ToastProvider");
  return ctx.toast;
}

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const idRef = useRef(0);

  const dismiss = useCallback((id: number) => {
    setToasts((prev) => prev.map((t) => (t.id === id ? { ...t, dismissing: true } : t)));
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, 300);
  }, []);

  const addToast = useCallback(
    (message: string, type: ToastType) => {
      const id = ++idRef.current;
      setToasts((prev) => {
        const next = [...prev, { id, message, type, dismissing: false }];
        // Keep max 3 toasts visible
        if (next.length > 3) {
          return next.slice(next.length - 3);
        }
        return next;
      });
      // Auto-dismiss after 3 seconds
      setTimeout(() => dismiss(id), 3000);
    },
    [dismiss]
  );

  const toast = useRef({
    success: (msg: string) => addToast(msg, "success"),
    error: (msg: string) => addToast(msg, "error"),
    info: (msg: string) => addToast(msg, "info"),
  });

  // Update refs when addToast changes
  useEffect(() => {
    toast.current = {
      success: (msg: string) => addToast(msg, "success"),
      error: (msg: string) => addToast(msg, "error"),
      info: (msg: string) => addToast(msg, "info"),
    };
  }, [addToast]);

  return (
    <ToastContext.Provider value={{ toast: toast.current }}>
      {children}
      {/* Toast container */}
      <div className="fixed bottom-4 left-1/2 -translate-x-1/2 z-[10000] flex flex-col gap-2 items-center pointer-events-none">
        {toasts.map((t) => (
          <ToastMessage key={t.id} item={t} onDismiss={() => dismiss(t.id)} />
        ))}
      </div>
    </ToastContext.Provider>
  );
}

const borderColors: Record<ToastType, string> = {
  success: "border-l-accent-green",
  error: "border-l-accent-red",
  info: "border-l-accent-blue",
};

const iconMap: Record<ToastType, string> = {
  success: "\u2713",
  error: "\u2717",
  info: "\u2139",
};

const iconColors: Record<ToastType, string> = {
  success: "text-accent-green",
  error: "text-accent-red",
  info: "text-accent-blue",
};

function ToastMessage({ item, onDismiss }: { item: ToastItem; onDismiss: () => void }) {
  return (
    <div
      className={cn(
        "pointer-events-auto bg-bg-card border border-border rounded-lg px-4 py-3 shadow-lg shadow-black/30 flex items-center gap-3 min-w-[280px] max-w-[420px] border-l-4",
        borderColors[item.type],
        item.dismissing ? "toast-out" : "toast-in"
      )}
      style={{
        animation: item.dismissing
          ? "toast-out 0.3s ease forwards"
          : "toast-in 0.3s ease forwards",
      }}
    >
      <span className={cn("text-sm font-bold", iconColors[item.type])}>{iconMap[item.type]}</span>
      <span className="text-sm text-text-primary flex-1">{item.message}</span>
      <button
        onClick={onDismiss}
        className="text-text-muted hover:text-text-primary text-xs ml-2 transition-colors"
      >
        &times;
      </button>
    </div>
  );
}
