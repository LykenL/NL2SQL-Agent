# Data Copilot

**一个受治理的数据平台，加一个只能读 gold 层的中文 Copilot。**
业务人员用自然语言取数，但"查的是哪张表、按什么口径算、数据有多新"由平台决定，不由模型临场发挥。

| | |
|---|---|
| 应用 | <https://nl2sql-agent-nl2sql.vercel.app> |
| 数据健康 | <https://nl2sql-agent-nl2sql.vercel.app/health> |
| 后端 | <https://nl2sql-agent-0hwl.onrender.com/api/health> |

---

## 数据链路

```
Kolada API ──Dagster──► bronze ──dbt staging──► silver ──dbt marts──► gold ──► Copilot
（瑞典官方开放数据）     原始落地          清洗/类型化视图        3 张业务宽表      只读
                                                              │
                                                    meta.dq_results · mart_freshness
```

| 层 | 内容 | 规模 |
|---|---|---|
| bronze | 原始落地表（Kolada v3 API） | 312 个市 · 5,000 KPI 目录 · 43,611 条数值（2022–2024） |
| silver | dbt view：`stg_municipalities` / `stg_kpi_values` / `stg_kpi_catalog` | 3 个视图 |
| gold | dbt table：`mart_municipality_population`（936 行）/ `mart_personnel_cost`（873 行）/ `mart_kpi_summary`（12 行） | 3 张 mart |
| meta | DQ 台账与新鲜度 | `dq_results`、`mart_freshness` |

**DQ 闸门**：dbt 测试结果写入 `meta.dq_results`，`dq_gate` 资产在任一测试失败时**阻断**下游，而不是发一条警告。

---

## Copilot 能力

| 能力 | 实现 |
|---|---|
| 自然语言 → SQL → 图表 | 同一次 `execute_python_code` 内完成：`ds.run(sql)` 取 DataFrame → 直接建 `go.Figure`；回复正文零代码，脚本只出现在 **Generated Script** 面板 |
| 图表强制规则 | 排名 / 对比 / 趋势 / 分布 / 占比必须出图；纯单值查询明确不出图，避免无意义图表 |
| **只读 gold 守卫** | `MartSource` 拦截任何触碰 `*_silver` / `*_bronze` 的查询并抛 `PermissionError`（6 条测试覆盖）。"模型不会乱查生产表"是代码路径，不是提示词承诺 |
| **语义层（口径注册表）** | 口径存 `data/semantic/registry.db`，带 `draft / confirmed / stale` 状态机与确认留痕；代码在**执行前**校验是否使用了已确认口径的表达式（覆盖 `SUM(col)`、`df['col'].sum()`、`df.agg(...)` 等写法），词法检索匹配问题 |
| **成本护栏** | BigQuery schema 走 metadata API（零字节扫描）；查询前 dry-run 估算字节与费用；强制外层 LIMIT 与 `maximum_bytes_billed` |
| **DQ 失败诊断** | `dq_gate` 负责"拦住"，`DQTriager` 负责"解释"：读 `meta.dq_results` 里最新一轮的失败测试，对每一条跑**一条**证据查询（空值数、重复键、孤儿键、越界值），给出数字、启发式成因与下一步。agent 有对应工具 `diagnose_data_quality`，也有 `GET /api/dq/triage` |
| **可观测性（Langfuse）** | 一次提问 = 一条 trace：根 span `chat-turn`（输入只有问题本身）下挂 `semantic-resolve`、每次模型调用一条 `generation`（带 token/成本）、每个工具一条 `tool`、以及拦截事件 `definition-block`。`session_id` 让同一次对话的多轮归入 Sessions 视图。未配置 `LANGFUSE_*` 时整体是 no-op |
| 报错自愈 | 沙盒失败时回传"出错行 + 真实列名"，模型下一轮自行改正；栈帧只写日志，不甩给用户 |
| 多数据源 | SQLite / PostgreSQL / MySQL / BigQuery / 已发布 marts，统一 `DataSource` 接口 |
| 会话记忆 | 情节记忆（SQLite，逐轮）+ 语义记忆（ChromaDB + Ollama embedding），后台压缩 agent 提炼结论供跨会话召回 |

---

## 技术栈

`Python 3.12` · `FastAPI` · `Next.js 15 / TypeScript / Tailwind` · `BigQuery` · `dbt 1.13` · `Dagster` · `Plotly / Altair` · `SQLAlchemy / Pandas` · `pytest` · `GitHub Actions`

> Streamlit 版本已完全移除，唯一支持的 UI 是 `frontend/` 的 Next.js 应用。

---

## 目录

