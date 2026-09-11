import re

with open("app/page.tsx", "r") as f:
    content = f.read()

# Replace dangerouslySetInnerHTML with iframe
old_div = '<div dangerouslySetInnerHTML={{ __html: renderHtml }} className="w-full h-full" />'
new_div = '<iframe srcDoc={renderHtml} className="w-full h-full border-0 bg-transparent rounded-xl" sandbox="allow-scripts allow-popups" />'

content = content.replace(old_div, new_div)

# While we are at it, we should ensure the HTML returned by FastAPI is a full HTML page so the iframe renders it properly.
# Actually, full_html=True is better for iframe, but full_html=False works too (browsers forgive missing <html> tags in srcdoc).
# Let's update backend to use full_html=True.

with open("app/page.tsx", "w") as f:
    f.write(content)
