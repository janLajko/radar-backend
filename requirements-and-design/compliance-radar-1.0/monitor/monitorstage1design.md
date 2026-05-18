# Stage 1 代码设计：CollectSourceItemsStage

## 背景

Stage 1 是周期性 Worker 流水线的入口。它从四个外部政府数据源抓取原始条目，统一转换为标准候选数据结构，并将每条新条目写入 `radar_raw_source_items`（幂等：已存在的条目静默跳过）。Stage 2 必须等待所有 source adapter 全部完成后才能启动。

骨架文件位于 [collect_source_items.py](../../../src/radar_backend/worker/stages/collect_source_items.py)，目前没有实际逻辑。

---

## 新模块：`src/radar_backend/sources/`

所有数据源相关代码集中在此模块，不引入其他新模块。

### 目录结构

```
src/radar_backend/sources/
├── __init__.py
├── base.py              # RawSourceItemCandidate + SourceAdapter Protocol
├── config.py            # YAML 配置加载，SourceConfig 数据类
├── http_client.py       # 带重试的 HttpClient
├── proclamation.py      # ProclamationAdapter
├── executive_order.py   # ExecutiveOrderAdapter
├── federal_register.py  # FederalRegisterNoticeAdapter
└── hts_archive.py       # HTSArchiveAdapter
```

---

## `sources/base.py` — 共享类型

### `RawSourceItemCandidate`（frozen dataclass）

| 字段 | 类型 | 说明 |
|---|---|---|
| `source_item_key` | `str` | 稳定去重键（见下方生成策略） |
| `source_url` | `str` | 文档的权威 URL |
| `title` | `str` | 标题 |
| `published_at` | `datetime \| None` | UTC 时区 |
| `raw_content` | `str` | Stage 2 LLM 使用的全文正文 |
| `raw_metadata` | `dict` | 数据源特有的只读快照 |
| `pdf_urls` | `list[str]` | 附件 URL 列表，Stage 1 不下载也不解析 PDF 内容 |

### `SourceAdapter`（Protocol）

```python
class SourceAdapter(Protocol):
    def fetch(self, fetch_config: dict, http: HttpClient) -> list[RawSourceItemCandidate]:
        ...
```

### `source_item_key` 生成优先级

1. 官方编号（proclamation 编号、EO 编号、document number、archive ID）
2. 稳定的 URL slug
3. `title + published_at + url` 的哈希值

---

## `sources/http_client.py` — 共享 HTTP 客户端

基于 `httpx.Client` 的薄封装：

- 连接/读取超时：30 秒
- 重试逻辑：遇到 `httpx.TransportError` 或 HTTP 5xx 最多重试 3 次，指数退避（1s、2s、4s）
- 最终失败时抛出异常，由调用方决定是否中止本次采集

不引入 `tenacity`，重试逻辑约 15 行可内联实现。

---

## `sources/config.py` — 数据源配置加载

从 YAML 文件（路径由 `settings.source_config_path` 指定）加载为 `SourceConfig` 列表。

### `SourceConfig`（frozen dataclass）

| 字段 | 类型 |
|---|---|
| `source_key` | `str` |
| `source_label` | `str` |
| `adapter` | `str`（注册表键名） |
| `enabled` | `bool` |
| `fetch` | `dict`（adapter 自定义参数） |

### YAML 示例（`config/sources.yaml`）

```yaml
sources:
  - source_key: presidency_proclamation
    source_label: "Presidential Proclamation"
    adapter: proclamation
    enabled: true
    fetch:
      lookback_days: 14
      items_per_page: 25

  - source_key: presidency_executive_order
    source_label: "Executive Order"
    adapter: executive_order
    enabled: true
    fetch:
      lookback_days: 14
      items_per_page: 25

  - source_key: federal_register_notice
    source_label: "Federal Register Notice"
    adapter: federal_register
    enabled: true
    fetch:
      per_page: 40
      agencies:
        - industry-and-security-bureau
        - international-trade-administration
        - office-of-the-united-states-trade-representative
        - us-customs-and-border-protection

  - source_key: hts_usitc_archive
    source_label: "HTS USITC Archive"
    adapter: hts_archive
    enabled: true
    fetch: {}
```

---

## `sources/proclamation.py` — ProclamationAdapter

目标：`https://www.presidency.ucsb.edu/advanced-search?category2[]=59`

**采集逻辑：**

