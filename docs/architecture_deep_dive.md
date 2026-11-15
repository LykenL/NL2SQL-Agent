# 🧠 Multi-Agent Data Copilot 架构级技术深度剖析

这是一份完全抛开“包装话术”，纯粹从底层原理解析本系统运作机制的硬核技术文档。我们将把这个项目的核心代码扒开，看看大模型是如何和本地代码协作的。

---

## 1. 大模型是如何“长出手脚”的？(Native Tool Calling 原理)

我们常说的 Agent，本质是赋予 LLM 调用外部工具的能力。在我们的代码中，这依赖于 Google Gemini SDK 的 **Function Calling（函数调用）** 机制。

### 1.1 内部通信流转机制
当你点击 `Execute Query`，在 `app.py` 中调用 `chat_session.send_message()` 时，底层其实发生了极其复杂的事情：
1. **LLM 暂停输出**：大模型接收到 Prompt 后，它的第一层输出**不是文字**，而是一段 JSON。这段 JSON 声明了：“我要调用 `get_database_schema`”。
2. **SDK 劫持与路由**：Google SDK 拦截到这段 JSON，发现你在 `create_agent_tools` 里注册过这个函数。于是，SDK 暂停与 Google 服务器的通信，转而在你的**本地机器上运行**该 Python 函数。
3. **回传与恢复**：本地函数运行完毕，SDK 把返回的表结构字符串重新打包发回给大模型。大模型拿到结果后，才开始第二轮思考，决定是直接输出文字，还是继续调用 `execute_python_code`。
> **技术洞察**：这就是为什么在 Console 里你会看到 `[Tool execution] get_database_schema called...`。这个 Agent 的核心决策权在云端，但**实际代码的执行权严格控制在你的本地机器上**。

### 1.2 `exec()` 沙盒的安全与反向注入
在 `agent_tools.py` 中的 `safe_execute`，我们使用 Python 内置的 `exec()` 函数来动态执行 LLM 传回来的代码字符串。
*   **状态隔离**：通过传入受限的 `custom_globals={"db_uri": db_uri}`，我们严格控制了代码的执行上下文。大模型只能在这个沙盒里访问你特意给它的变量。
*   **Stdout 劫持**：大模型写的代码通常是 `print(df)`。我们通过 `contextlib.redirect_stdout` 在内存中拦截了这些输出（而不是让它打印在终端），并将这些输出作为 Tool Result 回传给模型。
*   **反向注入 (Prompt Injection)**：这是整个系统最“黑客”的一招。大模型在收到执行结果后，由于结果往往很长，它的**注意力机制 (Attention Mechanism)** 会发生偏移，导致它只输出数据而不输出代码。我们在结果的最末尾硬编码追加了一段 `[SYSTEM DIRECTIVE]`，利用**近因效应 (Recency Bias)**，在模型即将进行自回归生成文字前，强行干预它的隐状态，逼迫它输出 ` ```python ` 代码块。

---

## 2. 双轨记忆系统 (Dual-Memory) 到底是怎么运作的？

为了让 Agent 不仅仅是个简单的问答机器，我们剥离了传统的“列表存储”，重构了整个存储引擎。

### 2.1 Episodic Memory（情景短时记忆）: SQLite
*   **痛点**：Streamlit 的 UI 渲染逻辑是：一旦有按钮点击，整个 Python 脚本从头到尾重新跑一遍（Re-run）。如果记忆放在局部变量里，点击瞬间就清空了。
*   **实现**：我们引入了 `memory_manager.py` 和 SQLite。每次大模型回复，我们都以 `INSERT` 强行落盘。界面刷新时，使用 `SELECT * FROM history WHERE session_id = ? ORDER BY id ASC LIMIT 20` 把当前上下文捞出来重新渲染。
*   **为什么不用 Session State 存全部？** 跨会话隔离。SQLite 让你拥有了左侧边栏的“历史记录列表”。

### 2.2 Semantic Memory（语义长期记忆）: 向量库 + 后台压缩
*   **痛点**：如果把过去一个月的聊天记录全塞进上下文，Token 会溢出，且会稀释大模型对当前问题的注意力。
*   **实现**：
    1.  **压缩 (`compression_agent.py`)**：每轮回答写库之后，用**同一个**模型把整段会话蒸馏成一条不超过 120 词的笔记——只保留谈妥的业务口径、该用/不该用的表和列、最终结论与用户纠正。不是“单独的第二个模型”，也没有 15 秒限速休眠。
    2.  **向量化落盘**：笔记经 Ollama 的 Embedding 端点（`nomic-embed-text`，768 维）转成向量。配置了 Pinecone 凭据就写 Pinecone，否则落本地 ChromaDB。文档 id 用 `session:<id>`，所以同一会话反复压缩是**覆盖**而不是堆积。
    3.  **隐式 RAG 召回**：新会话提问时，用问题向量检索最相关的一条历史笔记，作为背景注入上下文。检索**按数据源过滤**——一个库上谈妥的“营收”口径，不该被当成另一个库的证据。

---

## 3. 状态管理与连接保活 (FastAPI + Next.js)

早期版本用 Streamlit 做前端时，脚本每次交互都会从头重跑，内存状态随之被 GC 回收（`Cannot send a request, as the client has been closed`）。该前端已被彻底移除，当前架构把这个问题从根上绕开了：

*   **状态外置**：会话状态全部落在 SQLite（`memory_manager.py`），FastAPI 的每个请求都是无状态的，前端只持有 `session_id`。没有需要跨 Re-run 保活的进程内对象，也就不存在 GC 掐断连接的问题。
*   **连接按请求生命周期创建**：`run_agent_loop()` 每次调用新建 OpenAI-compatible 客户端，请求结束即释放，TCP 不必跨调用复用。
*   **代码与渲染分离**：`/api/chat` 只返回文本结论和 `sql_executed`；`/api/execute` 单独在沙盒里跑脚本并回传 Plotly/Altair 的 HTML，两者互不污染，也不依赖任何前端框架的运行时状态。

---

## 4. 总结：这套架构真正的壁垒在哪里？

如果其他人要复刻你的项目，最难的不是调用 API，而是：
1.  **容错性**：基于 Traceback 的自动重试机制。
2.  **高并发限流**：工具层精确到秒的令牌桶控制（`time.sleep(4)`）。
3.  **状态分离**：将 Web 框架的无状态性（FastAPI）与 Agent 需要的强状态性（Memory）通过数据库层优雅地桥接起来。
