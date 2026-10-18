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
7. MANDATORY: In your final explanation, include the EXACT Python code you executed in a ```python ... ``` markdown block.
8. VITAL FOR VISUALIZATIONS — you must adhere to the MODERN FINTECH DARK DASHBOARD style (Neo-Dark Aesthetic with Neon Purple & Cyan):

   A) COLOR & GEOMETRY PALETTE (100% MATCH REFERENCE DESIGN):
      - Primary Bars (Metrics like Volume, Users, Headcount):
        Soft neon purple `rgba(168, 85, 247, 0.75)` or `#a855f7` with large rounded corners `marker=dict(cornerradius=8)`.
      - Highlighting Key/Peak Bar:
        Color the top-performing category/month with Electric Cyan `#00f2fe` (e.g. `colors = ['#00f2fe' if v == max_val else '#a855f7' for v in values]`).
      - Trend / Rate Line (Metrics like Revenue, MRR, Salary):
        Neon Electric Cyan `#00f2fe` smooth line (`line=dict(shape='spline', width=3)`), with prominent WHITE hollow markers (`marker=dict(size=8, color='#ffffff', line=dict(color='#00f2fe', width=2.5))`).
      - Peak Callout Annotations:
        Add 1 or 2 elegant callout annotations pointing to the peak bar or peak line:
        ```python
        fig.add_annotation(
            x=peak_x, y=peak_y, text="<b>Peak: " + peak_label + "</b>",
            showarrow=True, arrowhead=2, arrowcolor="#00f2fe", arrowsize=1,
            bgcolor="#18181b", bordercolor="#00f2fe", borderwidth=1.5,
            font=dict(color="#f8fafc", size=11), borderpad=5
        )
        ```

   B) DUAL-AXIS / MULTI-METRIC PERFORMANCE OVERVIEW:
      - When comparing two metrics (e.g. Sales Volume & Average Salary, or MRR & Users):
        Use `from plotly.subplots import make_subplots; fig = make_subplots(specs=[[{"secondary_y": True}]])`.
        - Primary Y-axis (left): Bar trace with clean tick formatting (`tickformat="$,.2s"` or similar).
        - Secondary Y-axis (right): Cyan spline line trace with white markers.
        - Add clean legend at top left (`orientation='h'`, `y=1.08`, `x=0.05`).

   C) FULL REFERENCE CODE TEMPLATE:
      ```python
      import plotly.graph_objects as go
      from plotly.subplots import make_subplots

      # Create dual-axis performance overview
      fig = make_subplots(specs=[[{"secondary_y": True}]])

      # Highlight peak bar with electric cyan
      max_sales = df['total_sales'].max()
      bar_colors = ['#00f2fe' if s == max_sales else 'rgba(168, 85, 247, 0.75)' for s in df['total_sales']]

      fig.add_trace(go.Bar(
          x=df['category'], y=df['total_sales'],
          marker=dict(color=bar_colors, cornerradius=8),
          name='Total Sales ($)',
          text=df['total_sales'],
          texttemplate='$%{text:,.2s}',
          textposition='outside'
      ), secondary_y=False)

      fig.add_trace(go.Scatter(
          x=df['category'], y=df['avg_salary'],
          mode='lines+markers',
          line=dict(color='#00f2fe', width=3, shape='spline'),
          marker=dict(size=8, color='#ffffff', line=dict(color='#00f2fe', width=2.5)),
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
    
    while iteration < max_iterations:
        iteration += 1
        if iteration == max_iterations - 1:
            messages.append({
                "role": "user",
                "content": "CRITICAL WARNING: You have reached the maximum allowed tool calls. You have ONE turn left. You MUST stop using tools immediately and summarize your findings to the user based on the context above. Do NOT call any more tools."
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
                if fname in tool_map:
                    res = tool_map[fname](**fargs)
                else:
                    res = "Tool not found"
                messages.append({"role": "tool", "tool_call_id": t.id, "name": fname, "content": str(res)})
        else:
            break
            
    final_text = (msg.content or "")
    if iteration >= max_iterations:
        final_text += "\n\n⚠️ **System Warning:** Hard execution limit reached."
        
    sqlite_mem.add_message(session_id, "assistant", final_text)
    
    # We parse the python code block out if it exists
    import re
    executed_code = ""
    match = re.search(r'```(?:python|sql)(.*?)```', final_text, re.DOTALL | re.IGNORECASE)
    if match:
        executed_code = match.group(1).strip()
        final_text = re.sub(r'```(?:python|sql).*?```', '\n*(Code pushed to right Render pane)*\n', final_text, flags=re.DOTALL | re.IGNORECASE)
    
    final_text = final_text.strip()
    if not final_text:
        final_text = "I have finished processing your request. Please check the Code/Render pane for the results."
    exec_time = time.time() - start_time
    
    return final_text, executed_code, None, exec_time

