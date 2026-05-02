"use client";
import { Suspense, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import {
  GOOGLE_CLIENT_ID,
  loadGoogleScript,
  signInWithCredential,
  useUser,
} from "@/lib/auth";

export default function LoginPage() {
  return (
    <Suspense fallback={<div className="min-h-screen" style={{ background: "#0a0e17" }} />}>
      <LoginInner />
    </Suspense>
  );
}

function LoginInner() {
  const router = useRouter();
  const params = useSearchParams();
  const { user, loading } = useUser();
  const [error, setError] = useState<string | null>(null);
  const [signing, setSigning] = useState(false);
  const buttonRef = useRef<HTMLDivElement>(null);
  const next = params.get("next") || "";

  // Already signed in → go to app/admin
  useEffect(() => {
    if (loading) return;
    if (user) {
      const dest = next && next.startsWith("/") ? next : (user.is_admin ? "/admin" : "/app");
      router.push(dest);
    }
  }, [user, loading, router, next]);

  // Mount Google Sign-In button
  useEffect(() => {
    if (loading || user) return;
    let cancelled = false;
    loadGoogleScript()
      .then(() => {
        if (cancelled) return;
        const g = window.google?.accounts?.id;
        if (!g || !buttonRef.current) return;
        g.initialize({
          client_id: GOOGLE_CLIENT_ID,
          callback: async (response) => {
            setSigning(true);
            setError(null);
            try {
              const u = await signInWithCredential(response.credential);
              const dest = next && next.startsWith("/") ? next : (u.is_admin ? "/admin" : "/app");
              router.push(dest);
            } catch (e) {
              setError(e instanceof Error ? e.message : "Sign-in failed");
              setSigning(false);
            }
          },
          auto_select: false,
          cancel_on_tap_outside: true,
        });
        buttonRef.current.innerHTML = "";
        g.renderButton(buttonRef.current, {
          type: "standard",
          theme: "filled_black",
          size: "large",
          text: "continue_with",
          shape: "rectangular",
          logo_alignment: "left",
          width: 320,
        });
      })
      .catch(() => setError("Failed to load Google Sign-In"));
    return () => {
      cancelled = true;
    };
  }, [loading, user, router, next]);

  return (
    <div
      className="min-h-screen flex flex-col"
      style={{
        background:
          "radial-gradient(ellipse 80% 60% at 50% 0%, rgba(34,197,94,0.10) 0%, transparent 60%), #0a0e17",
      }}
    >
      <header className="px-4 md:px-6 py-4">
        <Link href="/" className="inline-flex items-center gap-2">
          <div
            className="flex items-center justify-center rounded-lg"
            style={{
              width: 28,
              height: 28,
              background: "linear-gradient(135deg, #22c55e, #60a5fa)",
            }}
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#0a0e17" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="22 7 13.5 15.5 8.5 10.5 2 17" />
            </svg>
          </div>
          <span className="font-bold tracking-tight">AlphaAgent</span>
        </Link>
      </header>

      <main className="flex-1 flex items-center justify-center px-4 py-8">
        <div
          className="w-full max-w-md rounded-2xl p-6 md:p-8"
          style={{
            background: "linear-gradient(180deg, #151d2e 0%, #0c1424 100%)",
            border: "1px solid #1e293b",
            boxShadow: "0 24px 48px -24px rgba(0,0,0,0.6)",
          }}
        >
          <h1 className="text-2xl font-bold tracking-tight mb-1.5">Sign in</h1>
          <p className="text-sm text-text-muted mb-6">
            Continue with your Google account to manage your trading sessions.
          </p>

          {/* Google sign-in button mount point */}
          <div className="flex flex-col items-center gap-3">
            {signing ? (
              <div
                className="w-full rounded-lg py-4 text-center text-sm text-text-muted"
                style={{ background: "#0a0e17", border: "1px solid #1e293b" }}
              >
                Signing in...
              </div>
            ) : (
              <div ref={buttonRef} style={{ minHeight: 44 }} />
            )}
            {error && (
              <div className="w-full text-xs text-accent-red text-center mt-1">{error}</div>
            )}
          </div>

          <div className="mt-8 pt-5 border-t border-border/50 space-y-2 text-xs text-text-muted">
            <div className="flex items-start gap-2">
              <span style={{ color: "#22c55e" }}>✓</span>
              <span>Free to sign up. We never see your trades or LLM keys.</span>
            </div>
            <div className="flex items-start gap-2">
              <span style={{ color: "#22c55e" }}>✓</span>
              <span>Bring your own OpenRouter key — encrypted at rest.</span>
            </div>
            <div className="flex items-start gap-2">
              <span style={{ color: "#22c55e" }}>✓</span>
              <span>Paper-trading only. No real money on the line.</span>
            </div>
          </div>
        </div>
      </main>

      <footer className="px-4 md:px-6 py-4 text-center text-[11px] text-text-muted">
        <Link href="/" className="hover:text-text-secondary">← Back to home</Link>
      </footer>
    </div>
  );
}
