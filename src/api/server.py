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
async def get_schema(db_uri: str = None):
    try:
        # Default fallback for testing
        uri = db_uri or os.getenv("DATABASE_URL", "sqlite:///examples/databases/company.db")
        schema_text = extract_db_schema(uri)
        return {"schema": schema_text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/chat")
async def chat(request: ChatRequest):
    """
    Standard chat endpoint (Returns full JSON at the end, not streaming).
    For streaming, you'd typically implement an AsyncGenerator with EventSourceResponse,
    but here we wrap the standard agent_loop.
    """
    uri = request.db_uri or os.getenv("DATABASE_URL", "sqlite:///examples/databases/company.db")
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
