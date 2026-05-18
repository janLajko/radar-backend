# Stage 2 代码设计：CreatePolicyUpdatesStage

## 背景

Stage 2 消费 Stage 1 写入的 `radar_raw_source_items`，决定哪些条目应进入 Recent Policy Updates feed。每条 raw item 最多产生一条 `radar_policy_updates`，或被标记为 `discarded` / `failed`。Stage 2 是状态驱动的：每个周期扫描所有符合条件的 pending/failed 记录，支持 durable retry（最多 3 次）。

入口骨架：[create_policy_updates.py](../../../src/radar_backend/worker/stages/create_policy_updates.py)

---

## 状态机回顾

### `radar_raw_source_items.policy_update_status`

```
pending  ──► ingested   (LLM 判断 should_ingest=true，已创建 policy update)
         ──► discarded  (LLM 判断 should_ingest=false)
         ──► failed     (PDF 下载失败 或 LLM 调用失败)
failed   ──► ingested / discarded / failed  (下次周期重试，attempt_count < 3)
```

每次处理（无论成功失败）都递增 `policy_update_attempt_count`。达到 3 次后不再重试，并写入 `attempt_exhausted` webhook 事件。

### `radar_policy_updates` 的三条独立状态轨道

| 字段 | 初始值 | 后续流转 |
|---|---|---|
| `policy_extract_status` | `pending` | Stage 3 处理 |
| `policy_review_status` | `pending` | 人工审核 |
| `action_calculate_status` | `pending` | Stage 4 处理（需 review approved） |

Stage 2 只负责写入初始值，不驱动这三条轨道。

---

## 新模块：`src/radar_backend/llm/`

### 目录结构

```
src/radar_backend/llm/
├── __init__.py
├── client.py          # LLM HTTP 客户端（带重试）
└── policy_filter.py   # 过滤 + briefing 生成的 prompt 和输出解析
```

### `llm/client.py`

薄封装，调用 LLM API（Claude）：

- 读取 `settings.llm_api_key`、`settings.llm_model`
- 连接/读取超时：60 秒
- 重试：最多 3 次，指数退避，覆盖 5xx / TransportError
- 最终失败抛出异常，由 Stage 层决定如何计入 attempt_count

### `llm/policy_filter.py` — 核心 LLM 交互

**输入结构 `PolicyFilterInput`**（dataclass）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `source_key` | `str` | 数据源标识 |
| `source_label` | `str` | 数据源名称 |
| `title` | `str` | 原始标题 |
| `raw_content` | `str` | 正文全文 |
| `attachment_text` | `str` | PDF 解析文本，无附件时为空字符串 |

**输出结构 `PolicyUpdateDraft`**（dataclass）：

| 字段 | 类型 | 约束 |
|---|---|---|
| `should_ingest` | `bool` | 主决策门 |
| `discard_reason` | `str \| None` | should_ingest=false 时必填 |
| `reference_number` | `str \| None` | 来源的可读编号（如 "Proclamation 11002"） |
| `headline` | `str` | 非空，should_ingest=true 时必填 |
| `summary` | `str` | 非空，should_ingest=true 时必填 |
| `briefing_markdown` | `str` | 非空，should_ingest=true 时必填 |
| `effective_date` | `str \| None` | `YYYY-MM-DD` 格式或 null |

**Prompt 设计要点：**

- 要求 LLM 返回严格的 JSON，包裹在 `<json>...</json>` 标签内
- LLM 只决定 `should_ingest` 和 briefing 内容，不输出 `source_key / source_label / source_url / pdf_urls / published_at / original_text`（这些由代码从 raw item 复制）
- `hts_archive` 来源需在 prompt 中额外说明：跳过 Source 字段为 `PP xxxxx`、`Executive Order`、`Notice` 的 change record 条目，只处理其他类型

**输出解析：**

- 解析 `<json>` 块
- 验证 `should_ingest=true` 时 `headline / summary / briefing_markdown` 非空
- 任何解析失败视为 `invalid output`，归入 failed 路径

