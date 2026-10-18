# 🏗️ Multi-Agent Data Copilot 系统架构设计文档 (System Architecture)

本文档旨在从全局视角，系统性地梳理 Data Copilot 的模块划分、数据流转机制以及底层架构设计。

---

## 1. 系统核心组件划分 (Components)

整个项目遵循松耦合、高内聚的设计原则，主要划分为以下四个核心大类：

1. **Frontend / UI 层 (`frontend/` + `src/backend/server.py`)**
   * Next.js 单页应用负责会话列表、Markdown 报告渲染、**Generated Script** 面板与图表画布；FastAPI 负责会话 API、数据库上传、`/api/execute` 渲染管线。
2. **Agent / 调度层 (`agent_loop.py`, `agent_tools.py`)**
   * 定义了大模型的 System Prompt，注册了 `get_database_schema` 和 `execute_python_code` 工具，负责处理 Tool Calling 闭环与限流。
3. **Sandbox / 执行层 (`executor.py`, `db_reader.py`)**
   * 安全沙盒环境。利用 AST 和 Regex 拦截高危操作，提供有限的 `globals()` 上下文供 `exec()` 运行大模型生成的代码。
4. **Memory / 存储层 (`memory_manager.py`, `compression_agent.py`)**
   * 核心存储枢纽。SQLite 负责 CRUD 会话明细；后台压缩智能体负责将数据推送到 ChromaDB 做向量持久化。

---

## 2. 核心系统调用链路 (Data Flow Sequence)

当你提出一个数据查询请求时，系统内部的数据流转如下：

```mermaid
sequenceDiagram
    participant User
    participant Next.js UI (frontend)
    participant FastAPI (server.py)
    participant SQLite (Memory)
    participant Gemini Agent (Main LLM)
    participant Sandbox (agent_tools)
    participant Database

    User->>Next.js UI: 输入自然语言 "查一下销量最高的商品"
    Next.js UI->>FastAPI: POST /api/chat
    FastAPI->>SQLite: 存储 User Message
    FastAPI->>Gemini Agent: 发送带历史记录的 Context
    
    activate Gemini Agent
    Gemini Agent-->>Sandbox: [Tool Call] get_database_schema()
    Sandbox->>Database: 读取表结构定义
    Database-->>Sandbox: 返回 Schema JSON
    Sandbox-->>Gemini Agent: [Tool Result] Schema JSON
    
    Gemini Agent-->>Sandbox: [Tool Call] execute_python_code(pandas code)
    activate Sandbox
    Sandbox->>Sandbox: AST 语法树安全检查
    Sandbox->>Database: 执行 Pandas SQL Query
    Database-->>Sandbox: 返回 DataFrame 数据
    Sandbox-->>Gemini Agent: [Tool Result] 劫持的 Stdout + System Directive
    deactivate Sandbox
    
    Gemini Agent->>Gemini Agent: 解析结果并自我纠错 (若有报错)
    Gemini Agent-->>FastAPI: 返回最终结论（已剔除代码块）
    deactivate Gemini Agent
    
    FastAPI->>SQLite: 存储 Model Message（清洗后，无代码）
    FastAPI-->>Next.js UI: response + sql_executed
    Next.js UI->>FastAPI: POST /api/execute（sql_executed）
    FastAPI-->>Next.js UI: Plotly/Altair 图表 HTML
    Next.js UI->>User: 渲染 Markdown 报告 + Generated Script + 图表
```

---

## 3. 双引擎记忆架构设计 (Dual-Memory Architecture)

系统抛弃了传统的 List Append 记忆法，转而采用工业级的 RAG (Retrieval-Augmented Generation) 架构设计。

```mermaid
graph TD
    A[用户当前 Query] --> B{是否存在相似历史?}
    
    subgraph Episodic Memory (情景短时记忆)
        C[SQLite: history 表]
        D[SQLite: sessions 表]
    end
    
    subgraph Semantic Memory (语义长期记忆)
        E[后台大模型: compression_agent.py]
        F[(ChromaDB 向量库)]
    end
    
    C -->|后台闲时抽取| E
    E -->|浓缩业务逻辑| F
    
    F -->|余弦相似度检索| B
    B -->|注入 Prompt Context| G[主 Agent 进行最终回答]
```

**设计哲学**：
*   **Episodic Memory (SQLite)** 是“短期照相机”，它精准地记录了对话的每一个字，保证了 UI 左侧边栏的准确还原。
*   **Semantic Memory (ChromaDB)** 是“长期经验池”，它去除了冗余的问候语和具体数据，只保留“方法论”。通过 `all-MiniLM-L6-v2` 嵌入模型，实现了跨越时间的隐式规则继承。

---

## 4. 目录结构与代码流向 (Directory Structure)

```text
data_copilot/
├── frontend/                 # Next.js UI：会话流、Generated Script 面板、图表画布
│   └── app/page.tsx
├── src/backend/server.py     # FastAPI：/api/chat, /api/execute, /api/upload, /api/schema
├── src/core/agent_loop.py    # Agent 循环：工具调用闭环、上下文装配、聊天文本清洗
├── src/core/agent_tools.py   # 提供给大模型的 Native Tools（schema + 代码执行）
├── src/core/executor.py      # 本地代码安全沙盒：stdout 劫持 + 危险关键词拦截
├── src/core/db_reader.py     # 目标数据库 Schema 的只读抽取组件
├── src/core/memory_manager.py# SQLite 情景记忆的 CRUD 封装 + ChromaDB 语义记忆
├── src/agents/compression_agent.py # 后台第二大模型：记忆降维 + ChromaDB 写入
└── data/episodic_memory/     # 本地生成的 SQLite 物理文件
```

## 5. 安全防御体系 (Security Shield)

系统在 `executor.py` 层建立了三道防线：
1.  **Read-Only 拦截器**：通过正则表达式和简单的抽象语法树 (AST) 解析，如果代码字符串中包含 `DROP`, `DELETE`, `UPDATE`, `TRUNCATE` 等关键字，直接抛出 `PermissionError`，拦截于执行前。
2.  **受限环境 (Restricted Environment)**：利用 Python `exec()` 的特性，向 `custom_globals` 字典中仅注入必要的依赖（如 `pd`, `create_engine`, 以及当前会话的 `db_uri`），彻底屏蔽 `os`, `sys`, `subprocess` 等越权操作库。
3.  **Human-in-the-Loop**：UI 层面设计的 `Preview SQL Only` 按钮，允许在生产环境中由 DBA 审查 SQL 后，再交由系统放行。
