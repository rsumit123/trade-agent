"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useUser } from "@/lib/auth";

export default function MarketingPage() {
  const { user, loading } = useUser();
  const router = useRouter();
  const [scrolled, setScrolled] = useState(false);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 8);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  // Already signed in → bounce to app (or admin)
  useEffect(() => {
    if (loading) return;
    if (user) router.push(user.is_admin ? "/admin" : "/app");
  }, [user, loading, router]);

  return (
    <div className="min-h-screen text-text-primary" style={{ background: "#0a0e17" }}>
      {/* Top nav */}
      <header
        className="sticky top-0 z-30 transition-all"
        style={{
          background: scrolled ? "rgba(10,14,23,0.85)" : "transparent",
          backdropFilter: scrolled ? "blur(8px)" : "none",
          borderBottom: scrolled ? "1px solid #1e293b" : "1px solid transparent",
        }}
      >
        <div className="max-w-6xl mx-auto px-4 md:px-6 h-14 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Logo />
            <span className="font-bold tracking-tight text-text-primary">AlphaAgent</span>
          </div>
          <Link
            href="/login"
            className="px-3.5 py-2 rounded-lg text-sm font-semibold transition-colors"
            style={{
              background: "rgba(96,165,250,0.12)",
              color: "#60a5fa",
              border: "1px solid rgba(96,165,250,0.3)",
              minHeight: 36,
            }}
          >
            Sign in
          </Link>
        </div>
      </header>

      {/* Hero */}
      <section className="relative overflow-hidden">
        {/* Ambient grid + glow */}
        <div
          className="absolute inset-0 pointer-events-none"
          style={{
            backgroundImage:
              "radial-gradient(ellipse 80% 50% at 50% 0%, rgba(34,197,94,0.10) 0%, transparent 60%), " +
              "radial-gradient(ellipse 60% 40% at 80% 100%, rgba(96,165,250,0.08) 0%, transparent 60%)",
          }}
        />
        <div
          className="absolute inset-0 pointer-events-none opacity-[0.04]"
          style={{
            backgroundImage:
              "linear-gradient(to right, #ffffff 1px, transparent 1px), linear-gradient(to bottom, #ffffff 1px, transparent 1px)",
            backgroundSize: "32px 32px",
            maskImage:
              "radial-gradient(ellipse 60% 60% at 50% 30%, #000 0%, transparent 80%)",
            WebkitMaskImage:
              "radial-gradient(ellipse 60% 60% at 50% 30%, #000 0%, transparent 80%)",
          }}
        />

        <div className="relative max-w-5xl mx-auto px-4 md:px-6 pt-12 md:pt-24 pb-16 md:pb-24 text-center">
          <div
            className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full text-[11px] font-semibold uppercase tracking-wider mb-6"
            style={{
              background: "rgba(34,197,94,0.1)",
              color: "#22c55e",
              border: "1px solid rgba(34,197,94,0.25)",
            }}
          >
            <span className="inline-block w-1.5 h-1.5 rounded-full" style={{ background: "#22c55e" }} />
            Free 24-hour trial · No credit card · No setup
          </div>

          <h1
            className="font-bold tracking-tight mb-4"
            style={{
              fontSize: "clamp(36px, 7vw, 64px)",
              lineHeight: 1.05,
              letterSpacing: "-0.02em",
            }}
          >
            <span className="text-text-primary">AI traders. </span>
            <span style={{
              background: "linear-gradient(120deg, #22c55e 0%, #60a5fa 100%)",
              WebkitBackgroundClip: "text",
              backgroundClip: "text",
              WebkitTextFillColor: "transparent",
            }}>On you.</span>
          </h1>

          <p className="text-text-secondary text-lg max-w-2xl mx-auto mb-8 leading-relaxed">
            Paper-trading agents that scan the market, place trades, and learn from every result —
            on Indian equities or crypto. Sign in for <strong className="text-text-primary">24 hours of free runtime</strong>.
          </p>

          <div className="flex flex-col sm:flex-row items-center justify-center gap-3 mb-10">
            <Link
              href="/login"
              className="w-full sm:w-auto inline-flex items-center justify-center gap-2 px-6 rounded-xl font-semibold text-sm transition-all"
              style={{
                background: "#22c55e",
                color: "#0a0e17",
                minHeight: 52,
                minWidth: 220,
                boxShadow: "0 8px 24px -8px rgba(34,197,94,0.5)",
              }}
            >
              Sign in with Google
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="M5 12h14M12 5l7 7-7 7" />
              </svg>
            </Link>
            <a
              href="#features"
              className="w-full sm:w-auto inline-flex items-center justify-center px-6 rounded-xl font-semibold text-sm text-text-secondary hover:text-text-primary transition-colors"
              style={{
                border: "1px solid #1e293b",
                background: "rgba(21,29,46,0.5)",
                minHeight: 52,
                minWidth: 180,
              }}
            >
              See how it works
            </a>
          </div>

          {/* Hero chart mock */}
          <HeroChart />
        </div>
      </section>

      {/* How the free tier works */}
      <section className="relative px-4 md:px-6 py-12 md:py-16">
        <div className="max-w-5xl mx-auto">
          <div className="text-center mb-8">
            <div className="text-[11px] uppercase tracking-[0.18em] font-semibold text-text-muted mb-2">
              The free plan
            </div>
            <h2 className="text-2xl md:text-3xl font-bold tracking-tight">
              24 hours of runtime. No card.
            </h2>
            <p className="text-text-secondary text-sm md:text-base mt-3 max-w-xl mx-auto">
              The clock only counts time the agent is actually running — pause it overnight, on weekends, whenever.
            </p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 md:gap-5">
            <PerkCard
              icon="✓"
              accent="#22c55e"
              title="One free agent"
              body="Pick a market, set a personality, hit go. Open-source models on us — no API keys, no setup."
            />
            <PerkCard
              icon="✓"
              accent="#22c55e"
              title="Pause-and-resume"
              body="Stop the agent any time. Your 24 hours don't expire on the calendar — only while it's actually trading."
            />
            <PerkCard
              icon="🔒"
              accent="#f59e0b"
              title="Paid: 5-day runtime"
              body="More runtime, multiple sessions, and the full model lineup — Claude, GPT-4o, Llama, Gemini, DeepSeek. Join the waitlist after you sign in."
              locked
            />
            <PerkCard
              icon="🔒"
              accent="#f59e0b"
              title="Paid: backtests + model compare"
              body="Replay months of history. Race two models on the same window and see who wins. Coming with the paid tier."
              locked
            />
          </div>
        </div>
      </section>

      {/* Feature blocks */}
      <section id="features" className="relative px-4 md:px-6 py-16 md:py-24">
        <div className="max-w-5xl mx-auto">
          <div className="text-center mb-10">
            <div className="text-[11px] uppercase tracking-[0.18em] font-semibold text-text-muted mb-2">
              What you get
            </div>
            <h2 className="text-2xl md:text-4xl font-bold tracking-tight">
              Real research tools, not a guessing game.
            </h2>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-4 md:gap-5">
            <Feature
              icon="🤖"
              title="Sign in, ship an agent"
              body="No API keys. No model wiring. Pick NSE or crypto, hit go — the agent is trading inside a minute."
              accent="#60a5fa"
            />
            <Feature
              icon="💭"
              title="See every decision"
              body="Why it skipped TATAMOTORS at 10:45. Why it shorted ICICIBANK. Each cycle's reasoning is logged and replayable."
              accent="#22c55e"
            />
            <Feature
              icon="🔁"
              title="Learns from itself"
              body="After every trade the agent reflects on what worked. Yesterday's losses become tomorrow's distilled rules."
              accent="#a78bfa"
            />
            <Feature
              icon="📈"
              title="Real dashboards"
              body="Equity curve, drawdown chart, daily P&L, per-session sparklines — the full performance view, not just a number."
              accent="#fbbf24"
            />
            <Feature
              icon="⏱"
              title="Runtime, not calendar"
              body="The 24h only ticks while the agent is live. Burn it in a day on crypto or stretch it across a week of NSE."
              accent="#f472b6"
            />
            <Feature
              icon="🛑"
              title="Kill switch"
              body="One tap liquidates every position and stops the agent. Let an AI drive only because you can pull the plug instantly."
              accent="#ef4444"
            />
          </div>
        </div>
      </section>

      {/* CTA strip */}
      <section className="px-4 md:px-6 pb-20">
        <div
          className="max-w-5xl mx-auto rounded-3xl p-8 md:p-12 text-center overflow-hidden relative"
          style={{
            background:
              "radial-gradient(ellipse 80% 60% at 50% 0%, rgba(34,197,94,0.18) 0%, transparent 60%), linear-gradient(135deg, #0c1424 0%, #0a0e17 100%)",
            border: "1px solid rgba(34,197,94,0.25)",
          }}
        >
          <h3 className="text-2xl md:text-3xl font-bold mb-3 tracking-tight">
            Test ideas before risking ₹1.
          </h3>
          <p className="text-text-secondary mb-6 max-w-xl mx-auto">
            Sign in with Google. 24 hours of free runtime, no card.
          </p>
          <Link
            href="/login"
            className="inline-flex items-center justify-center gap-2 px-6 rounded-xl font-semibold text-sm"
            style={{
              background: "#22c55e",
              color: "#0a0e17",
              minHeight: 52,
              minWidth: 220,
              boxShadow: "0 8px 24px -8px rgba(34,197,94,0.5)",
            }}
          >
            Sign in with Google
          </Link>
        </div>
      </section>

      <footer className="border-t border-border/50 px-4 md:px-6 py-6">
        <div className="max-w-5xl mx-auto flex flex-col sm:flex-row items-center justify-between gap-2 text-xs text-text-muted">
          <div className="flex items-center gap-2">
            <Logo small />
            <span>AlphaAgent · Paper-trading research, not financial advice.</span>
          </div>
          <div className="font-mono">v2 · multi-user</div>
        </div>
      </footer>
    </div>
  );
}

