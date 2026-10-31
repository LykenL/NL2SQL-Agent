"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  Activity,
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  Clock,
  Database,
  Loader2,
  RefreshCw,
  ShieldCheck,
  XCircle,
} from "lucide-react";

// Same runtime resolution as the chat page: never trust a build-time env value.
const DEFAULT_API = "https://nl2sql-agent-0hwl.onrender.com";

function resolveApiBase(): string {
  const envUrl = process.env.NEXT_PUBLIC_API_URL?.trim();
  if (envUrl) {
    const isLocalEnv = /^https?:\/\/(localhost|127\.0\.0\.1|\[::1\])(:|$)/i.test(envUrl);
    const isLocalPage =
      typeof window !== "undefined" &&
      /^(localhost|127\.0\.0\.1|\[::1\])$/i.test(window.location.hostname);
    if (!isLocalEnv || isLocalPage) return envUrl.replace(/\/+$/, "");
  }
  if (
    typeof window !== "undefined" &&
    /^(localhost|127\.0\.0\.1|\[::1\])$/i.test(window.location.hostname)
  ) {
    return "http://localhost:8000";
  }
  return DEFAULT_API;
}

type Mart = {
  table_name: string;
  last_loaded: string;
  row_count: number;
  age_hours: number;
};

type DQ = {
  counts: Record<string, number>;
  failing: { table_name: string; check_name: string; message: string }[];
  last_run: string | null;
};

type Health = {
  status: "ok" | "stale" | "down" | "unconfigured";
  project: string;
  dataset: string;
  marts: Mart[];
  dq: DQ;
  oldest_age_hours: number | null;
  message?: string;
  hint?: string;
};

const STATUS_STYLE = {
  ok: { ring: "border-emerald-500/30 bg-emerald-500/5", text: "text-emerald-300", label: "Healthy" },
  stale: { ring: "border-amber-500/30 bg-amber-500/5", text: "text-amber-300", label: "Stale" },
  down: { ring: "border-rose-500/30 bg-rose-500/5", text: "text-rose-300", label: "Blocked" },
  unconfigured: { ring: "border-white/10 bg-white/[0.02]", text: "text-gray-400", label: "Not connected" },
} as const;

