import os
import sys
import uuid
import shutil
from fastapi import FastAPI, HTTPException, UploadFile, File
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

# Temp upload directory (created on startup)
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "../../../tmp/uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)


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
        uri = db_uri or os.getenv("DATABASE_URL", "sqlite:///examples/databases/company.db")
        if uri and uri.startswith("postgres://"):
            uri = uri.replace("postgres://", "postgresql://", 1)
        schema_text = extract_db_schema(uri)
        return {"schema": schema_text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/upload")
def upload_database(file: UploadFile = File(...)):
    """
    Accepts a .db / .sqlite / .csv file upload.
    - SQLite/DB files are saved directly and returned as sqlite:/// URIs.
    - CSV files are converted to a single-table SQLite via pandas and returned as sqlite:/// URIs.
    Returns: { db_uri, filename, tables }
    """
    import pandas as pd
    from sqlalchemy import create_engine, inspect

    original_filename = file.filename or "uploaded"
    ext = os.path.splitext(original_filename)[1].lower()
    safe_stem = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in os.path.splitext(original_filename)[0])
    unique_id = uuid.uuid4().hex[:8]

    if ext in (".db", ".sqlite"):
        dest_filename = f"{unique_id}_{safe_stem}.sqlite"
        dest_path = os.path.join(UPLOAD_DIR, dest_filename)
        with open(dest_path, "wb") as f:
            shutil.copyfileobj(file.file, f)

    elif ext == ".csv":
        # Read CSV and write to a new SQLite file
        dest_filename = f"{unique_id}_{safe_stem}.sqlite"
        dest_path = os.path.join(UPLOAD_DIR, dest_filename)
        try:
            df = pd.read_csv(file.file)
            table_name = safe_stem[:50] or "data"
            engine = create_engine(f"sqlite:///{dest_path}")
            df.to_sql(table_name, engine, if_exists="replace", index=False)
            engine.dispose()
        except Exception as e:
            raise HTTPException(status_code=422, detail=f"Failed to parse CSV: {e}")

    else:
        raise HTTPException(
            status_code=415,
            detail="Unsupported file type. Please upload a .db, .sqlite, or .csv file."
        )

    # Build an absolute SQLite URI using four slashes for absolute paths
    abs_dest = os.path.abspath(dest_path)
    db_uri = f"sqlite:///{abs_dest}"

    # Inspect tables for the response
    try:
        engine = create_engine(db_uri)
        insp = inspect(engine)
        tables = insp.get_table_names()
        engine.dispose()
    except Exception:
        tables = []

    return {
        "db_uri": db_uri,
        "filename": original_filename,
        "tables": tables,
    }


@app.post("/api/chat")
def chat(request: ChatRequest):
    """
    Standard chat endpoint (returns full JSON at the end, not streaming).
    """
    uri = request.db_uri or os.getenv("DATABASE_URL", "sqlite:///examples/databases/company.db")
    if uri and uri.startswith("postgres://"):
        uri = uri.replace("postgres://", "postgresql://", 1)
    tools, tool_map = create_agent_tools(uri)

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
    db_uri: str = None  # Now accepts db_uri so the render pane uses the correct database


