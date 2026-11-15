"use client";

import React, { useCallback, useEffect, useState } from "react";
import {
  MessageSquare,
  Plus,
  Trash2,
  Loader2,
  RefreshCw,
  Clock,
} from "lucide-react";

export type SessionRow = {
  id: string;
  title: string;
  created_at?: string | null;
  last_at?: string | null;
  data_source?: string | null;
  messages?: number;
};

/**
 * Conversation list for the left pane.
 *
 * A conversation is identified by the id the frontend generates and sends with each
 * chat turn; this pane only lists and switches them. Switching rehydrates the
 * transcript from the server, so the chat area reflects the stored conversation
 * rather than whatever was on screen.
 */
export function SessionsPanel({
  apiBase,
  dataSource,
  activeId,
  refreshKey,
  onSelect,
  onNew,
  onDelete,
}: {
  apiBase: string;
  dataSource?: string;
  activeId: string;
  /** Bumped by the parent after a turn finishes, so the list picks up new titles. */
  refreshKey: number;
  onSelect: (session: SessionRow) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
}) {
  const [sessions, setSessions] = useState<SessionRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const qs = dataSource ? `?data_source=${encodeURIComponent(dataSource)}` : "";
      const res = await fetch(`${apiBase}/api/sessions${qs}`);
      const json = await res.json();
      if (json.error) setError(String(json.error));
      setSessions(json.sessions ?? []);
    } catch (e: any) {
      setError(e?.message ?? "Could not load conversations");
    } finally {
      setLoading(false);
    }
  }, [apiBase, dataSource]);

  useEffect(() => {
    load();
  }, [load, refreshKey]);

  const remove = async (id: string) => {
    setSessions((prev) => prev.filter((s) => s.id !== id));
    try {
      await fetch(`${apiBase}/api/sessions/${id}`, { method: "DELETE" });
    } catch {
      // the optimistic removal stands; a reload will reconcile
    }
    onDelete(id);
  };

  const when = (s: SessionRow) => {
    const raw = s.last_at || s.created_at;
    if (!raw) return "";
    const d = new Date(raw);
    if (Number.isNaN(d.getTime())) return "";
    const mins = Math.round((Date.now() - d.getTime()) / 60000);
    if (mins < 1) return "just now";
    if (mins < 60) return `${mins}m ago`;
    const hours = Math.round(mins / 60);
    if (hours < 24) return `${hours}h ago`;
    const days = Math.round(hours / 24);
    if (days < 7) return `${days}d ago`;
    return d.toLocaleDateString();
  };

  return (
    <div className="flex flex-col h-full min-h-0">
      <div className="px-4 py-3 border-b border-white/10 bg-[#121212] shrink-0 flex items-center justify-between">
        <div className="flex items-center gap-2 min-w-0">
          <MessageSquare className="w-4 h-4 text-indigo-400 shrink-0" />
          <div className="min-w-0">
            <h2 className="text-sm font-bold text-gray-200 uppercase tracking-wider">
              Conversations
            </h2>
            <p className="text-[10px] text-gray-500 truncate">
              {sessions.length ? `${sessions.length} saved` : "none yet"}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-1">
          <button
            onClick={load}
            title="Refresh"
            className="p-1.5 rounded-lg text-gray-500 hover:text-gray-200 hover:bg-white/5 transition-all"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} />
          </button>
          <button
            onClick={onNew}
            title="New conversation"
            className="p-1.5 rounded-lg text-indigo-400 hover:text-indigo-200 hover:bg-indigo-500/15 transition-all"
          >
            <Plus className="w-4 h-4" />
          </button>
        </div>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto">
        {error && (
          <p className="px-4 py-3 text-[11px] text-red-400/90 break-words">{error}</p>
        )}

        {!error && !loading && sessions.length === 0 && (
          <div className="py-10 px-4 text-center space-y-2">
            <MessageSquare className="w-7 h-7 text-gray-700 mx-auto" />
            <p className="text-xs text-gray-500">No conversations yet</p>
            <p className="text-[10px] text-gray-600 leading-relaxed">
              Ask something and it appears here, so you can come back to it later.
            </p>
          </div>
        )}

        <ul className="py-1">
          {sessions.map((s) => {
            const active = s.id === activeId;
            return (
              <li key={s.id}>
                <div
                  className={`group flex items-start gap-2 px-3 py-2.5 cursor-pointer border-l-2 transition-colors ${
                    active
                      ? "border-indigo-500 bg-indigo-500/10"
                      : "border-transparent hover:bg-white/[0.04]"
                  }`}
                  onClick={() => onSelect(s)}
                >
                  <div className="min-w-0 flex-1">
                    <p
                      className={`text-[12px] leading-snug truncate ${
                        active ? "text-gray-100" : "text-gray-300"
                      }`}
                      title={s.title}
                    >
                      {s.title}
                    </p>
                    <p className="text-[10px] text-gray-600 flex items-center gap-1.5 mt-0.5">
                      <Clock className="w-2.5 h-2.5" />
                      <span>{when(s)}</span>
                      {typeof s.messages === "number" && s.messages > 0 && (
                        <span className="text-gray-700">· {s.messages} msg</span>
                      )}
                    </p>
                  </div>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      remove(s.id);
                    }}
                    title="Delete this conversation"
                    className="opacity-0 group-hover:opacity-100 p-1 rounded text-gray-600 hover:text-red-400 hover:bg-red-500/10 transition-all shrink-0"
                  >
                    <Trash2 className="w-3 h-3" />
                  </button>
                </div>
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}
