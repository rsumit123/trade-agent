"use client";
import { Suspense, useEffect, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { api, cn, fmt } from "@/lib/api";
import { useToast } from "@/components/Toast";
import { Collapsible } from "@/components/Collapsible";
import type { MarketPreset, Session } from "@/lib/types";

type PresetKey = "nse-intraday" | "nse-swing" | "crypto" | "custom";

const PERSONALITY_CHIPS: { label: string; text: string }[] = [
  { label: "Aggressive", text: "Be aggressive. Target 2% daily returns. Favor momentum plays. Cut losers fast at 1% stop." },
  { label: "Conservative", text: "Preserve capital. Only take 5/5 conviction trades. Use small position sizes. Prefer waiting over forcing." },
  { label: "Scalper", text: "Scalp for quick moves. Target 0.3-1% per trade. Tight 0.3% stops. No entries in the last hour." },
  { label: "Swing", text: "Hold positions for multi-day moves. Ignore intraday noise. Use wider stops (2-3%). Trade only A+ setups." },
];

/** Slugify display name → valid session_id */
function slugify(name: string): string {
  return name
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 32);
}

export default function CreateSessionPage() {
  return (
    <Suspense fallback={<div className="px-4 py-8 max-w-3xl mx-auto"><div className="skeleton h-8 w-48 mb-6" /><div className="skeleton h-32 w-full rounded-xl" /></div>}>
      <CreateSessionInner />
    </Suspense>
  );
}

function CreateSessionInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const toast = useToast();
  const [presets, setPresets] = useState<Record<string, MarketPreset>>({});
  const [sessions, setSessions] = useState<Session[]>([]);
  const [creating, setCreating] = useState(false);
  const [sessionIdEdited, setSessionIdEdited] = useState(false);

  const [form, setForm] = useState({
    session_id: "",
    display_name: "",
    market: "nse",
    starting_capital: 0,
    max_position_pct: 0.20,
    max_open_positions: 5,
    daily_loss_limit_pct: 0.02,
    per_trade_loss_limit_pct: 0.01,
    llm_provider: "openrouter",
    llm_model: "anthropic/claude-haiku-4-5",
    api_key_env: "OPENROUTER_API_KEY",
    personality: "",
    import_learnings_from: "",
    backtest_mode: false,
    backtest_start_date: "",
    backtest_end_date: "",
  });

  const preset = presets[form.market];
  const currencyLocale = preset?.currency === "INR" ? "en-IN" : "en-US";
  const currencySymbol = preset?.currency_symbol || "$";

  // Load presets + existing sessions, apply URL preset param
  useEffect(() => {
    api<Record<string, MarketPreset>>("/api/market-presets").then((p) => {
      setPresets(p);
      const qPreset = searchParams.get("preset") as PresetKey | null;

      let market = "nse";
      let personality = "";
      if (qPreset === "crypto") {
        market = "crypto";
      } else if (qPreset === "nse-intraday") {
        market = "nse-intraday" in p ? "nse-intraday" : "nse";
        personality = PERSONALITY_CHIPS.find((c) => c.label === "Scalper")?.text || "";
      } else if (qPreset === "nse-swing") {
        market = "nse";
        personality = PERSONALITY_CHIPS.find((c) => c.label === "Swing")?.text || "";
      }

      const mPreset = p[market] || p.nse;
      const cap = mPreset?.default_starting_capital || 1000000;
      setForm((f) => ({
        ...f,
        market,
        starting_capital: cap,
        personality: personality || f.personality,
      }));
    });
    api<Session[]>("/api/sessions").then(setSessions).catch(() => {});
  }, [searchParams]);

  // Auto-sync session_id from display_name (until user edits it manually)
  useEffect(() => {
    if (!sessionIdEdited && form.display_name) {
      const slug = slugify(form.display_name);
      setForm((f) => ({ ...f, session_id: slug }));
    }
  }, [form.display_name, sessionIdEdited]);

  const capitalDisplay = useMemo(
    () => (form.starting_capital ? form.starting_capital.toLocaleString(currencyLocale) : ""),
    [form.starting_capital, currencyLocale]
  );

  const selectMarket = (m: string) => {
    const mPreset = presets[m];
    const cap = mPreset?.default_starting_capital || form.starting_capital;
    setForm((f) => ({ ...f, market: m, starting_capital: cap }));
  };

  const handleSubmit = async (startAfter: boolean) => {
    if (!form.session_id.trim()) {
      toast.error("Enter a display name to generate a session ID");
      return;
    }
    if (!/^[a-z0-9_-]+$/.test(form.session_id)) {
      toast.error("Session ID: lowercase letters, numbers, hyphens, underscores only");
      return;
    }
    if (form.backtest_mode && (!form.backtest_start_date || !form.backtest_end_date)) {
      toast.error("Backtest mode requires start and end dates");
      return;
    }

    setCreating(true);
    try {
      await api("/api/sessions", {
        method: "POST",
        body: JSON.stringify({
          ...form,
          display_name: form.display_name || form.session_id,
          backtest_start_date: form.backtest_start_date || null,
          backtest_end_date: form.backtest_end_date || null,
        }),
      });

      if (form.backtest_mode) {
        await api(`/api/backtest/start/${form.session_id}`, {
          method: "POST",
          body: JSON.stringify({
            start_date: form.backtest_start_date,
            end_date: form.backtest_end_date,
          }),
        });
      } else if (startAfter) {
        await api(`/api/agent/start/${form.session_id}`, { method: "POST" });
      }
      router.push(`/sessions/${form.session_id}`);
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Failed to create session");
      setCreating(false);
    }
  };

  const primaryCta = form.backtest_mode
    ? (creating ? "Creating & Starting Backtest..." : "Create & Run Backtest")
    : (creating ? "Creating..." : "Create & Start Agent");

  return (
    <div
      className="px-4 md:px-8 py-5 md:py-8 max-w-3xl mx-auto"
      style={{
        // Reserve space for BOTH the sticky action bar (~80px) AND the
        // BottomNav (56px + safe-area). Desktop falls back to default.
        paddingBottom: "calc(80px + 56px + env(safe-area-inset-bottom) + 16px)",
      }}
    >
      {/* Header */}
      <div className="mb-5 md:mb-7 animate-fade-in">
        <h1 className="text-xl md:text-2xl font-bold tracking-tight">Create Session</h1>
        <p className="text-text-muted text-xs md:text-sm mt-1">Configure a new AI trading agent in a few steps</p>
      </div>

      {/* Mode toggle — compact segmented control */}
      <div className="mb-5 animate-fade-in delay-1">
        <div className="flex gap-1 p-1 rounded-xl border border-border bg-bg-card w-full sm:w-fit">
          <ModePill
            label="Live Trading"
            subLabel="Real-time paper trades"
            active={!form.backtest_mode}
            onClick={() => setForm((f) => ({ ...f, backtest_mode: false }))}
            icon="📈"
          />
          <ModePill
            label="Backtest"
            subLabel="Replay history"
            active={form.backtest_mode}
            onClick={() => setForm((f) => ({ ...f, backtest_mode: true }))}
            icon="⏳"
            accent="purple"
          />
        </div>
      </div>

      {/* Market */}
      <FormGroup title="Market" delay={2}>
        <div className="grid grid-cols-2 gap-3">
          {Object.entries(presets).map(([id, p]) => {
            const active = form.market === id;
            return (
              <button
                key={id}
                onClick={() => selectMarket(id)}
                className={cn(
                  "p-3 md:p-4 rounded-xl border text-left transition-all",
                  active
                    ? "border-accent-blue bg-accent-blue/5 shadow-sm shadow-accent-blue/10"
                    : "border-border hover:border-border-accent bg-bg-card"
                )}
                style={{ minHeight: 80 }}
              >
                <div className="flex items-center gap-2 mb-1.5">
                  <span
                    className="flex items-center justify-center rounded-lg font-mono font-bold"
                    style={{
                      width: 32, height: 32,
                      background: id === "crypto" ? "rgba(59,130,246,0.15)" : "rgba(34,197,94,0.15)",
                      color: id === "crypto" ? "#3b82f6" : "#22c55e",
                      fontSize: 16,
                    }}
                  >
                    {id === "crypto" ? "₿" : "▲"}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="font-semibold text-text-primary text-sm truncate">{p.display_name}</div>
                  </div>
                  {active && <span className="text-accent-blue text-xs">✓</span>}
                </div>
                <div className="text-[11px] text-text-muted">
                  {p.currency_symbol}{p.default_starting_capital.toLocaleString()}
                  {p.is_24x7 && " · 24/7"}
                </div>
              </button>
            );
          })}
        </div>
      </FormGroup>

      {/* Backtest date range */}
      {form.backtest_mode && (
        <FormGroup title="Backtest Period" delay={3}>
          <div className="grid grid-cols-2 gap-3 mb-3">
            <div>
              <label className="block text-[11px] text-text-muted mb-1.5">Start</label>
              <input
                type="date"
                value={form.backtest_start_date}
                onChange={(e) => setForm({ ...form, backtest_start_date: e.target.value })}
                className="w-full font-mono text-sm"
              />
            </div>
            <div>
              <label className="block text-[11px] text-text-muted mb-1.5">End</label>
              <input
                type="date"
                value={form.backtest_end_date}
                onChange={(e) => setForm({ ...form, backtest_end_date: e.target.value })}
                className="w-full font-mono text-sm"
              />
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            {[
              { label: "1W", days: 7 },
              { label: "2W", days: 14 },
              { label: "1M", days: 30 },
              { label: "3M", days: 90 },
            ].map(({ label, days }) => (
              <button
                key={label}
                onClick={() => {
                  const end = new Date();
                  end.setDate(end.getDate() - 1);
                  const start = new Date(end);
                  start.setDate(start.getDate() - days);
                  setForm({
                    ...form,
                    backtest_start_date: start.toISOString().split("T")[0],
                    backtest_end_date: end.toISOString().split("T")[0],
                  });
                }}
                className="px-3 rounded-lg text-xs font-semibold text-[#a78bfa] border transition-all hover:bg-[#8b5cf6]/10"
                style={{ minHeight: 36, background: "rgba(139,92,246,0.08)", borderColor: "rgba(139,92,246,0.25)" }}
              >
                Last {label}
              </button>
            ))}
          </div>
          {form.backtest_start_date && form.backtest_end_date && (
            <div className="text-[11px] text-text-muted mt-3">
              {(() => {
                const start = new Date(form.backtest_start_date);
                const end = new Date(form.backtest_end_date);
                const days = Math.ceil((end.getTime() - start.getTime()) / (1000 * 60 * 60 * 24));
                const tradingDays = Math.floor(days * 5 / 7);
                return `~${tradingDays} trading days · each day takes 1-3 min to simulate`;
              })()}
            </div>
          )}
        </FormGroup>
      )}

      {/* Basics */}
      <FormGroup title="Basics" delay={3}>
        <div className="space-y-3">
          <div>
            <label className="block text-[11px] text-text-muted mb-1.5">Display Name</label>
            <input
              value={form.display_name}
              onChange={(e) => setForm({ ...form, display_name: e.target.value })}
              placeholder="My NSE Intraday Agent"
              className="w-full"
            />
          </div>
          <div>
            <div className="flex items-center justify-between mb-1.5">
              <label className="text-[11px] text-text-muted">Session ID</label>
              {!sessionIdEdited && form.session_id && (
                <button
                  type="button"
                  onClick={() => setSessionIdEdited(true)}
                  className="text-[10px] text-accent-blue hover:underline"
                >
                  Edit manually
                </button>
              )}
            </div>
            <input
              value={form.session_id}
              onChange={(e) => {
                setSessionIdEdited(true);
                setForm({ ...form, session_id: e.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, '') });
              }}
              placeholder={form.backtest_mode ? "nse_backtest_apr" : "my_session"}
              className="w-full font-mono text-sm"
              readOnly={!sessionIdEdited && !!form.display_name}
              style={!sessionIdEdited && !!form.display_name ? { opacity: 0.7, cursor: "not-allowed" } : undefined}
            />
            <span className="text-[10px] text-text-muted block mt-1">
              {sessionIdEdited ? "lowercase · numbers · _ · -" : "Auto-generated from display name"}
            </span>
          </div>
          <div>
            <label className="block text-[11px] text-text-muted mb-1.5">
              Starting Capital ({currencySymbol})
            </label>
            <input
              type="text"
              inputMode="numeric"
              value={capitalDisplay}
              onChange={(e) => {
                const raw = e.target.value.replace(/[^0-9]/g, "");
                const num = parseInt(raw) || 0;
                setForm((f) => ({ ...f, starting_capital: num }));
              }}
              placeholder={fmt(preset?.default_starting_capital || 1000000, currencySymbol, currencyLocale).replace(currencySymbol, "")}
              className="w-full font-mono"
            />
          </div>
        </div>
      </FormGroup>

      {/* Personality — always visible with chips */}
      <FormGroup title="Trading Personality" delay={4}>
        <div className="flex flex-wrap gap-2 mb-3">
          {PERSONALITY_CHIPS.map((chip) => {
            const active = form.personality === chip.text;
            return (
              <button
                key={chip.label}
                onClick={() => setForm({ ...form, personality: active ? "" : chip.text })}
                className={cn(
                  "px-3 rounded-full text-xs font-semibold transition-all border",
                  active
                    ? "bg-accent-blue/15 text-accent-blue border-accent-blue/40"
                    : "bg-bg-card text-text-muted border-border hover:text-text-primary hover:border-border-accent"
                )}
                style={{ minHeight: 36 }}
              >
                {chip.label}
              </button>
            );
          })}
        </div>
        <textarea
          value={form.personality}
          onChange={(e) => setForm({ ...form, personality: e.target.value })}
          placeholder="Or describe custom behavior. e.g. Focus on momentum plays, cut losers at 1% stop..."
          rows={3}
          className="w-full resize-none text-sm"
        />
        <span className="text-[10px] text-text-muted block mt-1.5">
          Injected into LLM system prompt. Leave empty for default balanced behavior.
        </span>
      </FormGroup>

      {/* LLM Model */}
      <FormGroup title="LLM Model" delay={5}>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div>
            <label className="block text-[11px] text-text-muted mb-1.5">Provider</label>
            <select
              value={form.llm_provider}
              onChange={(e) => {
                const provider = e.target.value;
                const defaults: Record<string, { model: string; keyEnv: string }> = {
                  openrouter: { model: "anthropic/claude-haiku-4-5", keyEnv: "OPENROUTER_API_KEY" },
                  anthropic: { model: "claude-sonnet-4-5-20250929", keyEnv: "ANTHROPIC_API_KEY" },
                  openai: { model: "gpt-4o", keyEnv: "OPENAI_API_KEY" },
                };
                const d = defaults[provider] || defaults.openrouter;
                setForm({ ...form, llm_provider: provider, llm_model: d.model, api_key_env: d.keyEnv });
              }}
              className="w-full text-sm"
            >
              <option value="openrouter">OpenRouter</option>
              <option value="anthropic">Anthropic</option>
              <option value="openai">OpenAI</option>
            </select>
          </div>
          <div>
            <label className="block text-[11px] text-text-muted mb-1.5">Model</label>
            <ModelSelect provider={form.llm_provider} value={form.llm_model} onChange={(v) => setForm({ ...form, llm_model: v })} />
          </div>
        </div>
      </FormGroup>

      {/* Advanced */}
      <div className="mb-4 animate-fade-in delay-5">
        <Collapsible title="Advanced" subtitle="Risk limits, API keys, import learnings" icon="⚙">
          <div className="p-4 space-y-5">
            {/* Risk */}
            <div>
              <div className="text-[11px] uppercase tracking-wider font-semibold text-text-muted mb-3">Risk Limits</div>
              <div className="space-y-4">
                <SliderField
                  label="Max Position Size"
                  value={form.max_position_pct}
                  onChange={(v) => setForm({ ...form, max_position_pct: v })}
                  min={0.05} max={0.50} step={0.05}
                  format={(v) => `${(v * 100).toFixed(0)}%`}
                />
                <SliderField
                  label="Daily Loss Limit"
                  value={form.daily_loss_limit_pct}
                  onChange={(v) => setForm({ ...form, daily_loss_limit_pct: v })}
                  min={0.01} max={0.10} step={0.01}
                  format={(v) => `${(v * 100).toFixed(0)}%`}
                />
                <div className="grid grid-cols-2 gap-3">
                  <NumberField
                    label="Max Open Positions"
                    value={form.max_open_positions}
                    onChange={(v) => setForm({ ...form, max_open_positions: v })}
                    min={1} max={20} step={1} integer
                  />
                  <NumberField
                    label="Per-Trade Stop %"
                    value={form.per_trade_loss_limit_pct * 100}
                    onChange={(v) => setForm({ ...form, per_trade_loss_limit_pct: v / 100 })}
                    min={0.1} max={10} step={0.1}
                  />
                </div>
              </div>
            </div>

            {/* Import Learnings */}
            {sessions.length > 0 && (
              <div className="pt-4 border-t border-border/60">
                <div className="text-[11px] uppercase tracking-wider font-semibold text-text-muted mb-2">Import Learnings</div>
                <select
                  value={form.import_learnings_from}
                  onChange={(e) => setForm({ ...form, import_learnings_from: e.target.value })}
                  className="w-full text-sm"
                >
                  <option value="">Start fresh (no import)</option>
                  {sessions.map((s) => (
                    <option key={s.session_id} value={s.session_id}>
                      {s.display_name} — {s.total_trades ?? 0} trades
                      {s.win_rate != null ? ` · ${s.win_rate}% WR` : ""}
                    </option>
                  ))}
                </select>
                <span className="text-[10px] text-text-muted block mt-1.5">
                  {form.import_learnings_from
                    ? (sessions.find((s) => s.session_id === form.import_learnings_from)?.market === form.market
                      ? "Same market — full journal copied"
                      : "Different market — only distilled rules imported")
                    : "Skip the learning curve by importing from an existing session"}
                </span>
              </div>
            )}

            {/* API Key env */}
            <div className="pt-4 border-t border-border/60">
              <div className="text-[11px] uppercase tracking-wider font-semibold text-text-muted mb-2">API Key Env</div>
              <input
                value={form.api_key_env}
                onChange={(e) => setForm({ ...form, api_key_env: e.target.value })}
                placeholder="OPENROUTER_API_KEY"
                className="w-full font-mono text-sm"
              />
              <span className="text-[10px] text-text-muted block mt-1.5">
                Name of server env var holding the API key. Leave as default unless using a custom key.
              </span>
            </div>
          </div>
        </Collapsible>
      </div>

      {/* Sticky bottom action bar — sits ABOVE the global BottomNav (56px) */}
      <div
        className="fixed inset-x-0 md:hidden border-t border-border px-4 py-3"
        style={{
          // BottomNav is 56px + safe-area-inset-bottom; place this directly above it
          bottom: "calc(56px + env(safe-area-inset-bottom))",
          zIndex: 9991, // 1 above BottomNav (9990)
          background: "rgba(10,14,23,0.95)",
          backdropFilter: "blur(12px)",
          WebkitBackdropFilter: "blur(12px)",
        }}
      >
        <button
          onClick={() => handleSubmit(true)}
          disabled={creating || !form.session_id}
          className={cn(
            "w-full rounded-xl font-semibold text-sm transition-all disabled:opacity-50",
            form.backtest_mode
              ? "bg-[#8b5cf6] hover:bg-[#7c4dec] text-white"
              : "bg-accent-green hover:bg-accent-green/90 text-white"
          )}
          style={{ minHeight: 48 }}
        >
          {primaryCta}
        </button>
        {!form.backtest_mode && (
          <button
            onClick={() => handleSubmit(false)}
            disabled={creating || !form.session_id}
            className="w-full mt-2 text-xs text-text-muted hover:text-text-primary transition-colors disabled:opacity-50"
            style={{ minHeight: 32 }}
          >
            or create without starting
          </button>
        )}
      </div>

      {/* Desktop action row */}
      <div className="hidden md:flex items-center justify-end gap-3 mt-6 mb-12 animate-fade-in delay-5">
        {!form.backtest_mode && (
          <button
            onClick={() => handleSubmit(false)}
            disabled={creating || !form.session_id}
            className="px-5 rounded-xl border border-border hover:border-border-accent text-text-secondary transition-all disabled:opacity-50"
            style={{ minHeight: 44 }}
          >
            Create without starting
          </button>
        )}
        <button
          onClick={() => handleSubmit(true)}
          disabled={creating || !form.session_id}
          className={cn(
            "px-6 rounded-xl font-semibold transition-all disabled:opacity-50",
            form.backtest_mode
              ? "bg-[#8b5cf6] hover:bg-[#7c4dec] text-white shadow-lg shadow-[#8b5cf6]/20"
              : "bg-accent-green hover:bg-accent-green/90 text-white shadow-lg shadow-accent-green/20"
          )}
          style={{ minHeight: 44 }}
        >
          {primaryCta}
        </button>
      </div>

      {/* Back link (desktop) */}
      <div className="hidden md:block mb-8">
        <Link href="/" className="text-xs text-text-muted hover:text-text-primary transition-colors">
          ← Back to sessions
        </Link>
      </div>
    </div>
  );
}