1. GET 列表页（HTML），用 `BeautifulSoup` 解析
2. 提取指向 `/documents/proclamation-*` 的文档链接
3. 对每个文档链接：GET 详情页
4. 提取：标题（`<h1>` 或 `<title>`）、发布日期、正文文本、`.pdf` 附件链接
5. `source_item_key` = URL slug，例如 `proclamation-11002-adjusting-imports-semiconductors-...`
6. `pdf_urls` = 绝对路径的 PDF 附件 URL 列表

**回溯窗口：** 只采集 `published_at >= now - lookback_days` 的条目；无日期的条目保留。

---

## `sources/executive_order.py` — ExecutiveOrderAdapter

目标：`https://www.presidency.ucsb.edu/advanced-search?category2[0]=58`

逻辑与 `ProclamationAdapter` 完全相同，仅 search category 参数和 URL slug 前缀不同（`executive-order-*`）。

**复用策略：** 两个 adapter 共享一个 `_presidency_fetch()` 内部函数，避免重复 HTML 解析代码。

---

## `sources/federal_register.py` — FederalRegisterNoticeAdapter

目标：Federal Register REST API `https://www.federalregister.gov/api/v1/documents.json`

**采集逻辑：**

1. GET API，参数：
   - `conditions[type][]=NOTICE`
   - `conditions[agencies][]` = fetch config 中配置的每个 agency slug
   - `per_page` = fetch config 中的值
2. 解析 JSON `results` 数组
3. 每条结果映射为 `RawSourceItemCandidate`：
   - `source_item_key` = `document_number`（如 `2026-07987`）
   - `source_url` = `html_url`
   - `title` = `title`
   - `published_at` = `publication_date`（解析为 UTC）
   - `raw_content` = `abstract`（若有 `body_html_url` 则追加全文）
   - `pdf_urls` = `[pdf_url]`（若有）
   - `raw_metadata` = `{ "document_number": ..., "agencies": [...], "docket_ids": [...] }`

**说明：** Agency 过滤在 adapter 层完成（结构过滤）。LLM 相关性过滤在 Stage 2 处理。

---

## `sources/hts_archive.py` — HTSArchiveAdapter

目标：`https://hts.usitc.gov/download/archive`

**采集逻辑：**

1. GET archive 列表页（HTML）
2. 解析所有 archive 条目及其下载链接（重点是 change record PDF）
3. 每个 archive 条目生成一条 `RawSourceItemCandidate`：
   - `source_item_key` = archive ID，如 `2026HTSBasic`
   - `source_url` = archive 下载页 URL
   - `title` = `"HTS Archive: {archive_id}"`
   - `published_at` = 可解析则填写，否则 `None`
   - `raw_content` = 简短描述，如 `"New HTS archive 2026HTSBasic available."`
   - `pdf_urls` = `[change_record_pdf_url]`
   - `raw_metadata` = `{ "archive_id": ..., "change_record_url": ... }`

**去重：** 数据库唯一约束 `(source_key, source_item_key)` 保证幂等，adapter 无需维护本地状态。每次周期提交所有已知 archive，已存在的静默跳过。

**关于 Source 字段过滤（PP / Executive Order / Notice）：** change record PDF 中的 Source 字段过滤属于 Stage 2 LLM 的 prompt 指令，不是 Stage 1 关注点。Stage 1 只保存 PDF URL。

---

## `RawSourceItemsRepository` — 新增方法

文件：[raw_source_items.py](../../../src/radar_backend/db/repositories/raw_source_items.py)

### `insert_if_not_exists()`

```python
def insert_if_not_exists(
    self,
    conn,
    candidate: RawSourceItemCandidate,
    source_key: str,
    source_label: str,
) -> bool:
    """插入一条原始数据源条目。返回 True 表示插入成功，False 表示已存在跳过。"""
```

SQL：

```sql
INSERT INTO radar_raw_source_items
  (source_key, source_label, source_item_key, source_url, title,
   published_at, pdf_urls, raw_metadata, raw_content)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (source_key, source_item_key) DO NOTHING
RETURNING id
```

`RETURNING id` 有结果 → 返回 `True`（新插入），无结果 → 返回 `False`（已存在）。

---

## `CollectSourceItemsStage` — 实现

文件：[collect_source_items.py](../../../src/radar_backend/worker/stages/collect_source_items.py)

### Adapter 注册表（模块级常量）

