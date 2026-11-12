# BIRD Mini-Dev 评测报告

**被测系统**：data_copilot / NL2SQL-Agent（受治理数据平台上的只读 Copilot）
**基准**：BIRD Mini-Dev `sqlite` split，全部 500 题，11 个数据库
**日期**：2026-11-12　**commit**：`49bbe77`　**报告文件**：`evals/bird/results/run_20261112T202429Z.json`

---

## 1. 结论

| 指标 | 数值 |
|---|---|
| **Execution Accuracy (EX)** | **0.750（375/500）** |
| 95% 置信区间 | **0.712 – 0.788**（±3.8 点） |
| 提交率（调用 `submit_query`） | 494/500 |
| 失败构成 | `wrong_result` 120、`timeout` 5 |
| 总耗时 | 4151.8 秒（约 69 分钟，4 并发），平均 20.6 秒/题 |

**一句话**：在 `deepseek-flash`（BIRD 官方榜单对应条目 DeepSeek-V4.1-Flash）、低思考档、
开启 schema linking 的条件下，本系统在 BIRD Mini-Dev SQLite 上取得 **EX 0.750**。

官方榜单该模型报 **61.00**，比本系统低 14 点。两者条件并不相同（官方条目未标注推理档位与
harness，本系统带完整 agent harness），因此不能直接归因；**可验证的差别来自工程层**：
schema 裁剪、工具守卫、SQL 修复回路、提交契约。

---

## 2. 与公开榜单的对照

BIRD 官方 Mini-Dev 榜（SQLite 列，2026-10-28 更新）：

| 排名 | 模型 | SQLite EX |
|---|---|---|
| — | Empirical Upper Bound（人工上限） | 99.60 |
| 1 | GPT-6.1 Sol | 88.00 |
| 2 | Claude Opus 5.5 | 90.00 |
| 3 | Claude Sonnet 5.5 | 86.60 |
| 4 | Qwen3.8-Flash-Next | 70.20 |
| 5 | **DeepSeek-V4.1-Flash** | **61.00** |
| 6 | Claude Haiku 5.5 | 59.60 |

**本系统：75.00（SQLite）**

定位：

- 相对**同基座模型**的官方报数（61.00），提升 **+14.0 点**
- 位于 Qwen3.8-Flash-Next（70.20）之上、Claude Sonnet 5.5（86.60）之下
- 与公开的同类 agent 工作可对照：`schemagate + Claude Opus 5.5` 报 81.00（SQLite，legacy 榜），
  `Jitto Build` 报 75.60（SQLite，legacy 榜）

> 注意：官方榜的模型条目未标注推理档位与 harness。本系统用的是**低思考档 + 完整 agent harness**，
> 两者不是同条件对照，只能作为量级参考，不能当作"击败某模型"的证据。

---

## 3. 按数据库

| 数据库 | EX | 失败数 | 说明 |
|---|---|---|---|
| superhero | **0.52** | 24/50 | 最弱。同名列多义（`gender`/`alignment` 在两表语义不同）、大量 NULL |
| european_football_2 | 0.65 | 11/31 | 表最多（201 列），schema linking 压缩效果最差 |
| student_club | 0.70 | 11/37 | 聚合粒度（"每名学生"vs"每个事件"） |
| toxicology | 0.75 | 8/32 | — |
| card_games | 0.75 | 17/69 | 大量多表 JOIN + 去重语义 |
| debit_card_specializing | 0.79 | 3/14 | 样本少 |
| california_schools | 0.80 | 10/49 | — |
| formula_1 | 0.80 | 11/55 | — |
| financial | 0.80 | 9/46 | — |
| codebase_community | 0.81 | 6/31 | — |
| thrombosis_prediction | **0.83** | 15/86 | 题量最大，表现最好 |

**superhero 与 european_football_2 合计 35 个失败，占全部失败的 28%。**
两个库的共同点是**列名歧义**：同一概念在多个表中存在同名列，但业务含义不同。
这是 schema linking 的表级筛选解决不了的——它选对了表，却没告诉模型"这一列该从哪张表取"。

---

## 4. 按难度

| 难度 | 题量 | EX |
|---|---|---|
| simple | 8 | 0.750 |
| moderate | 32 | 0.750 |
| challenging | 460 | 0.750 |

**三档完全一致（6/8、24/32、345/460），这是本次结果里最反常的一点，需要复核。**
可能解释：题目难度按 gold SQL 的结构复杂度标注，而本系统的失败与结构复杂度无关，
集中在一个共同因素上（列歧义 / 值格式）。也可能是难度标签本身与这批数据不匹配。
在有进一步验证前，不应把"难度不敏感"当作结论写进对外材料。

---

## 5. 失败分析

120 个 `wrong_result` 中：

| 类型 | 数量 | 含义 |
|---|---|---|
| **行数相同、值不同** | **97** | SQL 结构对、聚合口径或过滤条件错——能力问题 |
| 行数不同 | 23 | 粒度或去重错——部分可由形状提示覆盖 |

