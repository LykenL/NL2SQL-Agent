import os
import sys
import uuid
import shutil
from typing import Any
from fastapi import FastAPI, HTTPException, UploadFile, File, Request
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from src.core.memory_manager import SQLiteMemory, VectorMemory
from src.core.db_reader import extract_db_schema
from src.core.agent_loop import run_agent_loop
from src.core.agent_tools import create_agent_tools
from src.core.semantic_registry import Metric, SemanticRegistry, seed_chinook_demo
from src.core.datasource import (
    BigQueryDataSource,
    SqlDataSource,
    configured_bigquery_targets,
    source_from_spec,
)

app = FastAPI(title="Data Copilot API (V2)")

# If a chart never paints, say so instead of leaving an empty dark box.
_CHART_WATCHDOG = """<script>
(function () {
  var deadline = Date.now() + 8000;
  var timer = setInterval(function () {
    var painted = document.querySelector(
      '.plotly-graph-div svg, .plotly-graph-div canvas, .vega-embed svg, .vega-embed canvas, canvas, svg'
    );
    if (painted) { clearInterval(timer); return; }
    if (Date.now() > deadline) {
      clearInterval(timer);
      var d = document.createElement('div');
      d.style.cssText = 'padding:20px 24px;color:#fca5a5;font:13px/1.6 Inter,system-ui,sans-serif';
      d.textContent = '图表没有渲染出来：绘图库没有加载成功（网络拦截或脚本报错）。点 Re-run 重试；反复失败请检查浏览器控制台。';
      document.body.appendChild(d);
    }
  }, 500);
})();
</script>"""


def _plotly_js_path() -> str | None:
    """Absolute path to the plotly bundle that ships with the installed package."""
    import plotly

    candidate = os.path.join(os.path.dirname(plotly.__file__), "package_data", "plotly.min.js")
    return candidate if os.path.exists(candidate) else None


@app.get("/static/plotly.min.js")
def plotly_js():
    path = _plotly_js_path()
    if not path:
        raise HTTPException(status_code=404, detail="plotly bundle not found")
    return FileResponse(path, media_type="application/javascript", headers={"Cache-Control": "public, max-age=86400"})

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
    db_uri: str | None = None
    data_source: str | None = None  # "bigquery:project.dataset" or empty for db_uri


def _build_sha() -> str:
    """Commit the running container was built from (Render/Vercel inject it)."""
    sha = os.getenv("RENDER_GIT_COMMIT") or os.getenv("VERCEL_GIT_COMMIT_SHA") or ""
    return sha[:12] or "dev"


@app.get("/api/health")
async def health_check():
    # `capabilities` lets a frontend detect a stale backend in one request
    # instead of discovering missing routes as 404s.
    return {
        "status": "ok",
        "message": "FastAPI is running",
        "version": _build_sha(),
        "capabilities": ["chat", "execute", "upload", "schema", "metrics"],
    }


@app.get("/api/datasources")
def list_data_sources():
    """Data sources the UI may connect to. BigQuery targets come from env."""
    bq = configured_bigquery_targets()
    has_creds = bool(os.getenv("GOOGLE_APPLICATION_CREDENTIALS"))
    return {
        "sources": bq,
        "bigquery_configured": bool(bq),
        "service_account": has_creds,
        "note": "" if bq else (
            "No BigQuery source available. Set BQ_PROJECT + BQ_PREFIX to expose the "
            "published marts, or BIGQUERY_DATASETS=project.dataset for a raw dataset; "
            "credentials come from GOOGLE_APPLICATION_CREDENTIALS."
        ),
    }


@app.get("/api/marts")
def list_marts(project: str | None = None):
    """Published marts with freshness and the latest DQ status (for the UI)."""
    proj = project or os.getenv("BQ_PROJECT", "woven-surface-511317-g0")
    prefix = os.getenv("BQ_PREFIX", "data_copilot_dev")
    from src.core.mart_source import MartSource

    src = MartSource(project=proj, dataset=f"{prefix}_gold", meta_dataset=f"{prefix}_meta")
    marts = src.freshness()

    dq = {}
    try:
        from google.cloud import bigquery as _bq

        q = f"""
        select status, count(*) as n
        from `{proj}.{prefix}_meta.dq_results`
        where run_at = (select max(run_at) from `{proj}.{prefix}_meta.dq_results`)
        group by status
        """
        rows = list(_bq.Client(project=proj).query(q))
        dq = {r["status"]: r["n"] for r in rows}
    except Exception as exc:
        dq = {"error": str(exc)[:120]}

    return {
        "project": proj,
        "dataset": f"{prefix}_gold",
        "marts": marts,
        "dq": dq,
        "allowed": sorted(src.allowed),
    }


