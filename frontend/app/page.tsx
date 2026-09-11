"use client";

import React, { useState, useEffect, useRef } from "react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Textarea } from "@/components/ui/textarea";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Terminal, Code2, Play, Database, Send, Sparkles, LayoutDashboard, Key } from "lucide-react";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { vscDarkPlus } from "react-syntax-highlighter/dist/esm/styles/prism";

export default function Home() {
  const [schemaObj, setSchemaObj] = useState<any>(null);
  const [schemaText, setSchemaText] = useState<string>("Loading schema...");
  const [messages, setMessages] = useState<{ role: string; content: string }[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [activeCode, setActiveCode] = useState<string>("");
  const scrollRef = useRef<HTMLDivElement>(null);

  const [sessionId] = useState(() => Math.random().toString(36).substring(7));

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages]);

  useEffect(() => {
    fetch("http://localhost:8000/api/schema")
      .then((res) => res.json())
      .then((data) => {
        try {
          // extract_db_schema returns a JSON string, so we parse it into a renderable object
          setSchemaObj(JSON.parse(data.schema));
        } catch (e) {
          setSchemaText(data.schema || "No schema found");
        }
      })
      .catch(() => setSchemaText("Failed to load schema. Is the API running?"));
  }, []);

  const sendMessage = async (overrideInput?: string) => {
    const text = overrideInput || input;
    if (!text.trim()) return;

    setMessages((prev) => [...prev, { role: "user", content: text }]);
    setInput("");
    setLoading(true);

    try {
      const res = await fetch("http://localhost:8000/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId, message: text }),
      });

      const data = await res.json();
      
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: data.response || "No response" },
      ]);

      if (data.sql_executed) {
        setActiveCode(data.sql_executed);
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

  return (
    <div className="h-screen bg-[#050505] text-gray-100 flex flex-col font-sans selection:bg-indigo-500/30 overflow-hidden">
      
      {/* Header */}
      <header className="h-16 shrink-0 border-b border-white/10 bg-[#0a0a0a]/90 backdrop-blur-md flex items-center px-6 justify-between sticky top-0 z-50 shadow-sm shadow-black/50">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center shadow-lg shadow-indigo-500/20">
            <Sparkles className="w-4 h-4 text-white" />
          </div>
          <h1 className="text-xl font-bold tracking-tight text-transparent bg-clip-text bg-gradient-to-r from-indigo-200 to-indigo-400">
            Nexus Copilot
          </h1>
        </div>
        <div className="flex items-center gap-4 text-xs font-medium text-gray-500">
          <span className="flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-white/5 border border-white/10 shadow-inner">
            <div className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse shadow-[0_0_8px_rgba(16,185,129,0.8)]" /> API Connected
          </span>
        </div>
      </header>

      {/* Main 3-Pane Layout */}
      <main className="flex-1 w-full flex overflow-hidden">
        
        {/* Left Pane: Schema (25%) */}
        <aside className="w-1/4 border-r border-white/10 bg-[#0a0a0a] flex flex-col hidden lg:flex shadow-2xl z-20">
          <div className="p-4 border-b border-white/10 flex items-center gap-2 bg-[#121212]">
            <Database className="w-4 h-4 text-indigo-400" />
            <h2 className="text-sm font-bold text-gray-200 uppercase tracking-wider">Data Context</h2>
          </div>
          <ScrollArea className="flex-1 p-4 bg-[#050505]">
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
          </ScrollArea>
        </aside>

        {/* Center Pane: Chat (45%) */}
        <section className="flex-1 lg:w-[45%] flex flex-col bg-[#050505] relative z-10">
          <div className="flex-1 overflow-y-auto p-6 space-y-6 scroll-smooth" ref={scrollRef}>
            {messages.length === 0 && (
              <div className="h-full flex flex-col items-center justify-center text-center space-y-6 opacity-0 animate-in fade-in duration-1000">
                <div className="w-20 h-20 rounded-3xl bg-gradient-to-br from-indigo-500/20 to-purple-500/10 border border-indigo-500/20 flex items-center justify-center shadow-[0_0_30px_rgba(99,102,241,0.15)]">
                  <LayoutDashboard className="w-10 h-10 text-indigo-400" />
                </div>
                <div className="space-y-3">
                  <h3 className="text-2xl font-black text-gray-100 tracking-tight">How can I help you?</h3>
                  <p className="text-gray-500 text-sm max-w-sm font-medium leading-relaxed">
                    Ask me to analyze your database, create visualizations, or aggregate metrics.
                  </p>
                </div>
                <div className="flex flex-wrap justify-center gap-3 max-w-md pt-4">
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
                  <p className="whitespace-pre-wrap leading-relaxed text-[15px]">
                    {msg.content}
                  </p>
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
                className="min-h-[90px] w-full bg-transparent border-0 text-gray-100 placeholder:text-gray-600 resize-none p-5 pr-20 focus-visible:ring-0 text-[15px]"
                placeholder="Ask a question about your data..."
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
                className="absolute bottom-4 right-4 w-11 h-11 rounded-xl bg-indigo-600 hover:bg-indigo-500 disabled:bg-white/5 disabled:text-gray-700 text-white flex items-center justify-center transition-all shadow-[0_0_15px_rgba(99,102,241,0.4)] disabled:shadow-none"
              >
                <Send className="w-5 h-5 ml-0.5" />
              </button>
            </div>
          </div>
        </section>

        {/* Right Pane: Code & Render (30%) */}
        <aside className="w-[30%] border-l border-white/10 bg-[#0a0a0a] flex flex-col hidden xl:flex shadow-2xl z-20">
          <Tabs defaultValue="code" className="flex-1 flex flex-col h-full">
            <div className="p-4 border-b border-white/10 bg-[#121212]">
              <TabsList className="w-full bg-black/50 border border-white/5 p-1.5 rounded-xl h-auto">
                <TabsTrigger 
                  value="code" 
                  className="flex-1 text-[13px] py-2 text-gray-400 hover:text-gray-200 data-[state=active]:bg-indigo-600/20 data-[state=active]:text-indigo-300 font-bold transition-all rounded-lg"
                >
                  <Code2 className="w-4 h-4 mr-2" /> Code
                </TabsTrigger>
                <TabsTrigger 
                  value="render" 
                  className="flex-1 text-[13px] py-2 text-gray-400 hover:text-gray-200 data-[state=active]:bg-purple-600/20 data-[state=active]:text-purple-300 font-bold transition-all rounded-lg"
                >
                  <Play className="w-4 h-4 mr-2" /> Render
                </TabsTrigger>
              </TabsList>
            </div>
            
            <TabsContent value="code" className="flex-1 p-0 m-0 overflow-hidden flex flex-col data-[state=active]:flex">
              {activeCode ? (
                <div className="flex-1 overflow-auto bg-[#1e1e1e]/50">
                  <div className="flex items-center justify-between px-5 py-3 bg-[#121212] border-b border-white/5 text-xs text-gray-400 font-mono shadow-sm">
                    <span className="flex items-center gap-2"><Terminal className="w-4 h-4 text-indigo-400" /> Generated Script.py</span>
                  </div>
                  <SyntaxHighlighter 
                    language="python" 
                    style={vscDarkPlus} 
                    customStyle={{ margin: 0, padding: '1.5rem', background: 'transparent', fontSize: '13px', lineHeight: '1.6' }}
                  >
                    {activeCode}
                  </SyntaxHighlighter>
                </div>
              ) : (
                <div className="flex-1 flex flex-col items-center justify-center text-gray-600 space-y-5 bg-[#050505]">
                  <div className="w-20 h-20 rounded-full bg-white/5 flex items-center justify-center border border-white/5">
                    <Code2 className="w-10 h-10 opacity-30" />
                  </div>
                  <p className="text-sm font-medium">No script generated yet.</p>
                </div>
              )}
            </TabsContent>
            
            <TabsContent value="render" className="flex-1 p-8 m-0 flex flex-col data-[state=active]:flex bg-[#050505]">
              <div className="flex-1 rounded-2xl border-2 border-dashed border-white/10 bg-white/[0.01] flex flex-col items-center justify-center text-center p-8 space-y-6 hover:border-purple-500/30 hover:bg-purple-500/5 transition-all group">
                <div className="w-16 h-16 rounded-full bg-purple-500/10 flex items-center justify-center group-hover:scale-110 transition-transform">
                  <Play className="w-8 h-8 text-purple-400/60" />
                </div>
                <div className="space-y-2">
                  <h3 className="text-lg font-bold text-gray-200">Visualization Sandbox</h3>
                  <p className="text-sm text-gray-500 leading-relaxed max-w-[250px]">
                    This environment will securely execute the Python script and render interactive <span className="text-indigo-400 font-semibold">Plotly</span> charts directly in React.
                  </p>
                </div>
                <Button className="bg-purple-600/20 text-purple-300 hover:bg-purple-600/40 border border-purple-500/30 rounded-xl px-6 py-5 shadow-[0_0_15px_rgba(168,85,247,0.15)] font-bold">
                  Initialize WASM Runtime
                </Button>
              </div>
            </TabsContent>
          </Tabs>
        </aside>

      </main>
    </div>
  );
}
