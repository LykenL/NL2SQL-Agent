"use client";

import React, { useState, useEffect, useRef, useCallback } from "react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Textarea } from "@/components/ui/textarea";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { DefinitionsPanel, type MetricDef } from "@/components/definitions-panel";
import {
  Terminal,
  Code2,
  Play,
  Database,
  Send,
  Sparkles,
  LayoutDashboard,
  Key,
  Upload,
  Link2,
  CheckCircle2,
  XCircle,
  Loader2,
  ChevronRight,
  HardDrive,
  X,
  GripVertical,
  BookOpen,
  ShieldAlert,
} from "lucide-react";
import {
  Panel,
  Group as PanelGroup,
  Separator as PanelResizeHandle,
} from "react-resizable-panels";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { vscDarkPlus } from "react-syntax-highlighter/dist/esm/styles/prism";

// ─── Constants ───────────────────────────────────────────────────────────────
// Resolve the API base at RUNTIME from the browser's own hostname, never from
// build-time NODE_ENV. A NEXT_PUBLIC_* var is statically inlined at build time,
// so a stray localhost value in the Vercel dashboard silently ships to real
// users. Runtime detection makes that class of misconfiguration impossible.
const DEFAULT_API = "https://nl2sql-agent-0hwl.onrender.com";

function resolveApiBase(): string {
  // 1. Explicit override wins — but never let a localhost value escape into a
  //    non-local page, which is exactly the bug this guards against.
  const envUrl = process.env.NEXT_PUBLIC_API_URL?.trim();
  if (envUrl) {
    const isLocalEnv =
      /^https?:\/\/(localhost|127\.0\.0\.1|\[::1\])(:|\/|$)/i.test(envUrl);
    const isLocalPage =
      typeof window !== "undefined" &&
      /^(localhost|127\.0\.0\.1|\[::1\])$/i.test(window.location.hostname);
    if (!isLocalEnv || isLocalPage) return envUrl.replace(/\/+$/, "");
  }
  // 2. Running on this machine → local backend.
  if (
    typeof window !== "undefined" &&
    /^(localhost|127\.0\.0\.1|\[::1\])$/i.test(window.location.hostname)
  ) {
    return "http://localhost:8000";
  }
  // 3. Deployed → the hosted backend.
  return DEFAULT_API;
}

const API_BASE = resolveApiBase();

type ConnStatus = "none" | "connecting" | "ok" | "error";

