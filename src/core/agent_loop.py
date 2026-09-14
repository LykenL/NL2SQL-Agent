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
8. VITAL FOR VISUALIZATIONS — choose the right library for the chart type:

   A) For bar, line, scatter, pie, funnel charts → use `plotly.graph_objects` (NOT plotly.express).
      Store the figure in a variable named `fig`. Use professional styling:
      ```python
      import plotly.graph_objects as go
      fig = go.Figure()
      fig.add_trace(go.Bar(
          x=df['category'], y=df['value'],
          marker=dict(color='#8b5cf6', line=dict(width=0)),
          name='Revenue', text=df['value'], textposition='outside',
      ))
      fig.update_layout(
          title=dict(text='Title Here', font=dict(size=16, color='#f3f4f6')),
          bargap=0.25,
          xaxis=dict(showgrid=False, tickfont=dict(size=12)),
          yaxis=dict(gridcolor='rgba(255,255,255,0.08)', zeroline=False),
          hoverlabel=dict(bgcolor='#1f2937', font_size=13),
          showlegend=True,
      )
      ```

   B) For heatmaps, distributions, correlation matrices, faceted multi-panel layouts,
      statistical charts (boxplot, violin, histogram with KDE) → use `altair`.
      Store the chart in a variable named `chart`. Example:
      ```python
      import altair as alt
      chart = alt.Chart(df).mark_bar(
          cornerRadiusTopLeft=5, cornerRadiusTopRight=5
      ).encode(
          x=alt.X('month:O', title='Month', sort='-y'),
          y=alt.Y('revenue:Q', title='Revenue ($)'),
          color=alt.Color('category:N', scale=alt.Scale(scheme='viridis')),
          tooltip=['month:O', 'category:N', alt.Tooltip('revenue:Q', format=',.0f')],
      ).properties(width='container', height=360, title='Revenue by Month & Category')
      ```

   C) Do NOT use matplotlib, seaborn, or plotly.express.
   D) Do NOT manually set dark themes, background colors, or font colors —
      the execution sandbox automatically injects a unified dark theme.

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