function FormGroup({ title, delay, children }: { title: string; delay: number; children: React.ReactNode }) {
  return (
    <div className={`mb-4 animate-fade-in delay-${delay}`}>
      <h2 className="text-[11px] uppercase tracking-wider font-semibold text-text-muted mb-2 px-1">{title}</h2>
      <div className="bg-bg-card border border-border rounded-xl p-4">{children}</div>
    </div>
  );
}

function ModePill({ label, subLabel, active, onClick, icon, accent }: {
  label: string; subLabel: string; active: boolean; onClick: () => void; icon: string; accent?: "purple";
}) {
  const activeStyle = accent === "purple"
    ? "bg-[#8b5cf6]/15 text-[#a78bfa] shadow-sm"
    : "bg-accent-green/15 text-accent-green shadow-sm";
  return (
    <button
      onClick={onClick}
      className={cn(
        "flex-1 rounded-lg px-3 py-2 text-left transition-all",
        active ? activeStyle : "text-text-muted hover:text-text-primary"
      )}
      style={{ minHeight: 52 }}
    >
      <div className="flex items-center gap-2">
        <span style={{ fontSize: 16 }}>{icon}</span>
        <div className="min-w-0">
          <div className="text-xs font-semibold leading-none">{label}</div>
          <div className="text-[10px] opacity-70 mt-1 leading-none truncate">{subLabel}</div>
        </div>
      </div>
    </button>
  );
}