function Logo({ small }: { small?: boolean }) {
  const size = small ? 16 : 22;
  return (
    <div
      className="flex items-center justify-center rounded-lg"
      style={{
        width: size + 8,
        height: size + 8,
        background: "linear-gradient(135deg, #22c55e, #60a5fa)",
      }}
    >
      <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="#0a0e17" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
        <polyline points="22 7 13.5 15.5 8.5 10.5 2 17" />
      </svg>
    </div>
  );
}

function PerkCard({
  icon, title, body, accent, locked,
}: { icon: string; title: string; body: string; accent: string; locked?: boolean }) {
  return (
    <div
      className="rounded-2xl p-5 md:p-6"
      style={{
        background: locked
          ? "linear-gradient(180deg, rgba(245,158,11,0.04) 0%, rgba(12,20,36,1) 100%)"
          : "linear-gradient(180deg, #151d2e 0%, #0c1424 100%)",
        border: `1px solid ${accent}33`,
        opacity: locked ? 0.85 : 1,
      }}
    >
      <div className="flex items-start gap-3">
        <div
          className="flex items-center justify-center rounded-lg shrink-0"
          style={{
            width: 32, height: 32,
            background: `${accent}1a`,
            border: `1px solid ${accent}33`,
            color: accent,
            fontSize: 16,
            fontWeight: 700,
          }}
        >
          {icon}
        </div>
        <div>
          <div className="flex items-center gap-2 mb-1">
            <h3 className="text-base font-semibold" style={{ color: locked ? "#fcd34d" : "#e2e8f0" }}>
              {title}
            </h3>
            {locked && (
              <span
                className="text-[10px] uppercase tracking-wider font-bold px-1.5 py-0.5 rounded"
                style={{
                  background: "rgba(245,158,11,0.15)",
                  color: "#fcd34d",
                  border: "1px solid rgba(245,158,11,0.3)",
                }}
              >
                Coming soon
              </span>
            )}
          </div>
          <p className="text-sm text-text-secondary leading-relaxed">{body}</p>
        </div>
      </div>
    </div>
  );
}

