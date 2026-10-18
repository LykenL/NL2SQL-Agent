import re
import io
import contextlib
import pandas as pd
# A list of keywords we want to completely block the LLM from executing.
DANGEROUS_KEYWORDS = [
    "os.system", "os.remove", "os.rmdir", "subprocess", 
    "shutil", "rm -rf", "sys.exit"
]

def _format_execution_error(exc: Exception, code_string: str, scope: dict) -> str:
    """
    Turn a traceback into a self-correcting tool result.

    The most common LLM failure is referencing a column that does not exist
    (e.g. .agg({'id': 'count'}) on a frame with no `id`). Returning the real
    column names lets the model fix it on the next iteration instead of
    burning the whole budget and handing a crashing script to the UI.
    """
    # Point at the line in the LLM's own script, not the deepest pandas frame.
    tb = exc.__traceback__
    line_no = None
    while tb is not None:
        if tb.tb_frame.f_code.co_filename in ("<string>", "<exec>"):
            line_no = tb.tb_lineno
        tb = tb.tb_next

    lines = code_string.splitlines()
    src_line = lines[line_no - 1].strip() if line_no and 0 < line_no <= len(lines) else ""

    parts = [f"❌ Execution Error: {type(exc).__name__}: {exc}"]
    if line_no:
        parts.append(f"   at line {line_no}: {src_line}" if src_line else f"   at line {line_no}")

    frames = [
        (k, v) for k, v in list(scope.items())
        if not k.startswith("_") and isinstance(v, pd.DataFrame)
    ]
    if frames:
        parts.append("   DataFrames currently in scope (use these exact column names):")
        for name, df in frames:
            parts.append(f"   - {name}: {list(df.columns)}  rows={len(df)}")

    if isinstance(exc, KeyError):
        parts.append(
            "   Hint: a column/label you referenced does not exist. Build the aggregation from the "
            "names listed above, never from an assumed name like 'id'. Example: "
            ".agg(total=('amount', 'sum'), orders=('order_id', 'count'))."
        )
    return "\n".join(parts)


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
        return _format_execution_error(e, code_string, exec_globals)
    finally:
        output_buffer.close()