---

## 新模块：`src/radar_backend/pdf/`

### 目录结构

```
src/radar_backend/pdf/
├── __init__.py
└── downloader.py      # PDF 下载 + 文本提取
```

### `pdf/downloader.py`

**`download_and_parse(pdf_urls: list[str], http: HttpClient) -> str`**

- 遍历 `pdf_urls`，依次下载
- 用 `pypdf`（纯 Python，无系统依赖）提取文本
- 多个 PDF 的文本拼接，用 `\n\n---\n\n` 分隔
- 单个 PDF 失败：记录警告，继续处理其他
- 全部失败：抛出异常，由 Stage 层处理

**依赖：** `pypdf>=4,<6`（加入 `pyproject.toml`）

---

## `RawSourceItemsRepository` — 新增方法

文件：[raw_source_items.py](../../../src/radar_backend/db/repositories/raw_source_items.py)

### `fetch_pending_for_policy_update(conn, limit=100) -> list[RawSourceItem]`

```sql
SELECT id, source_key, source_label, source_url, source_item_key,
       title, published_at, pdf_urls, raw_metadata, raw_content,
       policy_update_attempt_count
FROM radar_raw_source_items
WHERE policy_update_status IN ('pending', 'failed')
  AND policy_update_attempt_count < 3
ORDER BY id ASC
LIMIT %s
```

返回值 `RawSourceItem`（dataclass，只包含 Stage 2 所需字段）。

### `set_policy_update_status(conn, item_id, status, attempt_count, discard_reason=None)`

```sql
UPDATE radar_raw_source_items
SET policy_update_status = %s,
    policy_update_attempt_count = %s,
    discard_reason = %s,
    updated_at = now()
WHERE id = %s
```

在短事务中调用。

---

## `PolicyUpdatesRepository` — 新增方法

文件：[policy_updates.py](../../../src/radar_backend/db/repositories/policy_updates.py)

### `insert(conn, raw_source_item_id, raw_item, draft) -> int`

返回新插入行的 `id`。

```sql
INSERT INTO radar_policy_updates (
    raw_source_item_id, source_key, source_label, source_url,
    reference_number, published_at, pdf_urls, source_metadata,
    headline, summary, briefing_markdown, original_text, effective_date,
    policy_extract_status, policy_review_status, action_calculate_status
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
          'pending', 'pending', 'pending')
RETURNING id
```

字段映射：

| DB 字段 | 来源 |
|---|---|
| `raw_source_item_id` | raw item `id` |
| `source_key / source_label / source_url` | raw item 快照 |
| `pdf_urls / published_at` | raw item 快照 |
| `source_metadata` | raw item `raw_metadata`（原样复制） |
| `reference_number` | LLM draft |
| `headline / summary / briefing_markdown` | LLM draft |
| `original_text` | raw item `raw_content`（清洗多余空白） |
| `effective_date` | LLM draft（`YYYY-MM-DD` → `date`） |

---

## `WebhookEventsRepository` — 新增方法

文件：[webhook_events.py](../../../src/radar_backend/db/repositories/webhook_events.py)

### `upsert_attempt_exhausted(conn, entity_type, entity_id)`

```sql
INSERT INTO radar_webhook_events
    (event_type, entity_type, entity_id, channel, payload, status)
VALUES ('attempt_exhausted', %s, %s, 'lark', %s, 'pending')
ON CONFLICT (event_type, entity_type, entity_id, channel) DO NOTHING
```

`entity_type = 'raw_policy_update'`，`entity_id = raw_source_item_id`。

---

## `CreatePolicyUpdatesStage` — 实现

文件：[create_policy_updates.py](../../../src/radar_backend/worker/stages/create_policy_updates.py)

### `run()` 逻辑

