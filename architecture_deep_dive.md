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

### 2.2 Semantic Memory（语义长期记忆）: ChromaDB + 异步大模型
*   **痛点**：如果把过去一个月的聊天记录全塞进 `send_message`，Token 会溢出，且会稀释大模型对当前问题的注意力。
*   **实现**：这就是为什么系统被称为 **Multi-Agent（多智能体）** 的原因。
    1.  **压缩智能体 (`compression_agent.py`)**：这是独立的第二个大模型（Flash-Lite）。当主会话闲置时，它负责读取 SQLite 里的长篇大论，将其**总结浓缩**为纯粹的“业务规则”或“逻辑规律”。
    2.  **向量化落盘 (ChromaDB)**：浓缩后的文本被本地的 Embedding 模型 (`all-MiniLM-L6-v2`) 转化为高维度的浮点数向量，存入本地数据库。
    3.  **隐式 RAG 召回**：当你在新 Session 问一个问题时，系统会计算你问题的向量，去 ChromaDB 里做**余弦相似度检索 (Cosine Similarity)**，找出最相关的 2 条历史经验，作为背景知识无声无息地塞给主 Agent。

---

## 3. TCP 连接保活与状态管理 (Streamlit GC 困局)

在开发过程中我们遇到了 `Cannot send a request, as the client has been closed` 的报错。
*   **底层原因**：在 Python 中，像 `genai.Client` 或 `httpx.Client` 这样的对象底层维护了一个 TCP 连接池 (Connection Pool)。如果这个对象在一个局部函数（比如 `reset_chat_session`）里创建，函数一旦 `return`，这个 Client 对象失去了引用计数，**Python 的垃圾回收器 (GC)** 会立刻将其销毁，从而强制发送 TCP FIN 包关闭底层网络连接。
*   **解决方案**：Streamlit 的 `st.session_state` 是存在于服务器内存中的全局字典，其生命周期等同于用户的浏览器会话生命周期。我们将 `Client` 实例强引用挂载到 `st.session_state` 上，欺骗了 GC，使得底层 TCP 连接得以在跨页面的多次 Re-run 中保持长连接（Keep-Alive），极大地降低了建立 HTTPS 连接的握手延迟并避免了断连报错。

---

## 4. 总结：这套架构真正的壁垒在哪里？

如果其他人要复刻你的项目，最难的不是调用 API，而是：
1.  **容错性**：基于 Traceback 的自动重试机制。
2.  **高并发限流**：工具层精确到秒的令牌桶控制（`time.sleep(4)`）。
3.  **状态分离**：将 Web 框架的无状态性（Streamlit）与 Agent 需要的强状态性（Memory）通过数据库层优雅地桥接起来。