@app.post("/api/execute")
def execute_code(request: ExecuteRequest):
    import io
    import traceback
    from contextlib import redirect_stdout

    code = request.code

    # Auto-replace Streamlit rendering calls the LLM might generate
    code = code.replace("import streamlit as st", "")
    code = code.replace("st.plotly_chart(", "__captured_fig = (")
    code = code.replace("st.write(", "print(")
    code = code.replace("st.dataframe(", "print(")

    # Resolve the database URI: prefer the one sent by the frontend, fall back to env
    uri = request.db_uri or os.getenv("DATABASE_URL", "sqlite:///examples/databases/company.db")
    if uri and uri.startswith("postgres://"):
        uri = uri.replace("postgres://", "postgresql://", 1)

    local_vars = {"DATABASE_URI": uri, "db_uri": uri}

    output_html = ""
    error_msg = ""

    f = io.StringIO()
    with redirect_stdout(f):
        try:
            exec(code, local_vars, local_vars)

            # ── 1. Try to capture a Plotly Figure ────────────────────────────
            fig_to_render = local_vars.get("__captured_fig") or local_vars.get("fig")

            if not fig_to_render:
                for k, v in local_vars.items():
                    if not k.startswith("__") and hasattr(v, "to_html") and type(v).__name__ in ("Figure", "FigureWidget"):
                        fig_to_render = v
                        break

            if fig_to_render:
                try:
                    fig_to_render.update_layout(
                        template="plotly_dark",
                        plot_bgcolor="rgba(0,0,0,0)",
                        paper_bgcolor="rgba(0,0,0,0)",
                        font=dict(
                            family="Inter, system-ui, -apple-system, sans-serif",
                            color="#d1d5db",
                            size=13,
                        ),
                        title_font=dict(size=16, color="#f3f4f6"),
                        margin=dict(l=48, r=32, t=72, b=48),
                        hovermode="x unified",
                        hoverlabel=dict(bgcolor="#1f2937", bordercolor="#374151", font_size=13),
                        legend=dict(
                            bgcolor="rgba(0,0,0,0)",
                            bordercolor="rgba(255,255,255,0.1)",
                            borderwidth=1,
                            font=dict(color="#9ca3af"),
                        ),
                        colorway=["#8b5cf6", "#a855f7", "#6366f1", "#ec4899", "#14b8a6",
                                  "#f59e0b", "#10b981", "#3b82f6"],
                        xaxis=dict(
                            gridcolor="rgba(255,255,255,0.06)",
                            linecolor="rgba(255,255,255,0.1)",
                            tickcolor="rgba(255,255,255,0.1)",
                            zerolinecolor="rgba(255,255,255,0.1)",
                        ),
                        yaxis=dict(
                            gridcolor="rgba(255,255,255,0.06)",
                            linecolor="rgba(255,255,255,0.1)",
                            tickcolor="rgba(255,255,255,0.1)",
                            zerolinecolor="rgba(255,255,255,0.08)",
                        ),
                    )
                    # Soften bar/pie edges
                    if hasattr(fig_to_render, "data") and len(fig_to_render.data) > 0:
                        fig_to_render.update_traces(
                            marker=dict(line=dict(width=0)), selector=dict(type="bar")
                        )
                        fig_to_render.update_traces(
                            marker=dict(line=dict(width=0)), selector=dict(type="pie")
                        )
                except Exception:
                    pass
                output_html = fig_to_render.to_html(full_html=True, include_plotlyjs="cdn")

            # ── 2. If no Plotly figure, try to capture an Altair Chart ───────
            if not output_html:
                try:
                    import altair as alt

                    # Altair chart types we want to capture
                    _altair_base = (
                        alt.Chart,
                        alt.LayerChart,
                        alt.HConcatChart,
                        alt.VConcatChart,
                        alt.ConcatChart,
                        alt.FacetChart,
                        alt.RepeatChart,
                    )

                    alt_to_render = local_vars.get("chart")

                    if not alt_to_render:
                        for k, v in local_vars.items():
                            if not k.startswith("__") and isinstance(v, _altair_base):
                                alt_to_render = v
                                break

                    if alt_to_render and isinstance(alt_to_render, _altair_base):
                        # Apply unified dark theme via Altair's configure API
                        styled = (
                            alt_to_render
                            .configure(background="transparent")
                            .configure_view(stroke="transparent", fill="transparent")
                            .configure_axis(
                                labelColor="#9ca3af",
                                titleColor="#d1d5db",
                                labelFont="Inter, system-ui, sans-serif",
                                titleFont="Inter, system-ui, sans-serif",
                                gridColor="rgba(255,255,255,0.07)",
                                gridOpacity=1,
                                domainColor="rgba(255,255,255,0.12)",
                                tickColor="rgba(255,255,255,0.12)",
                                titleFontSize=13,
                                labelFontSize=12,
                            )
                            .configure_legend(
                                labelColor="#9ca3af",
                                titleColor="#d1d5db",
                                labelFont="Inter, system-ui, sans-serif",
                                titleFont="Inter, system-ui, sans-serif",
                                strokeColor="rgba(255,255,255,0.1)",
                                fillColor="rgba(15,15,15,0.6)",
                                padding=8,
                                cornerRadius=6,
                                labelFontSize=12,
                                titleFontSize=12,
                            )
                            .configure_title(
                                color="#f3f4f6",
                                font="Inter, system-ui, sans-serif",
                                fontSize=15,
                                fontWeight="bold",
                                anchor="start",
                                offset=16,
                            )
                            .configure_header(
                                labelColor="#9ca3af",
                                titleColor="#d1d5db",
                                labelFont="Inter, system-ui, sans-serif",
                            )
                        )

                        raw_html = styled.to_html()

                        # Wrap in a dark shell so the iframe background matches
                        output_html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ background: transparent; display: flex; align-items: center; justify-content: center; min-height: 100vh; padding: 12px; }}
    .vega-embed {{ width: 100% !important; }}
    .vega-embed summary {{ display: none !important; }}
  </style>
</head>
<body>
{raw_html}
</body>
</html>"""

                except ImportError:
                    pass  # altair not installed, skip silently

        except Exception as e:
            error_msg = traceback.format_exc()

    stdout = f.getvalue()

    # Fallback: wrap stdout in a styled <pre>
    if not output_html and not error_msg:
        output_html = (
            f"<pre style='color:#d1d5db;padding:1.25rem;font-family:\"JetBrains Mono\",monospace;"
            f"font-size:13px;line-height:1.6;white-space:pre-wrap;'>"
            f"{stdout or 'Code executed successfully but produced no output.'}</pre>"
        )

    return {
        "html": output_html,
        "error": error_msg,
        "stdout": stdout,
    }