```
1. 用 db.connection() 查询待处理的 raw items
   raw_items = repo.raw_source_items.fetch_pending_for_policy_update(conn, limit=100)

2. 创建共享 HttpClient 和 LLMClient

3. 逐条处理每个 raw item（串行）：

   a. 若 pdf_urls 不为空：
      - 调用 download_and_parse(pdf_urls, http)
      - 失败（3次重试均失败）：
        → short transaction: set status=failed, increment attempt_count
        → if new attempt_count == 3: upsert attempt_exhausted webhook
        → continue 下一条

   b. 调用 LLMClient: filter_and_generate(PolicyFilterInput)
      - 失败（3次重试均失败）或输出无效：
        → short transaction: set status=failed, increment attempt_count
        → if new attempt_count == 3: upsert attempt_exhausted webhook
        → continue 下一条

   c. 若 should_ingest = false：
      → short transaction:
           set status=discarded, increment attempt_count
           set discard_reason = draft.discard_reason

   d. 若 should_ingest = true：
      → begin transaction:
           INSERT radar_policy_updates (所有字段)
           UPDATE raw item: set status=ingested, increment attempt_count
        commit

4. 返回 StageResult(processed_count=ingested_count)
```

**关键规则：**
- 每条 item 独立事务，一条失败不影响其他
- PDF 下载和 LLM 调用在事务**外**执行
- `attempt_count` 在同一个短事务里和 `status` 一起写，进程崩溃不计入本次尝试
- `original_text` = `raw_content` 清洗（去除多余空白），不截断

---

## 需要修改的文件

| 文件 | 变更 |
|---|---|
| [pyproject.toml](../../../pyproject.toml) | 新增 `pypdf>=4,<6`，新增 `settings.llm_api_key` / `settings.llm_model` |
| [config/settings.py](../../../src/radar_backend/config/settings.py) | 新增 `llm_api_key: str`（必填）、`llm_model: str`（默认 `claude-opus-4-6`） |
| [raw_source_items.py](../../../src/radar_backend/db/repositories/raw_source_items.py) | 新增 `fetch_pending_for_policy_update()`、`set_policy_update_status()` |
| [policy_updates.py](../../../src/radar_backend/db/repositories/policy_updates.py) | 新增 `insert()` |
| [webhook_events.py](../../../src/radar_backend/db/repositories/webhook_events.py) | 新增 `upsert_attempt_exhausted()` |
| [create_policy_updates.py](../../../src/radar_backend/worker/stages/create_policy_updates.py) | 实现 Stage 2 完整逻辑 |

## 需要新建的文件

| 文件 | 用途 |
|---|---|
| `src/radar_backend/llm/__init__.py` | 包初始化 |
| `src/radar_backend/llm/client.py` | LLM HTTP 客户端（带重试） |
| `src/radar_backend/llm/policy_filter.py` | `PolicyFilterInput`、`PolicyUpdateDraft`、prompt 构造、输出解析 |
| `src/radar_backend/pdf/__init__.py` | 包初始化 |
| `src/radar_backend/pdf/downloader.py` | `download_and_parse()` |

---

## 事务边界总结

| 操作 | 事务范围 |
|---|---|
| PDF 下载 | 事务外 |
| LLM 调用 | 事务外 |
| set status=failed + increment + (可选) webhook | 单独短事务 |
| set status=discarded + increment + discard_reason | 单独短事务 |
| INSERT policy_updates + set status=ingested + increment | 同一个事务（原子） |

---

## 验证方式

1. **单元测试** `tests/test_create_policy_updates/`：
   - mock LLM client + PDF downloader
   - 验证 should_ingest=true 路径：policy_updates 插入 + raw item 状态变 ingested
   - 验证 should_ingest=false 路径：raw item 状态变 discarded，discard_reason 写入
   - 验证 PDF 失败路径：raw item 状态变 failed，attempt_count 递增
   - 验证 attempt_count=3 时写入 webhook 事件

2. **集成测试**：先跑 `radar-worker --once` 完成 Stage 1 入库，再跑第二次触发 Stage 2，查询 `radar_policy_updates` 验证数据

3. **幂等性**：对同一批 raw items 重复运行 Stage 2，`ingested` 的记录不会被再次处理（status 已不在 pending/failed）
