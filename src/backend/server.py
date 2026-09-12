import os
import sys
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from src.core.memory_manager import SQLiteMemory, VectorMemory
from src.core.db_reader import extract_db_schema
from src.core.agent_loop import run_agent_loop
from src.core.agent_tools import create_agent_tools

app = FastAPI(title="Data Copilot API (V2)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ChatRequest(BaseModel):
    session_id: str
    message: str
    db_uri: str = None

@app.get("/api/health")
async def health_check():
    return {"status": "ok", "message": "FastAPI is running"}

@app.get("/api/schema")
def get_schema(db_uri: str = None):
    try:
        # Default fallback for testing
        uri = db_uri or os.getenv("DATABASE_URL", "sqlite:///examples/databases/company.db")
        if uri and uri.startswith("postgres://"):
            uri = uri.replace("postgres://", "postgresql://", 1)
        schema_text = extract_db_schema(uri)
        return {"schema": schema_text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/chat")
def chat(request: ChatRequest):
    """
    Standard chat endpoint (Returns full JSON at the end, not streaming).
    For streaming, you'd typically implement an AsyncGenerator with EventSourceResponse,
    but here we wrap the standard agent_loop.
    """
    uri = request.db_uri or os.getenv("DATABASE_URL", "sqlite:///examples/databases/company.db")
    if uri and uri.startswith("postgres://"):
        uri = uri.replace("postgres://", "postgresql://", 1)
    tools, tool_map = create_agent_tools(uri)
    
    # Run the agent (this is synchronous and will block; for a real production app, run in thread/async)
    try:
        final_response, sql, df, exec_time = run_agent_loop(
            request.message, 
            request.session_id, 
            tools, 
            tool_map
        )
        
        return {
            "response": final_response,
            "sql_executed": sql,
            "execution_time_seconds": exec_time,
            "rows_returned": len(df) if df is not None else 0
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/sessions")
async def list_sessions():
    memory = SQLiteMemory()
    return {"sessions": memory.get_all_sessions()}

class ExecuteRequest(BaseModel):
    code: str

@app.post("/api/execute")
def execute_code(request: ExecuteRequest):
    import io
    import traceback
    from contextlib import redirect_stdout
    
    code = request.code
    
    # 1. 自动兼容并替换大模型可能生成的 Streamlit 渲染语句
    # 大模型经常写 import streamlit as st; st.plotly_chart(fig)
    code = code.replace("import streamlit as st", "")
    code = code.replace("st.plotly_chart(", "__captured_fig = (")
    code = code.replace("st.write(", "print(")
    code = code.replace("st.dataframe(", "print(")
    
    # 连接当前数据库
    uri = os.getenv("DATABASE_URL", "sqlite:///examples/databases/company.db")
    if uri and uri.startswith("postgres://"):
        uri = uri.replace("postgres://", "postgresql://", 1)
    local_vars = {"DATABASE_URI": uri, "db_uri": uri}
    
    output_html = ""
    error_msg = ""
    
    f = io.StringIO()
    with redirect_stdout(f):
        try:
            # 运行代码。将 local_vars 同时传给 globals 和 locals，防止大模型使用 globals() 找不到变量
            exec(code, local_vars, local_vars)
            
            # 2. 尝试从本地变量里捕获 Plotly 图像
            fig_to_render = local_vars.get("__captured_fig") or local_vars.get("fig")
            
            # 如果没找到，扫描所有变量，找类型为 Figure 的
            if not fig_to_render:
                for k, v in local_vars.items():
                    if hasattr(v, 'to_html') and type(v).__name__ in ['Figure', 'FigureWidget']:
                        fig_to_render = v
                        break
                        
            # 如果找到了 Plotly 图表，转换成独立 HTML
            if fig_to_render:
                try:
                    # 强制套用深色极客主题
                    fig_to_render.update_layout(
                        template="plotly_dark",
                        plot_bgcolor="rgba(0,0,0,0)",
                        paper_bgcolor="rgba(0,0,0,0)",
                        font=dict(family="system-ui, -apple-system, sans-serif", color="#9ca3af"),
                        margin=dict(l=40, r=40, t=60, b=40),
                        hovermode="x unified",
                        colorway=["#8b5cf6", "#a855f7", "#6366f1", "#ec4899", "#14b8a6"]
                    )
                    # 柔化边缘
                    if hasattr(fig_to_render, "data") and len(fig_to_render.data) > 0:
                        fig_to_render.update_traces(marker=dict(line=dict(width=0)), selector=dict(type='bar'))
                        fig_to_render.update_traces(marker=dict(line=dict(width=0)), selector=dict(type='pie'))
                except Exception:
                    pass
                output_html = fig_to_render.to_html(full_html=True, include_plotlyjs='cdn')
                
        except Exception as e:
            error_msg = traceback.format_exc()
            
    stdout = f.getvalue()
    
    # 如果没有图表且没有报错，就把 print 结果包在 pre 里当做纯文本返回
    if not output_html and not error_msg:
        output_html = f"<pre style='color: #d1d5db; padding: 1rem; font-family: monospace;'>{stdout or 'Code executed successfully but produced no output.'}</pre>"
        
    return {
        "html": output_html,
        "error": error_msg,
        "stdout": stdout
    }
