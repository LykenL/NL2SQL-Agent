import re
import io
import contextlib
# A list of keywords we want to completely block the LLM from executing.
DANGEROUS_KEYWORDS = [
    "os.system", "os.remove", "os.rmdir", "subprocess", 
    "shutil", "rm -rf", "sys.exit"
]

def extract_code(llm_response: str) -> str:
    """
    Extracts the pure Python code from the Markdown code block.
    If no code block is found, it returns the raw text.
    """
    pattern = r"```python(.*?)```"
    match = re.search(pattern, llm_response, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()
    
    # Fallback: Just return the raw response if no markdown block was used
    return llm_response.strip()

def safe_execute(code_string: str, custom_globals: dict = None) -> str:
    """
    Performs static security checks, then executes the code dynamically using exec().
    Captures any print() outputs or errors and returns them as a string.
    Accepts custom_globals to inject objects (like db engines) into the runtime environment.
    """
    # 1. Static Security Check
    for keyword in DANGEROUS_KEYWORDS:
        if keyword in code_string:
            return f"🚨 SECURITY ALERT: Blocked dangerous keyword '{keyword}' in the code."

    # 2. Dynamic Execution with Output Capture
    exec_globals = custom_globals if custom_globals is not None else {}
    output_buffer = io.StringIO()
    
    try:
        # Redirect standard output (print statements) into our buffer
        with contextlib.redirect_stdout(output_buffer):
            exec(code_string, exec_globals)
        
        # CAPTURE DATAFRAMES: Look for any pandas DataFrames created in the global scope
        # and automatically print them so they appear in the tool output.
        for var_name, var_val in exec_globals.items():
            if not var_name.startswith("__") and isinstance(var_val, pd.DataFrame):
                output_buffer.write(f"\n\n[DataFrame: {var_name}]\n{var_val.to_string()}\n")
        
        return output_buffer.getvalue()
    except Exception as e:
        return f"❌ Execution Error: {str(e)}"
    finally:
        output_buffer.close()
