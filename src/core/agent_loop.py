import os
import json
import uuid
import time
from openai import OpenAI
from src.core.memory_manager import SQLiteMemory, VectorMemory
from src.agents.compression_agent import compress_session_memory

def run_agent_loop(user_message: str, session_id: str, tools_schema: list, tool_map: dict):
    sqlite_mem = SQLiteMemory()
    vector_mem = VectorMemory()
    
    client = OpenAI(
        api_key=os.getenv('OLLAMA_API_KEY', 'ollama'), 
        base_url=os.getenv('OLLAMA_BASE_URL', 'http://localhost:11434/v1'),
        default_headers={"ngrok-skip-browser-warning": "true"}
    )
    
    system_instruction = """You are a Multi-Agent Database Copilot and Data Scientist.
You have access to tools to fetch the database schema and execute Python code.
1. ALWAYS use the `get_database_schema` tool first to understand the tables.
2. Write Python code using pandas and SQLAlchemy to query the database.
3. ALWAYS use the `execute_python_code` tool to run your code and see the result.
4. If the code fails, read the error message, fix your code, and try again.
5. NEVER write INSERT, UPDATE, DELETE, or DROP statements. Only SELECT.
6. CRITICAL PERFORMANCE RULE: The server has strict RAM limits. You must NEVER use `SELECT *` without a limit.
   - For aggregations, merges, and grouping, write advanced SQL (Push-down computation) using JOINs, GROUP BY, SUM. Only load final aggregated results into pandas.
   - For scatter plots or raw distribution analysis, ALWAYS append `ORDER BY RANDOM() LIMIT 2000` to safely sample.
7. MANDATORY RESPONSE FORMAT — You must deliver a COMPLETE two-part response:
   - PART 1: COMPREHENSIVE FACTUAL FINDINGS (in Markdown):
     Present clear, structured data answers (tables, KPI bullet points, key takeaways, specific numbers and ratios). Never give a generic or one-sentence brush-off like "Check the render pane". The user needs the detailed factual answer right here in the chat!
   - PART 2: THE FULL EXECUTABLE SCRIPT:
     ALWAYS append the exact, complete Python code you ran inside a ```python ... ``` block at the very end of your response. This is required so the UI can capture the code for interactive inspection.
8. VITAL FOR VISUALIZATIONS — you must adhere to the MODERN FINTECH DARK DASHBOARD style:

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

9. Explain the final result clearly to the user in their language.
"""

    messages = [{"role": "system", "content": system_instruction}]
    history = sqlite_mem.get_recent_context(session_id, limit=10)
    for msg in history:
        messages.append({"role": msg["role"], "content": msg["content"]})
        
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
                "content": "CRITICAL: You have ONE turn left. Summarize your factual data findings and conclusion in clear Markdown tables and bullet points. Include the full Python code you executed."
            })
            
        response = client.chat.completions.create(
            model="gemma4:31b-cloud",
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
                if fname == "execute_python_code":
                    last_executed_code = fargs.get("code_string", last_executed_code)
                if fname in tool_map:
                    res = tool_map[fname](**fargs)
                else:
                    res = "Tool not found"
                tool_results_history.append(str(res))
                messages.append({"role": "tool", "tool_call_id": t.id, "name": fname, "content": str(res)})
        else:
            break
            
    final_text = (msg.content or "")
    if iteration >= max_iterations:
        final_text += "\n\n⚠️ **System Warning:** Hard execution limit reached."
        
    sqlite_mem.add_message(session_id, "assistant", final_text)
    
    # 1. First priority: look for explicit code block in the assistant response
    import re
    match = re.search(r'```(?:python|sql)(.*?)```', final_text, re.DOTALL | re.IGNORECASE)
    if match:
        code_in_text = match.group(1).strip()
        if code_in_text:
            last_executed_code = code_in_text
            final_text = re.sub(r'```(?:python|sql).*?```', '', final_text, flags=re.DOTALL | re.IGNORECASE)
    
    final_text = final_text.strip()
    if not final_text:
        # If assistant returned empty text after stripping code, synthesize factual output from tool results
        clean_results = "\n".join([r for r in tool_results_history if "schema" not in r.lower()])
        final_text = f"### Analysis Summary\n\n{clean_results if clean_results else 'Execution completed successfully. See interactive chart below.'}"
    
    exec_time = time.time() - start_time
    
    return final_text, last_executed_code, None, exec_time

