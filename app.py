import streamlit as st
import uuid

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

# Initialize session state defaults BEFORE checking chat_session
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

if "chat_session" not in st.session_state:
    reset_chat_session()
