import re

def fix_text(final_text):
    executed_code = ""
    # Find python or sql blocks
    match = re.search(r'```(?:python|sql)(.*?)```', final_text, re.DOTALL | re.IGNORECASE)
    if match:
        executed_code = match.group(1).strip()
        final_text = re.sub(r'```(?:python|sql).*?```', '\n*(Code pushed to right Render pane)*\n', final_text, flags=re.DOTALL | re.IGNORECASE)
    return final_text.strip(), executed_code

