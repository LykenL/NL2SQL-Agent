"use client";

import React, { useState, useEffect, useRef } from "react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Textarea } from "@/components/ui/textarea";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Separator } from "@/components/ui/separator";
import { Terminal, Code2, Play, Database, Send, Sparkles, LayoutDashboard } from "lucide-react";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { vscDarkPlus } from "react-syntax-highlighter/dist/esm/styles/prism";

export default function Home() {
  const [schema, setSchema] = useState<string>("Loading schema...");
  const [messages, setMessages] = useState<{ role: string; content: string }[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  
  // The code to display in the right panel
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
      .then((data) => setSchema(data.schema || "No schema found"))
      .catch(() => setSchema("Failed to load schema. Is the API running?"));
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
    <div className="min-h-screen bg-[#050505] text-gray-100 flex flex-col font-sans selection:bg-indigo-500/30 overflow-hidden">
      
      {/* Header */}
      <header className="h-16 shrink-0 border-b border-white/5 bg-[#0a0a0a]/80 backdrop-blur-md flex items-center px-6 justify-between sticky top-0 z-50">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center shadow-lg shadow-indigo-500/20">
            <Sparkles className="w-4 h-4 text-white" />
          </div>
          <h1 className="text-xl font-bold tracking-tight text-transparent bg-clip-text bg-gradient-to-r from-indigo-200 to-indigo-400">
            Nexus Copilot
          </h1>
        </div>
        <div className="flex items-center gap-4 text-xs font-medium text-gray-500">
          <span className="flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-white/5 border border-white/10">
            <div className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" /> API Connected
          </span>
        </div>
      </header>

      {/* Main 3-Pane Layout */}
      <main className="flex-1 w-full flex overflow-hidden">
        
        {/* Left Pane: Schema (25%) */}
        <aside className="w-1/4 border-r border-white/5 bg-[#0a0a0a] flex flex-col hidden lg:flex">
          <div className="p-4 border-b border-white/5 flex items-center gap-2">
            <Database className="w-4 h-4 text-indigo-400" />
            <h2 className="text-sm font-semibold text-gray-200 uppercase tracking-wider">Data Context</h2>
          </div>
          <ScrollArea className="flex-1 p-4">
            <pre className="text-xs font-mono text-indigo-200/60 whitespace-pre-wrap leading-relaxed">
              {schema}
            </pre>
          </ScrollArea>
        </aside>

        {/* Center Pane: Chat (45%) */}
        <section className="flex-1 lg:w-[45%] flex flex-col bg-[#050505] relative shadow-2xl z-10">
          <div className="flex-1 overflow-y-auto p-6 space-y-6 scroll-smooth" ref={scrollRef}>
            {messages.length === 0 && (
              <div className="h-full flex flex-col items-center justify-center text-center space-y-6 opacity-0 animate-in fade-in duration-1000">
                <div className="w-16 h-16 rounded-2xl bg-indigo-500/10 border border-indigo-500/20 flex items-center justify-center">
                  <LayoutDashboard className="w-8 h-8 text-indigo-400" />
                </div>
                <div className="space-y-2">
                  <h3 className="text-2xl font-bold text-gray-200">How can I help you?</h3>
                  <p className="text-gray-500 text-sm max-w-sm">
                    Ask me to analyze your database, create visualizations, or aggregate metrics.
                  </p>
                </div>
                <div className="flex flex-wrap justify-center gap-2 max-w-md">
                  {["Analyze Q3 Sales", "Top 5 Employees by Revenue", "Data Quality Check"].map((lbl) => (
                    <button 
                      key={lbl} 
                      onClick={() => setInput(lbl)}
                      className="px-4 py-2 rounded-full text-xs font-medium border border-white/10 bg-white/5 text-gray-300 hover:bg-indigo-500/20 hover:text-indigo-300 hover:border-indigo-500/30 transition-all"
                    >
                      {lbl}
                    </button>
                  ))}
                </div>
              </div>
            )}
            
            {messages.map((msg, i) => (
              <div key={i} className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"} animate-in slide-in-from-bottom-2 duration-300`}>
                <div className={`max-w-[85%] rounded-2xl px-5 py-3.5 shadow-sm ${
                  msg.role === "user"
                    ? "bg-gradient-to-br from-indigo-600 to-indigo-700 text-white rounded-br-sm shadow-indigo-900/20"
                    : "bg-[#121212] text-gray-200 rounded-bl-sm border border-white/5 shadow-black/50"
                }`}>
                  <p className="whitespace-pre-wrap leading-relaxed text-sm">
                    {msg.content}
                  </p>
                </div>
              </div>
            ))}
            
            {loading && (
              <div className="flex justify-start animate-in fade-in">
                <div className="max-w-[85%] rounded-2xl px-5 py-4 bg-[#121212] border border-white/5 rounded-bl-sm flex items-center gap-2">
                  <div className="w-2 h-2 bg-indigo-500 rounded-full animate-pulse" />
                  <div className="w-2 h-2 bg-indigo-500 rounded-full animate-pulse delay-75" />
                  <div className="w-2 h-2 bg-indigo-500 rounded-full animate-pulse delay-150" />
                </div>
              </div>
            )}
          </div>

          <div className="p-4 bg-[#050505] border-t border-white/5">
            <div className="relative group rounded-2xl bg-[#121212] border border-white/10 focus-within:border-indigo-500/50 focus-within:ring-1 focus-within:ring-indigo-500/50 transition-all overflow-hidden shadow-lg">
              <Textarea
                className="min-h-[100px] w-full bg-transparent border-0 text-gray-100 placeholder:text-gray-600 resize-none p-4 pr-16 focus-visible:ring-0"
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
                className="absolute bottom-3 right-3 w-10 h-10 rounded-xl bg-indigo-600 hover:bg-indigo-500 disabled:bg-white/5 disabled:text-gray-500 text-white flex items-center justify-center transition-all shadow-md"
              >
                <Send className="w-4 h-4 ml-0.5" />
              </button>
            </div>
          </div>
        </section>

        {/* Right Pane: Code & Render (30%) */}
        <aside className="w-[30%] border-l border-white/5 bg-[#0a0a0a] flex flex-col hidden xl:flex">
          <Tabs defaultValue="code" className="flex-1 flex flex-col h-full">
            <div className="p-3 border-b border-white/5 bg-[#0a0a0a]">
              <TabsList className="w-full bg-[#121212] border border-white/5 p-1 rounded-lg">
                <TabsTrigger value="code" className="flex-1 text-xs data-[state=active]:bg-indigo-500/10 data-[state=active]:text-indigo-400">
                  <Code2 className="w-3.5 h-3.5 mr-2" /> Code
                </TabsTrigger>
                <TabsTrigger value="render" className="flex-1 text-xs data-[state=active]:bg-indigo-500/10 data-[state=active]:text-indigo-400">
                  <Play className="w-3.5 h-3.5 mr-2" /> Render
                </TabsTrigger>
              </TabsList>
            </div>
            
            <TabsContent value="code" className="flex-1 p-0 m-0 overflow-hidden flex flex-col data-[state=active]:flex">
              {activeCode ? (
                <div className="flex-1 overflow-auto bg-[#1e1e1e]">
                  <div className="flex items-center justify-between px-4 py-2 bg-[#121212] border-b border-white/5 text-xs text-gray-400 font-mono">
                    <span>Generated Script.py</span>
                    <Terminal className="w-3.5 h-3.5" />
                  </div>
                  <SyntaxHighlighter 
                    language="python" 
                    style={vscDarkPlus} 
                    customStyle={{ margin: 0, padding: '1rem', background: 'transparent', fontSize: '13px' }}
                  >
                    {activeCode}
                  </SyntaxHighlighter>
                </div>
              ) : (
                <div className="flex-1 flex flex-col items-center justify-center text-gray-600 space-y-4">
                  <Code2 className="w-12 h-12 opacity-20" />
                  <p className="text-sm">No code generated yet.</p>
                </div>
              )}
            </TabsContent>
            
            <TabsContent value="render" className="flex-1 p-6 m-0 flex flex-col data-[state=active]:flex">
              <div className="flex-1 rounded-xl border border-dashed border-white/10 bg-white/[0.02] flex flex-col items-center justify-center text-center p-6 space-y-4">
                <Play className="w-10 h-10 text-indigo-500/40" />
                <h3 className="text-lg font-medium text-gray-300">Visualization Sandbox</h3>
                <p className="text-xs text-gray-500 leading-relaxed max-w-xs">
                  This pane will safely execute the Python generated in the Code tab and render interactive Plotly charts natively in React.
                </p>
                <Button variant="outline" className="bg-[#121212] border-white/10 text-xs mt-4">
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
