"use client";

import React, { useState, useEffect, useRef } from "react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Textarea } from "@/components/ui/textarea";

export default function Home() {
  const [schema, setSchema] = useState<string>("Loading schema...");
  const [messages, setMessages] = useState<{ role: string; content: string }[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  // Generate a random session ID on mount
  const [sessionId] = useState(() => Math.random().toString(36).substring(7));

  // Auto-scroll to bottom of chat
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages]);

  // Fetch schema from FastAPI
  useEffect(() => {
    fetch("http://localhost:8000/api/schema")
      .then((res) => res.json())
      .then((data) => setSchema(data.schema || "No schema found"))
      .catch((err) => setSchema("Failed to load schema from API"));
  }, []);

  const sendMessage = async (overrideInput?: string) => {
    const text = overrideInput || input;
    if (!text.trim()) return;

    const newMsg = { role: "user", content: text };
    setMessages((prev) => [...prev, newMsg]);
    setInput("");
    setLoading(true);

    try {
      const res = await fetch("http://localhost:8000/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: sessionId,
          message: text,
        }),
      });

      const data = await res.json();
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: data.response || "No response" },
      ]);
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: "Error connecting to the API." },
      ]);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-gray-100 flex flex-col font-sans selection:bg-indigo-500/30">
      
      {/* Header */}
      <header className="w-full py-6 border-b border-white/5 bg-[#121212]">
        <div className="max-w-7xl mx-auto px-6 text-center">
          <h1 className="text-4xl font-black tracking-tight text-transparent bg-clip-text bg-gradient-to-r from-indigo-400 to-indigo-600 drop-shadow-lg">
            Agentic Data Copilot
          </h1>
        </div>
      </header>

      {/* Main Content */}
      <main className="flex-1 max-w-7xl w-full mx-auto p-6 grid grid-cols-1 md:grid-cols-3 gap-8">
        
        {/* Left Column: Schema Explorer */}
        <div className="col-span-1 flex flex-col h-[calc(100vh-140px)]">
          <h2 className="text-xl font-semibold mb-4 text-gray-200">Schema Explorer</h2>
          <Card className="flex-1 border-white/10 bg-[#121212] overflow-hidden flex flex-col">
            <ScrollArea className="flex-1 p-4">
              <pre className="text-sm font-mono text-indigo-200/80 whitespace-pre-wrap">
                {schema}
              </pre>
            </ScrollArea>
          </Card>
        </div>

        {/* Right Column: Query Workspace */}
        <div className="col-span-2 flex flex-col h-[calc(100vh-140px)]">
          <h2 className="text-xl font-semibold mb-4 text-gray-200">Query Workspace</h2>
          
          {/* Quick Templates */}
          <div className="grid grid-cols-3 gap-4 mb-4">
            {["Top 10 Records", "Aggregate Sum", "Join Analysis"].map((lbl) => (
              <Button 
                key={lbl} 
                variant="outline" 
                className="bg-transparent border-indigo-500/30 text-indigo-300 hover:bg-indigo-500/10 hover:text-indigo-200"
                onClick={() => setInput(`Show me ${lbl.toLowerCase()} for `)}
              >
                {lbl}
              </Button>
            ))}
          </div>

          {/* Chat Container */}
          <Card className="flex-1 border-white/10 bg-[#121212] mb-4 overflow-hidden flex flex-col">
            <div className="flex-1 overflow-y-auto p-4 space-y-4" ref={scrollRef}>
              {messages.length === 0 && (
                <div className="text-center text-gray-500 mt-10">
                  Start a new conversation below.
                </div>
              )}
              {messages.map((msg, i) => (
                <div
                  key={i}
                  className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
                >
                  <div
                    className={`max-w-[85%] rounded-2xl px-5 py-3 ${
                      msg.role === "user"
                        ? "bg-indigo-600 text-white rounded-br-sm"
                        : "bg-[#1e1e1e] text-gray-200 rounded-bl-sm border border-white/5"
                    }`}
                  >
                    <p className="whitespace-pre-wrap leading-relaxed text-sm">
                      {msg.content}
                    </p>
                  </div>
                </div>
              ))}
              {loading && (
                <div className="flex justify-start">
                  <div className="max-w-[85%] rounded-2xl px-5 py-3 bg-[#1e1e1e] text-gray-200 rounded-bl-sm border border-white/5 flex items-center space-x-2">
                    <div className="w-2 h-2 bg-indigo-400 rounded-full animate-bounce" />
                    <div className="w-2 h-2 bg-indigo-400 rounded-full animate-bounce delay-100" />
                    <div className="w-2 h-2 bg-indigo-400 rounded-full animate-bounce delay-200" />
                  </div>
                </div>
              )}
            </div>
          </Card>

          {/* Input Form */}
          <div className="relative">
            <Textarea
              className="min-h-[100px] w-full bg-[#121212] border-white/10 text-gray-100 focus-visible:ring-indigo-500 placeholder:text-gray-600 resize-none rounded-xl p-4 pr-24"
              placeholder="What do you want to know?"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  sendMessage();
                }
              }}
            />
            <Button
              className="absolute bottom-3 right-3 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg px-6"
              onClick={() => sendMessage()}
              disabled={loading || !input.trim()}
            >
              Send
            </Button>
          </div>
        </div>
        
      </main>
    </div>
  );
}