// ─── DB Connect Modal ─────────────────────────────────────────────────────────
function DbConnectModal({
  onConnect,
  onClose,
  onSkip,
}: {
  onConnect: (uri: string, label: string) => void;
  onClose: () => void;
  onSkip: () => void;
}) {
  const [tab, setTab] = useState<"upload" | "url">("upload");
  const [uriInput, setUriInput] = useState("");
  const [status, setStatus] = useState<ConnStatus>("none");
  const [errorMsg, setErrorMsg] = useState("");
  const [isDragging, setIsDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // ── URL connect ──────────────────────────────────────────────────────────
  const handleUriConnect = async () => {
    if (!uriInput.trim()) return;
    setStatus("connecting");
    setErrorMsg("");
    try {
      const res = await fetch(
        `${API_BASE}/api/schema?db_uri=${encodeURIComponent(uriInput.trim())}`
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (!data.schema) throw new Error("Empty schema returned");
      setStatus("ok");
      // Short label: last segment of URI
      const label = uriInput.trim().split("/").pop() || uriInput.trim();
      setTimeout(() => onConnect(uriInput.trim(), label), 600);
    } catch (e: any) {
      setStatus("error");
      setErrorMsg(e.message || "Connection failed");
    }
  };

  // ── File upload ──────────────────────────────────────────────────────────
  const handleFile = useCallback(
    async (file: File) => {
      setStatus("connecting");
      setErrorMsg("");
      const form = new FormData();
      form.append("file", file);
      try {
        const res = await fetch(`${API_BASE}/api/upload`, {
          method: "POST",
          body: form,
        });
        if (!res.ok) {
          const err = await res.json().catch(() => ({ detail: "Upload failed" }));
          throw new Error(err.detail || "Upload failed");
        }
        const data = await res.json();
        setStatus("ok");
        setTimeout(() => onConnect(data.db_uri, data.filename), 600);
      } catch (e: any) {
        setStatus("error");
        setErrorMsg(e.message || "Upload failed");
      }
    },
    [onConnect]
  );

  const onDrop = useCallback(
    (e: React.DragEvent<HTMLDivElement>) => {
      e.preventDefault();
      setIsDragging(false);
      const file = e.dataTransfer.files?.[0];
      if (file) handleFile(file);
    },
    [handleFile]
  );

  const onFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) handleFile(file);
  };

  const isLoading = status === "connecting";

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm">
      <div className="relative w-full max-w-lg mx-4 bg-[#111] border border-white/10 rounded-2xl shadow-2xl shadow-black/60 overflow-hidden">
        {/* Close button */}
        <button
          onClick={onClose}
          className="absolute top-4 right-4 text-gray-500 hover:text-white transition-colors z-10"
          aria-label="Close"
        >
          <X className="w-5 h-5" />
        </button>

        {/* Header */}
        <div className="px-6 pt-6 pb-4 border-b border-white/10">
          <div className="flex items-center gap-3 mb-1">
            <div className="w-8 h-8 rounded-lg bg-indigo-500/20 border border-indigo-500/30 flex items-center justify-center">
              <Database className="w-4 h-4 text-indigo-400" />
            </div>
            <h2 className="text-lg font-bold text-gray-100">Connect a Database</h2>
          </div>
          <p className="text-xs text-gray-500 ml-11">
            Upload a local file or paste a connection URI.
          </p>
        </div>

        {/* Tabs */}
        <div className="flex border-b border-white/10">
          {(["upload", "url"] as const).map((t) => (
            <button
              key={t}
              onClick={() => { setTab(t); setStatus("none"); setErrorMsg(""); }}
              className={`flex-1 py-3 text-sm font-semibold transition-colors flex items-center justify-center gap-2 ${
                tab === t
                  ? "text-indigo-300 border-b-2 border-indigo-500"
                  : "text-gray-500 hover:text-gray-300"
              }`}
            >
              {t === "upload" ? (
                <><Upload className="w-4 h-4" /> Upload File</>
              ) : (
                <><Link2 className="w-4 h-4" /> Database URI</>
              )}
            </button>
          ))}
        </div>

        {/* Body */}
        <div className="p-6 space-y-5">
          {tab === "upload" && (
            <div
              onClick={() => fileInputRef.current?.click()}
              onDragOver={(e) => { e.preventDefault(); setIsDragging(true); }}
              onDragLeave={() => setIsDragging(false)}
              onDrop={onDrop}
              className={`relative flex flex-col items-center justify-center gap-4 rounded-xl border-2 border-dashed p-10 cursor-pointer transition-all ${
                isDragging
                  ? "border-indigo-500 bg-indigo-500/10"
                  : "border-white/15 bg-white/[0.02] hover:border-indigo-500/50 hover:bg-indigo-500/5"
              }`}
            >
              <input
                ref={fileInputRef}
                type="file"
                accept=".db,.sqlite,.csv"
                className="hidden"
                onChange={onFileChange}
              />
              {isLoading ? (
                <Loader2 className="w-10 h-10 text-indigo-400 animate-spin" />
              ) : status === "ok" ? (
                <CheckCircle2 className="w-10 h-10 text-emerald-400" />
              ) : (
                <div className="w-14 h-14 rounded-2xl bg-indigo-500/10 border border-indigo-500/20 flex items-center justify-center">
                  <HardDrive className="w-7 h-7 text-indigo-400" />
                </div>
              )}
              <div className="text-center">
                <p className="text-sm font-semibold text-gray-200">
                  {isLoading
                    ? "Uploading & scanning…"
                    : status === "ok"
                    ? "Connected! Opening…"
                    : "Drop your file here"}
                </p>
                <p className="text-xs text-gray-500 mt-1">
                  Supports <code className="text-indigo-300">.db</code>{" "}
                  <code className="text-indigo-300">.sqlite</code>{" "}
                  <code className="text-indigo-300">.csv</code>
                </p>
              </div>
            </div>
          )}

          {tab === "url" && (
            <div className="space-y-3">
              <label className="text-xs font-semibold text-gray-400 uppercase tracking-wider">
                Connection String
              </label>
              <div className="flex gap-2">
                <Input
                  value={uriInput}
                  onChange={(e) => setUriInput(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter") handleUriConnect(); }}
                  placeholder="postgresql://user:pass@host:5432/mydb"
                  className="flex-1 bg-black/40 border-white/10 text-gray-100 placeholder:text-gray-600 font-mono text-sm focus-visible:border-indigo-500/60 focus-visible:ring-indigo-500/20"
                  disabled={isLoading}
                />
                <Button
                  onClick={handleUriConnect}
                  disabled={isLoading || !uriInput.trim()}
                  className="bg-indigo-600 hover:bg-indigo-500 text-white px-4 rounded-xl"
                >
                  {isLoading ? (
                    <Loader2 className="w-4 h-4 animate-spin" />
                  ) : status === "ok" ? (
                    <CheckCircle2 className="w-4 h-4 text-emerald-300" />
                  ) : (
                    <ChevronRight className="w-4 h-4" />
                  )}
                </Button>
              </div>
              <p className="text-[11px] text-gray-600">
                Also accepts <code className="text-gray-400">sqlite:///path/to/file.db</code> for local files.
              </p>
            </div>
          )}

          {/* Error display */}
          {status === "error" && (
            <div className="flex items-start gap-2 text-xs text-red-400 bg-red-950/30 border border-red-500/30 rounded-lg px-4 py-3">
              <XCircle className="w-4 h-4 shrink-0 mt-0.5" />
              <span>{errorMsg}</span>
            </div>
          )}

          {/* Skip */}
          <div className="pt-1 border-t border-white/5">
            <button
              onClick={onSkip}
              className="w-full flex items-center justify-center gap-2 py-2.5 rounded-xl text-sm text-gray-400 hover:text-gray-200 border border-white/8 hover:border-white/20 hover:bg-white/5 transition-all"
            >
              <span>Try with sample database</span>
              <span className="text-[11px] px-2 py-0.5 rounded-full bg-indigo-500/15 text-indigo-400 border border-indigo-500/20">
                Neon PostgreSQL demo
              </span>
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ─── Chat cleanliness ─────────────────────────────────────────────────────────
// The chat bubble must never show source code; scripts belong to the
// "Generated Script" tab only. Applied at send time and again at render time
// so messages loaded from older sessions are scrubbed too.
const CODE_FENCE_RE = /```[A-Za-z0-9_+-]*\n?[\s\S]*?(?:```|$)/g;

const stripCodeFences = (text: string): string => {
  if (!text || !text.includes("```")) return text || "";
  // Drop the block entirely — no placeholder line in the chat bubble.
  return text
    .replace(CODE_FENCE_RE, "\n\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
};

// ─── Main Page ────────────────────────────────────────────────────────────────
export default function Home() {
  const [schemaObj, setSchemaObj] = useState<any>(null);
  const [schemaText, setSchemaText] = useState<string>("Loading schema…");
  const [messages, setMessages] = useState<{
    role: string;
    content: string;
    metrics?: MetricDef[];
    blocked?: number;
  }[]>([]);
  const [showDefs, setShowDefs] = useState(false);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [activeCode, setActiveCode] = useState<string>("");
  const [renderHtml, setRenderHtml] = useState<string>("");
  const [renderError, setRenderError] = useState<string>("");
  const [rendering, setRendering] = useState<boolean>(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  const [sessionId] = useState(() => Math.random().toString(36).substring(7));

  // ── DB connection state ─────────────────────────────────────────────────
  const [dbUri, setDbUri] = useState<string>("");
  const [dbLabel, setDbLabel] = useState<string>("Default DB");
  const [dbConnected, setDbConnected] = useState<ConnStatus>("none");
  const [showDbModal, setShowDbModal] = useState(true);

  // ── Load schema ─────────────────────────────────────────────────────────
  const loadSchema = useCallback((uri?: string) => {
    setSchemaObj(null);
    setSchemaText("Loading schema…");
    const url = uri
      ? `${API_BASE}/api/schema?db_uri=${encodeURIComponent(uri)}`
      : `${API_BASE}/api/schema`;

    // Render's free tier sleeps after ~15min idle and needs ~50s to wake, and a
    // cold fetch is indistinguishable from a dead server. Retry a couple of
    // times before surfacing an error, and always report WHAT failed.
    const attempt = async (triesLeft: number): Promise<void> => {
      try {
        const res = await fetch(url);
        if (!res.ok) {
          // Non-2xx: the body is often a plain-text or HTML error page, not JSON.
          const body = await res.text().catch(() => "");
          throw new Error(
            `HTTP ${res.status} ${res.statusText}` +
              (body ? ` — ${body.slice(0, 200)}` : "")
          );
        }
        const data = await res.json();
        try {
          setSchemaObj(JSON.parse(data.schema));
        } catch {
          setSchemaText(data.schema || "No schema found");
        }
      } catch (err: any) {
        // A 5xx / timeout may just be the backend waking up — give it a retry.
        const msg: string = err?.message || String(err);
        const isTransient =
          /HTTP 5\d\d|Failed to fetch|NetworkError|timed out|timeout/i.test(msg);
        if (triesLeft > 0 && isTransient) {
          setSchemaText(`Connecting to backend… (retrying)`);
          await new Promise((r) => setTimeout(r, 3000));
          return attempt(triesLeft - 1);
        }
        // Refused / DNS / offline arrive as a TypeError with an empty message.
        const detail =
          /Failed to fetch|NetworkError|Load failed/i.test(msg) || !msg
            ? "could not reach the server"
            : msg;
        setSchemaText(`Failed to load schema: ${detail}\nAPI: ${url}`);
      }
    };

    attempt(2);
  }, []);

  // Initial schema load (default DB)
  useEffect(() => {
    loadSchema();
  }, [loadSchema]);

  // ── Handle DB connection from modal ────────────────────────────────────
  const handleConnect = (uri: string, label: string) => {
    setDbUri(uri);
    setDbLabel(label);
    setDbConnected("ok");
    setShowDbModal(false);
    // Reset chat context when switching databases
    setMessages([]);
    setActiveCode("");
    setRenderHtml("");
    setRenderError("");
    loadSchema(uri);
  };

  // ── Skip: use default DB (schema already loading on mount) ─────────────
  const handleSkip = () => {
    setDbUri("");
    setDbLabel("Sample DB");
    setDbConnected("ok");
    setShowDbModal(false);
  };

  // ── Execute code (render pane) ──────────────────────────────────────────
  const executeCode = async (codeOverride?: string) => {
    const codeToRun = codeOverride || activeCode;
    if (!codeToRun) return;
    setRendering(true);
    setRenderError("");
    try {
      const res = await fetch(`${API_BASE}/api/execute`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code: codeToRun, db_uri: dbUri || undefined }),
      });
      const data = await res.json();
      if (data.error) {
        setRenderError(data.error);
      } else {
        setRenderHtml(data.html);
      }
    } catch (err: any) {
      setRenderError(err.message || "Execution failed");
    } finally {
      setRendering(false);
    }
  };

  // ── Send message ────────────────────────────────────────────────────────
  const sendMessage = async (overrideInput?: string) => {
    const text = overrideInput || input;
    if (!text.trim()) return;

    setMessages((prev) => [...prev, { role: "user", content: text }]);
    setInput("");
    setLoading(true);

    try {
      const res = await fetch(`${API_BASE}/api/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: sessionId,
          message: text,
          db_uri: dbUri || undefined,
        }),
      });

      const data = await res.json();

      const raw = data.response || "No response";

      // Harvest the script BEFORE scrubbing: the chat bubble is prose-only,
      // the code belongs exclusively to the Generated Script pane.
      let codeToRun = data.sql_executed || "";
      if (!codeToRun && data.response) {
        const match = data.response.match(/```(?:python|sql)?([\s\S]*?)```/i);
        if (match) codeToRun = match[1].trim();
      }

      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: stripCodeFences(raw),
          metrics: data.metrics_used || [],
          blocked: data.blocked || 0,
        },
      ]);

      if (codeToRun) {
        setActiveCode(codeToRun);
        executeCode(codeToRun);
      }
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: "⚠️ API Connection Error." },
      ]);
    } finally {
      setLoading(false);
    }
  };

  // ── DB status pill (header) ─────────────────────────────────────────────
  const dbPillClass =
    dbConnected === "ok"
      ? "bg-emerald-500/10 border-emerald-500/20 text-emerald-300"
      : "bg-white/5 border-white/10 text-gray-400";
  const dbDotClass =
    dbConnected === "ok"
      ? "bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.8)]"
      : "bg-yellow-500 animate-pulse shadow-[0_0_8px_rgba(234,179,8,0.6)]";

  return (
    <div className="h-screen bg-[#050505] text-gray-100 flex flex-col font-sans selection:bg-indigo-500/30 overflow-hidden">

      {/* DB Connect Modal */}
      <DefinitionsPanel
        apiBase={API_BASE}
        open={showDefs}
        onClose={() => setShowDefs(false)}
      />

      {showDbModal && (
        <DbConnectModal
          onConnect={handleConnect}
          onClose={() => setShowDbModal(false)}
          onSkip={handleSkip}
        />
      )}

      {/* Header */}
      <header className="h-16 shrink-0 border-b border-white/10 bg-[#0a0a0a]/90 backdrop-blur-md flex items-center px-6 justify-between sticky top-0 z-40 shadow-sm shadow-black/50">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center shadow-lg shadow-indigo-500/20">
            <Sparkles className="w-4 h-4 text-white" />
          </div>
          <h1 className="text-xl font-bold tracking-tight text-transparent bg-clip-text bg-gradient-to-r from-indigo-200 to-indigo-400">
            Nexus Copilot
          </h1>
        </div>

        <div className="flex items-center gap-3 text-xs font-medium">
          {/* DB status pill — always visible, click to open modal */}
          <button
            onClick={() => setShowDbModal(true)}
            title="Click to connect or change database"
            className={`flex items-center gap-2 px-3 py-1.5 rounded-full border transition-all hover:opacity-80 ${
              dbConnected === "ok"
                ? "bg-emerald-500/10 border-emerald-500/30 text-emerald-300"
                : "bg-indigo-500/15 border-indigo-500/40 text-indigo-300 animate-pulse"
            }`}
          >
            <div className={`w-2 h-2 rounded-full ${
              dbConnected === "ok"
                ? "bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.8)]"
                : "bg-indigo-400 shadow-[0_0_6px_rgba(99,102,241,0.8)]"
            }`} />
            <Database className="w-3.5 h-3.5" />
            <span className="max-w-[180px] truncate">
              {dbConnected === "ok" ? dbLabel : "Connect DB"}
            </span>
          </button>

          <button
            onClick={() => setShowDefs(true)}
            title="Confirmed metric definitions"
            className="flex items-center gap-2 px-3 py-1.5 rounded-full bg-white/5 border border-white/10 text-gray-300 hover:bg-white/10 transition-all"
          >
            <BookOpen className="w-3.5 h-3.5 text-indigo-400" />
            <span>Definitions</span>
          </button>

          <span className="flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-white/5 border border-white/10 shadow-inner text-gray-500">
            <div className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse shadow-[0_0_8px_rgba(16,185,129,0.8)]" />
            API Connected
          </span>
        </div>
      </header>

      {/* Main 3-Pane Resizable Layout */}
      <main className="flex-1 min-h-0 w-full flex overflow-hidden">
        <PanelGroup orientation="horizontal" className="h-full w-full">
          {/* Left Pane: Schema (Data Context) */}
          <Panel defaultSize={22} minSize={18} id="schema-panel" className="flex flex-col min-h-0 bg-[#0a0a0a] shadow-2xl z-20">
          <div className="p-4 border-b border-white/10 flex items-center justify-between bg-[#121212] shrink-0">
            <div className="flex items-center gap-2">
              <Database className="w-4 h-4 text-indigo-400" />
              <h2 className="text-sm font-bold text-gray-200 uppercase tracking-wider">Data Context</h2>
            </div>
            <button
              onClick={() => setShowDbModal(true)}
              title="Connect a different database"
              className="flex items-center gap-1.5 text-[11px] text-indigo-400 hover:text-indigo-300 bg-indigo-500/10 border border-indigo-500/20 px-2.5 py-1 rounded-lg transition-all hover:bg-indigo-500/20"
            >
              <Upload className="w-3 h-3" />
              Change DB
            </button>
          </div>
          <ScrollArea className="flex-1 min-h-0 bg-[#050505]">
            <div className="p-4">
              {schemaObj ? (
                <div className="space-y-4 pb-10">
                  {Object.entries(schemaObj).map(([tableName, tableInfo]: any) => (
                    <div key={tableName} className="border border-white/10 rounded-xl bg-[#121212] overflow-hidden shadow-lg shadow-black/40 transition-all hover:border-indigo-500/30">
                      <div className="px-4 py-3 border-b border-white/5 flex items-center justify-between bg-gradient-to-r from-white/[0.02] to-transparent">
                        <span className="font-bold text-indigo-300 text-sm tracking-wide">{tableName}</span>
                        <span className="text-[10px] bg-indigo-500/20 text-indigo-300 px-2 py-0.5 rounded-full border border-indigo-500/30">{tableInfo.columns.length} cols</span>
                      </div>
                      <div className="p-3 space-y-2">
                        {tableInfo.columns.map((col: any) => (
                          <div key={col.name} className="flex items-center justify-between text-[13px] group">
                            <span className="text-gray-300 font-medium group-hover:text-white transition-colors">{col.name}</span>
                            <span className="text-gray-500 font-mono text-[11px] bg-black/40 px-1.5 rounded">{col.type.toLowerCase()}</span>
                          </div>
                        ))}
                        {tableInfo.foreign_keys.length > 0 && (
                          <div className="mt-3 pt-3 border-t border-white/5 space-y-1.5">
                            {tableInfo.foreign_keys.map((fk: any, i: number) => (
                              <div key={i} className="text-[11px] text-emerald-400/90 flex items-center gap-1.5 bg-emerald-500/10 px-2 py-1 rounded border border-emerald-500/20">
                                <Key className="w-3 h-3 shrink-0" />
                                <span className="truncate">{fk.from_column} → {fk.to_table}.{fk.to_column}</span>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <pre className="text-xs font-mono text-gray-400 whitespace-pre-wrap leading-relaxed">
                  {schemaText}
                </pre>
              )}
            </div>
          </ScrollArea>
          </Panel>

          {/* Resizer 1 (Left / Center) */}
          <PanelResizeHandle className="w-1.5 bg-[#0a0a0a] hover:bg-indigo-500/30 active:bg-indigo-500 transition-colors relative flex items-center justify-center cursor-col-resize group z-30">
            <div className="w-[1px] h-full bg-white/10 group-hover:bg-indigo-500/50 transition-colors" />
            <div className="absolute w-4 h-7 rounded-full bg-[#161616] border border-white/20 group-hover:border-indigo-400 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity shadow-md pointer-events-none">
              <GripVertical className="w-2.5 h-2.5 text-gray-400 group-hover:text-indigo-300" />
            </div>
          </PanelResizeHandle>

          {/* Right Workspace: Unified Single Scroll Container */}
          <Panel defaultSize={78} minSize={40} id="workspace-panel" className="flex flex-col min-h-0 bg-[#050505]">
            <div className="flex-1 overflow-y-auto w-full h-full flex flex-col scroll-smooth divide-y divide-white/10" ref={scrollRef}>
              {/* Upper Section: Copilot Dialogue */}
              <div className="flex flex-col bg-[#050505] shrink-0">
                {/* Chat Panel Header */}
                <div className="px-6 py-3.5 border-b border-white/10 bg-[#0d0d12] flex items-center justify-between sticky top-0 z-30 shadow-md">
                  <div className="flex items-center gap-2.5">
                    <div className="w-2.5 h-2.5 rounded-full bg-indigo-500 shadow-[0_0_10px_rgba(99,102,241,0.9)]" />
                    <h2 className="text-sm font-extrabold text-white tracking-wide">
                      Copilot Dialogue
                    </h2>
                  </div>
                  <span className="text-xs text-gray-400 font-mono bg-white/5 px-2.5 py-1 rounded-full border border-white/5">
                    {messages.length} exchanges
                  </span>
                </div>

                <div className="p-6 space-y-6">
                  {messages.length === 0 && (
                    <div className="py-12 flex flex-col items-center justify-center text-center space-y-6 opacity-0 animate-in fade-in duration-1000">
                      <div className="w-20 h-20 rounded-3xl bg-gradient-to-br from-indigo-500/20 to-purple-500/10 border border-indigo-500/20 flex items-center justify-center shadow-[0_0_30px_rgba(99,102,241,0.15)]">
                        <LayoutDashboard className="w-10 h-10 text-indigo-400" />
                      </div>
                      <div className="space-y-3">
                        <h3 className="text-2xl font-black text-gray-100 tracking-tight">How can I help you?</h3>
                        <p className="text-gray-500 text-sm max-w-sm font-medium leading-relaxed">
                          {dbConnected === "ok"
                            ? `Connected to ${dbLabel}. Ask me anything about your data.`
                            : "Connect your own database, or try with the built-in sample data."}
                        </p>
                      </div>

                      {/* Connect DB CTA — shown when no DB is connected */}
                      {dbConnected !== "ok" && (
                        <button
                          onClick={() => setShowDbModal(true)}
                          className="flex items-center gap-3 px-6 py-3.5 rounded-2xl bg-indigo-600/20 hover:bg-indigo-600/35 border border-indigo-500/40 hover:border-indigo-500/70 text-indigo-300 hover:text-indigo-200 transition-all shadow-[0_0_20px_rgba(99,102,241,0.15)] hover:shadow-[0_0_28px_rgba(99,102,241,0.3)] font-semibold text-sm"
                        >
                          <Database className="w-4 h-4" />
                          Connect your database
                          <ChevronRight className="w-4 h-4 opacity-60" />
                        </button>
                      )}

                      <div className="flex flex-wrap justify-center gap-3 max-w-md pt-2">
                        {["Analyze Q3 Sales", "Top 5 Employees by Revenue", "Data Quality Check"].map((lbl) => (
                          <button
                            key={lbl}
                            onClick={() => setInput(lbl)}
                            className="px-5 py-2.5 rounded-xl text-xs font-semibold border border-white/10 bg-[#121212] text-gray-300 hover:bg-indigo-500/20 hover:text-indigo-200 hover:border-indigo-500/40 hover:shadow-[0_0_15px_rgba(99,102,241,0.2)] transition-all"
                          >
                            {lbl}
                          </button>
                        ))}
                      </div>
                    </div>
                  )}

                  {messages.map((msg, i) => (
                    <div key={i} className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"} animate-in slide-in-from-bottom-2 duration-300`}>
                      <div className={`max-w-[85%] rounded-3xl px-6 py-4 shadow-sm ${
                        msg.role === "user"
                          ? "bg-gradient-to-br from-indigo-600 to-indigo-800 text-white rounded-br-sm shadow-[0_4px_20px_rgba(79,70,229,0.3)]"
                          : "bg-[#121212] text-gray-200 rounded-bl-sm border border-white/10 shadow-xl"
                      }`}>
                        <div className="prose prose-invert max-w-none text-[15px] prose-p:leading-relaxed prose-pre:bg-black/20 prose-pre:border prose-pre:border-white/10 prose-code:text-indigo-200">
                          <ReactMarkdown remarkPlugins={[remarkGfm]}>
                            {msg.role === "assistant" ? stripCodeFences(msg.content) : msg.content}
                          </ReactMarkdown>
                        </div>

                        {msg.role === "assistant" && (msg.metrics?.length || msg.blocked) ? (
                          <div className="flex flex-wrap items-center gap-1.5 mt-3 pt-3 border-t border-white/10">
                            <span className="text-[10px] uppercase tracking-wide text-gray-500 mr-1">
                              Definitions applied
                            </span>
                            {msg.metrics?.map((m) => (
                              <button
                                key={m.id}
                                onClick={() => setShowDefs(true)}
                                title={m.expr}
                                className={`text-[10px] px-2 py-0.5 rounded-full border transition-colors ${
                                  m.status === "confirmed"
                                    ? "bg-emerald-500/10 border-emerald-500/30 text-emerald-300 hover:bg-emerald-500/20"
                                    : "bg-amber-500/10 border-amber-500/30 text-amber-300 hover:bg-amber-500/20"
                                }`}
                              >
                                {m.display_name}
                              </button>
                            ))}
                            {msg.blocked ? (
                              <span className="text-[10px] px-2 py-0.5 rounded-full border bg-rose-500/10 border-rose-500/30 text-rose-300 flex items-center gap-1">
                                <ShieldAlert className="w-2.5 h-2.5" />
                                {msg.blocked} blocked
                              </span>
                            ) : null}
                          </div>
                        ) : null}
                      </div>
                    </div>
                  ))}

                  {loading && (
                    <div className="flex justify-start animate-in fade-in">
                      <div className="max-w-[85%] rounded-3xl px-6 py-5 bg-[#121212] border border-white/10 rounded-bl-sm shadow-xl flex items-center gap-2.5">
                        <div className="w-2.5 h-2.5 bg-indigo-500 rounded-full animate-bounce shadow-[0_0_8px_rgba(99,102,241,0.8)]" />
                        <div className="w-2.5 h-2.5 bg-indigo-500 rounded-full animate-bounce delay-100 shadow-[0_0_8px_rgba(99,102,241,0.8)]" />
                        <div className="w-2.5 h-2.5 bg-indigo-500 rounded-full animate-bounce delay-200 shadow-[0_0_8px_rgba(99,102,241,0.8)]" />
                      </div>
                    </div>
                  )}
                </div>

                <div className="p-5 bg-gradient-to-t from-[#050505] via-[#050505] to-transparent">
                  <div className="relative group rounded-2xl bg-[#121212] border border-white/10 focus-within:border-indigo-500/60 focus-within:ring-4 focus-within:ring-indigo-500/10 transition-all overflow-hidden shadow-2xl">
                    <Textarea
                      className="min-h-[72px] w-full bg-transparent border-0 text-gray-100 placeholder:text-gray-600 resize-none p-4 pr-16 focus-visible:ring-0 text-[14px]"
                      placeholder="Ask a question about your data…"
                      value={input}
                      onChange={(e) => setInput(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && !e.shiftKey) {
                          e.preventDefault();
                          sendMessage();
                        }
                      }}
                    />
                    <button
                      onClick={() => sendMessage()}
                      disabled={loading || !input.trim()}
                      className="absolute bottom-3 right-3 w-10 h-10 rounded-xl bg-indigo-600 hover:bg-indigo-500 disabled:bg-white/5 disabled:text-gray-700 text-white flex items-center justify-center transition-all shadow-[0_0_15px_rgba(99,102,241,0.4)] disabled:shadow-none"
                    >
                      <Send className="w-4 h-4 ml-0.5" />
                    </button>
                  </div>
                </div>
              </div>

              {/* Lower Section: Analytics Canvas & Script (Natural Flow on Same Scrollbar) */}
              <div className="flex flex-col bg-[#0a0a0a] min-h-[560px]">
                <Tabs defaultValue="render" className="flex-1 flex flex-col">
                  <div className="px-6 py-3.5 border-b border-white/10 bg-[#0d0d12] flex items-center justify-between sticky top-0 z-20 shadow-md">
                    <div className="flex items-center gap-2.5">
                      <LayoutDashboard className="w-4 h-4 text-purple-400" />
                      <h2 className="text-sm font-extrabold text-white tracking-wide">
                        Analytics Canvas & Script
                      </h2>
                    </div>

                    <div className="flex items-center gap-3">
                      <TabsList className="bg-black/50 border border-white/5 p-1 rounded-xl h-auto">
                        <TabsTrigger
                          value="render"
                          className="text-[12px] px-3.5 py-1 text-gray-400 hover:text-gray-200 data-[state=active]:bg-purple-600/20 data-[state=active]:text-purple-300 font-bold transition-all rounded-lg"
                        >
                          <Play className="w-3 h-3 mr-1.5" /> Live Visualization
                        </TabsTrigger>
                        <TabsTrigger
                          value="code"
                          className="text-[12px] px-3.5 py-1 text-gray-400 hover:text-gray-200 data-[state=active]:bg-indigo-600/20 data-[state=active]:text-indigo-300 font-bold transition-all rounded-lg"
                        >
                          <Code2 className="w-3 h-3 mr-1.5" /> Generated Script
                        </TabsTrigger>
                      </TabsList>

                      {activeCode && (
                        <Button
                          onClick={() => executeCode()}
                          disabled={rendering}
                          size="sm"
                          className="bg-purple-600 hover:bg-purple-500 text-white text-xs px-3 py-1 h-7 rounded-lg shadow-[0_0_12px_rgba(168,85,247,0.3)] font-semibold"
                        >
                          {rendering ? (
                            <>
                              <Loader2 className="w-3 h-3 mr-1.5 animate-spin" /> Rendering...
                            </>
                          ) : (
                            <>
                              <Play className="w-3 h-3 mr-1.5" /> Re-run
                            </>
                          )}
                        </Button>
                      )}
                    </div>
                  </div>

                  <TabsContent value="render" className="flex-1 p-5 m-0 flex flex-col data-[state=active]:flex bg-[#050505]">
                    {renderHtml ? (
                      <div className="w-full min-h-[520px] bg-black/20 border border-white/5 overflow-hidden rounded-2xl shadow-inner">
                        <iframe srcDoc={renderHtml} className="w-full min-h-[520px] border-0 bg-transparent" sandbox="allow-scripts allow-popups" />
                      </div>
                    ) : renderError ? (
                      <div className="bg-red-950/20 border border-red-500/30 rounded-2xl p-5 text-red-400 text-xs font-mono whitespace-pre-wrap">
                        {renderError}
                      </div>
                    ) : (
                      <div className="min-h-[380px] rounded-2xl border border-dashed border-white/10 bg-white/[0.01] flex flex-col items-center justify-center text-center p-8 space-y-4">
                        <div className="w-16 h-16 rounded-full bg-purple-500/10 flex items-center justify-center">
                          <Play className="w-8 h-8 text-purple-400/60" />
                        </div>
                        <div className="space-y-1">
                          <h3 className="text-base font-bold text-gray-200">Interactive Analytics Canvas</h3>
                          <p className="text-xs text-gray-500 max-w-[340px]">
                            Generated charts and visual KPI summaries render directly across this wide canvas.
                          </p>
                        </div>
                        {activeCode && (
                          <Button
                            onClick={() => executeCode()}
                            disabled={rendering}
                            className="bg-purple-600/20 text-purple-300 hover:bg-purple-600/40 border border-purple-500/30 rounded-xl px-5 py-2.5 text-xs font-bold"
                          >
                            {rendering ? "Rendering..." : "Run Code & Render"}
                          </Button>
                        )}
                      </div>
                    )}
                  </TabsContent>

                  <TabsContent value="code" className="flex-1 p-0 m-0 flex flex-col data-[state=active]:flex">
                    {activeCode ? (
                      <div className="p-4 bg-[#1e1e1e]/50 rounded-2xl m-4 border border-white/5">
                        <SyntaxHighlighter
                          language="python"
                          style={vscDarkPlus}
                          customStyle={{ margin: 0, padding: "1.25rem", background: "transparent", fontSize: "13px", lineHeight: "1.6" }}
                        >
                          {activeCode}
                        </SyntaxHighlighter>
                      </div>
                    ) : (
                      <div className="min-h-[260px] flex flex-col items-center justify-center text-gray-600 space-y-4 bg-[#050505]">
                        <Code2 className="w-8 h-8 opacity-30" />
                        <p className="text-xs font-medium">No script generated yet.</p>
                      </div>
                    )}
                  </TabsContent>
                </Tabs>
              </div>
            </div>
          </Panel>
        </PanelGroup>
      </main>
    </div>
  );
}