export default function DataHealthPage() {
  const apiBase = resolveApiBase();
  const [health, setHealth] = useState<Health | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const res = await fetch(`${apiBase}/api/health/data`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setHealth(await res.json());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load data health");
    } finally {
      setLoading(false);
    }
  }, [apiBase]);

  useEffect(() => {
    load();
    const id = setInterval(load, 60_000);
    return () => clearInterval(id);
  }, [load]);

  const style = STATUS_STYLE[health?.status ?? "unconfigured"];
  const failing = health?.dq.failing.length ?? 0;
  const passed = health?.dq.counts.pass ?? 0;
  const totalChecks = passed + failing + (health?.dq.counts.warn ?? 0);

  return (
    <div className="min-h-screen bg-[#050505] text-gray-100 font-sans">
      <header className="h-16 border-b border-white/10 bg-[#0a0a0a]/90 backdrop-blur-md flex items-center justify-between px-6 sticky top-0 z-40">
        <div className="flex items-center gap-3">
          <Link
            href="/"
            className="flex items-center gap-2 text-xs text-gray-400 hover:text-gray-200 px-2.5 py-1.5 rounded-lg hover:bg-white/5"
          >
            <ArrowLeft className="w-3.5 h-3.5" /> Copilot
          </Link>
          <div className="h-5 w-px bg-white/10" />
          <Activity className="w-4 h-4 text-indigo-400" />
          <h1 className="text-sm font-bold tracking-tight text-gray-100">Data Health</h1>
        </div>
        <div className="flex items-center gap-3">
          <span className={`text-[11px] px-2.5 py-1 rounded-full border ${style.ring} ${style.text}`}>
            {style.label}
          </span>
          <button
            onClick={load}
            className="flex items-center gap-1.5 text-[11px] px-2.5 py-1.5 rounded-lg bg-white/5 border border-white/10 text-gray-300 hover:bg-white/10"
          >
            <RefreshCw className={`w-3 h-3 ${loading ? "animate-spin" : ""}`} /> Refresh
          </button>
        </div>
      </header>

      <main className="max-w-6xl mx-auto px-6 py-8 space-y-6">
        {error && (
          <div className="rounded-xl border border-rose-500/30 bg-rose-500/5 px-4 py-3 text-xs text-rose-300">
            {error}
          </div>
        )}

        {health?.status === "unconfigured" && (
          <div className="rounded-xl border border-amber-500/30 bg-amber-500/5 px-4 py-4 text-xs text-amber-200 space-y-1.5">
            <div className="flex items-center gap-2 font-semibold">
              <AlertTriangle className="w-4 h-4" /> 后端还没连上 BigQuery
            </div>
            <p className="text-amber-200/80">{health.message}</p>
            <p className="text-amber-200/60">{health.hint}</p>
          </div>
        )}

        {health?.status === "down" && (
          <div className="rounded-xl border border-rose-500/30 bg-rose-500/5 px-4 py-4 text-xs text-rose-200 space-y-1">
            <div className="flex items-center gap-2 font-semibold">
              <XCircle className="w-4 h-4" /> {failing} 项数据质量检查未通过 —— gold 刷新已被阻断
            </div>
            <p className="text-rose-200/70">
              下游的 copilot 不应被信任，直到测试恢复。
            </p>
          </div>
        )}

        {/* KPI cards */}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <div className="rounded-2xl border border-white/10 bg-[#101014] px-5 py-4">
            <div className="flex items-center gap-2 text-[11px] uppercase tracking-wide text-gray-500">
              <Database className="w-3.5 h-3.5" /> Published marts
            </div>
            <p className="text-2xl font-bold text-gray-100 mt-2">{health?.marts.length ?? "—"}</p>
            <p className="text-[11px] text-gray-600 mt-1 truncate">{health?.dataset}</p>
          </div>

          <div className="rounded-2xl border border-white/10 bg-[#101014] px-5 py-4">
            <div className="flex items-center gap-2 text-[11px] uppercase tracking-wide text-gray-500">
              <Clock className="w-3.5 h-3.5" /> Oldest refresh
            </div>
            <p className="text-2xl font-bold text-gray-100 mt-2">
              {health?.oldest_age_hours != null ? `${health.oldest_age_hours.toFixed(1)}h` : "—"}
            </p>
            <p className="text-[11px] text-gray-600 mt-1">超过 26 小时标记为 stale</p>
          </div>

          <div className="rounded-2xl border border-white/10 bg-[#101014] px-5 py-4">
            <div className="flex items-center gap-2 text-[11px] uppercase tracking-wide text-gray-500">
              <ShieldCheck className="w-3.5 h-3.5" /> DQ checks
            </div>
            <p className="text-2xl font-bold mt-2">
              <span className="text-emerald-300">{passed}</span>
              <span className="text-gray-600 text-base"> / </span>
              <span className={failing ? "text-rose-300" : "text-gray-200"}>{totalChecks || "—"}</span>
            </p>
            <p className="text-[11px] text-gray-600 mt-1">
              {health?.dq.last_run ? `最近一次运行 ${health.dq.last_run.slice(0, 19)}` : "尚无运行记录"}
            </p>
          </div>
        </div>

        {/* Marts table */}
        <div className="rounded-2xl border border-white/10 bg-[#101014] overflow-hidden">
          <div className="px-5 py-3.5 border-b border-white/10 flex items-center justify-between">
            <h2 className="text-xs font-semibold text-gray-300">Gold marts</h2>
            <span className="text-[10px] text-gray-600">refresh 由管道写入 meta.mart_freshness</span>
          </div>
          <table className="w-full text-xs">
            <thead className="text-[10px] uppercase tracking-wide text-gray-600">
              <tr className="border-b border-white/5">
                <th className="text-left font-medium px-5 py-2.5">Table</th>
                <th className="text-right font-medium px-5 py-2.5">Rows</th>
                <th className="text-right font-medium px-5 py-2.5">Age</th>
                <th className="text-left font-medium px-5 py-2.5">Last loaded</th>
              </tr>
            </thead>
            <tbody>
              {loading && !health?.marts.length && (
                <tr>
                  <td colSpan={4} className="px-5 py-8 text-center text-gray-500">
                    <Loader2 className="w-4 h-4 animate-spin inline mr-2" /> Loading…
                  </td>
                </tr>
              )}
              {!loading && !health?.marts.length && (
                <tr>
                  <td colSpan={4} className="px-5 py-8 text-center text-gray-600">
                    没有可显示的 mart（管道尚未运行，或后端未连接）
                  </td>
                </tr>
              )}
              {health?.marts.map((m) => {
                const stale = (m.age_hours ?? 0) > 26;
                return (
                  <tr key={m.table_name} className="border-b border-white/5 last:border-0">
                    <td className="px-5 py-3 font-mono text-[11px] text-indigo-200">{m.table_name}</td>
                    <td className="px-5 py-3 text-right text-gray-300">{m.row_count.toLocaleString()}</td>
                    <td className={`px-5 py-3 text-right ${stale ? "text-amber-300" : "text-gray-400"}`}>
                      {m.age_hours != null ? `${m.age_hours.toFixed(1)}h` : "—"}
                    </td>
                    <td className="px-5 py-3 text-gray-500">{String(m.last_loaded).slice(0, 19)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        {/* Failing checks */}
        <div className="rounded-2xl border border-white/10 bg-[#101014] overflow-hidden">
          <div className="px-5 py-3.5 border-b border-white/10 flex items-center gap-2">
            <h2 className="text-xs font-semibold text-gray-300">Failing checks</h2>
            {failing > 0 ? (
              <span className="text-[10px] px-2 py-0.5 rounded-full bg-rose-500/10 border border-rose-500/30 text-rose-300">
                {failing}
              </span>
            ) : (
              <span className="text-[10px] px-2 py-0.5 rounded-full bg-emerald-500/10 border border-emerald-500/30 text-emerald-300 flex items-center gap-1">
                <CheckCircle2 className="w-2.5 h-2.5" /> all clear
              </span>
            )}
          </div>
          {health?.dq.failing.length ? (
            <ul className="divide-y divide-white/5">
              {health.dq.failing.map((f, i) => (
                <li key={i} className="px-5 py-3 text-[11px]">
                  <span className="text-rose-300 font-mono">{f.check_name}</span>
                  <span className="text-gray-500"> · {f.table_name}</span>
                  <p className="text-gray-600 mt-0.5">{f.message || "(no message)"}</p>
                </li>
              ))}
            </ul>
          ) : (
            <p className="px-5 py-6 text-[11px] text-gray-600">
              最近一次运行没有失败项。
            </p>
          )}
        </div>

        <p className="text-[10px] text-gray-700">
          source: {health?.project} · {health?.dataset} · 自动每 60s 刷新
        </p>
      </main>
    </div>
  );
}