function ModelSelect({ provider, value, onChange }: { provider: string; value: string; onChange: (v: string) => void }) {
  if (provider === "openrouter") {
    return (
      <select value={value} onChange={(e) => onChange(e.target.value)} className="w-full text-sm">
        <optgroup label="Anthropic">
          <option value="anthropic/claude-haiku-4-5">Claude Haiku 4.5 — $0.80/M</option>
        </optgroup>
        <optgroup label="Google">
          <option value="google/gemini-2.5-flash">Gemini 2.5 Flash — $0.15/M</option>
        </optgroup>
        <optgroup label="OpenAI">
          <option value="openai/gpt-4o-mini">GPT-4o Mini — $0.15/M</option>
        </optgroup>
        <optgroup label="Meta">
          <option value="meta-llama/llama-4-maverick">Llama 4 Maverick — $0.20/M</option>
          <option value="meta-llama/llama-4-scout">Llama 4 Scout — $0.10/M</option>
        </optgroup>
        <optgroup label="DeepSeek">
          <option value="deepseek/deepseek-chat-v3-0324">DeepSeek V3 — $0.14/M</option>
          <option value="deepseek/deepseek-r1">DeepSeek R1 — $0.55/M</option>
        </optgroup>
      </select>
    );
  }
  if (provider === "anthropic") {
    return (
      <select value={value} onChange={(e) => onChange(e.target.value)} className="w-full text-sm">
        <option value="claude-haiku-4-5-20250929">Claude Haiku 4.5</option>
        <option value="claude-sonnet-4-5-20250929">Claude Sonnet 4.5</option>
        <option value="claude-opus-4-0-20250514">Claude Opus 4</option>
      </select>
    );
  }
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)} className="w-full text-sm">
      <option value="gpt-4o">GPT-4o</option>
      <option value="gpt-4o-mini">GPT-4o Mini</option>
      <option value="o3-mini">o3-mini (reasoning)</option>
    </select>
  );
}