@app.get("/api/health/data")
def data_health(project: str | None = None):
    """
    Data Health: freshness per mart + the latest DQ run.

    Degrades explicitly: without BigQuery credentials it returns
    status="unconfigured" plus what to set, so the UI can say "backend not
    connected" instead of showing a stack trace.
    """
    proj = project or os.getenv("BQ_PROJECT", "woven-surface-511317-g0")
    prefix = os.getenv("BQ_PREFIX", "data_copilot_dev")

    try:
        from google.cloud import bigquery as _bq

        client = _bq.Client(project=proj)

        freshness_sql = f"""
        select table_name,
               last_loaded,
               row_count,
               round(timestamp_diff(current_timestamp(), last_loaded, MINUTE) / 60.0, 2) as age_hours
        from `{proj}.{prefix}_meta.mart_freshness`
        order by table_name
        """
        marts = [dict(r) for r in client.query(freshness_sql)]

        dq_sql = f"""
        select status, count(*) as n
        from `{proj}.{prefix}_meta.dq_results`
        where run_at = (select max(run_at) from `{proj}.{prefix}_meta.dq_results`)
        group by status
        """
        dq_counts = {r["status"]: r["n"] for r in client.query(dq_sql)}

        failing_sql = f"""
        select table_name, check_name, message
        from `{proj}.{prefix}_meta.dq_results`
        where status = 'fail'
          and run_at = (select max(run_at) from `{proj}.{prefix}_meta.dq_results`)
        limit 20
        """
        failing = [dict(r) for r in client.query(failing_sql)]

        last_run = [dict(r) for r in client.query(
            f"select max(run_at) as last_run from `{proj}.{prefix}_meta.dq_results`"
        )]

        oldest = max((m["age_hours"] or 0) for m in marts) if marts else 0
        failing_n = dq_counts.get("fail", 0)
        status = "down" if failing_n else ("stale" if oldest > 26 else "ok")

        return {
            "status": status,
            "project": proj,
            "dataset": f"{prefix}_gold",
            "marts": marts,
            "dq": {
                "counts": dq_counts,
                "failing": failing,
                "last_run": str(last_run[0]["last_run"]) if last_run and last_run[0]["last_run"] else None,
            },
            "oldest_age_hours": oldest,
        }
    except Exception as exc:
        return {
            "status": "unconfigured",
            "project": proj,
            "dataset": f"{prefix}_gold",
            "marts": [],
            "dq": {"counts": {}, "failing": [], "last_run": None},
            "oldest_age_hours": None,
            "message": f"{type(exc).__name__}: {str(exc)[:200]}",
            "hint": "Render 需要 GOOGLE_APPLICATION_CREDENTIALS 指向 Secret File，并设置 BQ_PROJECT / BQ_PREFIX。",
        }


