import os
import re

# 1. Update agent_tools.py
with open("agent_tools.py", "r") as f:
    tools_code = f.read()

new_tools_code = """import time
import json
from db_reader import extract_db_schema
from executor import safe_execute

def create_agent_tools(db_uri: str) -> tuple:
    def get_database_schema(force_refresh: bool = False) -> str:
        return extract_db_schema(db_uri, force_refresh=force_refresh)
    
    def execute_python_code(code_string: str) -> str:
        print("\\n[Tool execution] execute_python_code called. Pacing for rate limits (4s)...")
        time.sleep(4)
        output = safe_execute(code_string, custom_globals={"db_uri": db_uri})
        if "❌" not in output:
            directive = "\\n\\n[SYSTEM DIRECTIVE]: Execution successful. Keep your final text response extremely concise and DO NOT repeat the raw code in the chat."
            return output + directive
        return output

    tools_schema = [
        {
            "type": "function",
            "function": {
                "name": "get_database_schema",
                "description": "Retrieves the complete JSON schema of the target relational database. Call this tool FIRST whenever you are asked to analyze data.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "force_refresh": {
                            "type": "boolean",
                            "description": "Set to True to bypass the schema cache and re-scan the database."
                        }
                    },
                    "required": []
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "execute_python_code",
                "description": "Executes Python code locally and returns the standard output or error tracebacks. The code automatically has a global variable named `db_uri` injected. Use pandas and SQLAlchemy.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "code_string": {
                            "type": "string",
                            "description": "The raw executable Python code. Do not include markdown code block formatting (like ```python)."
                        }
                    },
                    "required": ["code_string"]
                }
            }
        }
    ]

    tool_map = {
        "get_database_schema": get_database_schema,
        "execute_python_code": execute_python_code
    }

    return tools_schema, tool_map
"""

with open("agent_tools.py", "w") as f:
    f.write(new_tools_code)


# 2. Update app.py
with open("app.py", "r") as f:
    app_code = f.read()

# Replace google genai import with openai
app_code = app_code.replace("from google import genai", "from openai import OpenAI")

# Replace API key loading
api_key_block_old = """# Try loading from Streamlit Cloud Secrets first
try:
    if "GEMINI_API_KEY" in st.secrets:
        api_key = st.secrets["GEMINI_API_KEY"]
except Exception:
    pass

# Fallback to local .env file
if not api_key:
    api_key = os.getenv("GEMINI_API_KEY")

if not api_key:
    st.error("GEMINI_API_KEY not found.")
    st.info("💡 **If running locally**: Add it to your `.env` file.\\n\\n💡 **If on Streamlit Cloud**: Go to `App Settings` -> `Secrets`, and paste: \\n\\n`GEMINI_API_KEY = \\"your_api_key_here\\"`")
    st.stop()"""

api_key_block_new = """# Try loading from Streamlit Cloud Secrets first
try:
    if "OLLAMA_API_KEY" in st.secrets:
        api_key = st.secrets["OLLAMA_API_KEY"]
except Exception:
    pass

# Fallback to local .env file
if not api_key:
    api_key = os.getenv("OLLAMA_API_KEY", "ollama")

base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
client = OpenAI(api_key=api_key, base_url=base_url)"""

app_code = app_code.replace(api_key_block_old, api_key_block_new)

# Replace chat_session logic
old_chat_session_logic = """def reset_chat_session():
    if "genai_client" not in st.session_state:
        st.session_state.genai_client = genai.Client(api_key=api_key)
        
    st.session_state.chat_session = st.session_state.genai_client.chats.create(
        model="gemma-4-31b-it",
        config={"tools": create_agent_tools(st.session_state.db_uri), "system_instruction": SYS_INST, "temperature": 0.0}
    )

if "chat_session" not in st.session_state:
    reset_chat_session()

def generate_title(prompt):
    client = genai.Client(api_key=api_key)
    resp = client.models.generate_content(
        model="gemma-4-31b-it",
        contents=f"Summarize this query into a short title (around 7 words, return just the string): {prompt}"
    )
    return resp.text.strip().replace('"', '')"""

