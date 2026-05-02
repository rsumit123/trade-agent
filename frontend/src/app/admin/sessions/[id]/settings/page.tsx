"use client";
import { useEffect, useMemo, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { api, cn } from "@/lib/api";
import { useToast } from "@/components/Toast";
import { Collapsible } from "@/components/Collapsible";
import type { AgentStatus, Performance, PortfolioSummary } from "@/lib/types";

interface SettingsForm {
  display_name: string;
  starting_capital: number;
  max_position_pct: number;
  max_open_positions: number;
  daily_loss_limit_pct: number;
  per_trade_loss_limit_pct: number;
  max_trade_amount: number;
  llm_provider: string;
  llm_model: string;
  api_key_env: string;
  intraday_interval_min: number;
  personality: string;
}

const DEFAULT_FORM: SettingsForm = {
  display_name: "",
  starting_capital: 0,
  max_position_pct: 0.20,
  max_open_positions: 5,
  daily_loss_limit_pct: 0.02,
  per_trade_loss_limit_pct: 0.01,
  max_trade_amount: 0,
  llm_provider: "openrouter",
  llm_model: "",
  api_key_env: "OPENROUTER_API_KEY",
  intraday_interval_min: 15,
  personality: "",
};

export default function SessionSettingsPage() {
  const params = useParams();
  const router = useRouter();
  const toast = useToast();
  const sessionId = params.id as string;

  const [form, setForm] = useState<SettingsForm>(DEFAULT_FORM);
  const [initial, setInitial] = useState<SettingsForm>(DEFAULT_FORM);
  const [saving, setSaving] = useState(false);
  const [showDelete, setShowDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);

  // Context for danger modal + status pill
  const [agentStatus, setAgentStatus] = useState<AgentStatus>({ running: false, pid: null });
  const [perf, setPerf] = useState<Performance | null>(null);
  const [portfolio, setPortfolio] = useState<PortfolioSummary | null>(null);

  // Load config + status + perf
  useEffect(() => {
    api<Record<string, unknown>>(`/api/config?session=${sessionId}`).then((cfg) => {
      const loaded: SettingsForm = {
        display_name: (cfg.session_name as string) || sessionId,
        starting_capital: (cfg.starting_capital as number) || 0,
        max_position_pct: (cfg.max_position_pct as number) || 0.20,
        max_open_positions: (cfg.max_open_positions as number) || 5,
        daily_loss_limit_pct: (cfg.daily_loss_limit_pct as number) || 0.02,
        per_trade_loss_limit_pct: (cfg.per_trade_loss_limit_pct as number) || 0.01,
        max_trade_amount: (cfg.max_trade_amount as number) || 0,
        llm_provider: (cfg.llm_provider as string) || "openrouter",
        llm_model: (cfg.llm_model as string) || "anthropic/claude-haiku-4-5",
        api_key_env: "OPENROUTER_API_KEY",
        intraday_interval_min: (cfg.intraday_interval_min as number) || 15,
        personality: (cfg.personality as string) || "",
      };
      setForm(loaded);
      setInitial(loaded);
    });
    api<AgentStatus>(`/api/agent/status/${sessionId}`).then(setAgentStatus).catch(() => {});
    api<Performance>(`/api/performance?session=${sessionId}`).then(setPerf).catch(() => {});
    api<PortfolioSummary>(`/api/portfolio?session=${sessionId}`).then(setPortfolio).catch(() => {});
  }, [sessionId]);

  const isDirty = useMemo(
    () => JSON.stringify(form) !== JSON.stringify(initial),
    [form, initial]
  );

  // Detect hot-changes (model/interval) vs cold
  const modelChanged = form.llm_model !== initial.llm_model;
  const intervalChanged = form.intraday_interval_min !== initial.intraday_interval_min;
  const willRestartAgent = (modelChanged || intervalChanged) && agentStatus.running;

  const handleSave = async () => {
    setSaving(true);
    try {
      await api(`/api/sessions/${sessionId}`, {
        method: "PUT",
        body: JSON.stringify(form),
      });
      setInitial(form);
      toast.success(willRestartAgent ? "Settings saved — agent restarting" : "Settings saved");
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Failed to save");
    } finally {
      setSaving(false);
    }
  };

  const handleReset = () => {
    setForm(initial);
  };

  const handleDelete = async () => {
    setDeleting(true);
    try {
      await api(`/api/sessions/${sessionId}`, { method: "DELETE" });
      toast.success("Session deleted");
      router.push("/admin");
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Failed to delete");
      setDeleting(false);
      setShowDelete(false);
    }
  };

  return (
    <div className="px-4 md:px-8 py-4 md:py-6 max-w-3xl mx-auto pb-40">
      {/* Header */}
      <div className="flex items-center gap-3 mb-5">
        <Link
          href={`/admin/sessions/${sessionId}`}
          className="flex items-center justify-center rounded-xl border border-border hover:border-border-accent hover:bg-bg-card transition-all text-text-muted hover:text-text-primary shrink-0"
          style={{ width: 44, height: 44 }}
          aria-label="Back to dashboard"
        >
          ←
        </Link>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h1 className="text-lg md:text-xl font-bold tracking-tight truncate">Session Settings</h1>
            <StatusPill running={agentStatus.running} />
          </div>
          <p className="text-text-muted text-xs font-mono truncate">{sessionId}</p>
        </div>
      </div>

      {/* Warn banner if hot change + running */}
      {willRestartAgent && (
        <div className="flex items-start gap-3 p-3 rounded-xl border border-amber-500/30 bg-amber-500/10 mb-5">
          <span className="text-base" style={{ color: "#f59e0b" }}>⚠</span>
          <div className="text-xs text-amber-300/90">
            You've changed {modelChanged && "the model"}{modelChanged && intervalChanged && " and "}{intervalChanged && "the cycle interval"}. Saving will <span className="font-semibold">auto-restart the running agent</span>.
          </div>
        </div>
      )}

      {/* General */}
      <Group title="General">
        <Field label="Display Name">
          <input
            value={form.display_name}
            onChange={(e) => setForm({ ...form, display_name: e.target.value })}
            className="w-full"
          />
        </Field>
      </Group>

      {/* Agent Settings (hot - warn) */}
      <Group title="Agent Settings" warn={agentStatus.running ? "Changes restart the agent" : undefined}>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <Field label="Model" changed={modelChanged}>
            <select
              value={form.llm_model}
              onChange={(e) => setForm({ ...form, llm_model: e.target.value })}
              className="w-full text-sm"
            >
              <optgroup label="Anthropic">
                <option value="anthropic/claude-haiku-4-5">Claude Haiku 4.5 ($0.80/M)</option>
              </optgroup>
              <optgroup label="Google">
                <option value="google/gemini-2.5-flash">Gemini 2.5 Flash ($0.15/M)</option>
              </optgroup>
              <optgroup label="OpenAI">
                <option value="openai/gpt-4o-mini">GPT-4o Mini ($0.15/M)</option>
              </optgroup>
              <optgroup label="Meta">
                <option value="meta-llama/llama-4-maverick">Llama 4 Maverick ($0.20/M)</option>
                <option value="meta-llama/llama-4-scout">Llama 4 Scout ($0.10/M)</option>
              </optgroup>
              <optgroup label="DeepSeek">
                <option value="deepseek/deepseek-chat-v3-0324">DeepSeek V3 ($0.14/M)</option>
                <option value="deepseek/deepseek-r1">DeepSeek R1 ($0.55/M)</option>
              </optgroup>
            </select>
          </Field>
          <Field label="Cycle Interval (min)" changed={intervalChanged}>
            <input
              type="number"
              value={form.intraday_interval_min}
              onChange={(e) => setForm({ ...form, intraday_interval_min: parseInt(e.target.value) || 15 })}
              className="w-full font-mono"
              min={5} max={120}
            />
          </Field>
        </div>
      </Group>

      {/* Personality */}
      <Group title="Trading Personality">
        <textarea
          value={form.personality}
          onChange={(e) => setForm({ ...form, personality: e.target.value })}
          placeholder="e.g. Be aggressive. Target 2% daily returns. Focus on momentum plays."
          rows={4}
          className="w-full resize-none text-sm"
        />
        <span className="text-[10px] text-text-muted block mt-1.5">
          Injected into the LLM system prompt every cycle. Takes effect next restart.
        </span>
      </Group>

      {/* Advanced (Risk) */}
      <div className="mb-4">
        <Collapsible title="Risk Limits" subtitle="Position size, loss caps, stops" icon="🛡">
          <div className="p-4">
            <div className="grid grid-cols-2 gap-3">
              <Field label="Max Position %">
                <input
                  type="number"
                  value={(form.max_position_pct * 100).toFixed(0)}
                  onChange={(e) => setForm({ ...form, max_position_pct: (parseInt(e.target.value) || 20) / 100 })}
                  className="w-full font-mono"
                  min={5} max={50}
                />
              </Field>
              <Field label="Max Open Positions">
                <input
                  type="number"
                  value={form.max_open_positions}
                  onChange={(e) => setForm({ ...form, max_open_positions: parseInt(e.target.value) || 5 })}
                  className="w-full font-mono"
                  min={1} max={20}
                />
              </Field>
              <Field label="Daily Loss Limit %">
                <input
                  type="number"
                  value={(form.daily_loss_limit_pct * 100).toFixed(1)}
                  onChange={(e) => setForm({ ...form, daily_loss_limit_pct: (parseFloat(e.target.value) || 2) / 100 })}
                  step={0.5}
                  className="w-full font-mono"
                  min={0.5} max={10}
                />
              </Field>
              <Field label="Per-Trade Stop %">
                <input
                  type="number"
                  value={(form.per_trade_loss_limit_pct * 100).toFixed(1)}
                  onChange={(e) => setForm({ ...form, per_trade_loss_limit_pct: (parseFloat(e.target.value) || 1) / 100 })}
                  step={0.5}
                  className="w-full font-mono"
                  min={0.5} max={10}
                />
              </Field>
            </div>
          </div>
        </Collapsible>
      </div>

      {/* Danger Zone */}
      <div className="mt-6 mb-4 rounded-2xl border border-accent-red/30 bg-accent-red/5 overflow-hidden">
        <div className="px-4 py-3 border-b border-accent-red/20 flex items-center gap-2">
          <span className="text-accent-red">⚠</span>
          <h3 className="text-xs uppercase tracking-wider font-semibold text-accent-red">Danger Zone</h3>
        </div>
        <div className="p-4">
          <div className="flex items-center justify-between gap-3 flex-wrap">
            <div className="min-w-0 flex-1">
              <div className="text-sm font-semibold text-text-primary">Delete this session</div>
              <div className="text-xs text-text-muted mt-0.5">
                Permanently removes all trades, journal, logs, and config
              </div>
            </div>
            <button
              onClick={() => setShowDelete(true)}
              className="shrink-0 px-4 rounded-xl text-sm font-semibold border border-accent-red/40 text-accent-red hover:bg-accent-red/10 transition-all"
              style={{ minHeight: 40 }}
            >
              Delete Session
            </button>
          </div>
        </div>
      </div>

      {/* Sticky save bar */}
      {isDirty && (
        <div
          className="fixed bottom-0 inset-x-0 z-30 border-t border-border px-4 py-3 md:px-8 animate-fade-in"
          style={{
            background: "rgba(10,14,23,0.97)",
            backdropFilter: "blur(12px)",
            WebkitBackdropFilter: "blur(12px)",
            paddingBottom: "calc(12px + env(safe-area-inset-bottom))",
          }}
        >
          <div className="max-w-3xl mx-auto flex items-center justify-between gap-3">
            <span className="text-xs text-text-muted truncate">Unsaved changes</span>
            <div className="flex items-center gap-2 shrink-0">
              <button
                onClick={handleReset}
                disabled={saving}
                className="px-3 rounded-xl border border-border hover:border-border-accent text-sm text-text-secondary transition-all disabled:opacity-50"
                style={{ minHeight: 40 }}
              >
                Reset
              </button>
              <button
                onClick={handleSave}
                disabled={saving}
                className="px-5 rounded-xl bg-accent-blue hover:bg-accent-blue/80 text-white font-semibold text-sm transition-all disabled:opacity-50 shadow-lg shadow-accent-blue/20"
                style={{ minHeight: 40 }}
              >
                {saving ? "Saving..." : willRestartAgent ? "Save & Restart" : "Save"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Delete modal */}
      {showDelete && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center p-4"
          style={{ background: "rgba(0,0,0,0.7)" }}
          onClick={() => !deleting && setShowDelete(false)}
        >
          <div
            className="bg-bg-card border border-border rounded-2xl p-5 max-w-md w-full animate-fade-in"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center gap-3 mb-4">
              <span
                className="flex items-center justify-center rounded-xl shrink-0"
                style={{ width: 44, height: 44, background: "rgba(239,68,68,0.15)", color: "#ef4444", fontSize: 22 }}
              >
                ⚠
              </span>
              <div>
                <h3 className="font-semibold text-base">Delete session?</h3>
                <div className="text-xs text-text-muted font-mono">{sessionId}</div>
              </div>
            </div>

            <div className="rounded-xl border border-border bg-bg-secondary/50 p-3 mb-4 space-y-1.5">
              <div className="text-[10px] uppercase tracking-wider font-semibold text-text-muted mb-1.5">Will be permanently deleted</div>
              <StatRow label="Trades" value={perf?.total_trades ?? 0} />
              <StatRow label="Current portfolio" value={portfolio ? `${portfolio.total_value.toLocaleString()}` : "—"} />
              <StatRow label="Open positions" value={portfolio?.open_positions ?? 0} />
              <StatRow label="Journal & logs" value="all" />
            </div>

            <p className="text-xs text-text-muted mb-4">
              This cannot be undone. Consider stopping the agent first if it's running.
            </p>

            <div className="flex items-center justify-end gap-2">
              <button
                onClick={() => setShowDelete(false)}
                disabled={deleting}
                className="px-4 rounded-xl border border-border hover:border-border-accent text-sm text-text-secondary transition-all disabled:opacity-50"
                style={{ minHeight: 44 }}
              >
                Cancel
              </button>
              <button
                onClick={handleDelete}
                disabled={deleting}
                className="px-4 rounded-xl bg-accent-red text-white font-semibold text-sm hover:bg-accent-red/90 transition-all disabled:opacity-60"
                style={{ minHeight: 44 }}
              >
                {deleting ? "Deleting..." : "Delete Permanently"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function StatusPill({ running }: { running: boolean }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 px-2 rounded-full text-[10px] uppercase tracking-wider font-bold shrink-0",
        running ? "bg-accent-green/15 text-accent-green" : "bg-bg-secondary text-text-muted"
      )}
      style={{ paddingTop: 4, paddingBottom: 4 }}
    >
      <span
        className={cn("rounded-full", running && "animate-pulse-dot")}
        style={{ width: 6, height: 6, background: running ? "#22c55e" : "rgba(100,116,139,0.6)" }}
      />
      {running ? "Live" : "Stopped"}
    </span>
  );
}

function Group({ title, children, warn }: { title: string; children: React.ReactNode; warn?: string }) {
  return (
    <div className="mb-4">
      <div className="flex items-center justify-between mb-2 px-1">
        <h2 className="text-[11px] uppercase tracking-wider font-semibold text-text-muted">{title}</h2>
        {warn && (
          <span className="text-[10px] text-amber-400/80 flex items-center gap-1">
            <span>⚠</span>
            <span>{warn}</span>
          </span>
        )}
      </div>
      <div className="bg-bg-card border border-border rounded-xl p-4">{children}</div>
    </div>
  );
}

function Field({ label, changed, children }: { label: string; changed?: boolean; children: React.ReactNode }) {
  return (
    <div>
      <div className="flex items-center gap-1.5 mb-1.5">
        <label className="text-[11px] text-text-muted">{label}</label>
        {changed && <span className="w-1.5 h-1.5 rounded-full bg-amber-400" title="Changed" />}
      </div>
      {children}
    </div>
  );
}

function StatRow({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="flex items-center justify-between text-xs">
      <span className="text-text-muted">{label}</span>
      <span className="font-mono font-semibold text-text-primary">{value}</span>
    </div>
  );
}
