# 🎯 面试通关指南：Multi-Agent Data Copilot 技术解析
# 🎯 Interview Guide: Multi-Agent Data Copilot Technical Deep Dive

这份指南专为 DS（数据科学家）和 AI Engineer（AI 算法/研发工程师）的面试设计。
This guide is designed for Data Scientist (DS) and AI Engineer interviews. Interviewers care about the engineering pain points you solved and the architectural decisions you made, not just the APIs you called.

---

## 1. 一分钟电梯演讲 (Elevator Pitch)

**🇺🇸 English Version:**
> "I developed a Multi-Agent Data Copilot from scratch. It is not just a Text-to-SQL chatbot, but an autonomous data scientist. By utilizing Native Tool Calling, I empowered the agent to autonomously fetch database schemas and execute Python code in a secure sandbox. To solve the context window overflow issue during long multi-turn conversations, I designed a Dual-Memory architecture (SQLite for episodic memory + ChromaDB for semantic memory) and implemented a background compression agent for asynchronous knowledge retrieval (RAG). Ultimately, the system achieved a 75% success rate on a highly adversarial dataset I built."

**🇨🇳 中文话术:**
> “我独立从 0 到 1 开发了一个基于 Multi-Agent 架构的 Data Copilot。它不仅仅是一个能够 Text-to-SQL 的聊天机器人，而是一个真正的智能体数据科学家。我通过 Native Tool Calling 赋予了它读取数据库 Schema 和沙盒执行 Python 代码的能力。为了解决多轮长对话的上下文爆炸问题，我设计了 SQLite (短时) + ChromaDB (长时) 的双轨记忆系统，并引入了一个后台异步运转的记忆压缩大模型，实现了真正的跨会话语义知识召回。最终，这套系统在我自己构建的包含‘幻觉陷阱’的对抗性测评集上达到了 75% 的通过率。”

---

## 2. 核心技术亮点 (Core Technical Highlights)

### 🌟 亮点 1：基于 ReAct 范式的 Tool Calling 闭环 (ReAct-based Tool Calling Loop)
*   **Technical Detail (技术细节)**: The system utilizes a "Sense -> Act -> Observe" loop rather than one-way code generation.
*   **How to Pitch (话术)**: 
    *   **EN**: "Instead of blindly generating SQL, I provided the agent with tools: `get_database_schema` and `execute_python_code`. The core innovation is that I capture the sandbox Traceback errors. If the execution fails, the error is fed back to the LLM as a Tool Result, triggering a **Self-Correction** loop where the agent rewrites and re-executes the code until it succeeds."
    *   **CN**: “我摒弃了单纯让大模型盲写 SQL 的弱架构，而是给了它探测 Schema 和沙盒执行两个工具。最核心的是，我捕获了沙盒的报错 (Traceback)，一旦执行失败，报错信息会扔回给大模型，触发反思（Self-Correction）并自己重写代码，直到跑通。”

### 🌟 亮点 2：Dual-Engine 双轨记忆架构 (Dual-Engine Memory Architecture)
*   **Technical Detail (技术细节)**: Solves the "goldfish memory" (amnesia) and Token overflow issues inherent in LLMs.
*   **How to Pitch (话术)**: 
    *   **EN**: "I designed an enterprise-grade memory system: 1) A frontend **SQLite** database for Episodic Memory to render UI history. 2) A background `compression_agent` that asynchronously distills conversations into high-dimensional logic chunks, stored in **ChromaDB** for Semantic Memory. 3) When a new session starts, it performs a vector similarity search (RAG) to silently inject learned business rules into the current context."
    *   **CN**: “为了解决上下文溢出，我设计了双轨系统：1) 前台用 SQLite 做情景记忆。2) 后台写了一个独立 Agent，在闲时将对话浓缩，存入 ChromaDB 向量数据库做语义记忆。3) 新对话开启时，系统通过 RAG 检索，悄无声息地将过去学到的业务规则注入到当前上下文中。”

---

## 3. 踩过的坑与硬核解决方案 (Hard Problems Solved)
*Prepare these three stories as they demonstrate strong engineering capability (展示你的工程解决能力):*

### 🧨 踩坑 1：大模型被工具“劫持”不吐文字 (Recency Bias & Tool Hijacking)
*   **Problem (问题)**: After executing tools and receiving large data outputs, the LLM would often stop generating text, resulting in a blank UI with only raw data (failing to show the code it wrote).
*   **Solution (解法)**: 
    *   **EN**: "I leveraged the LLM's 'Recency Bias'. Inside the sandbox, after returning the actual execution result, I hardcoded a **System Directive prompt injection** at the very end: `"You MUST include the exact Python code you just ran..."`. This forced the LLM to output the code block reliably."
    *   **CN**: “利用大模型的‘近因效应（Recency Bias）’。在沙盒返回真实执行结果的最后一秒，我强行追加了一段 System Directive 警告，强制它必须输出刚才跑的代码，完美解决了格式丢失问题。”