@app.get("/api/schema")
def get_schema(db_uri: str | None = None, data_source: str | None = None):
    try:
        if data_source:
            return {"schema": source_from_spec(data_source).schema_json()}
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

    try:
        data_source = source_from_spec(request.data_source, uri) if request.data_source else SqlDataSource(uri)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Bad data source: {e}")

    tools, tool_map = create_agent_tools(source=data_source)

    trace: dict = {}
    try:
        final_response, sql, df, exec_time = run_agent_loop(
            request.message,
            request.session_id,
            tools,
            tool_map,
            db_uri=None if isinstance(data_source, BigQueryDataSource) else uri,
            trace=trace,
            data_source=data_source,
        )

        return {
            "response": final_response,
            "sql_executed": sql,
            "execution_time_seconds": exec_time,
            "rows_returned": len(df) if df is not None else 0,
            "metrics_used": trace.get("metrics", []),
            "blocked": trace.get("blocked", 0),
            "warned": trace.get("warned", 0),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Semantic metric registry ─────────────────────────────────────────────────
class MetricPayload(BaseModel):
    id: str
    display_name: str
    expr: str
    aliases: list[str] = []
    grain: str = ""
    filters: str = ""
    forbidden_columns: list[str] = []
    owner: str = ""
    source: str = ""


@app.get("/api/metrics")
def list_metrics(status: str | None = None):
    reg = SemanticRegistry()
    return {"metrics": [m.to_dict() for m in reg.list(status=status)]}


@app.post("/api/metrics")
def create_metric(payload: MetricPayload):
    """Create or update a definition as a draft."""
    reg = SemanticRegistry()
    metric = reg.upsert(Metric(**payload.model_dump()))
    return metric.to_dict()


@app.post("/api/metrics/{metric_id}/confirm")
def confirm_metric(metric_id: str, by: str = "unknown", evidence_query_id: str = ""):
    reg = SemanticRegistry()
    try:
        return reg.confirm(metric_id, by=by, evidence_query_id=evidence_query_id).to_dict()
    except KeyError:
        raise HTTPException(status_code=404, detail=f"metric '{metric_id}' not found")


@app.post("/api/metrics/{metric_id}/stale")
def stale_metric(metric_id: str):
    reg = SemanticRegistry()
    try:
        return reg.mark_stale(metric_id).to_dict()
    except KeyError:
        raise HTTPException(status_code=404, detail=f"metric '{metric_id}' not found")


@app.get("/api/metrics/resolve")
def resolve_metrics(q: str, limit: int = 3):
    reg = SemanticRegistry()
    return {"matches": [m.to_dict() for m in reg.resolve(q, limit=limit)]}


@app.post("/api/metrics/seed")
def seed_metrics():
    """Seed the bundled Chinook definitions as drafts (idempotent)."""
    reg = SemanticRegistry()
    return {"metrics": [m.to_dict() for m in seed_chinook_demo(reg)]}


@app.get("/api/sessions")
async def list_sessions():
    memory = SQLiteMemory()
    return {"sessions": memory.get_all_sessions()}


class ExecuteRequest(BaseModel):
    code: str
    db_uri: str | None = None  # so the render pane uses the correct database
    data_source: str | None = None  # "bigquery:project.dataset"


@app.post("/api/execute")
def execute_code(http_request: Request, request: ExecuteRequest):
    import io
    import traceback
    from contextlib import redirect_stdout

    code = request.code

    # Resolve the data source: BigQuery spec, else a SQLAlchemy URI.
    uri = request.db_uri or os.getenv("DATABASE_URL", "sqlite:///examples/databases/company.db")
    if uri and uri.startswith("postgres://"):
        uri = uri.replace("postgres://", "postgresql://", 1)

    try:
        data_source = source_from_spec(request.data_source, uri) if request.data_source else SqlDataSource(uri)
    except Exception as e:
        return {"html": "", "error": f"Bad data source: {e}", "error_detail": "", "stdout": ""}

    local_vars: dict[str, Any] = {"ds": data_source}
    if isinstance(data_source, SqlDataSource):
        local_vars["DATABASE_URI"] = uri
        local_vars["db_uri"] = uri

    output_html = ""
    error_msg = ""
    error_detail = ""

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
                            family="'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
                            color="#94a3b8",
                            size=12,
                        ),
                        title_font=dict(
                            size=16,
                            color="#f8fafc",
                            family="'Inter', -apple-system, sans-serif",
                        ),
                        margin=dict(l=40, r=24, t=64, b=40),
                        hovermode="x unified",
                        hoverlabel=dict(
                            bgcolor="#0f172a",
                            bordercolor="rgba(255,255,255,0.15)",
                            font=dict(color="#f8fafc", size=12, family="Inter, sans-serif"),
                        ),
                        legend=dict(
                            bgcolor="rgba(15,23,42,0.6)",
                            bordercolor="rgba(255,255,255,0.08)",
                            borderwidth=1,
                            font=dict(color="#94a3b8", size=11),
                            orientation="h",
                            yanchor="bottom",
                            y=1.02,
                            xanchor="right",
                            x=1,
                        ),
                        colorway=[
                            "rgba(168, 85, 247, 0.75)",  # Soft Neon Purple
                            "#ff7a00",                   # High-contrast Vivid Coral/Amber
                            "#00f2fe",                   # Electric Cyan
                            "#10b981",                   # Emerald
                            "#ec4899",                   # Pink
                            "#3b82f6",                   # Blue
                        ],
                        xaxis=dict(
                            gridcolor="rgba(255,255,255,0.04)",
                            linecolor="rgba(255,255,255,0.08)",
                            tickcolor="rgba(255,255,255,0.08)",
                            tickfont=dict(color="#94a3b8", size=11),
                            tickangle=0,
                            zerolinecolor="rgba(255,255,255,0.08)",
                        ),
                        yaxis=dict(
                            gridcolor="rgba(255,255,255,0.04)",
                            linecolor="rgba(255,255,255,0.08)",
                            tickcolor="rgba(255,255,255,0.08)",
                            tickfont=dict(color="#94a3b8", size=11),
                            zerolinecolor="rgba(255,255,255,0.06)",
                        ),
                    )
                    # Force-override traces: semi-transparent bars (opacity=0.65) + high-contrast vivid coral line (#ff7a00)
                    if hasattr(fig_to_render, "data") and len(fig_to_render.data) > 0:
                        for trace in fig_to_render.data:
                            trace_type = getattr(trace, "type", "")
                            if trace_type == "bar":
                                trace.opacity = 0.65
                                if hasattr(trace, "marker") and trace.marker:
                                    trace.marker.cornerradius = 8
                                    trace.marker.line = dict(width=0)
                            elif trace_type == "scatter":
                                # Force line color to high-contrast complementary coral
                                if hasattr(trace, "line") and trace.line:
                                    trace.line.color = "#ff7a00"
                                    trace.line.width = 3.5
                                    trace.line.shape = "spline"
                                else:
                                    trace.line = dict(color="#ff7a00", width=3.5, shape="spline")
                                # Force prominent white markers with coral border
                                trace.marker = dict(size=8, color="#ffffff", line=dict(color="#ff7a00", width=2.5))
                            elif trace_type == "pie":
                                if hasattr(trace, "marker") and trace.marker:
                                    trace.marker.line = dict(width=0)

                        # Also sync any annotations arrow & border to coral
                        if hasattr(fig_to_render.layout, "annotations") and fig_to_render.layout.annotations:
                            for ann in fig_to_render.layout.annotations:
                                ann.arrowcolor = "#ff7a00"
                                ann.bordercolor = "#ff7a00"
                except Exception:
                    pass

                raw_plotly = fig_to_render.to_html(
                    full_html=False,
                    include_plotlyjs=f"{str(http_request.base_url).rstrip('/')}/static/plotly.min.js",
                    config={
                        "displayModeBar": "hover",
                        "displaylogo": False,
                        "responsive": True,
                        "modeBarButtonsToRemove": ["lasso2d", "select2d"],
                    },
                )
                output_html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      background: #09090b;
      color: #f8fafc;
      font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      overflow-x: hidden;
      padding: 12px;
    }}
    .dashboard-container {{
      background: #111116;
      border: 1px solid rgba(255, 255, 255, 0.08);
      border-radius: 16px;
      padding: 16px;
      box-shadow: 0 20px 40px -15px rgba(0, 0, 0, 0.7);
      flex: 1;
      display: flex;
      flex-direction: column;
      gap: 12px;
    }}
    .plotly-graph-div {{
      width: 100% !important;
      height: 100% !important;
      flex: 1;
      min-height: 440px;
    }}
  </style>
