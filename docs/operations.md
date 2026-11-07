# 运维手册（管道 + CI + 定时刷新）

## 数据流

```
Kolada API ──ingest──► bronze ──dbt staging──► silver ──dbt marts──► gold
                         │                                          │
                         └──────────── meta.dq_results / mart_freshness
                                                                    │
                                              FastAPI /api/* ◄──────┘
                                                                    │
                                          Copilot（只能读 gold）─────┘
```

一条命令刷新全部（本地或 Render Cron）：

```bash
python scripts/refresh.py
```

依次执行：`dagster asset materialize`（3 个摄取资产）→ `dbt build` → `dq_results` → `dq_gate`；任一步失败即非零退出。实测约 60 秒（摄取 27s / dbt 22s / DQ 11s）。

## 分支

| 分支 | 角色 |
|---|---|
| `v2-nextjs-fastapi` | **发布分支**：Render 服务、Vercel Production、定时刷新都跟它 |
| `main` | 历史分支，冻结在转到 `v2-nextjs-fastapi` 之前的状态，不再更新 |

每个提交流程：在 `v2-nextjs-fastapi` 上提交 → 自动 push → Vercel/Render 自动部署 → CI 跑测试。

## CI（GitHub Actions）

`.github/workflows/data.yml`，push 到 `v2-nextjs-fastapi`、以及 PR 时触发。

| Job | 内容 | 依赖 secret |
|---|---|---|
| `backend` | `pytest`；`dbt parse --target ci` | 否 |
| `backend`（第二步） | `dbt build --target ci`，写入 `data_copilot_ci_silver/_gold` | **是**（`GCP_SA_JSON`） |
| `frontend` | `npm ci` + `npm run build`（含类型检查） | 否 |

没有 secret 时自动降级为“只做语法解析”，不会红。

## 服务账号与权限

| 账号 | 用途 | 权限 |
|---|---|---|
| `copilot-reader` | Render 线上服务（只读） | 项目级 `bigquery.jobUser` **+ 数据集级 READER，仅 `data_copilot_dev_gold` / `_meta`**。刻意不给项目级 `dataViewer`：那是 agent 越权读 silver/bronze 的通道（见 README「评估结果」）。改权限后需等约 1 分钟生效，并用 `scripts/evaluate_agent.py --only guard_silver_explicit` 复验 |
| `copilot-ci` | GitHub Actions | 项目级 `bigquery.jobUser` + `bigquery.dataEditor` |

**已知取舍**：`dbt build` 启动时会执行 `CREATE SCHEMA IF NOT EXISTS`，该操作需要**项目级** `bigquery.datasets.create`，而数据集级 ACL 和 IAM 条件都不覆盖“创建尚不存在的资源”。因此 CI 账号拿到了项目级 `dataEditor`，理论上可以写生产数据集。

缓解措施：
- CI 的 `BQ_PREFIX` 固定为 `data_copilot_ci`，workflow 只写 CI 数据集；
- 密钥只存在于 GitHub Secrets，不在任何人的机器上；
- 如果这条风险不可接受，正解是给 CI 单独一个 GCP 项目（数据从 dev 只读复制过去），成本是维护两套项目。

## 定时刷新（GitHub Actions，每天 06:00 UTC）

`.github/workflows/refresh.yml`，`schedule: 0 6 * * *`，也可手动 `workflow_dispatch`。

选择它而不是 Render Cron Job 的原因：Render 的 Free 实例类型**不支持** Cron Job，最低要付费档（约 $7/月级，虽按秒计费但需绑卡）；Actions 对 public 仓库免费，且 `GCP_SA_JSON` 已经配好。

流程：写 key → `python scripts/refresh.py` → 断言 `mart_freshness` 行数非零。
workspace 的 `BQ_PREFIX` 必须是 `data_copilot_dev`，否则 `/health` 会一直显示旧数据。

已知限制：GitHub 的 schedule 在高峰期可能延迟几分钟到十几分钟（日更无影响）；仓库连续 60 天没有提交时定时任务会被自动停用，有一次提交即可恢复。

## 备选：Render Cron Job（需付费）

| 字段 | 值 |
|---|---|
| Type | Cron Job |
| Repo / Branch | `LykenL/NL2SQL-Agent` · `main` |
| Schedule | `0 6 * * *`（每天 06:00 UTC） |
| Command | `python scripts/refresh.py` |
| Env | `BQ_PROJECT`、`BQ_PREFIX=data_copilot_dev`，以及 Secret File 挂载的 `GOOGLE_APPLICATION_CREDENTIALS` |
| 依赖 | `pipelines-requirements.txt` 需在构建命令里安装（Build Command: `pip install -r backend-requirements.txt -r pipelines-requirements.txt`） |