new_chat_session_logic = """def reset_chat_session():
    st.session_state.messages = [{"role": "system", "content": SYS_INST}]

if "messages" not in st.session_state:
    reset_chat_session()

def generate_title(prompt):
    resp = client.chat.completions.create(
        model="gemma-4-31b-it",
        messages=[{"role": "user", "content": f"Summarize this query into a short title (around 7 words, return just the string): {prompt}"}]
    )
    return resp.choices[0].message.content.strip().replace('"', '')"""

app_code = app_code.replace(old_chat_session_logic, new_chat_session_logic)

# Replace execute logic
old_exec_logic = """        start_time = time.time()
        with st.spinner("Processing..."):
            try:
                resp = st.session_state.chat_session.send_message(final_prompt)
                st.session_state.last_exec_time = time.time() - start_time
                sqlite_mem.add_message(st.session_state.current_session_id, "model", resp.text)
                
                # Try to extract SQL
                if "```sql" in resp.text:
                    sql_block = resp.text.split("```sql")[1].split("```")[0].strip()
                    st.session_state.last_sql = sql_block
                    # If Execute mode, try to fetch DF directly for the UI Preview
                    if btn_execute:
                        engine = create_engine(st.session_state.db_uri)
                        st.session_state.last_df = pd.read_sql(sql_block, engine)
            except Exception as e:
                st.error(f"Error: {e}")"""

new_exec_logic = """        start_time = time.time()
        with st.spinner("Processing..."):
            try:
                st.session_state.messages.append({"role": "user", "content": final_prompt})
                tools_schema, tool_map = create_agent_tools(st.session_state.db_uri)
                
                while True:
                    response = client.chat.completions.create(
                        model="gemma-4-31b-it",
                        messages=st.session_state.messages,
                        tools=tools_schema,
                        temperature=0.0
                    )
                    
                    response_message = response.choices[0].message
                    # convert openai obj to dict for appending
                    msg_dict = {"role": response_message.role, "content": response_message.content}
                    if response_message.tool_calls:
                        msg_dict["tool_calls"] = [{"id": t.id, "type": t.type, "function": {"name": t.function.name, "arguments": t.function.arguments}} for t in response_message.tool_calls]
                    
                    st.session_state.messages.append(msg_dict)
                    
                    tool_calls = response_message.tool_calls
                    if tool_calls:
                        for tool_call in tool_calls:
                            func_name = tool_call.function.name
                            func_args = json.loads(tool_call.function.arguments)
                            
                            if func_name in tool_map:
                                result = tool_map[func_name](**func_args)
                            else:
                                result = f"Error: Tool {func_name} not found."
                                
                            st.session_state.messages.append({
                                "tool_call_id": tool_call.id,
                                "role": "tool",
                                "name": func_name,
                                "content": str(result)
                            })
                    else:
                        final_text = response_message.content or ""
                        break

                st.session_state.last_exec_time = time.time() - start_time
                sqlite_mem.add_message(st.session_state.current_session_id, "model", final_text)
                
                # Try to extract SQL
                if "```sql" in final_text:
                    sql_block = final_text.split("```sql")[1].split("```")[0].strip()
                    st.session_state.last_sql = sql_block
                    # If Execute mode, try to fetch DF directly for the UI Preview
                    if btn_execute:
                        engine = create_engine(st.session_state.db_uri)
                        st.session_state.last_df = pd.read_sql(sql_block, engine)
            except Exception as e:
                st.error(f"Error: {e}")"""

app_code = app_code.replace(old_exec_logic, new_exec_logic)

with open("app.py", "w") as f:
    f.write(app_code)


# 3. Update evaluator.py
with open("evaluator.py", "r") as f:
    eval_code = f.read()

eval_code = eval_code.replace("from google import genai", "from openai import OpenAI\\nimport os")
eval_code = eval_code.replace("client = genai.Client()", "client = OpenAI(api_key=os.getenv('OLLAMA_API_KEY', 'ollama'), base_url=os.getenv('OLLAMA_BASE_URL', 'http://localhost:11434/v1'))")

# evaluator.py uses genai's chat_session... this is too complex to regex perfectly without breaking other things.
# I will skip evaluator and compression agent for a moment, or rewrite them later if they are needed right now.
# We focus on the main app.py first.

print("Migration script executed.")
