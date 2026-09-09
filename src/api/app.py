import streamlit as st
import os
import sys
import uuid
import json
import time 
import pandas as pd
from dotenv import load_dotenv
from openai import OpenAI
from sqlalchemy import create_engine, inspect, text

# Add project root to sys.path so 'src' module can be found
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from src.core.agent_tools import create_agent_tools
from src.core.memory_manager import SQLiteMemory, VectorMemory
from src.core.db_reader import extract_db_schema

# --- 1. Page Config & CSS ---
st.set_page_config(page_title="Agentic Data Copilot", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
    /* Dark theme base - Obsidian palette */
    .stApp {
        background-color: #0a0a0a;
        color: #f8f4f0;
    }
    
    /* Main container styling */
    .main .block-container {
        padding-top: 2rem;
        padding-bottom: 2rem;
    }
    
    /* App title styling - more prominent and elegant */
    .ide-title {
        font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
        font-weight: 900;
        font-size: 2.4rem;
        letter-spacing: -0.8px;
        margin: 1.5rem 0 1rem 0;
        text-align: center;
        background: linear-gradient(135deg, #818cf8 0%, #4f46e5 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        background-clip: text;
        display: inline-block;
        padding: 0.4rem 0;
        box-shadow: 0 6px 20px rgba(99, 102, 241, 0.4);
        text-shadow: 0 2px 4px rgba(0,0,0,0.3);
        position: relative;
    }
    
    /* Add a subtle glow effect to the title */
    .ide-title::after {
        content: '';
        position: absolute;
        bottom: -4px;
        left: 50%;
        transform: translateX(-50%);
        width: 60%;
        height: 2px;
        background: linear-gradient(90deg, transparent, rgba(99, 102, 241, 0.3), transparent);
        border-radius: 2px;
    }
    
    /* Removed legacy Streamlit hash CSS to let the native Dark Theme config work smoothly */
    
    /* Metrics styling */
    div[data-testid="stMetric"] {
        background-color: rgba(30, 30, 30, 0.7);
        border: 1px solid #2a2a2a;
        padding: 1rem;
        border-radius: 8px;
    }
    
    div[data-testid="stMetricLabel"] {
        color: #aaa;
        font-size: 0.9rem;
    }
    
    div[data-testid="stMetricValue"] {
        font-size: 1.8rem;
        font-weight: 600;
        color: #f8f4f0;
    }
    
    /* Expander styling */
    .streamlit-expanderHeader {
        background-color: rgba(30, 30, 30, 0.7);
        color: #f8f4f0;
        font-weight: 600;
        border-radius: 6px;
    }
    
    .streamlit-expanderContent {
        background-color: #0a0a0a;
        border-top: 1px solid #2a2a2a;
    }
    
    /* Tab styling */
    .stTabs [data-baseweb="tab-list"] {
        gap: 8px;
        background-color: #1a1a1a;
        padding: 4px;
        border-radius: 8px;
    }
    
    .stTabs [data-baseweb="tab"] {
        height: 40px;
        white-space: pre-wrap;
        background-color: transparent;
        border-radius: 6px;
        color: #aaa;
        font-size: 0.9rem;
        padding: 0 16px;
    }
    
    .stTabs [aria-selected="true"] {
        background-color: #6366f1;
        color: white;
    }
    
    /* Input widgets */
    .stTextInput>div>div>input {
        background-color: rgba(30, 30, 30, 0.7);
        color: #f8f4f0;
        border: 1px solid #2a2a2a;
        border-radius: 6px;
    }
    
    /* Selectbox */
    .stSelectbox>div>div>div {
        background-color: rgba(30, 30, 30, 0.7);
        color: #f8f4f0;
    }
    
    /* Code styling */
    .stCode {
        background-color: #0a0a0a;
        border-radius: 8px;
        border: 1px solid #2a2a2a;
    }
    
    /* Syntax highlighting tags - indigo/blue theme */
    .tag-int { color: #818cf8; font-size: 0.8em; padding: 2px 4px; background: rgba(99, 102, 241, 0.1); border-radius: 3px; }
    .tag-str { color: #6366f1; font-size: 0.8em; padding: 2px 4px; background: rgba(99, 102, 241, 0.1); border-radius: 3px; }
    .tag-pk { color: #4f46e5; font-weight: bold; font-size: 0.8em; margin-left: 5px; }
    .tag-fk { color: #3730a3; font-weight: bold; font-size: 0.8em; margin-left: 5px; }
    
    /* Dataframe styling */
    .dataframe {
        background-color: #0a0a0a !important;
        color: #f8f4f0 !important;
    }
    
    .dataframe th {
        background-color: rgba(30, 30, 30, 0.7) !important;
        color: #fff !important;
        font-weight: 600 !important;
    }
    
    .dataframe td {
        background-color: #121212 !important;
        color: #f8f4f0 !important;
        border-bottom: 1px solid #2a2a2a !important;
    }
    
    /* Chat message styling */
    .stChatMessage {
        background-color: rgba(30, 30, 30, 0.7);
        border-radius: 10px;
        padding: 1rem;
        margin: 0.5rem 0;
    }
    
    .stChatMessage[data-testid="user"] {
        background-color: #1e293b;
        border-left: 3px solid #818cf8;
    }
    
    .stChatMessage[data-testid="assistant"] {
        background-color: rgba(30, 30, 30, 0.7);
        border-left: 3px solid #6366f1;
    }
    
    /* Scrollbar styling */
    ::-webkit-scrollbar {
        width: 8px;
    }
    
    ::-webkit-scrollbar-track {
        background: #1a1a1a;
    }
    
    ::-webkit-scrollbar-thumb {
        background: #2a2a2a;
        border-radius: 4px;
    }
    
    ::-webkit-scrollbar-thumb:hover {
        background: #3a3a3a;
    }
    
    /* Fix font rendering */
    body, p, div, span, h1, h2, h3, h4, h5, h6, li {
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, 'Open Sans', 'Helvetica Neue', sans-serif;
        font-smoothing: antialiased;
        -webkit-font-smoothing: antialiased;
        -moz-osx-font-smoothing: grayscale;
    }
    
    /* Relying on Streamlit native primaryColor for buttons */
</style>
""", unsafe_allow_html=True)

# --- 2. Initialize State ---
load_dotenv()
api_key = None

# Try loading from Streamlit Cloud Secrets first
base_url = None
try:
    if "OLLAMA_BASE_URL" in st.secrets:
        base_url = st.secrets["OLLAMA_BASE_URL"]
    if "OLLAMA_API_KEY" in st.secrets:
        api_key = st.secrets["OLLAMA_API_KEY"]
except Exception:
    pass

if not api_key:
    api_key = os.getenv("OLLAMA_API_KEY", "ollama")
if not base_url:
    base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")

# Auto-append /v1 if missing
base_url = base_url.rstrip("/")
if not base_url.endswith("/v1"):
    base_url += "/v1"

client = OpenAI(
    api_key=api_key, 
    base_url=base_url,
    default_headers={"ngrok-skip-browser-warning": "true"}
)

@st.cache_resource
def get_memories():
    return SQLiteMemory(), VectorMemory()

sqlite_mem, vector_mem = get_memories()

if "db_uri" not in st.session_state:
     st.session_state.db_uri = "sqlite:///examples/databases/company.db"
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

SYS_INST = """You are an Enterprise Data Intelligence Copilot.
1. Use tools to understand the database schema and execute Python code for detailed exploratory data analysis (EDA).
2. NEVER ask for permission or outline plans. Proactively execute your analysis.
3. Once your analysis is complete, you MUST summarize your findings to the user with bold metrics and actionable insights in a professional tone.
4. In your final text response, you MUST also output EXACTLY ONE ```sql markdown block containing the primary query that reflects your findings, so the frontend can render it.
"""

def reset_chat_session():
    st.session_state.messages = [{"role": "system", "content": SYS_INST}]

if "messages" not in st.session_state:
    reset_chat_session()

def generate_title(prompt):
    resp = client.chat.completions.create(
        model="gemma4:31b-cloud",
        messages=[{"role": "user", "content": f"Summarize this query into a short title (around 7 words, return just the string): {prompt}"}]
    )
    return resp.choices[0].message.content.strip().replace('"', '')

# --- 3. Sidebar ---
with st.sidebar:
    st.markdown("<div class='ide-title'>Agentic Data Copilot</div>", unsafe_allow_html=True)
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
            
            col_btn, col_del = st.columns([0.85, 0.15])
            if col_btn.button(btn_label, key=f"btn_{sess['id']}", use_container_width=True):
                st.session_state.current_session_id = sess["id"]
                reset_chat_session()
                st.rerun()
            if col_del.button("🗑️", key=f"del_{sess['id']}", use_container_width=True, help="Delete session"):
                sqlite_mem.delete_session(sess["id"])
                if sess["id"] == st.session_state.current_session_id:
                    st.session_state.current_session_id = str(uuid.uuid4())
                    reset_chat_session()
                st.rerun()

# --- 4. Top Info Bar ---
col_m1, col_m2, col_m3 = st.columns(3)
col_m1.metric("Scanned Tables", len(tables) if 'tables' in locals() else 0)
col_m2.metric("Last Exec Time", f"{st.session_state.last_exec_time:.2f}s")
col_m3.metric("Rows Returned", len(st.session_state.last_df))

st.divider()

# --- 5. Main 2-Column Layout ---
col_schema, col_chat = st.columns([1, 2])

with col_schema:
    st.subheader("Schema Explorer")
    # Fixed height and scrollable container for schema explorer
    schema_container = st.container(height=600, border=True)
    with schema_container:
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
    chat_container = st.container(height=500, border=True)
    with chat_container:
        if not history_messages:
            st.info("Start a new conversation below.")
        for msg in history_messages:
            role = "user" if msg["role"] == "user" else "assistant"
            st.chat_message(role).markdown(msg["content"])
    
    # Input Form
    with st.form("chat_form"):
        user_input = st.text_area("What do you want to know?", value=st.session_state.prompt_text, height=200)
        
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
                max_iterations = 7
                iteration = 0
                
                while iteration < max_iterations:
                    iteration += 1
                    
                    if iteration == max_iterations - 1:
                        st.session_state.messages.append({
                            "role": "user",
                            "content": "CRITICAL WARNING: You have reached the maximum allowed tool calls. You have ONE turn left. You MUST stop using tools immediately and summarize your findings to the user based on the context above. Do NOT call any more tools."
                        })
                        
                    response = client.chat.completions.create(
                        model="gemma4:31b-cloud",
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
                            try:
                                func_args = json.loads(tool_call.function.arguments)
                            except Exception:
                                func_args = {}
                            
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
                
                if iteration >= max_iterations:
                    final_text = (response_message.content or "") + "\n\n⚠️ **System Warning:** Hard execution limit reached."
                
                st.session_state.last_exec_time = time.time() - start_time
                # Try to extract SQL and remove it from the chat text
                if "```sql" in final_text:
                    parts = final_text.split("```sql", 1)
                    before_code = parts[0]
                    code_and_after = parts[1]
                    if "```" in code_and_after:
                        sql_block = code_and_after.split("```", 1)[0].strip()
                        after_code = code_and_after.split("```", 1)[1]
                    else:
                        sql_block = code_and_after.strip()
                        after_code = ""
                    
                    st.session_state.last_sql = sql_block
                    # Clean up the final text that goes into the chat bubble
                    final_text = (before_code + after_code).strip()
                    
                    # If Execute mode, try to fetch DF directly for the UI Preview
                    if btn_execute:
                        engine = create_engine(st.session_state.db_uri)
                        st.session_state.last_df = pd.read_sql(sql_block, engine)
                
                sqlite_mem.add_message(st.session_state.current_session_id, "model", final_text)
            except Exception as e:
                import traceback
                st.error(f"❌ Connection Failed! Targeting: {client.base_url}")
                st.code(traceback.format_exc(), language="python")
                
        if 'resp' in locals():
            st.rerun()

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
            import plotly.io as pio
            import plotly.express as px
            pio.templates.default = "plotly_dark"
            df = st.session_state.last_df
            if len(df.columns) >= 2:
                num_cols = df.select_dtypes(include=['number']).columns.tolist()
                cat_cols = df.select_dtypes(exclude=['number']).columns.tolist()
                date_cols = df.select_dtypes(include=['datetime', 'datetimetz']).columns.tolist()
                
                # Fallback heuristic: check if any cat_col looks like a date
                if not date_cols and cat_cols:
                    for col in cat_cols:
                        if df[col].astype(str).str.match(r'^\d{4}-\d{2}-\d{2}').any():
                            date_cols.append(col)
                            cat_cols.remove(col)

                if len(df) == 1 and num_cols:
                    # Single row of aggregated metrics (e.g., total customers, total tracks)
                    melted_df = df[num_cols].melt(var_name="Metric", value_name="Value")
                    fig = px.bar(melted_df, x="Metric", y="Value", title="📊 Aggregated Metrics", color="Metric", text="Value")
                    fig.update_traces(texttemplate='%{text:.2s}', textposition='outside')
                    st.plotly_chart(fig, use_container_width=True)
                elif date_cols and num_cols:
                    fig = px.line(df, x=date_cols[0], y=num_cols, title="📈 Time Series Trend")
                    fig.update_xaxes(rangeslider_visible=True)
                    st.plotly_chart(fig, use_container_width=True)
                elif cat_cols and num_cols:
                    cat_col = cat_cols[0]
                    num_col = num_cols[0]
                    unique_cats = df[cat_col].nunique()
                    if unique_cats <= 10:
                        fig = px.pie(df, names=cat_col, values=num_col, title="🍩 Category Breakdown", hole=0.4)
                    else:
                        sorted_df = df.sort_values(by=num_col, ascending=False).head(20)
                        fig = px.bar(sorted_df, x=cat_col, y=num_col, title="📊 Top 20 Category Ranking", color=num_col, color_continuous_scale="Viridis")
                    st.plotly_chart(fig, use_container_width=True)
                elif len(num_cols) >= 2:
                    fig = px.scatter(df, x=num_cols[0], y=num_cols[1], title="🔍 Correlation Scatter", color=num_cols[1], color_continuous_scale="Plasma")
                    st.plotly_chart(fig, use_container_width=True)
                else:
                    st.line_chart(df)
            else:
                st.line_chart(df)
        except Exception as e:
            st.error(f"Could not automatically render chart: {e}")

with tab_sql:
    st.code(st.session_state.last_sql, language="sql")

with tab_export:
    if not st.session_state.last_df.empty:
        csv = st.session_state.last_df.to_csv(index=False).encode('utf-8')
        st.download_button("Download CSV", data=csv, file_name="export.csv", mime="text/csv")
