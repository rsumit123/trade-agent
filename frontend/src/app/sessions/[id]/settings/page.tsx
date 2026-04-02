"use client";
import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { api } from "@/lib/api";

export default function SessionSettingsPage() {
  const params = useParams();
  const router = useRouter();
  const sessionId = params.id as string;

  const [form, setForm] = useState({
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
  });
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [showDelete, setShowDelete] = useState(false);

  useEffect(() => {
    api<Record<string, unknown>>(`/api/config?session=${sessionId}`).then((cfg) => {
      setForm({
        display_name: (cfg.session_name as string) || sessionId,
        starting_capital: cfg.starting_capital as number || 0,
        max_position_pct: cfg.max_position_pct as number || 0.20,
        max_open_positions: cfg.max_open_positions as number || 5,
        daily_loss_limit_pct: cfg.daily_loss_limit_pct as number || 0.02,
        per_trade_loss_limit_pct: cfg.per_trade_loss_limit_pct as number || 0.01,
        max_trade_amount: cfg.max_trade_amount as number || 0,
        llm_provider: cfg.llm_provider as string || "openrouter",
        llm_model: "",
        api_key_env: "OPENROUTER_API_KEY",
        intraday_interval_min: cfg.intraday_interval_min as number || 15,
        personality: "",
      });
    });
  }, [sessionId]);

  const handleSave = async () => {
    setSaving(true);
    setMessage("");
    try {
      await api(`/api/sessions/${sessionId}`, {
        method: "PUT",
        body: JSON.stringify(form),
      });
      setMessage("Settings saved successfully!");
    } catch (err: unknown) {
      setMessage(err instanceof Error ? err.message : "Failed to save");
    }
    setSaving(false);
  };

  const handleDelete = async () => {
    try {
      await api(`/api/sessions/${sessionId}`, { method: "DELETE" });
      router.push("/");
    } catch (err: unknown) {
      setMessage(err instanceof Error ? err.message : "Failed to delete");
      setShowDelete(false);
    }
  };

  return (
    <div className="px-4 md:px-8 py-4 md:py-8 max-w-3xl mx-auto">
      <div className="flex items-center gap-4 mb-6">
        <Link
          href={`/sessions/${sessionId}`}
          className="flex items-center justify-center rounded-xl border border-border hover:border-border-accent hover:bg-bg-card transition-all text-text-muted hover:text-text-primary text-sm"
          style={{ minHeight: 44, paddingLeft: 12, paddingRight: 12 }}
        >
          &larr; Back
        </Link>
        <div>
          <h1 className="text-xl font-bold tracking-tight">Session Settings</h1>
          <p className="text-text-muted text-xs">{sessionId}</p>
        </div>
      </div>

      <div className="space-y-6">
        {/* General */}
        <SettingsSection title="General">
          <Field label="Display Name">
            <input value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} className="w-full" />
          </Field>
          <Field label="Starting Capital">
            <input type="number" value={form.starting_capital} onChange={(e) => setForm({ ...form, starting_capital: parseFloat(e.target.value) || 0 })} className="w-full font-mono" />
          </Field>
        </SettingsSection>

        {/* Risk */}
        <SettingsSection title="Risk Limits">
          <div className="grid grid-cols-2 gap-4">
            <Field label="Max Position %">
              <input type="number" value={(form.max_position_pct * 100).toFixed(0)} onChange={(e) => setForm({ ...form, max_position_pct: (parseInt(e.target.value) || 20) / 100 })} className="w-full font-mono" />
            </Field>
            <Field label="Max Open Positions">
              <input type="number" value={form.max_open_positions} onChange={(e) => setForm({ ...form, max_open_positions: parseInt(e.target.value) || 5 })} className="w-full font-mono" />
            </Field>
            <Field label="Daily Loss Limit %">
              <input type="number" value={(form.daily_loss_limit_pct * 100).toFixed(1)} onChange={(e) => setForm({ ...form, daily_loss_limit_pct: (parseFloat(e.target.value) || 2) / 100 })} step={0.5} className="w-full font-mono" />
            </Field>
            <Field label="Per-Trade Stop %">
              <input type="number" value={(form.per_trade_loss_limit_pct * 100).toFixed(1)} onChange={(e) => setForm({ ...form, per_trade_loss_limit_pct: (parseFloat(e.target.value) || 1) / 100 })} step={0.5} className="w-full font-mono" />
            </Field>
          </div>
        </SettingsSection>

        {/* LLM */}
        <SettingsSection title="LLM Configuration">
          <div className="grid grid-cols-2 gap-4">
            <Field label="Provider">
              <select value={form.llm_provider} onChange={(e) => setForm({ ...form, llm_provider: e.target.value })} className="w-full">
                <option value="openrouter">OpenRouter</option>
                <option value="anthropic">Anthropic</option>
                <option value="openai">OpenAI</option>
              </select>
            </Field>
            <Field label="Cycle Interval (min)">
              <input type="number" value={form.intraday_interval_min} onChange={(e) => setForm({ ...form, intraday_interval_min: parseInt(e.target.value) || 15 })} className="w-full font-mono" />
            </Field>
          </div>
        </SettingsSection>

        {/* Personality */}
        <SettingsSection title="Trading Personality">
          <textarea
            value={form.personality}
            onChange={(e) => setForm({ ...form, personality: e.target.value })}
            placeholder="Custom instructions for the LLM..."
            rows={4}
            className="w-full resize-none"
          />
        </SettingsSection>
      </div>

      {/* Actions */}
      {message && (
        <div className={`mt-4 p-3 rounded-lg text-sm ${message.includes("success") ? "bg-accent-green/10 text-accent-green border border-accent-green/30" : "bg-accent-red/10 text-accent-red border border-accent-red/30"}`}>
          {message}
        </div>
      )}

      <div className="flex items-center justify-between mt-6 mb-8">
        <button
          onClick={() => setShowDelete(true)}
          className="text-sm font-medium text-accent-red border border-accent-red/40 rounded-xl hover:bg-accent-red/10 transition-all"
          style={{ minHeight: 48, paddingLeft: 20, paddingRight: 20 }}
        >
          Delete Session
        </button>
        <button
          onClick={handleSave}
          disabled={saving}
          className="bg-accent-blue hover:bg-accent-blue/80 text-white rounded-xl font-semibold text-sm transition-all disabled:opacity-50"
          style={{ minHeight: 48, paddingLeft: 24, paddingRight: 24 }}
        >
          {saving ? "Saving..." : "Save Settings"}
        </button>
      </div>

      {/* Delete Confirmation Modal */}
      {showDelete && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={() => setShowDelete(false)}>
          <div className="bg-bg-card border border-border rounded-xl p-6 max-w-md mx-4" onClick={(e) => e.stopPropagation()}>
            <h3 className="font-semibold text-lg mb-2">Delete Session?</h3>
            <p className="text-text-secondary text-sm mb-6">
              This will permanently delete <strong>{sessionId}</strong> and all its data (trades, journal, logs). This cannot be undone.
            </p>
            <div className="flex gap-3 justify-end">
              <button onClick={() => setShowDelete(false)} className="px-5 text-sm border border-border rounded-xl hover:bg-bg-card-hover transition-all" style={{ minHeight: 44 }}>
                Cancel
              </button>
              <button onClick={handleDelete} className="px-5 text-sm bg-accent-red text-white rounded-xl hover:bg-accent-red-dim transition-all" style={{ minHeight: 44 }}>
                Delete Permanently
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function SettingsSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="bg-bg-card border border-border rounded-xl overflow-hidden">
      <div className="px-5 py-3 border-b border-border">
        <h3 className="text-xs uppercase tracking-wider font-semibold text-text-secondary">{title}</h3>
      </div>
      <div className="p-5">{children}</div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="block text-xs text-text-muted mb-1.5">{label}</label>
      {children}
    </div>
  );
}
