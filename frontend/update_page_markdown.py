import re

with open("app/page.tsx", "r") as f:
    content = f.read()

# Add import
import_statement = "import ReactMarkdown from 'react-markdown';\nimport remarkGfm from 'remark-gfm';\n"
content = content.replace('import { Tabs', import_statement + 'import { Tabs')

# Replace message rendering
old_msg = """<p className="whitespace-pre-wrap leading-relaxed text-[15px]">
                    {msg.content}
                  </p>"""
new_msg = """<div className="prose prose-invert max-w-none text-[15px] prose-p:leading-relaxed prose-pre:bg-black/20 prose-pre:border prose-pre:border-white/10 prose-code:text-indigo-200">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>
                      {msg.content}
                    </ReactMarkdown>
                  </div>"""

content = content.replace(old_msg, new_msg)

with open("app/page.tsx", "w") as f:
    f.write(content)