```python
ADAPTER_REGISTRY: dict[str, SourceAdapter] = {
    "proclamation":      ProclamationAdapter(),
    "executive_order":   ExecutiveOrderAdapter(),
    "federal_register":  FederalRegisterNoticeAdapter(),
    "hts_archive":       HTSArchiveAdapter(),
}
```

### `run()` 逻辑

```
1. 从 context.settings.source_config_path 加载 SourceConfig 列表
2. 过滤出 enabled=True 的数据源
3. 创建共享 HttpClient 实例
4. 用 ThreadPoolExecutor(max_workers=len(enabled_sources)) 并行运行所有 adapter
   - 每个 future：adapter.fetch(config.fetch, http_client)
   - 等待全部 future 完成（futures.wait），再进入写库阶段
   - 若某个 adapter 抛出异常，记录日志后继续处理其他 adapter 的结果
5. 遍历所有 (source_config, candidates) 对，依次调用：
   repo.insert_if_not_exists(conn, candidate, source_key, source_label)
   使用单个 db.connection() 上下文管理器覆盖整个写循环
6. 统计 inserted_count / skipped_count，每个数据源输出一条 INFO 日志
7. 返回 StageResult(stage_name=self.name, processed_count=inserted_count)
```

**关键约束：** 所有 adapter 必须全部完成（包括失败的）才能进入 DB 写入步骤，保证 Stage 2 不会在数据采集未完成时启动。

---

## 需要修改的文件

| 文件 | 变更内容 |
|---|---|
| [pyproject.toml](../../../pyproject.toml) | 新增依赖：`httpx>=0.27,<1`、`beautifulsoup4>=4.12,<5`、`pyyaml>=6,<7` |
| [config/settings.py](../../../src/radar_backend/config/settings.py) | 新增 `source_config_path: str`，从 `SOURCE_CONFIG_PATH` 环境变量读取（必填） |
| [raw_source_items.py](../../../src/radar_backend/db/repositories/raw_source_items.py) | 新增 `insert_if_not_exists()` 方法 |
| [collect_source_items.py](../../../src/radar_backend/worker/stages/collect_source_items.py) | 实现 Stage 1 完整逻辑 |

## 需要新建的文件

| 文件 | 用途 |
|---|---|
| `src/radar_backend/sources/__init__.py` | 包初始化 |
| `src/radar_backend/sources/base.py` | `RawSourceItemCandidate`、`SourceAdapter` Protocol |
| `src/radar_backend/sources/config.py` | YAML 配置加载，`SourceConfig` 数据类 |
| `src/radar_backend/sources/http_client.py` | 带重试的 `HttpClient` |
| `src/radar_backend/sources/proclamation.py` | `ProclamationAdapter` |
| `src/radar_backend/sources/executive_order.py` | `ExecutiveOrderAdapter` |
| `src/radar_backend/sources/federal_register.py` | `FederalRegisterNoticeAdapter` |
| `src/radar_backend/sources/hts_archive.py` | `HTSArchiveAdapter` |
| `config/sources.yaml` | 默认数据源配置文件 |

---

## 核心设计约束

- **不 upsert**：已存在的原始条目不更新，只插入新条目
- **Stage 1 不解析 PDF**：只保存 `pdf_urls`，PDF 内容在 Stage 2 按需下载
- **所有 adapter 必须完成**后才进入 DB 写入（`ThreadPoolExecutor.wait`）
- **Adapter 故障隔离**：单个 adapter 失败只记日志，不中断其他 adapter
- **`source_key` / `source_label` 在插入时快照**，写入后不可变
- **重试策略**：HTTP 层重试（3 次，退避）在各 adapter 内部处理；Stage 1 没有 durable retry（原始条目只有"插入"或"跳过"，不存在"失败"状态）

---

## 验证方式

1. **单元测试** `tests/test_sources/`：用 mock `HttpClient` 测试各 adapter 的 `fetch()`；测试 `insert_if_not_exists()` 返回正确 bool；测试 `CollectSourceItemsStage.run()` 使用 fake adapter
2. **集成冒烟测试**：`radar-worker --once`（配置 `SOURCE_CONFIG_PATH=config/sources.yaml`），验证 `radar_raw_source_items` 有新行写入
3. **幂等性测试**：连续运行两次，第二次 `inserted_count=0`（全部跳过）
4. **Adapter 隔离测试**：将一个 adapter 配置为无效 URL，验证其他 adapter 仍正常插入并有错误日志输出
