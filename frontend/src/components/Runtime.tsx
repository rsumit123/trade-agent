"use client";
import { useState } from "react";
import Link from "next/link";
import { useRuntime, formatRuntime, formatRuntimeLong } from "@/lib/useRuntime";
import { api } from "@/lib/api";

function chipColors(remaining: number, ended: boolean) {
  if (ended) return { bg: "rgba(239,68,68,0.12)", border: "#7f1d1d", fg: "#fca5a5" };
  if (remaining < 600) return { bg: "rgba(239,68,68,0.10)", border: "#7f1d1d", fg: "#fca5a5" };
  if (remaining < 3600) return { bg: "rgba(245,158,11,0.12)", border: "#78350f", fg: "#fcd34d" };
  return { bg: "rgba(34,197,94,0.10)", border: "#14532d", fg: "#86efac" };
}

/** Compact runtime chip — shows remaining free trial time. Hidden for admins. */
export function RuntimeChip({ compact = false }: { compact?: boolean }) {
  const r = useRuntime();
  if (r.loading || r.isAdmin) return null;
  const c = chipColors(r.remainingSeconds, r.trialEnded);
  const label = r.trialEnded ? "Trial ended" : `${formatRuntime(r.remainingSeconds)} free left`;
  return (
    <div
      className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-medium"
      style={{
        background: c.bg,
        border: `1px solid ${c.border}`,
        color: c.fg,
      }}
      title={
        r.trialEnded
          ? "Your free trial has ended"
          : `Free trial: ${formatRuntimeLong(r.remainingSeconds)} remaining` +
            (r.hasRunningSession ? " · counting down" : " · clock paused")
      }
    >
      <span>{r.hasRunningSession && !r.trialEnded ? "⏱" : r.trialEnded ? "🔒" : "🆓"}</span>
      {!compact && <span>{label}</span>}
      {compact && <span>{formatRuntime(r.remainingSeconds)}</span>}
    </div>
  );
}

/** Full-width banner shown on app shell when remaining < 60min. */
export function TrialBanner() {
  const r = useRuntime();
  const [open, setOpen] = useState(false);
  if (r.loading || r.isAdmin) return null;
  if (r.remainingSeconds >= 3600) return null;
  if (r.trialEnded) return null; // modal handles ended state
  const critical = r.remainingSeconds < 600;
  return (
    <>
      <div
        className="w-full px-4 py-2.5 text-xs md:text-sm flex items-center justify-between gap-3"
        style={{
          background: critical ? "rgba(239,68,68,0.12)" : "rgba(245,158,11,0.12)",
          borderBottom: `1px solid ${critical ? "#7f1d1d" : "#78350f"}`,
          color: critical ? "#fca5a5" : "#fcd34d",
        }}
      >
        <span>
          {critical ? "🚨" : "⚠️"}{" "}
          <strong>{formatRuntime(r.remainingSeconds)}</strong> left in your free
          trial. Your agent will auto-stop when the clock hits zero.
        </span>
        <button
          onClick={() => setOpen(true)}
          className="rounded-md px-3 py-1 text-xs font-semibold"
          style={{
            background: "linear-gradient(135deg, #22c55e, #60a5fa)",
            color: "#0a0e17",
            minHeight: 32,
          }}
        >
          Notify me when paid is live
        </button>
      </div>
      {open && <WaitlistModal onClose={() => setOpen(false)} source="banner" />}
    </>
  );
}

/** Full-screen modal when the trial has ended. */
export function TrialEndedModal() {
  const r = useRuntime();
  const [open, setOpen] = useState(true);
  const [waitlistOpen, setWaitlistOpen] = useState(false);
  if (r.loading || r.isAdmin || !r.trialEnded || !open) return null;
  return (
    <>
      <div
        className="fixed inset-0 z-50 flex items-center justify-center px-4"
        style={{ background: "rgba(10,14,23,0.85)", backdropFilter: "blur(4px)" }}
      >
        <div
          className="w-full max-w-md rounded-2xl p-6 md:p-8"
          style={{
            background: "linear-gradient(180deg, #151d2e 0%, #0c1424 100%)",
            border: "1px solid #1e293b",
            boxShadow: "0 30px 60px -20px rgba(0,0,0,0.7)",
          }}
        >
          <div
            className="w-12 h-12 rounded-full flex items-center justify-center mb-4"
            style={{
              background: "rgba(239,68,68,0.12)",
              border: "1px solid #7f1d1d",
            }}
          >
            <span style={{ fontSize: 24 }}>🔒</span>
          </div>
          <h2 className="text-xl md:text-2xl font-bold mb-2">Your free trial has ended</h2>
          <p className="text-sm text-text-muted mb-5">
            You used your full 24 hours of AI trading runtime. Your trade
            history, journal, and learnings are all preserved — you can browse
            them any time.
          </p>
          <div className="space-y-2 text-xs text-text-muted mb-6">
            <div className="flex items-start gap-2">
              <span style={{ color: "#22c55e" }}>✓</span>
              <span>All your past trades and charts stay viewable</span>
            </div>
            <div className="flex items-start gap-2">
              <span style={{ color: "#f59e0b" }}>🔒</span>
              <span>Starting agents, new sessions, and backtests are locked</span>
            </div>
          </div>
          <button
            onClick={() => setWaitlistOpen(true)}
            className="w-full rounded-lg py-3 text-sm font-semibold mb-2"
            style={{
              background: "linear-gradient(135deg, #22c55e, #60a5fa)",
              color: "#0a0e17",
              minHeight: 44,
            }}
          >
            Join the upgrade waitlist
          </button>
          <button
            onClick={() => setOpen(false)}
            className="w-full rounded-lg py-3 text-sm text-text-muted"
            style={{
              background: "transparent",
              border: "1px solid #1e293b",
              minHeight: 44,
            }}
          >
            Browse my history
          </button>
        </div>
      </div>
      {waitlistOpen && (
        <WaitlistModal
          onClose={() => {
            setWaitlistOpen(false);
            setOpen(false);
          }}
          source="trial_ended"
        />
      )}
    </>
  );
}