### 🧨 踩坑 2：Streamlit 垃圾回收引发的 "Client Closed" (Streamlit GC vs HTTP Connections)
*   **Problem (问题)**: Adding `@st.cache_resource` caused frequent `Cannot send a request, as the client has been closed` errors on the UI.
*   **Solution (解法)**: 
    *   **EN**: "I traced it to a conflict between Python's Garbage Collection (GC) and Streamlit's lifecycle. The local `genai.Client` was being destroyed after the function returned, killing the underlying HTTP connection. I fixed this by persisting the Client object into Streamlit's global `st.session_state` tree, keeping the TCP connection alive."
    *   **CN**: “定位到了 Python 垃圾回收 (GC) 与 Streamlit 生命周期的冲突。临时 `genai.Client` 被强制 GC 销毁，顺带掐断了 HTTP 线程。我通过将 Client 对象提升至 `st.session_state` 全局状态树中，成功保活了底层的长连接。”

### 🧨 踩坑 3：并发太高导致 API 429 熔断 (Rate Limiting & 429 Errors)
*   **Problem (问题)**: The autonomous agent called tools too rapidly in a single loop, triggering the strict 15 RPM free-tier quota limits (HTTP 429).
*   **Solution (解法)**: 
    *   **EN**: "I introduced a precise Rate-Limiting Pacer at the tool layer. Knowing the limit is 15 RPM, the mathematical safe interval is 4 seconds. I implemented a `time.sleep(4)` token-bucket throttling mechanism inside the sandbox, slightly trading latency for 100% system stability."
    *   **CN**: “在工具层引入了精准的速率起搏器。15 RPM 意味着最佳安全间隔是 4 秒。我在沙盒底层切入了 `time.sleep(4)` 的限流机制，以微微牺牲延迟的代价，换来了系统 100% 的稳定性。”

---

## 4. 面试必考 Q&A (Expected Q&A)

**Q1: Why have the agent write Python code to query the DB instead of just writing pure SQL? (为什么写 Python 而不是纯 SQL？)**
> *   **EN**: "Three reasons: 1) **Compatibility**: Using SQLAlchemy, the Python code works across MySQL, PostgreSQL, or SQLite agnostically. 2) **Post-processing**: Complex operations like time-series smoothing or pivot tables are hard in pure SQL but require only one line in `pandas.DataFrame`. 3) **Extensibility**: In the Python sandbox, I can easily inject ML or plotting libraries later, allowing the agent to predict and visualize, not just query."
> *   **CN**: “三个考量。1) 兼容性，借用 SQLAlchemy 无视底层方言；2) 数据后处理，复杂的透视表用 Pandas 就是一行代码的事；3) 扩展性，未来沙盒里可以塞入机器学习或绘图库，让 Agent 不仅能查数，还能预测。”

**Q2: How do you prevent the LLM from running destructive code like `DROP DATABASE`? (如何防止模型删库？)**
> *   **EN**: "Security is paramount. First, I restricted the `custom_globals` dictionary in `executor.py`, denying access to dangerous modules like `os` or `sys`. Second, in a production environment, the DB connection string would strictly use a Read-Only user. I can also implement Regex/AST blockers to intercept keywords like `DROP`, `DELETE`, or `TRUNCATE`."
> *   **CN**: “首先，在执行器内部用字典限制了全局变量，没注入 `os` 等高危库；其次，生产环境中强制使用 Read-Only 账号；最后，还可以加上正则拦截器，包含 `DROP|DELETE` 就直接阻断执行。”

**Q3: How did you evaluate the 75% success rate? (75% 的成功率是怎么测的？)**
> *   **EN**: "I didn't test on trivial queries. I built an automated `evaluator.py` containing a 'Golden Dataset' with **Adversarial Traps**—such as asking for nonexistent tables or using ambiguous columns. Achieving 75% on this rigorous dataset using the smaller `gemini-3.5-flash-lite` model proves the robustness of the Self-Correction architecture."
> *   **CN**: “我用 Python 写了一个自动化测试集，里面设计了对抗性陷阱（如故意问不存在的表）。能在这些坑里，依靠小模型跑出 75% 的成绩，证明我这套带有 Self-Correction 的架构是极其鲁棒的。”