```
src/core/            agent loop、工具、数据源、marts 守卫、语义注册表、记忆
src/backend/         FastAPI 服务（/api/chat、/api/execute、/api/marts、/api/health…）
frontend/            Next.js 应用（聊天 + 画布 + 数据健康页）
pipelines/           Dagster 资产（Kolada 摄取、DQ 台账、DQ 闸门）
dbt/                 staging 视图 + marts + 31 个模型与测试
scripts/             refresh.py（一条命令刷新全链路）、semantic_admin.py、evaluator.py
tests/               41 个 pytest
docs/                operations.md（运维）、bigquery_source.md、semantic_layer.md
```

---

## 本地运行

```bash
pip install -r backend-requirements.txt -r pipelines-requirements.txt

cp configs/.env.example .env          # OLLAMA_BASE_URL / OLLAMA_API_KEY，任意 OpenAI 兼容端点
uvicorn src.backend.server:app --reload --port 8000

cd frontend && npm install && npm run dev    # http://localhost:3000
```

本地页面会自动指向 `http://localhost:8000`，线上页面指向 Render 后端（运行时判断，不依赖构建期变量）。

### 刷新数据（一条命令）

```bash
python scripts/refresh.py
# 1/4 ingest (Kolada → bronze)   约 30s
# 2/4 dbt build (silver + gold + tests)   31/31 PASS，约 20s
# 3/4 dq ledger                  约 7s
# 4/4 dq gate                    约 4s
# refresh complete               合计约 60s
```

任一步失败即非零退出，可直接给调度器用。

---

## 质量与运维

**测试** — 41 个 pytest：语义层 CRUD 与状态机、gold 守卫拦截、BigQuery dry-run、SQLite schema 抽取回归。

**CI**（`.github/workflows/data.yml`，push 到 `v2-nextjs-fastapi` 或 PR 触发）

- `pytest` + `dbt parse --target ci`（无 secret 也能跑）
- 有 `GCP_SA_JSON` 时执行 `dbt build --target ci`，写入一次性 `data_copilot_ci_silver/_gold`，bronze 只读自 dev 项目
- 前端 `npm run build`（含类型检查）

**定时刷新**（`.github/workflows/refresh.yml`，每天 06:00 UTC）

跑 `scripts/refresh.py` 后断言 `mart_freshness` 每张表行数 > 0。选 Actions 而非 Render Cron Job：后者不在免费实例类型内。

**部署**

| 组件 | 平台 | 说明 |
|---|---|---|
| 前端 | Vercel | Production 分支 `v2-nextjs-fastapi` |
| 后端 | Render | FastAPI，服务账号只读，凭据走 Secret File |
| 数据 | BigQuery 免费层 | 无结算，DML 被禁用（管道只用 CTAS + load job） |

**分支**：`v2-nextjs-fastapi` 是发布分支（Render + Vercel Production 都跟它）；`main` 已冻结。

详细运维步骤见 [`docs/operations.md`](docs/operations.md)。

---

## 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 存活 + `version`（部署 commit）+ `capabilities` |
| GET | `/api/health/data` | marts 行数、最新加载时间、DQ 计数、最旧新鲜度 |
| GET | `/api/marts` | 已发布 marts 与允许表名 |
| GET | `/api/dq/triage` | 最新一轮 DQ 失败诊断（只读，含证据查询） |
| GET | `/api/datasources` | 可用数据源（BigQuery 是否已配置） |
| GET | `/api/schema` | 指定数据源的表结构 |
| POST | `/api/chat` | 自然语言提问 → 回复 + 可重跑脚本 |
| POST | `/api/execute` | 执行脚本，返回 `html`（图表）与 `has_chart` |
| POST | `/api/upload` | 上传 `.db` / `.sqlite` / `.csv` |
| GET/POST | `/api/metrics` | 语义层口径的查询、确认、标记过期 |

---

## 历史基准（早期阶段，Chinook / company.db）

`scripts/evaluator.py` 是一套关键词匹配的黄金数据集评测（简单聚合、复杂 join、陷阱题、幻觉题共 8 例）：

| 指标 | 结果 |
|---|---|
| 任务成功率 | 75.0%（6/8） |
| 平均 token | 约 1,327 / 次查询 |
| 单次执行成本 | < $0.002 |

这组数字来自 SQLite 示例库阶段，与当前 gold-only 架构不可直接对比，保留作回归参考。

---

## 文档

- [`docs/operations.md`](docs/operations.md) — 数据流、CI、定时刷新、权限模型、密钥轮换
- [`docs/bigquery_source.md`](docs/bigquery_source.md) — BigQuery 数据源与成本护栏
- [`docs/semantic_layer.md`](docs/semantic_layer.md) — 语义层设计
- [`docs/system_architecture.md`](docs/system_architecture.md) — 架构演进
