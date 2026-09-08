import streamlit as st
import os
import uuid
import json
import time
import pandas as pd
from dotenv import load_dotenv
from openai import OpenAI
from sqlalchemy import create_engine, inspect, text

from agent_tools import create_agent_tools
from memory_manager import SQLiteMemory, VectorMemory
from db_reader import extract_db_schema

# --- 1. Page Config & CSS ---
st.set_page_config(page_title="Data Copilot Pro", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}
    
    .stTextArea textarea {
        background-color: #1e1e2e;
        color: #cdd6f4;
        border: 1px solid #313244;
        border-radius: 8px;
    }
    
    .stButton>button {
        border-radius: 6px;
    }
    
    /* Syntax highlighting tags */
    .tag-int { color: #89b4fa; font-size: 0.8em; padding: 2px 4px; background: rgba(137, 180, 250, 0.1); border-radius: 3px; }
    .tag-str { color: #a6e3a1; font-size: 0.8em; padding: 2px 4px; background: rgba(166, 227, 161, 0.1); border-radius: 3px; }
    .tag-pk { color: #f9e2af; font-weight: bold; font-size: 0.8em; margin-left: 5px; }
    .tag-fk { color: #f38ba8; font-weight: bold; font-size: 0.8em; margin-left: 5px; }
    
    /* Metric Cards */
    div[data-testid="stMetricValue"] {
        font-size: 28px;
        color: #4CAF50;
    }
</style>
""", unsafe_allow_html=True)

# --- 2. Initialize State ---
load_dotenv()
api_key = None

# Try loading from Streamlit Cloud Secrets first
try:
    if "OLLAMA_API_KEY" in st.secrets:
        api_key = st.secrets["OLLAMA_API_KEY"]
except Exception:
    pass

# Fallback to local .env file
if not api_key:
    api_key = os.getenv("OLLAMA_API_KEY", "ollama")

base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
client = OpenAI(api_key=api_key, base_url=base_url)

@st.cache_resource
def get_memories():
    return SQLiteMemory(), VectorMemory()

sqlite_mem, vector_mem = get_memories()

if "db_uri" not in st.session_state:
    st.session_state.db_uri = "sqlite:///examples/company.db"
if "current_session_id" not in st.session_state:
    st.session_state.current_session_id = str(uuid.uuid4())
if "prompt_text" not in st.session_state:
    st.session_state.prompt_text = ""
if "last_sql" not in st.session_state:
    st.session_state.last_sql = ""
if "last_df" not in st.session_state:
    st.session_state.last_df = pd.DataFrame()
if "last_exec_time" not in st.session_state:
    st.session_state.last_exec_time = 0.0

SYS_INST = """You are a Multi-Agent Database Copilot.
You have access to tools to fetch schema and execute Python code.
CRITICAL RULES:
1. NEVER ask for permission or outline plans. ALWAYS execute data analysis proactively.
2. DO NOT show raw Python/SQL code snippets in your conversational text unless specifically asked.
3. For UI rendering purposes, you MUST output exactly ONE comprehensive ```sql block at the very end of your response if a chart or table is expected. The UI will automatically execute this SQL block and render the data.
4. Keep your text explanations extremely concise.
"""

def reset_chat_session():
    st.session_state.messages = [{"role": "system", "content": SYS_INST}]

if "messages" not in st.session_state:
    reset_chat_session()

def generate_title(prompt):
    resp = client.chat.completions.create(
        model="gemma-4-31b-it",
        messages=[{"role": "user", "content": f"Summarize this query into a short title (around 7 words, return just the string): {prompt}"}]
    )
    return resp.choices[0].message.content.strip().replace('"', '')

# --- 3. Sidebar ---
with st.sidebar:
    st.markdown("<div class='ide-title'>Data Copilot</div>", unsafe_allow_html=True)
    st.caption("v2.0 Cognitive IDE")
    st.divider()
    
    with st.expander("Connection Status", expanded=True):
        conn_method = st.radio("Method", ["URL String", "Upload Local File"], horizontal=True, label_visibility="collapsed")
        
        if conn_method == "URL String":
            current_val = st.session_state.db_uri if not st.session_state.db_uri.endswith("uploaded_db.sqlite") else ""
            new_uri = st.text_input("Database URI", value=current_val)
            if new_uri and new_uri != st.session_state.db_uri:
                st.session_state.db_uri = new_uri
                reset_chat_session()
                st.rerun()
        else:
            uploaded_file = st.file_uploader("Upload SQLite file (.db, .sqlite)", type=["db", "sqlite"])
            if uploaded_file is not None:
                with open("uploaded_db.sqlite", "wb") as f:
                    f.write(uploaded_file.getbuffer())
                new_uri = "sqlite:///uploaded_db.sqlite"
                if new_uri != st.session_state.db_uri:
                    st.session_state.db_uri = new_uri
                    reset_chat_session()
                    st.rerun()
                    
        try:
            engine = create_engine(st.session_state.db_uri)
            insp = inspect(engine)
            tables = insp.get_table_names()
            st.success(f"Connected ({len(tables)} tables)")
        except:
            st.error("Disconnected")
            tables = []

    st.divider()
    
    col1, col2 = st.columns(2)
    if col1.button("New Query", use_container_width=True):
        st.session_state.current_session_id = str(uuid.uuid4())
        reset_chat_session()
        st.rerun()
    if col2.button("Save", use_container_width=True):
        st.toast("Session Saved!")

    st.subheader("History")
    search_q = st.text_input("Search history...", placeholder="Keyword...")
    
    tab_recent, tab_saved = st.tabs(["Recent", "Saved"])
    with tab_recent:
        sessions = sqlite_mem.get_all_sessions()
        for sess in sessions:
            if search_q and search_q.lower() not in sess["title"].lower():
                continue
            btn_label = sess['title']
            if sess["id"] == st.session_state.current_session_id:
                btn_label = f"> {btn_label}"
            
            if st.button(btn_label, key=sess["id"], use_container_width=True):
                st.session_state.current_session_id = sess["id"]
                reset_chat_session()
                st.rerun()

# --- 4. Top Info Bar ---
col_m1, col_m2, col_m3 = st.columns(3)
col_m1.metric("Scanned Tables", len(tables) if 'tables' in locals() else 0)
col_m2.metric("Last Exec Time", f"{st.session_state.last_exec_time:.2f}s")
col_m3.metric("Rows Returned", len(st.session_state.last_df))

st.divider()

# --- 5. Main 3-Column Layout ---
col_schema, col_chat, col_prev = st.columns([1, 2, 1])

with col_schema:
    st.subheader("Schema Explorer")
    schema_search = st.text_input("Search tables...", key="schema_search")
    
    try:
        engine = create_engine(st.session_state.db_uri)
        insp = inspect(engine)
        for t in insp.get_table_names():
            if schema_search and schema_search.lower() not in t.lower(): continue
            
            row_count = 0
            try:
                with engine.connect() as conn:
                    row_count = conn.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar()
            except: pass
            
            with st.expander(f"{t} ({row_count} rows)"):
                cols = insp.get_columns(t)
                fks = insp.get_foreign_keys(t)
                
                try:
                    pks = insp.get_pk_constraint(t).get('constrained_columns', [])
                except:
                    pks = []
                
                fk_cols = [fk['constrained_columns'][0] for fk in fks]
                
                for c in cols:
                    cname = c['name']
                    ctype = str(c['type']).lower()
                    
                    # Workaround for drag and drop: Add a + button to append to prompt
                    col_name_html = f"`{cname}`"
                    tag_class = "tag-int" if "int" in ctype or "num" in ctype else "tag-str"
                    html_str = f"<span class='{tag_class}'>{ctype}</span>"
                    if cname in pks: html_str += "<span class='tag-pk'>PK</span>"
                    if cname in fk_cols: html_str += "<span class='tag-fk'>FK</span>"
                    
                    col_left, col_right = st.columns([4, 1])
                    with col_left:
                        st.markdown(f"{col_name_html} {html_str}", unsafe_allow_html=True)
                    with col_right:
                        if st.button("+", key=f"add_{t}_{cname}"):
                            st.session_state.prompt_text += f" {t}.{cname} "
                            st.rerun()
    except Exception as e:
        st.error("Cannot load schema.")


with col_chat:
    st.subheader("Query Workspace")
    
    # Quick Templates
    q_col1, q_col2, q_col3 = st.columns(3)
    if q_col1.button("Top 10 Records"): 
        st.session_state.prompt_text = "Show me the top 10 records from "
        st.rerun()
    if q_col2.button("Aggregate Sum"): 
        st.session_state.prompt_text = "Calculate the total sum of "
        st.rerun()
    if q_col3.button("Join Analysis"): 
        st.session_state.prompt_text = "Join table A and B and find "
        st.rerun()
    
    # Render Chat History
    history_messages = sqlite_mem.get_recent_context(st.session_state.current_session_id, limit=20)
    chat_container = st.container(height=300)
    with chat_container:
        if not history_messages:
            st.info("Start a new conversation below.")
        for msg in history_messages:
            role = "user" if msg["role"] == "user" else "assistant"
            st.chat_message(role).markdown(msg["content"])
    
    # Input Form
    with st.form("chat_form"):
        user_input = st.text_area("What do you want to know?", value=st.session_state.prompt_text, height=250)
        
        c1, c2 = st.columns([1, 1])
        btn_execute = c1.form_submit_button("Execute Query", type="primary")
        btn_preview = c2.form_submit_button("Preview SQL Only")

    if btn_execute or btn_preview:
        st.session_state.prompt_text = user_input # save state
        
        # Check if first message to set title
        if not history_messages:
            title = generate_title(user_input)
            sqlite_mem.set_session_title(st.session_state.current_session_id, title)
            
        sqlite_mem.add_message(st.session_state.current_session_id, "user", user_input)
        
        mode_instruction = "IMPORTANT: Only write SQL in a ```sql block. DO NOT USE ANY TOOLS." if btn_preview else ""
        final_prompt = f"{user_input}\n{mode_instruction}"
        
        start_time = time.time()
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
                st.error(f"Error: {e}")
                
        if 'resp' in locals():
            st.rerun()


with col_prev:
    st.subheader("Quick Preview")
    if not st.session_state.last_df.empty:
        st.dataframe(st.session_state.last_df.head(5), use_container_width=True)
        st.caption(f"Showing top 5 of {len(st.session_state.last_df)} rows")
        st.markdown("[View Full Results](#bottom-results)")
    else:
        st.info("Execute a query to see preview.")
        if st.session_state.last_sql:
            st.code(st.session_state.last_sql, language="sql")

st.divider()

# --- 6. Bottom Results Area ---
st.markdown("<div id='bottom-results'></div>", unsafe_allow_html=True)
st.subheader("Execution Results")

tab_table, tab_chart, tab_sql, tab_export = st.tabs(["Table", "Chart", "SQL", "Export"])

with tab_table:
    if not st.session_state.last_df.empty:
        st.dataframe(st.session_state.last_df, use_container_width=True)
    else:
        st.write("No dataframe available.")

with tab_chart:
    if not st.session_state.last_df.empty:
        try:
            st.bar_chart(st.session_state.last_df)
        except:
            st.error("Could not automatically render chart.")

with tab_sql:
    st.code(st.session_state.last_sql, language="sql")

with tab_export:
    if not st.session_state.last_df.empty:
        csv = st.session_state.last_df.to_csv(index=False).encode('utf-8')
        st.download_button("Download CSV", data=csv, file_name="export.csv", mime="text/csv")
