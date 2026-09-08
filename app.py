import streamlit as st
import os
import uuid
import json
import time
import pandas as pd
import plotly.express as px
from dotenv import load_dotenv
from google import genai
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
    
    /* Global Theme Overrides */
    .stApp {
        background-color: #0f0f1a;
    }

    /* Card-like Containers */
    .custom-card {
        background-color: #1a1a2e;
        border: 1px solid #313244;
        border-radius: 12px;
        padding: 20px;
        margin-bottom: 20px;
        box-shadow: 0 4px 6px rgba(0,0,0,0.3);
    }

    .stTextArea textarea {
        background-color: #1e1e2e !important;
        color: #cdd6f4 !important;
        border: 1px solid #313244 !important;
        border-radius: 8px !important;
    }
    
    .stButton>button {
        border-radius: 8px;
        transition: all 0.2s ease;
    }
    
    .stButton>button:hover {
        border-color: #89b4fa;
        box-shadow: 0 0 8px rgba(137, 180, 250, 0.3);
    }
    
    /* Syntax highlighting tags */
    .tag-int { color: #89b4fa; font-size: 0.8em; padding: 2px 4px; background: rgba(137, 180, 250, 0.1); border-radius: 3px; }
    .tag-str { color: #a6e3a1; font-size: 0.8em; padding: 2px 4px; background: rgba(166, 227, 161, 0.1); border-radius: 3px; }
    .tag-pk { color: #f9e2af; font-weight: bold; font-size: 0.8em; margin-left: 5px; }
    .tag-fk { color: #f38ba8; font-weight: bold; font-size: 0.8em; margin-left: 5px; }
    
    /* Custom Metric Cards */
    .metric-container {
        display: flex;
        justify-content: space-between;
        gap: 10px;
        margin-bottom: 20px;
    }
    .metric-card {
        background: linear-gradient(135deg, #1e1e2e 0%, #11111b 100%);
        border: 1px solid #313244;
        border-radius: 12px;
        padding: 15px;
        flex: 1;
        text-align: center;
    }
    .metric-label {
        color: #a6adc8;
        font-size: 0.9em;
        margin-bottom: 5px;
        text-transform: uppercase;
        letter-spacing: 1px;
    }
    .metric-value {
        color: #89b4fa;
        font-size: 24px;
        font-weight: bold;
    }

    /* Status Indicator */
    .status-indicator {
        display: inline-block;
        width: 10px;
        height: 10px;
        border-radius: 50%;
        margin-right: 8px;
        background-color: #a6e3a1;
        box-shadow: 0 0 8px #a6e3a1;
        animation: pulse 2s infinite;
    }
    @keyframes pulse {
        0% { opacity: 1; }
        50% { opacity: 0.4; }
        100% { opacity: 1; }
    }
</style>
""", unsafe_allow_html=True)

# --- 2. Initialize State ---
load_dotenv()
api_key = None

def resolve_db_uri(uri: str) -> str:
    """
    Resolves a database URI to a valid SQLAlchemy connection string.
    Handles relative paths, absolute paths, raw file paths, and remote URIs.
    """
    if not uri:
        return ""
    
    # 1. Handle Remote Databases (Postgres, MySQL, etc.)
    # These should be passed directly to SQLAlchemy
    remote_protocols = ("postgresql://", "mysql://", "mssql://", "oracle://", "mariadb://")
    if any(uri.startswith(proto) for proto in remote_protocols):
        return uri

    # 2. Handle raw absolute paths (no protocol)
    if uri.startswith("/") and not uri.startswith("sqlite"):
        return f"sqlite:////{uri}"
    
    # 3. Handle SQLite URIs
    if uri.startswith("sqlite"):
        # Separate protocol from path
        # sqlite:///path (relative) or sqlite:////path (absolute)
        path = uri.replace("sqlite:///", "")
        if path.startswith("/"):
            # Already absolute
            return uri
        
        # Resolve relative path against app.py directory
        app_dir = os.path.dirname(os.path.abspath(__file__))
        abs_path = os.path.join(app_dir, path)
        return f"sqlite:////{abs_path}"
    
    return uri

# Try loading from Streamlit Cloud Secrets first
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
    st.info("💡 **If running locally**: Add it to your `.env` file.\n\n💡 **If on Streamlit Cloud**: Go to `App Settings` -> `Secrets`, and paste: \n\n`GEMINI_API_KEY = \"your_api_key_here\"`")
    st.stop()

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
IMPORTANT: ALWAYS write the generated SQL query in a ```sql block so the UI can render it.
"""

def reset_chat_session():
    if "genai_client" not in st.session_state:
        st.session_state.genai_client = genai.Client(api_key=api_key)
        
    st.session_state.chat_session = st.session_state.genai_client.chats.create(
        model="gemini-3.5-flash-lite",
        config={"tools": create_agent_tools(st.session_state.db_uri), "system_instruction": SYS_INST, "temperature": 0.0}
    )

if "chat_session" not in st.session_state:
    reset_chat_session()

def generate_title(prompt):
    client = genai.Client(api_key=api_key)
    resp = client.models.generate_content(
        model="gemini-3.5-flash-lite",
        contents=f"Summarize this query into a short title (around 7 words, return just the string): {prompt}"
    )
    return resp.text.strip().replace('"', '')

# --- 3. Sidebar ---
with st.sidebar:
    st.markdown("""
        <div style='display: flex; align-items: center; gap: 10px; margin-bottom: 5px;'>
            <div class='status-indicator'></div>
            <div style='font-size: 1.5em; font-weight: bold; color: #cdd6f4;'>Data Copilot</div>
        </div>
        <div style='color: #a6adc8; font-size: 0.8em; margin-bottom: 20px;'>v2.0 Cognitive IDE</div>
    """, unsafe_allow_html=True)
    st.divider()
    
    with st.expander("Connection Settings", expanded=True):
        conn_mode = st.radio("Connection Mode", ["Remote/URI", "Upload SQLite"], horizontal=True)
        
        if conn_mode == "Remote/URI":
            st.text_input("Database URI", key="new_uri", value=st.session_state.db_uri)
        else:
            uploaded_file = st.file_uploader("Upload .sqlite / .db file", type=["sqlite", "db", "sqlite3"])
            if uploaded_file:
                # Save uploaded file to a temporary location
                temp_path = os.path.join("/tmp", uploaded_file.name)
                with open(temp_path, "wb") as f:
                    f.write(uploaded_file.getbuffer())
                
                # Update URI to point to the uploaded file
                new_uri = f"sqlite:////{temp_path}"
                if st.session_state.db_uri != new_uri:
                    st.session_state.db_uri = new_uri
                    reset_chat_session()
                    st.rerun()
        
        # Resolve URI for internal use
        resolved_uri = resolve_db_uri(st.session_state.db_uri)
        
        if "new_uri" in locals() and st.session_state.new_uri != st.session_state.db_uri:
            st.session_state.db_uri = st.session_state.new_uri
            reset_chat_session()
            st.rerun()
            
        try:
            # Check if file exists on disk first (only for SQLite)
            if resolved_uri.startswith("sqlite:////"):
                db_file = resolved_uri.replace("sqlite:////", "")
                if not os.path.exists(db_file):
                    st.error(f"File not found: {db_file}")
                    tables = []
                else:
                    engine = create_engine(resolved_uri)
                    insp = inspect(engine)
                    tables = insp.get_table_names()
                    st.success(f"Connected ({len(tables)} tables)")
            else:
                # Remote databases or other URIs
                engine = create_engine(resolved_uri)
                insp = inspect(engine)
                tables = insp.get_table_names()
                st.success(f"Connected ({len(tables)} tables)")
        except Exception as e:
            st.error(f"Connection Error: {e}")
            tables = []
            
        st.caption(f"CWD: {os.getcwd()}")

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
metrics_html = f"""
<div class='metric-container'>
    <div class='metric-card'>
        <div class='metric-label'>Scanned Tables</div>
        <div class='metric-value'>{len(tables) if 'tables' in locals() else 0}</div>
    </div>
    <div class='metric-card'>
        <div class='metric-label'>Last Exec Time</div>
        <div class='metric-value'>{st.session_state.last_exec_time:.2f}s</div>
    </div>
    <div class='metric-card'>
        <div class='metric-label'>Rows Returned</div>
        <div class='metric-value'>{len(st.session_state.last_df)}</div>
    </div>
</div>
"""
st.markdown(metrics_html, unsafe_allow_html=True)

st.divider()

# --- 5. Main 3-Column Layout ---
col_schema, col_chat, col_prev = st.columns([1, 2, 1])

with col_schema:
    with st.container(border=True):
        st.subheader("Schema Explorer")
        schema_search = st.text_input("Search tables...", key="schema_search")
        
        try:
            resolved_uri = resolve_db_uri(st.session_state.db_uri)
            engine = create_engine(resolved_uri)
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
    with st.container(border=True):
        st.subheader("Query Workspace")
        
        # Quick Templates
        q_col1, q_col2, q_col3 = st.columns(3)
        if q_col1.button("Top 10 Records", use_container_width=True): 
            st.session_state.prompt_text = "Show me the top 10 records from "
            st.rerun()
        if q_col2.button("Aggregate Sum", use_container_width=True): 
            st.session_state.prompt_text = "Calculate the total sum of "
            st.rerun()
        if q_col3.button("Join Analysis", use_container_width=True): 
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
            btn_execute = c1.form_submit_button("Execute Query", type="primary", use_container_width=True)
            btn_preview = c2.form_submit_button("Preview SQL Only", use_container_width=True)

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
                    resp = st.session_state.chat_session.send_message(final_prompt)
                    st.session_state.last_exec_time = time.time() - start_time
                    sqlite_mem.add_message(st.session_state.current_session_id, "model", resp.text)
                    
                    # Try to extract SQL
                    if "```sql" in resp.text:
                        sql_block = resp.text.split("```sql")[1].split("```")[0].strip()
                        st.session_state.last_sql = sql_block
                        # If Execute mode, try to fetch DF directly for the UI Preview
                        if btn_execute:
                            resolved_uri = resolve_db_uri(st.session_state.db_uri)
                            engine = create_engine(resolved_uri)
                            st.session_state.last_df = pd.read_sql(sql_block, engine)
                except Exception as e:
                    st.error(f"Error: {e}")
                    
            if 'resp' in locals():
                st.rerun()


with col_prev:
    with st.container(border=True):
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
            # Try to identify numeric columns for a better chart
            numeric_cols = st.session_state.last_df.select_dtypes(include=['number']).columns.tolist()
            if len(numeric_cols) >= 1:
                # Use the first numeric column as Y, and the first non-numeric (or first numeric) as X
                x_col = st.session_state.last_df.columns[0]
                y_col = numeric_cols[0]
                
                fig = px.bar(
                    st.session_state.last_df, 
                    x=x_col, 
                    y=y_col, 
                    template="plotly_dark",
                    color_discrete_sequence=["#89b4fa"]
                )
                fig.update_layout(
                    paper_bgcolor='rgba(0,0,0,0)',
                    plot_bgcolor='rgba(0,0,0,0)',
                    margin=dict(l=20, r=20, t=20, b=20)
                )
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("No numeric columns found to plot.")
        except Exception as e:
            st.error(f"Could not automatically render chart: {e}")
    else:
        st.info("No data to visualize.")

with tab_sql:
    st.code(st.session_state.last_sql, language="sql")

with tab_export:
    if not st.session_state.last_df.empty:
        csv = st.session_state.last_df.to_csv(index=False).encode('utf-8')
        st.download_button("Download CSV", data=csv, file_name="export.csv", mime="text/csv")