function Feature({ icon, title, body, accent }: { icon: string; title: string; body: string; accent: string }) {
  return (
    <div
      className="rounded-2xl p-5 md:p-6 transition-transform"
      style={{
        background: "linear-gradient(180deg, #151d2e 0%, #0c1424 100%)",
        border: `1px solid ${accent}22`,
      }}
    >
      <div
        className="inline-flex items-center justify-center rounded-xl mb-4"
        style={{
          width: 44,
          height: 44,
          background: `${accent}1a`,
          border: `1px solid ${accent}33`,
          fontSize: 22,
        }}
      >
        {icon}
      </div>
      <h3 className="text-base font-semibold mb-1.5" style={{ color: accent }}>{title}</h3>
      <p className="text-sm text-text-secondary leading-relaxed">{body}</p>
    </div>
  );
}

function HeroChart() {
  // Hand-tuned synthetic curve so it always looks "interesting"
  const W = 800;
  const H = 220;
  const PAD = 16;
  const points = [
    100, 102, 99, 101, 105, 108, 106, 112, 115, 110, 114, 118, 122, 119,
    125, 130, 128, 134, 138, 142, 139, 144, 148, 152, 155, 158, 154, 162,
    168, 172,
  ];
  const min = Math.min(...points);
  const max = Math.max(...points);
  const range = max - min || 1;
  const innerW = W - 2 * PAD;
  const innerH = H - 2 * PAD;
  const pts = points.map((v, i) => {
    const x = PAD + (i / (points.length - 1)) * innerW;
    const y = PAD + (1 - (v - min) / range) * innerH;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  return (
    <div
      className="mx-auto rounded-2xl overflow-hidden"
      style={{
        maxWidth: 720,
        background: "rgba(15,23,42,0.5)",
        border: "1px solid #1e293b",
        backdropFilter: "blur(8px)",
      }}
    >
      <div className="px-4 py-3 flex items-center justify-between" style={{ borderBottom: "1px solid #1e293b" }}>
        <div className="flex items-center gap-2">
          <span className="inline-block w-2 h-2 rounded-full animate-pulse-dot" style={{ background: "#22c55e" }} />
          <span className="text-[10px] uppercase tracking-wider font-semibold text-text-secondary">
            DEMO PORTFOLIO · LIVE
          </span>
        </div>
        <span className="font-mono text-sm font-bold text-accent-green tabular-nums">+72.4%</span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" style={{ width: "100%", height: 200, display: "block" }}>
        <defs>
          <linearGradient id="hero-grad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#22c55e" stopOpacity="0.4" />
            <stop offset="100%" stopColor="#22c55e" stopOpacity="0" />
          </linearGradient>
        </defs>
        <path
          d={`M ${pts.join(" L ")} L ${W - PAD},${H - PAD} L ${PAD},${H - PAD} Z`}
          fill="url(#hero-grad)"
        />
        <path
          d={`M ${pts.join(" L ")}`}
          fill="none"
          stroke="#22c55e"
          strokeWidth={2}
          strokeLinejoin="round"
          strokeLinecap="round"
        />
      </svg>
    </div>
  );
}
