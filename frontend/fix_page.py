import re

with open("app/page.tsx", "r") as f:
    content = f.read()

# 1. Add state variables
state_vars = """
  const [activeCode, setActiveCode] = useState<string>("");
  const [renderHtml, setRenderHtml] = useState<string>("");
  const [renderError, setRenderError] = useState<string>("");
  const [rendering, setRendering] = useState<boolean>(false);
"""
content = re.sub(r'const \[activeCode, setActiveCode\] = useState<string>\(""\);', state_vars.strip(), content)

# 2. Add execute function
exec_func = """
  const executeCode = async () => {
    if (!activeCode) return;
    setRendering(true);
    setRenderError("");
    setRenderHtml("");
    try {
      const res = await fetch("http://localhost:8000/api/execute", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code: activeCode }),
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

  useEffect(() => {
"""
content = re.sub(r'useEffect\(\(\) => {', exec_func.strip() + "\n", content, count=1)

# 3. Update the Render tab content
old_render = """<div className="flex-1 rounded-2xl border-2 border-dashed border-white/10 bg-white/[0.01] flex flex-col items-center justify-center text-center p-8 space-y-6 hover:border-purple-500/30 hover:bg-purple-500/5 transition-all group">
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
              </div>"""

new_render = """
              {renderHtml ? (
                 <div className="flex-1 bg-white overflow-auto rounded-xl">
                    <div dangerouslySetInnerHTML={{ __html: renderHtml }} className="w-full h-full" />
                 </div>
              ) : renderError ? (
                 <div className="flex-1 bg-red-950/20 border border-red-500/30 rounded-xl p-4 overflow-auto text-red-400 text-xs font-mono whitespace-pre-wrap">
                    {renderError}
                 </div>
              ) : (
                <div className="flex-1 rounded-2xl border-2 border-dashed border-white/10 bg-white/[0.01] flex flex-col items-center justify-center text-center p-8 space-y-6">
                  <div className="w-16 h-16 rounded-full bg-purple-500/10 flex items-center justify-center">
                    <Play className="w-8 h-8 text-purple-400/60" />
                  </div>
                  <div className="space-y-2">
                    <h3 className="text-lg font-bold text-gray-200">Backend Execution Engine</h3>
                    <p className="text-sm text-gray-500 leading-relaxed max-w-[250px]">
                      Execute the generated Python code securely on the FastAPI backend and render interactive Plotly charts natively here.
                    </p>
                  </div>
                  <Button 
                    onClick={executeCode} 
                    disabled={rendering || !activeCode}
                    className="bg-purple-600/20 text-purple-300 hover:bg-purple-600/40 border border-purple-500/30 rounded-xl px-6 py-5 shadow-[0_0_15px_rgba(168,85,247,0.15)] font-bold"
                  >
                    {rendering ? "Executing Code..." : "Run Code & Render"}
                  </Button>
                </div>
              )}
              { (renderHtml || renderError) && (
                 <div className="mt-4 flex justify-end">
                    <Button onClick={() => { setRenderHtml(""); setRenderError(""); }} variant="outline" className="border-white/10 text-xs">
                        Clear Render
                    </Button>
                 </div>
              )}
"""

content = content.replace(old_render, new_render.strip())

with open("app/page.tsx", "w") as f:
    f.write(content)
