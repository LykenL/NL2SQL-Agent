"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { BadgeCheck, BookOpen, Loader2, RefreshCw, Search, ShieldAlert, Trash2, X } from "lucide-react";

// ─── Types ───────────────────────────────────────────────────────────────────
export type MetricDef = {
  id: string;
  display_name: string;
  expr: string;
  aliases: string[];
  grain: string;
  filters: string;
  forbidden_columns: string[];
  status: "draft" | "confirmed" | "stale";
  owner: string;
  confirmed_by: string;
  confirmed_at: string;
  evidence_query_id: string;
  score?: number;
};

type Props = {
  apiBase: string;
  open: boolean;
  onClose: () => void;
  /** Notify the chat pane that definitions changed (e.g. to refresh badges). */
  onChanged?: () => void;
};

const STATUS_STYLE: Record<MetricDef["status"], string> = {
  confirmed: "bg-emerald-500/10 border-emerald-500/30 text-emerald-300",
  draft: "bg-amber-500/10 border-amber-500/30 text-amber-300",
  stale: "bg-white/5 border-white/10 text-gray-500",
};

export function DefinitionsPanel({ apiBase, open, onClose, onChanged }: Props) {
  const [metrics, setMetrics] = useState<MetricDef[]>([]);
  const [loading, setLoading] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [matches, setMatches] = useState<MetricDef[] | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const res = await fetch(`${apiBase}/api/metrics`);
      if (res.status === 404) {
        throw new Error(
          `This API build has no /api/metrics (HTTP 404) — the backend at ${apiBase} predates the semantic layer. Redeploy it, then reload.`
        );
      }
      if (!res.ok) throw new Error(`HTTP ${res.status} from ${apiBase}/api/metrics`);
      const data = await res.json();
      setMetrics(data.metrics || []);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load definitions");
    } finally {
      setLoading(false);
    }
  }, [apiBase]);

  useEffect(() => {
    if (open) load();
  }, [open, load]);

  const act = async (id: string, action: "confirm" | "stale") => {
    setBusyId(id);
    setError("");
    try {
      const url =
        action === "confirm"
          ? `${apiBase}/api/metrics/${id}/confirm?by=ui&evidence_query_id=`
          : `${apiBase}/api/metrics/${id}/stale`;
      const res = await fetch(url, { method: "POST" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      await load();
      onChanged?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Action failed");
    } finally {
      setBusyId(null);
    }
  };

  const seed = async () => {
    setLoading(true);
    try {
      await fetch(`${apiBase}/api/metrics/seed`, { method: "POST" });
      await load();
      onChanged?.();
    } finally {
      setLoading(false);
    }
  };

  const testQuestion = async () => {
    if (!query.trim()) return;
    setLoading(true);
    setError("");
    try {
      const res = await fetch(`${apiBase}/api/metrics/resolve?q=${encodeURIComponent(query)}`);
      const data = await res.json();
      setMatches(data.matches || []);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Resolve failed");
    } finally {
      setLoading(false);
    }
  };

  const confirmedCount = useMemo(
    () => metrics.filter((m) => m.status === "confirmed").length,
    [metrics]
  );

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/60 backdrop-blur-sm" onClick={onClose}>
      <aside
        className="w-full max-w-[520px] h-full bg-[#0a0a0a] border-l border-white/10 shadow-2xl flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="px-5 py-4 border-b border-white/10 flex items-center justify-between shrink-0">
          <div className="flex items-center gap-2.5">
            <BookOpen className="w-4 h-4 text-indigo-400" />
            <div>
              <h2 className="text-sm font-bold text-gray-100">Approved Definitions</h2>
              <p className="text-[11px] text-gray-500">
                {confirmedCount} enforced · {metrics.length} total
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={load}
              className="p-1.5 rounded-lg text-gray-500 hover:text-gray-200 hover:bg-white/5"
              title="Reload"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} />
            </button>
            <button onClick={onClose} className="p-1.5 rounded-lg text-gray-500 hover:text-gray-200 hover:bg-white/5">
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* Resolve tester */}
        <div className="px-5 py-3 border-b border-white/10 shrink-0 space-y-2">
          <label className="text-[11px] uppercase tracking-wide text-gray-500">Which definitions apply to a question?</label>
          <div className="flex gap-2">
            <div className="relative flex-1">
              <Search className="w-3.5 h-3.5 text-gray-600 absolute left-2.5 top-1/2 -translate-y-1/2" />
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && testQuestion()}
                placeholder="今年的营收是多少…"
                className="w-full bg-black/40 border border-white/10 rounded-lg pl-8 pr-3 py-2 text-xs text-gray-200 placeholder:text-gray-600 focus:outline-none focus:border-indigo-500/50"
              />
            </div>
            <button
              onClick={testQuestion}
              className="px-3 py-2 rounded-lg bg-indigo-600/20 border border-indigo-500/30 text-indigo-300 text-xs font-medium hover:bg-indigo-600/30"
            >
              Resolve
            </button>
          </div>
          {matches !== null && (
            <div className="text-[11px] text-gray-400">
              {matches.length === 0 ? (
                <span className="text-gray-600">No definition matched — the agent would infer on its own.</span>
              ) : (
                matches.map((m) => (
                  <span key={m.id} className="inline-flex items-center gap-1 mr-2 mb-1 px-2 py-0.5 rounded-md bg-white/5 border border-white/10">
                    <span className="text-gray-200">{m.display_name}</span>
                    <span className="text-gray-500">{m.score?.toFixed(2)}</span>
                  </span>
                ))
              )}
            </div>
          )}
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-3">
          {loading && metrics.length === 0 && (
            <div className="flex items-center gap-2 text-xs text-gray-500 py-6 justify-center">
              <Loader2 className="w-3.5 h-3.5 animate-spin" /> Loading…
            </div>
          )}

          {!loading && metrics.length === 0 && (
            <div className="text-center py-8 space-y-3">
              <p className="text-xs text-gray-500">No definitions yet.</p>
              <button
                onClick={seed}
                className="px-3 py-2 rounded-lg bg-white/5 border border-white/10 text-xs text-gray-300 hover:bg-white/10"
              >
                Seed the Chinook examples
              </button>
            </div>
          )}

          {metrics.map((m) => (
            <div key={m.id} className="rounded-xl border border-white/10 bg-[#101014] p-3.5 space-y-2">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-semibold text-gray-100">{m.display_name}</span>
                    <span className="text-[10px] text-gray-500 font-mono">{m.id}</span>
                  </div>
                  <code className="text-[11px] text-indigo-300 break-all">{m.expr}</code>
                </div>
                <span className={`shrink-0 text-[10px] px-2 py-0.5 rounded-full border ${STATUS_STYLE[m.status]}`}>
                  {m.status}
                </span>
              </div>

              {(m.grain || m.filters) && (
                <p className="text-[11px] text-gray-500">
                  {m.grain && <>grain: {m.grain}</>}
                  {m.grain && m.filters && " · "}
                  {m.filters && <>filters: {m.filters}</>}
                </p>
              )}

              {m.forbidden_columns.length > 0 && (
                <p className="text-[11px] text-rose-300/80 flex items-center gap-1">
                  <ShieldAlert className="w-3 h-3" /> blocked alternatives: {m.forbidden_columns.join(", ")}
                </p>
              )}

              {m.confirmed_by && (
                <p className="text-[11px] text-emerald-300/80 flex items-center gap-1">
                  <BadgeCheck className="w-3 h-3" /> {m.confirmed_by} · {(m.confirmed_at || "").slice(0, 10)}
                  {m.evidence_query_id && <span className="text-gray-500">· evidence {m.evidence_query_id}</span>}
                </p>
              )}

              <div className="flex items-center gap-2 pt-1">
                {m.status !== "confirmed" && (
                  <button
                    onClick={() => act(m.id, "confirm")}
                    disabled={busyId === m.id}
                    className="text-[11px] px-2.5 py-1 rounded-md bg-emerald-500/10 border border-emerald-500/30 text-emerald-300 hover:bg-emerald-500/20 disabled:opacity-50"
                  >
                    {busyId === m.id ? "…" : "Confirm & enforce"}
                  </button>
                )}
                {m.status === "confirmed" && (
                  <button
                    onClick={() => act(m.id, "stale")}
                    disabled={busyId === m.id}
                    className="text-[11px] px-2.5 py-1 rounded-md bg-white/5 border border-white/10 text-gray-400 hover:bg-white/10 disabled:opacity-50"
                  >
                    <Trash2 className="w-3 h-3 inline mr-1" />
                    Mark stale
                  </button>
                )}
                <span className="text-[10px] text-gray-600 ml-auto">
                  {m.status === "confirmed" ? "enforced before every run" : "advisory only"}
                </span>
              </div>
            </div>
          ))}

          {error && (
            <p className="text-[11px] text-rose-400 bg-rose-500/10 border border-rose-500/20 rounded-lg px-3 py-2">
              {error}
            </p>
          )}
        </div>

        <div className="px-5 py-3 border-t border-white/10 text-[10px] text-gray-600 shrink-0">
          Confirmed definitions are injected into the prompt and checked before code runs. Drafts are advisory.
        </div>
      </aside>
    </div>
  );
}
