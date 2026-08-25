import time
from db_reader import extract_db_schema
from executor import safe_execute

def create_agent_tools(db_uri: str) -> list:
    """
    Returns a list of tools (functions) that the Google Gemini Agent can call autonomously.
    We use closures here to inject the db_uri into the tools without requiring the LLM to provide it.
    """
    
    def get_database_schema() -> str:
        """
        Retrieves the complete JSON schema of the target relational database.
        Call this tool FIRST whenever you are asked to analyze data, so you know what tables and columns exist.
        """
        print("\n[Tool execution] get_database_schema called. Pacing for rate limits (4s)...")
        time.sleep(4)
        return extract_db_schema(db_uri)
        
    def execute_python_code(code_string: str) -> str:
        """
        Executes Python code locally and returns the standard output (print statements) or error tracebacks.
        The executed code automatically has a global variable named `db_uri` injected into it, which is the database connection string.
        You must use pandas and SQLAlchemy to query the database using this `db_uri` variable.
        
        Args:
            code_string: The raw executable Python code. Do not include markdown code block formatting (like ```python).
        """
        print("\n[Tool execution] execute_python_code called. Pacing for rate limits (4s)...")
        time.sleep(4)
        output = safe_execute(code_string, custom_globals={"db_uri": db_uri})
        
        # 【黑客技巧】：在大模型接收工具执行结果时，利用“近因效应”狠狠敲打它
        if "❌" not in output:
            directive = "\n\n[SYSTEM DIRECTIVE]: Execution successful. You MUST include the exact Python code you just ran inside a ```python block in your final explanation to the user. Do not omit the code!"
            return output + directive
        return output
        
    # Return the functions themselves as tools
    return [get_database_schema, execute_python_code]
