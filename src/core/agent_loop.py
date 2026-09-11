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
6. MANDATORY: In your final explanation to the user, you MUST include the EXACT Python code you successfully executed, formatted in a ```python ... ``` markdown block.
7. VITAL FOR VISUALIZATIONS: If the user requests a chart or visualization, your Python code MUST import `plotly.express` and store the final figure in a variable exactly named `fig`. Do NOT use matplotlib.
8. Explain the final result clearly to the user in their language.
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

