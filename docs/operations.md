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
| `copilot-reader` | Render 线上服务（只读） | 项目级 `bigquery.dataViewer` + `jobUser`，数据集 ACL 的 READER/WRITER 视需要 |
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
