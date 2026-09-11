import time
import json
from src.core.db_reader import extract_db_schema
from src.core.executor import safe_execute

def create_agent_tools(db_uri: str) -> tuple:
    def get_database_schema(force_refresh: bool = False) -> str:
        return extract_db_schema(db_uri, force_refresh=force_refresh)
    
    def execute_python_code(code_string: str) -> str:
        print("\n[Tool execution] execute_python_code called. Pacing for rate limits (4s)...")
        time.sleep(4)
        output = safe_execute(code_string, custom_globals={"db_uri": db_uri})
        if "❌" not in output:
            directive = "\n\n[SYSTEM DIRECTIVE]: Execution successful. Keep your final text response extremely concise and DO NOT repeat the raw code in the chat."
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
                "description": "Executes Python code locally and returns the standard output or error tracebacks. The code automatically has a global variable named `DATABASE_URI` injected. Use pandas and SQLAlchemy. IMPORTANT: Whenever asked to visualize, analyze, or plot data, you MUST generate an interactive chart using `plotly.express` and store the final figure in a variable named `fig`. Do NOT just print text statistics unless explicitly asked.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "code_string": {
                            "type": "string",
                            "description": "The raw executable Python code. ALWAYS output clean, complete Python code. Do not include markdown code block formatting."
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