Cron 使用**只读**的 `copilot-reader` 是不行的——它需要写 bronze/silver/gold。两种做法：

1. 给 `copilot-reader` 加 `dataEditor`（简单，但线上服务就有了写权限，不推荐）；
2. **推荐**：Cron 用 `copilot-ci` 的密钥（它就是为“写 CI 数据集”准备的），把 `BQ_PREFIX` 指向 `data_copilot_dev`。若坚持最小权限，可以为管道单独建一个 `copilot-pipeline` 账号，只授予 `data_copilot_dev_*` 数据集的写权限 + 项目级 `jobUser`。

> 注意：目前线上服务读的是 `data_copilot_dev_gold`，所以定时刷新必须写 **dev** 前缀，否则 `/health` 永远显示旧数据。

## DQ 失败诊断（triage）

`dq_gate` 阻断发布，`DQTriager`（`src/core/dq_triage.py`）解释"为什么断了"。

```bash
curl -s https://nl2sql-agent-0hwl.onrender.com/api/dq/triage | python3 -m json.tool
```

输出结构：`status`（healthy / failing / unknown）、`failing` 数量，以及每条失败的
`test_type`、`column`、`evidence`（真实行数与坏行数、占比、可复现的 SQL）、`likely_cause`、
`suggested_fix`。

三种入口共用同一份逻辑：agent 工具 `diagnose_data_quality`、`GET /api/dq/triage`、脚本内直接
`DQTriage(...).report()`。

**只读边界**：只跑 `SELECT` / `WITH`（非查询语句直接抛 `PermissionError`），只读 `*_meta` 与
`*_gold`，单次 `maximum_bytes_billed` 2 GB。

**注意**：`likely_cause` 是启发式判断，`evidence` 里的数字才是事实。如果两者冲突（台账说 900
条失败、证据查询说 101 条为空），以证据为准并检查测试本身。

## 追踪（Langfuse，可选）

`src/core/tracing.py` 是唯一 import `langfuse` 的地方。

| 变量 | 作用 |
|---|---|
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` | 二者缺一即关闭追踪，全部 helper 退化为 no-op |
| `LANGFUSE_HOST` | `https://cloud.langfuse.com`（EU）或 `https://us.cloud.langfuse.com` |
| `LANGFUSE_TRACING=off` | 有 key 也强制关闭（CI、压测时用） |

trace 结构（一次 `/api/chat` = 一条 trace）：

```
chat-turn                    span   input = 问题本身；output = 清洗后的回答
├── semantic-resolve         span   命中的口径 id
├── agent-step × N           generation   模型、token、成本
├── execute_python_code      tool   脚本片段、stdout、iteration
├── run_query / estimate_query / describe_table / diagnose_data_quality   tool
└── definition-block         span   语义层拦截事件（被拦时代替工具执行）
```

实现要点：
- 模型调用走 `langfuse.openai` 的 OpenAI 包装（drop-in），因此每次调用自动成为
  `generation` 并带 token/成本；未启用时回落到原生客户端。
- 系统提示词与工具 schema 在 trace 里被替换为占位符（约 3k token，每轮相同），
  只保留问题与回答 —— trace 是拿来调试一次提问的，不是拿来归档提示词的。
- `langfuse>=5`：SDK 默认过滤 HTTP/数据库 span，BigQuery 客户端内部 span 不会污染
  trace 树（v3 需要手写 processor 过滤）。
- 服务启动时 `load_dotenv()` 必须在任何 Langfuse import 之前。
- `tracing.flush()` 挂在 FastAPI lifespan 的关闭阶段，避免批量上报丢最后一条。

## 验证清单

```bash
# 1. CI 是否绿
gh run list --workflow=data.yml --limit 3

# 2. 管道是否真的刷新（时间应变新）
bq --project_id=woven-surface-511317-g0 query --use_legacy_sql=false \
 'SELECT table_name, last_loaded FROM `woven-surface-511317-g0.data_copilot_dev_meta.mart_freshness` ORDER BY 1'

# 3. DQ 是否全绿
curl -s https://nl2sql-agent-0hwl.onrender.com/api/health/data \
 | python3 -c "import json,sys;d=json.load(sys.stdin);print(d['status'], d['dq']['counts'])"

# 4. 线上页面
open https://nl2sql-agent-htrp1j6sw-nl2sql.vercel.app/health
```

## 密钥轮换

```bash
gcloud iam service-accounts keys list --iam-account=copilot-ci@woven-surface-511317-g0.iam.gserviceaccount.com
# 建新 key → 更新 GitHub secret GCP_SA_JSON → 删旧 key → 重跑一次 CI
```