5 个 `timeout`：`58`、`73`（card_games）、`119`（codebase_community）、`174`、`187`（european_football_2），
均在 900 秒单题上限处被切断。

**提交率为 494/500（98.8%）**，未提交的 6 题全部失败——提交契约（先 `validate_query_shape`
再 `submit_query`）已基本被模型遵守，但它仍是这 6 题的直接死因，不能忽略。

---

## 6. 本次评测暴露并修复的工程缺陷

这些都不属于"模型不会"。修复效果的证据是**机制层面**的：上一轮因 #5 未提交的 6 道题
（11、87、175、286、297、354）在修复后重跑，6/6 全部提交且全部答对。
**0.75 是本轮 500 题的成绩，与之前 n=50 的各轮不可直接比较**（题目集合、样本量、方差都不同）。

| # | 缺陷 | 后果 | 修复 |
|---|---|---|---|
| 1 | 工具参数直接 `**fargs` 展开 | 模型写 `code=` 而参数名是 `code_string`，整题 TypeError 失败 | 按签名过滤 + 别名映射 |
| 2 | 工具结果无裁剪 | 5000 行结果每轮重发，撞上 1,048,576 token 上限 | 单条裁剪 + 历史预算淘汰 |
| 3 | 评测给每个工具套 `*a/**k` 包装 | 包装层签名掩盖真实签名，使 #1 的修复失效 | `functools.update_wrapper` |
| 4 | 记忆库指向远端 Postgres | 4 并发下连接被服务端掐断，3 题计为 error | 评测模式改本地 sqlite，且记忆失败不致任务失败 |
| 5 | 提交闸门要求 SQL 字符串完全相等 | 换行/空格不同就拒收，6 题最终未提交、被按探索查询计分 | 规范化比较 + 就地验证 |
| 6 | SQLite 无 `REGEXP` | `no such function: REGEXP`，1 题 sql_error | 连接时注册自定义函数 |

其中 #1、#3 的教训值得记录：**修复必须穿过真实调用路径验证**。第一次修完 #1 时，
单测直接调用未包装的函数、全部通过，线上仍然报同样的错。

---

## 7. 局限性（对外引用时必须一并说明）

1. **不是严格 blind。** 500 题中有 50 题（blind cohort）在调优阶段已被使用过。
   严格说法是"开发期接触过 500 题中的 50 题"。
2. **EX 由本项目自行实现**，口径与 BIRD 官方 `evaluation_ex.py` 一致（结果集多重集比较、
   忽略行序、浮点容差、空结果视为合法），但**没有用官方脚本跑我们的产物做交叉验证**。
   本轮报告里的 `predicted_sql` 曾被截断到 800 字符，无法事后复算；已改为保存完整 SQL
   （含 gold SQL），**下一次跑的产物可以直接喂给官方 `evaluation_ex.py`**。
3. **样本量决定分辨率。** n=500 时 95% CI 为 ±3.8 点；任何小于 4 点的差异都不应被解读为提升。
   此前 n=50 的几轮（0.48 / 0.62 / 0.52 / 0.66）中，28/50 的题目在不同轮次间反复翻转，
   那些数字之间的差异属于噪声。
4. **5 个 timeout 计为失败**，与官方口径（超时即算错）一致，但会随机器负载波动。
5. **推理档位为 low**（DeepSeek 三档为 low / high / max，`medium` 映射到 `high`）。
   换到 high 档的收益未测。

---

## 8. 配置与复现

```bash
cd /Users/lykenl/Documents/local_agent/data_copilot

AGENT_THINKING=on AGENT_THINKING_EFFORT=low \
  /Users/lykenl/miniforge3/bin/python3 -u scripts/eval_bird.py \
  --limit 500 --workers 4 --schema-linking \
  > /tmp/bird500_low.log 2>&1
```

| 配置项 | 值 |
|---|---|
| 模型 | `deepseek-flash`（DeepSeek-V4.1-Flash） |
| 思考 | 开启，effort = low |
| schema linking | 开启（487/500 命中，平均 schema 6632 → 5727 字符） |
| 工具守卫 | shape / value grounding / SQL repair 全开 |
| 单题上限 | 900 秒；SQL 超时 12 秒 |
| 最大轮次 | 12（实测中位数 6，达到上限 7 题） |
| 并发 | 4（每题独立子进程，spawn） |

**成本**：按 500 题的 token 规模估算约 **$3–5**（未记录逐题 usage，无法精确核算）。

---

## 9. 下一步

| 优先级 | 动作 | 预期 |
|---|---|---|
| 高 | 用 BIRD 官方 `evaluation_ex.py` 复算，验证 EX 口径 | 决定这个 0.750 能不能对外引用 |
| 高 | 列级消歧：在 schema 里标注同名列的业务含义（superhero / european_football_2） | 这两个库占 28% 的失败 |
| 中 | 推理档位 A/B：low vs high，同一批题 | 唯一能提升剩余 97 道"值错"的手段 |
| 中 | 难度标签异常复核 | 关系到报告的严谨性 |
| 低 | 迭代上限 12 → 16（7 题触顶） | 最多 1 点 |
