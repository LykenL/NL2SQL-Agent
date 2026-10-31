import os
import re
import sys
import json
import uuid
import time
from openai import OpenAI
from src.core.memory_manager import SQLiteMemory, VectorMemory
from src.core.semantic_registry import SemanticRegistry, format_violations
from src.agents.compression_agent import compress_session_memory

# ── Chat-cleanliness guard ────────────────────────────────────────────────────
# Code must live ONLY in the "Generated Script" pane. The model is told not to
# emit fences, but models drift; this strips any fenced block that still slips
# through so the chat bubble never shows a script.
_CODE_FENCE_RE = re.compile(r"```[ \t]*[A-Za-z0-9_+\-]*[ \t]*\n?[\s\S]*?(?:```|\Z)")
_CODE_LANG_RE = re.compile(r"^```[ \t]*([A-Za-z0-9_+\-]*)", re.MULTILINE)
def strip_code_fences(text: str, hint: str = "") -> str:
    """Remove every fenced code block from a chat reply, leaving prose only.

    Nothing is substituted by default: a placeholder line in the chat bubble is
    itself noise, and the Generated Script pane already carries the code.
    """
    if not text:
        return text
    if "```" not in text:
        return text

    def _repl(match: "re.Match") -> str:
        # Indented fences (whole block indented under a list item) still count.
        return ("\n\n" + hint + "\n\n") if hint else "\n\n"

    cleaned = _CODE_FENCE_RE.sub(_repl, text)
    # Collapse the blank-line runs the removal leaves behind.
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned


def run_agent_loop(
    user_message: str,
    session_id: str,
    tools_schema: list,
    tool_map: dict,
    db_uri: str | None = None,
    trace: dict | None = None,
    data_source=None,
):
    """trace (optional, mutated in place) reports what the semantic layer did:
    {"metrics": [...], "blocked": n, "warned": n}."""
    sqlite_mem = SQLiteMemory()
    vector_mem = VectorMemory()

    # ── Confirmed business definitions for this question ─────────────────────
    # Enforcement is on by default; SEMANTIC_ENFORCE=off disables both the
    # prompt block and the pre-execution check (used for A/B measurement).
    enforce = os.getenv("SEMANTIC_ENFORCE", "on").lower() not in ("0", "off", "false")
    registry = None
    active_metrics: list = []
    if enforce:
        try:
            registry = SemanticRegistry()
            active_metrics = registry.resolve(user_message)
        except Exception as exc:  # a broken registry must never block analysis
            print(f"[semantic] registry unavailable: {exc}", file=sys.stderr)
            registry, active_metrics = None, []

    client = OpenAI(
        api_key=os.getenv('OLLAMA_API_KEY', 'ollama'), 
        base_url=os.getenv('OLLAMA_BASE_URL', 'http://localhost:11434/v1'),
        default_headers={"ngrok-skip-browser-warning": "true"}
    )
    
    system_instruction = """You are a Multi-Agent Database Copilot and Data Scientist.
You have access to tools to fetch the database schema and execute Python code.
1. ALWAYS use the `get_database_schema` tool first to understand the tables.
2. Write Python code using pandas and SQLAlchemy to query the database.
   NEVER assume column names. Use ONLY the identifiers returned by get_database_schema; a fabricated `id`/`name`/`amount` column is the most common crash. Before any groupby/agg/merge/rename, check `df.columns` and build the aggregation from real names, e.g. `.agg(total=('<real_amount_col>', 'sum'))` — never `.agg({'id': 'count'})`.
3. ALWAYS use the `execute_python_code` tool to run your code and see the result.
4. If the code fails, read the error message, fix your code, and try again.
5. NEVER write INSERT, UPDATE, DELETE, or DROP statements. Only SELECT.
6. CRITICAL PERFORMANCE RULE: The server has strict RAM limits. You must NEVER use `SELECT *` without a limit.
   - For aggregations, merges, and grouping, write advanced SQL (Push-down computation) using JOINs, GROUP BY, SUM. Only load final aggregated results into pandas.
   - For scatter plots or raw distribution analysis, ALWAYS append `ORDER BY RANDOM() LIMIT 2000` to safely sample.
7. MANDATORY RESPONSE FORMAT — Your reply has EXACTLY ONE part:
   - COMPREHENSIVE FACTUAL FINDINGS (in Markdown):
     Present clear, structured data answers (tables, KPI bullet points, key takeaways, specific numbers and ratios). Never give a generic or one-sentence brush-off like "Check the render pane". The user needs the detailed factual answer right here in the chat!
8. ZERO CODE IN YOUR REPLY — HARD RULE, NO EXCEPTIONS:
   - NEVER output a fenced code block of any language (```python, ```sql, ```json, or a bare ```). NEVER paste, quote or reprint the script you executed, not even partially, not even "for reference", not even when the user asks for the code.
   - The UI already captures every script you run and renders it in the "Generated Script" panel below the chat. Repeating it in chat is a formatting failure.
   - Refer to the code only in words, e.g. "完整脚本见下方 Generated Script 面板". Prose, tables and KPI bullets only — no source code.
9. VITAL FOR VISUALIZATIONS — you must adhere to the MODERN FINTECH DARK DASHBOARD style:

   A) COLOR & GEOMETRY PALETTE:
      - Primary Bars (Metrics like Volume, Headcount, Users):
        Semi-transparent soft neon purple `rgba(168, 85, 247, 0.60)` with rounded corners `marker=dict(cornerradius=8)`.
      - Highlighting Key/Peak Bar:
        Color the top-performing category/month with semi-transparent Electric Cyan `rgba(0, 242, 254, 0.75)`.
      - Trend / Rate Line (Metrics like Revenue, MRR, Avg Salary):
        Use the COMPLEMENTARY HIGH-CONTRAST VIVID CORAL/AMBER `#ff7a00` (DO NOT use blue/cyan so it never blends with the bars/background)!
        Smooth spline line (`line=dict(color='#ff7a00', width=3.5, shape='spline')`), with prominent WHITE hollow markers (`marker=dict(size=8, color='#ffffff', line=dict(color='#ff7a00', width=2.5))`).
      - Peak Callout Annotations:
        Add 1 or 2 elegant callout annotations pointing to the peak bar or peak line:
        ```python
        fig.add_annotation(
            x=peak_x, y=peak_y, text="<b>Peak: " + peak_label + "</b>",
            showarrow=True, arrowhead=2, arrowcolor="#ff7a00", arrowsize=1,
            bgcolor="#18181b", bordercolor="#ff7a00", borderwidth=1.5,
            font=dict(color="#f8fafc", size=11), borderpad=5
        )
        ```

   B) DUAL-AXIS / MULTI-METRIC PERFORMANCE OVERVIEW:
      - When comparing two metrics (e.g. Sales Volume & Average Salary):
        Use `from plotly.subplots import make_subplots; fig = make_subplots(specs=[[{"secondary_y": True}]])`.
        - Primary Y-axis (left): Semi-transparent Purple Bar trace (`rgba(168, 85, 247, 0.60)`), tickformat="$,.2s".
        - Secondary Y-axis (right): Vivid Coral/Amber Line trace (`#ff7a00`) with white hollow markers, tickformat="$,.2s".
        - Add clean legend at top left (`orientation='h'`, `y=1.08`, `x=0.02`).

   C) FULL REFERENCE CODE TEMPLATE:
      ```python
      import plotly.graph_objects as go
      from plotly.subplots import make_subplots

      fig = make_subplots(specs=[[{"secondary_y": True}]])

      # Semi-transparent bars with cyan highlight on top performer
      max_sales = df['total_sales'].max()
      bar_colors = ['rgba(0, 242, 254, 0.75)' if s == max_sales else 'rgba(168, 85, 247, 0.60)' for s in df['total_sales']]

      fig.add_trace(go.Bar(
          x=df['category'], y=df['total_sales'],
          marker=dict(color=bar_colors, cornerradius=8),
          name='Total Sales ($)',
          text=df['total_sales'],
          texttemplate='$%{text:,.2s}',
          textposition='outside'
      ), secondary_y=False)

      # High-contrast complementary coral line for rate/salary metrics
      fig.add_trace(go.Scatter(
          x=df['category'], y=df['avg_salary'],
          mode='lines+markers',
          line=dict(color='#ff7a00', width=3.5, shape='spline'),
          marker=dict(size=8, color='#ffffff', line=dict(color='#ff7a00', width=2.5)),
          name='Avg Salary ($)'
      ), secondary_y=True)

      fig.update_layout(
          title=dict(text='Performance Overview', font=dict(size=18, color='#f8fafc')),
          bargap=0.35,
          legend=dict(orientation='h', y=1.08, x=0.02, font=dict(size=11, color='#94a3b8')),
          hovermode='x unified',
          xaxis=dict(tickfont=dict(size=11, color='#94a3b8')),
          yaxis=dict(tickformat='$,.2s', gridcolor='rgba(255,255,255,0.05)'),
          yaxis2=dict(tickformat='$,.2s', gridcolor='rgba(255,255,255,0.02)')
      )
      ```

   B) For heatmaps, correlations, distributions, faceted multi-panel layouts → use `altair` (store in `chart`).
      Example:
      ```python
      import altair as alt
      chart = alt.Chart(df).mark_bar(
          cornerRadiusTopLeft=6, cornerRadiusTopRight=6
      ).encode(
          x=alt.X('department:N', title='Department', sort='-y'),
          y=alt.Y('total_sales:Q', title='Total Sales ($)'),
          color=alt.Color('department:N', legend=None),
          tooltip=['department:N', alt.Tooltip('total_sales:Q', format='$,.0f')],
      ).properties(width='container', height=380, title='Department Performance')
      ```

   C) Do NOT use matplotlib, seaborn, or plotly.express.
   D) Do NOT hardcode opaque dark/black backgrounds — the sandbox automatically injects unified glassmorphic dark theming and responsiveness.

10. Explain the final result clearly to the user in their language.
"""

    if trace is not None:
        trace["metrics"] = [m.to_dict() for m in active_metrics]
        trace["blocked"] = 0
        trace["warned"] = 0

    if data_source is not None:
        system_instruction += (
            "\n\n## DATA SOURCE\n"
            f"You are querying `{data_source.label}`. {data_source.dialect_notes()}\n"
        )
        if hasattr(data_source, "freshness_text"):
            system_instruction += f"\n数据最后刷新：{data_source.freshness_text()}\n"
        if getattr(data_source, "supports_estimates", False):
            system_instruction += (
                "This source bills by bytes scanned. Call `estimate_query` before any query that "
                "might read more than a few MB, and prefer partition filters and narrow column lists.\n"
            )

    if active_metrics and registry is not None:
        system_instruction += registry.prompt_block(active_metrics)
        system_instruction += (
            "11. Definitions above are ENFORCED before your code runs. Computing an active metric "
            "from a forbidden alternative is rejected with an error you must fix. If you believe a "
            "definition is wrong, say so in your final answer — do not silently substitute your own."
        )

    messages = [{"role": "system", "content": system_instruction}]
    history = sqlite_mem.get_recent_context(session_id, limit=10)
    for msg in history:
        # Old rows were stored before the no-code rule existed; never replay a
        # script back into the context, otherwise the model imitates it.
        content = msg["content"]
        if msg["role"] in ("assistant", "model"):
            content = strip_code_fences(content)
        messages.append({"role": msg["role"], "content": content})
        
    recalled_memories = vector_mem.search_memory(user_message, n_results=1)
    
    sqlite_mem.add_message(session_id, "user", user_message)
    
    if recalled_memories:
        enriched_input = f"[Recalled Past Knowledge: {recalled_memories[0]}]\n\nCurrent Request: {user_message}"
    else:
        enriched_input = user_message
        
    messages.append({"role": "user", "content": enriched_input})
    
    max_iterations = 7
    iteration = 0
    start_time = time.time()
    
    last_executed_code = ""
    tool_results_history = []
    
    while iteration < max_iterations:
        iteration += 1
        if iteration == max_iterations - 1:
            messages.append({
                "role": "user",
                "content": "CRITICAL: You have ONE turn left. Summarize your factual data findings and conclusion in clear Markdown tables and bullet points. Do NOT include any code, code fences or the script itself — the UI renders it in the Generated Script panel."
            })
            
        response = client.chat.completions.create(
            model=os.getenv("AGENT_MODEL", "nemotron-3-super:cloud"),
            messages=messages,
            tools=tools_schema,
            temperature=0.0
        )
        msg = response.choices[0].message
        
        msg_dict = {"role": msg.role, "content": msg.content or ""}
        if msg.tool_calls:
            msg_dict["tool_calls"] = [{"id": t.id, "type": t.type, "function": {"name": t.function.name, "arguments": t.function.arguments}} for t in msg.tool_calls]
        messages.append(msg_dict)

        if msg.tool_calls:
            for t in msg.tool_calls:
                fname = t.function.name
                try:
                    fargs = json.loads(t.function.arguments)
                except Exception:
                    fargs = {}
                if fname == "run_query" and fargs.get("sql"):
                    # The Generated Script pane must stay re-runnable even when the
                    # agent answered with a plain SQL tool call (typical on BigQuery).
                    import json as _json

                    _rows = fargs.get("max_rows", 1000)
                    last_executed_code = (
                        "sql = " + _json.dumps(fargs["sql"]) + "\n"
                        "df = ds.run(sql, max_rows=" + str(_rows) + ")\n"
                        "print(f'rows={len(df)}')\n"
                        "print(df.to_string(index=False))"
                    )

                if fname == "execute_python_code":
                    candidate = fargs.get("code_string", "")
                    last_executed_code = candidate or last_executed_code

                    # Pre-execution enforcement of confirmed definitions.
                    if active_metrics and registry is not None:
                        violations = registry.validate(candidate, active_metrics)
                        blocked = [v for v in violations if v["severity"] == "block"]
                        if blocked:
                            if trace is not None:
                                trace["blocked"] = trace.get("blocked", 0) + 1
                            res = format_violations(blocked)
                            tool_results_history.append(res)
                            messages.append({"role": "tool", "tool_call_id": t.id,
                                             "name": fname, "content": res})
                            continue

                if fname in tool_map:
                    res = tool_map[fname](**fargs)
                    if fname == "execute_python_code" and active_metrics and registry is not None:
                        warns = [v for v in registry.validate(fargs.get("code_string", ""), active_metrics)
                                 if v["severity"] != "block"]
                        if warns:
                            if trace is not None:
                                trace["warned"] = trace.get("warned", 0) + 1
                            res = str(res) + "\n\n" + format_violations(warns)
                else:
                    res = "Tool not found"
                tool_results_history.append(str(res))
                messages.append({"role": "tool", "tool_call_id": t.id, "name": fname, "content": str(res)})
        else:
            break
            
    raw_text = (msg.content or "")
    if iteration >= max_iterations:
        raw_text += "\n\n⚠️ **System Warning:** Hard execution limit reached."

    # Harvest the code from the raw reply FIRST, then scrub the reply so the
    # chat bubble is prose-only. The script that actually ran in the sandbox
    # always wins; a fenced block in the prose is only a fallback for turns
    # where no tool call was made.
    if not last_executed_code:
        for _m in re.finditer(r'```(?:python|sql)([\s\S]*?)```', raw_text, re.IGNORECASE):
            _candidate = _m.group(1).strip()
            if _candidate:
                last_executed_code = _candidate

    final_text = strip_code_fences(raw_text)
    if not final_text:
        final_text = "Analysis complete. The script is available in the Generated Script panel."

    # Persist the scrubbed version so history replay can never re-teach the model
    # to paste code into the chat.
    sqlite_mem.add_message(session_id, "assistant", final_text)
    
    exec_time = time.time() - start_time
    
    return final_text, last_executed_code, None, exec_time