</head>
<body>
<div class="dashboard-container">
  {raw_plotly}
</div>
</body>
</html>"""

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
            # The canvas shows a readable one-liner; the full traceback goes to
            # logs/`error_detail` so the panel is not wall-to-wall stack frames.
            # Point at the line in the LLM's own script, not the deepest pandas frame.
            tb_ = e.__traceback__
            line_no = None
            while tb_ is not None:
                if tb_.tb_frame.f_code.co_filename in ("<string>", "<exec>"):
                    line_no = tb_.tb_lineno
                tb_ = tb_.tb_next
            lines = code.splitlines()
            src = lines[line_no - 1].strip() if line_no and 0 < line_no <= len(lines) else ""

            error_msg = f"{type(e).__name__}: {e}"
            if line_no:
                error_msg += f"\n  at line {line_no}"
            if src:
                error_msg += f": {' '.join(src.split())}"
            if isinstance(e, KeyError):
                frames = [
                    (k, v) for k, v in local_vars.items()
                    if not k.startswith("_") and hasattr(v, "columns")
                ]
                if frames:
                    error_msg += "\n\n  Available columns:"
                    for name, df in frames:
                        error_msg += f"\n  - {name}: {list(df.columns)}"
            error_detail = traceback.format_exc()
            print(error_detail, file=sys.stderr)

    stdout = f.getvalue()

    # Fallback: wrap stdout in a styled <pre>
    if not output_html and not error_msg:
        output_html = (
            f"<pre style='color:#d1d5db;padding:1.25rem;font-family:\"JetBrains Mono\",monospace;"
            f"font-size:13px;line-height:1.6;white-space:pre-wrap;'>"
            f"{stdout or 'Code executed successfully but produced no output.'}</pre>"
        )

    if output_html:
        output_html = output_html.replace("</body>", _CHART_WATCHDOG + "</body>")

    has_chart = bool(output_html) and ("plotly-graph-div" in output_html or "vega-embed" in output_html)

    return {
        "html": output_html,
        "error": error_msg,
        "error_detail": error_detail,
        "stdout": stdout,
        "has_chart": has_chart,
    }