/** Persistent footer banner: "Trial ended · Upgrade — coming soon." */
export function TrialEndedFooterStrip() {
  const r = useRuntime();
  const [open, setOpen] = useState(false);
  if (r.loading || r.isAdmin || !r.trialEnded) return null;
  return (
    <>
      <div
        className="w-full px-4 py-2 text-[11px] md:text-xs flex items-center justify-between gap-3"
        style={{
          background: "rgba(239,68,68,0.10)",
          borderBottom: "1px solid #7f1d1d",
          color: "#fca5a5",
        }}
      >
        <span>🔒 Free trial ended — agents locked. Upgrade for more runtime (coming soon).</span>
        <button
          onClick={() => setOpen(true)}
          className="rounded-md px-2.5 py-1 text-[11px] font-semibold"
          style={{
            background: "rgba(239,68,68,0.18)",
            border: "1px solid #7f1d1d",
            color: "#fca5a5",
          }}
        >
          Join waitlist
        </button>
      </div>
      {open && <WaitlistModal onClose={() => setOpen(false)} source="footer" />}
    </>
  );
}

/** Locked CTA — looks like a button but opens the waitlist modal. */
export function UpgradeLockButton({
  label = "Upgrade",
  className = "",
  fullWidth = false,
}: {
  label?: string;
  className?: string;
  fullWidth?: boolean;
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        onClick={() => setOpen(true)}
        className={`inline-flex items-center justify-center gap-2 rounded-lg px-4 py-2 text-sm font-medium ${className}`}
        style={{
          background: "rgba(245,158,11,0.10)",
          border: "1px solid #78350f",
          color: "#fcd34d",
          minHeight: 44,
          width: fullWidth ? "100%" : undefined,
        }}
      >
        <span>🔒</span>
        <span>{label} — coming soon</span>
      </button>
      {open && <WaitlistModal onClose={() => setOpen(false)} source="upgrade_button" />}
    </>
  );
}

function WaitlistModal({
  onClose,
  source,
}: {
  onClose: () => void;
  source: string;
}) {
  const [email, setEmail] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [done, setDone] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!email.includes("@")) {
      setErr("Please enter a valid email");
      return;
    }
    setSubmitting(true);
    setErr(null);
    try {
      await api("/api/waitlist", {
        method: "POST",
        body: JSON.stringify({ email, source }),
      });
      setDone(true);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Failed to join waitlist");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center px-4"
      style={{ background: "rgba(10,14,23,0.85)", backdropFilter: "blur(4px)" }}
      onClick={onClose}
    >
      <div
        className="w-full max-w-sm rounded-2xl p-6"
        style={{
          background: "linear-gradient(180deg, #151d2e 0%, #0c1424 100%)",
          border: "1px solid #1e293b",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        {!done ? (
          <>
            <h3 className="text-lg font-bold mb-1">Join the upgrade waitlist</h3>
            <p className="text-xs text-text-muted mb-4">
              We&rsquo;ll email you the moment paid plans are live.
            </p>
            <form onSubmit={submit} className="space-y-3">
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@example.com"
                className="w-full rounded-lg px-3 text-sm outline-none"
                style={{
                  background: "#0a0e17",
                  border: "1px solid #1e293b",
                  color: "#e2e8f0",
                  minHeight: 44,
                  fontSize: 16,
                }}
                autoFocus
              />
              {err && <div className="text-xs text-accent-red">{err}</div>}
              <button
                type="submit"
                disabled={submitting}
                className="w-full rounded-lg py-3 text-sm font-semibold"
                style={{
                  background: "linear-gradient(135deg, #22c55e, #60a5fa)",
                  color: "#0a0e17",
                  minHeight: 44,
                  opacity: submitting ? 0.6 : 1,
                }}
              >
                {submitting ? "Joining..." : "Notify me"}
              </button>
              <button
                type="button"
                onClick={onClose}
                className="w-full text-xs text-text-muted"
                style={{ minHeight: 36 }}
              >
                Maybe later
              </button>
            </form>
          </>
        ) : (
          <>
            <div
              className="w-12 h-12 rounded-full flex items-center justify-center mb-3"
              style={{
                background: "rgba(34,197,94,0.12)",
                border: "1px solid #14532d",
              }}
            >
              <span style={{ fontSize: 22 }}>✓</span>
            </div>
            <h3 className="text-lg font-bold mb-1">You&rsquo;re on the list</h3>
            <p className="text-xs text-text-muted mb-4">
              We&rsquo;ll reach out at <strong>{email}</strong>.
            </p>
            <button
              onClick={onClose}
              className="w-full rounded-lg py-3 text-sm"
              style={{
                background: "transparent",
                border: "1px solid #1e293b",
                color: "#e2e8f0",
                minHeight: 44,
              }}
            >
              Close
            </button>
          </>
        )}
      </div>
    </div>
  );
}

// Suppress unused Link import warning — keeping for future use
void Link;