function NumberField({ label, value, onChange, min, max, step, integer = false }: {
  label: string;
  value: number;
  onChange: (v: number) => void;
  min: number; max: number; step: number;
  integer?: boolean;
}) {
  // Local string buffer so user can clear / mid-edit without snapping back.
  // Sync external value → buffer when value changes from outside.
  const [buf, setBuf] = useState<string>(String(value));
  const [focused, setFocused] = useState(false);

  useEffect(() => {
    if (!focused) setBuf(String(value));
  }, [value, focused]);

  const commit = (raw: string) => {
    const n = integer ? parseInt(raw, 10) : parseFloat(raw);
    if (Number.isNaN(n)) return; // ignore invalid; keep buffer for user
    const clamped = Math.max(min, Math.min(max, n));
    onChange(clamped);
  };

  return (
    <div>
      <label className="block text-[11px] text-text-muted mb-1.5">{label}</label>
      <input
        type="text"
        inputMode={integer ? "numeric" : "decimal"}
        value={buf}
        onChange={(e) => {
          const v = e.target.value;
          setBuf(v);
          // Optimistically commit valid intermediate values, but DON'T snap on empty
          if (v === "" || v === "-" || v === ".") return;
          const n = integer ? parseInt(v, 10) : parseFloat(v);
          if (!Number.isNaN(n) && n >= min && n <= max) onChange(n);
        }}
        onFocus={() => setFocused(true)}
        onBlur={() => {
          setFocused(false);
          // On blur: clamp + reformat to canonical display
          const n = integer ? parseInt(buf, 10) : parseFloat(buf);
          if (Number.isNaN(n)) {
            setBuf(String(value));
            return;
          }
          const clamped = Math.max(min, Math.min(max, n));
          onChange(clamped);
          setBuf(String(clamped));
        }}
        className="w-full font-mono"
        style={{ minHeight: 44, fontSize: 16, padding: "10px 12px" }}
      />
      <div className="text-[10px] text-text-muted mt-1 font-mono">
        range: {min}–{max}{!integer && ` · step ${step}`}
      </div>
    </div>
  );
}

function SliderField({ label, value, onChange, min, max, step, format }: {
  label: string; value: number; onChange: (v: number) => void;
  min: number; max: number; step: number; format: (v: number) => string;
}) {
  const fillPct = ((value - min) / (max - min)) * 100;
  return (
    <div>
      <div className="flex items-center justify-between mb-2">
        <label className="text-[11px] text-text-muted">{label}</label>
        <span className="text-xs font-mono font-semibold text-accent-blue">{format(value)}</span>
      </div>
      <input
        type="range"
        value={value}
        onChange={(e) => onChange(parseFloat(e.target.value))}
        min={min} max={max} step={step}
        className="w-full"
        style={{ "--range-fill": `${fillPct}%` } as React.CSSProperties}
      />
      <div className="flex justify-between mt-1">
        <span className="text-[10px] text-text-muted font-mono">{format(min)}</span>
        <span className="text-[10px] text-text-muted font-mono">{format(max)}</span>
      </div>
    </div>
  );
}
