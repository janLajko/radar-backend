# Compliance Radar BLB 1.0 需求与设计讨论结论

最后更新：2026-05-08

本文档记录 Compliance Radar BLB 1.0 需求澄清阶段已经达成的结论。它不是传统意义上只描述产品需求的 PRD，而是把本轮讨论中已经基本确定的产品边界、架构、存储、状态机、接口、任务编排、重试、竞态控制、黑盒边界和待定项统一沉淀下来，作为后续架构蓝图、存储层设计、接口实现和任务拆解的共同依据。

本文档只固化已经讨论并确认过的结论。未完全确定的内容会明确标记为待定或后续专题。

## 1. 背景与项目边界

当前工作区是多项目环境，核心相关项目包括：

- `classification-backend`
- `gov-backend`
- `gov-frontend`
- `tarriff-simulator`
- 原 PRD：`Compliance Rardar 1.0 PRD.docx`

经过项目熟悉和需求澄清后，Compliance Radar 的项目边界修正如下：

- Compliance Radar **不依赖、不改造、不触碰** `gov-backend`。
- `gov-backend` 仅作为历史参考，不作为本次实现基础。
- Compliance Radar 应新建独立 worker 项目，例如 `radar-monitor` / `radar-backend`。
- `radar-backend` 负责重逻辑：数据抓取、解析、入库、Recent Policy Update 生成、policy impact 抽取调度、action 计算调度、邮件发送。
- `classification-backend` 和 `radar-backend` 共享同一个 Postgres 数据库。
- `classification-backend` 承载所有 HTTP/API/auth 能力，包括用户侧 API、review API、unsubscribe API。
- `radar-backend` 被设计为**单实例 worker**部署。
- `gov-frontend` 与 `tarriff-simulator` 只作为前端体验、历史原型或 mock 参考，不决定后端设计。

一个反直觉但已经确认的结论是：

> Compliance Radar 的核心重逻辑在独立的 `radar-backend` worker 中；用户 API 和 review API 在 `classification-backend` 中；两者通过同一个数据库 schema 协作。

## 2. BLB 1.0 范围

本次只实现 BLB 1.0 范围。

必须实现：

- `[p0]` Recent Policy Updates
- `[p1]` Actions / 行动建议
- `[p1]` email 通知
- 用户点击 Action 后进入 reclassify / recalculate 的执行入口所需上下文支持

明确不做：

- 趋势预警
- Quick Check
- Topic subscription / 主题订阅
- Google Calendar
- 自动重新分类 / 自动重新计算关税
- 跨 source 的 policy/action 语义去重
- Radar Action 重新计算入口
- bounce 自动停发处理
- 管理后台 reset attempt
- 复杂 observability/dashboard

需要特别区分两个概念：

- **Radar Action 重新计算入口**：重新调用 `calculate_user_actions(policy_update_id, user_id)`，重建 Radar 中的 Action Card。这个本期明确不做。
- **Action 执行入口**：用户点击 Action Card 上的 Go，跳转到 classification 或 sandbox 产品完成 reclassify / recalculate。这个本期需要支持，但执行主体在 classification / sandbox，不在 Radar。

## 3. 产品概念与业务边界

### 3.1 Recent Policy Updates

Recent Policy Updates 是全局政策更新流：

- 所有登录用户看到同一份 policy update 列表。
- 不按用户产品过滤。
- 不依赖用户是否有 Actions。
- 与 User Impact Actions 是两条独立业务线。
- 可以包含尚未真正生效、但未来会生效的政策。
- 可以包含未来会影响 Action 的政策，即使用户暂时还没有可执行操作。
- 需要登录后查看。

Recent Policy Updates 的数据源不再沿用 `gov-backend` 中的 CSMS / Federal Register / USTR / CBP ruling / White House 那套固定组合。Radar 会有自己全新的一套 source 配置。

Recent Policy Update 的详情内容包括独立 AI briefing，而不是黑盒 policy impact。

文章过滤与 briefing 生成应在同一个 LLM 调用中完成：

- 如果不应进入 Recent Updates，则 raw item 标记为 `discarded`。
- 如果应进入 Recent Updates，则创建 `radar_policy_updates`。

Recent Updates 不暴露：

- 黑盒 policy impact
- policy extract/review/action calculate 状态
- 用户 action 相关字段

### 3.2 Policy Impact

Policy Impact 是黑盒函数负责的领域。

黑盒不是第三方远程服务，而是同事实现的本地业务模块。我们称它为黑盒，是为了划分工作边界，不代表不信任它的程序边界，也不代表它不能提供 API/UI 支持。

黑盒负责：

- 根据 policy update 抽取政策影响结构。
- 存储自己的 policy impact 数据。
- 提供 review 读写与校验 helper。
- 基于已审核通过的 policy impact 和用户数据计算候选 user actions。

Radar 不负责设计黑盒内部 policy impact 表结构。

### 3.3 User Impact Actions

User Impact Actions 只认 Recent Policy Updates 为唯一数据来源。

Action 是用户级别的：

- 以 `user_id` 为核心。
- 不引入 company/account/tenant。
- 不按 topic subscription 过滤。
- 不基于用户产品提前过滤 policy update。

一个用户对一个 policy update 最多有一个 Action Card：

```text
unique(user_id, policy_update_id)
```

Action Card 由两类信息组成：

- affected products：受影响产品列表。
- suggested actions：建议操作列表。

1.0 只支持两个 action type：

- `reclassify_product`
- `recalculate_tariff`

即便未来，也只支持这两个 action type。

### 3.4 Notification Recipients 与 Email

1.0 只发送 Impact Action 通知，不发送 Recent Policy Update digest。

每封邮件对应：

```text
one user_action_id + one recipient
```

邮件只发给当时 active 的 notification recipients。

新增邮箱：

- 只做格式校验。
- 不做邮箱验证。
- 每个用户最多 5 个 active recipient。

退订：

- 每封邮件必须带 unsubscribe 链接。
- unsubscribe 必须真实生效。
- unsubscribe 走前端页面，前端地址通过环境变量配置。

recipient 状态：

- `active`
- `unsubscribed`
- `deleted`

1.0 不做 `bounced` 状态，也不做 bounce 自动停发。

## 4. 总体架构

### 4.1 项目职责

`radar-backend`：

- 单实例 worker。
- 读取 source 配置。
- 抓取外部数据源。
- 写 raw source items。
- 处理 raw items，调用 LLM 过滤并生成 briefing。
- 创建 policy updates。
- 调用黑盒 `extract_policy_impact()`。
- 在 policy review approved 后调用黑盒 `calculate_user_actions()`。
- 写入 user actions、affected products、action items、email deliveries。
- 发送邮件。
- 周期任务回补失败或未完成状态。

`classification-backend`：

- 维护 Radar 数据库 migration。
- 提供用户侧 API。
- 提供 review API。
- 提供 unsubscribe API。
- 使用现有登录/auth 体系保护用户侧 API。
- 使用 token 保护 review API。

黑盒模块：

- 与 `radar-backend` 同进程本地函数调用。
- 可能也被 `classification-backend` review API 调用其 helper。
- 自己维护 policy impact 业务表。
- 不负责更新 `radar_policy_updates` 的状态字段。
- 不负责存储 user actions。
- 不负责发邮件。

### 4.2 数据库归属

所有 Radar 自有表都建在 `classification-backend` 当前使用的 Postgres 中，表名前缀统一为 `radar_`。

迁移边界：

- migration 由 `classification-backend` 管理。
- `radar-backend` 依赖这些表，但不自行偷偷建表。
- 黑盒自己的 policy impact 表也应落在同一个 DB 中；表结构由黑盒 owner 定义，但迁移最好仍通过同一套 migration 流程落库。

1.0 不做共享 package / common library：

- 两个项目通过数据库 schema 契约协作。
- 状态枚举值写入 migration/check constraint。
- 两边代码可以各自定义轻量 DTO/常量。
- 不为复用枚举或 SQL model 引入共享包。

## 5. 主流程

整体流程：

```text
source config
  -> fetch source items
  -> insert raw source items if not exists
  -> process raw items with LLM
  -> create policy updates or discard raw items
  -> extract policy impact by black-box
  -> human review policy impact
  -> approve
  -> calculate user actions
  -> persist actions/products/items/email deliveries
  -> send emails
  -> user views actions and completes action items
```

### 5.1 周期任务入口

`radar-backend` 使用单一周期任务，外层显式编排 stage：

```text
run_periodic_cycle():
  1. fetch_sources()
  2. process_raw_items()
  3. extract_policy_impacts()
  4. calculate_approved_actions()
  5. send_pending_emails()
```

约束：

- stage 之间串行。
- 一个 stage 完成后才进入下一个 stage。
- 定时任务内部不再启动异步线程。
- `fetch_sources()` 是唯一允许 source 级并发的 stage。
- fetch 阶段所有 source 完成并写入 raw items 后，才进入 policy update 处理阶段。
- stage 函数内部只做本阶段，不 tail-call 下一阶段。

### 5.2 实时触发入口

任何状态推进后，如果能实时尝试下一步，就实时尝试一次；失败时只更新状态，最终由周期任务扫描状态回补。

policy update 创建后的实时入口：

```text
run_after_policy_update_created(policy_update_id):
  1. extract_policy_impacts(policy_update_id)
```

approve 后实时入口：

```text
run_after_approve(policy_update_id):
  1. calculate_approved_actions(policy_update_id)
  2. send_pending_emails(policy_update_id)
```

注意：approve 后的 `send_pending_emails()` 必须带 `policy_update_id`，只针对这个 policy update 发送邮件，不扫全局。

## 6. 数据源与 Raw Items

### 6.1 Source 配置

source 只基于配置文件，不使用环境变量控制 source 行为。

建议配置结构：

```yaml
sources:
  - source_key: "federal_register"
    source_label: "Federal Register"
    adapter: "federal_register"
    enabled: true
    fetch:
      lookback_days: 7
      max_items: 100

  - source_key: "ustr"
    source_label: "USTR"
    adapter: "ustr"
    enabled: true
    fetch:
      lookback_days: 14
      max_items: 100
```

语义：

- `source_key` 是数据库、API、前端 filter 的稳定值。
- `source_label` 是展示快照，会写入 raw item 和 policy update。
- `adapter` 是代码里的 adapter 名称。
- `enabled` 控制是否抓取。
- `fetch` 是 adapter-specific 配置，框架不强行解释所有字段，只传给 adapter。

worker 启动时应校验：

- `source_key` 唯一。
- `source_key/source_label/adapter/enabled` 必填。
- adapter 名称必须存在。
- enabled source 的 fetch 配置必须通过 adapter 自己的校验。

### 6.2 Source Adapter 契约

每个 adapter 最小接口：

```ts
type SourceAdapter = {
  source_key: string
  source_label: string

  fetch(config): Array<RawSourceCandidate>
}
```

`fetch()` 返回标准化候选项：

```ts
type RawSourceCandidate = {
  source_item_key: string
  source_url: string
  title: string
  published_at: string | null
  raw_content: string
  raw_metadata: object
  pdf_urls: string[]
}
```

约束：

- `source_item_key` 必须稳定，由 adapter 自己负责生成。
- `raw_content` 是后续 LLM 判断和 briefing 的主要文本来源。
- `raw_metadata` 保存 source-specific 的结构化补充信息，例如 docket number、agency、document type、tags、feed id、附件标题等。
- `pdf_urls` 只保存 URL，不保存 PDF 正文。
- adapter 只负责抓取和标准化，不判断是否进入 policy update。
- adapter 内部可以做 source-specific 的 limit/lookback。
- adapter 内部网络请求做 RPC 级 3 次重试。
- framework 负责 insert-if-not-exists。

### 6.3 去重键

统一契约：

> 每个 source adapter 必须产出稳定的 `source_item_key`，数据库用 `(source_key, source_item_key)` 做唯一键；抓取时如果已存在，则跳过。

```text
unique(source_key, source_item_key)
```

生成规则：

- 如果源站有官方 ID，用官方 ID。
- 如果有稳定 URL，用 canonical URL。
- 如果是 RSS/feed/item，用 feed entry id 或 link。
- 如果实在没有稳定 ID，adapter 可以用标题 + 发布时间 + URL 组合后 hash。
- 不建议用正文 hash 作为主去重键，因为正文可能因网页模板、附件解析、空白字符变化而变化。

抓取阶段不是 upsert，也不是幂等更新，而是：

```text
exists -> skip
not exists -> insert
```

### 6.4 PDF 附件

有些文章有附件，附件可能是判断是否进入 policy update、extract policy impact、calculate actions 的关键原材料。

规则：

- `pdf_urls` 必须解析出来并保存到库中。
- 不保存 PDF 正文到数据库。
- 在重要处理时按需下载使用。
- PDF 下载/解析失败时，内部重试 3 次并带退避。
- 如果仍失败，则该 raw item 本次处理失败，后续由周期任务回补。

## 7. Raw Item -> Policy Update

### 7.1 一对一边界

1.0 按一对一设计：

```text
one radar_raw_source_item -> zero or one radar_policy_update
one radar_policy_update -> exactly one radar_raw_source_item
```

规则：

- 一个 raw item 被判断相关，就生成一个 policy update。
- 一个 raw item 被判断不相关，就不生成 policy update，标记 `discarded`。
- 不做多个 raw items 合并成一个 policy update。
- 不做一个 raw item 拆成多个 policy updates。
- `radar_policy_updates.raw_source_item_id` 加唯一约束。

从用户体验看，同一个政策被多个源转发时，Action 层最好按真实政策事件去重。但这属于复杂的实体归并问题，1.0 不做。后续如果要做，可以引入 `policy_event_id` / canonical policy event 层。

### 7.2 LLM 输出契约

LLM 只负责一个合并判断：

> 这条 raw item 是否应该进入 Recent Policy Updates；如果应该，同时产出 briefing 所需字段。

输出结构：

```ts
type PolicyUpdateDraft = {
  should_ingest: boolean
  discard_reason: string | null

  reference_number: string | null
  headline: string
  summary: string
  briefing_markdown: string
  effective_date: string | null // YYYY-MM-DD
}
```

语义：

- `should_ingest = false`：raw item 标记为 `discarded`。
- `discard_reason` 有助于 debug prompt 质量，但不入库，只打日志。
- `should_ingest = true`：创建一条 `radar_policy_updates`。
- `headline/summary/briefing_markdown` 必须非空。
- `source_key/source_label/source_url/pdf_urls/published_at/raw_source_item_id` 不由 LLM 决定，从 raw item 拷贝。
- `original_text` 不由 LLM 生成，而是来自 `raw_source_item.raw_content` 的清洗结果。
- LLM 不输出 topic、severity、impact action、policy impact。
- 如果输出结构不合法，算 raw item processing failed。

`reference_number` 指源站自己的可读编号，不是我们的主键，也不是去重键。例如：

- Federal Register document number / docket id
- USTR notice id / docket number
- Executive order number / proclamation number
- 其它 source 的 announcement id / notice number

它只是展示和人工识别用，可为空、可重复，不参与核心流程。

## 8. 黑盒边界

### 8.1 核心函数

黑盒至少提供以下本地函数：

```ts
extract_policy_impact(policy_update_id: string) -> bool

get_policy_impact(policy_update_id: string) -> object | null

validate_policy_impact(
  policy_update_id: string,
  policy_impact: object
) -> ValidationResult

save_policy_impact(
  policy_update_id: string,
  policy_impact: object
) -> void

calculate_user_actions(
  policy_update_id: string,
  user_id: string
) -> UserActionCandidates
```

`ValidationResult`：

```ts
type ValidationResult = {
  success: boolean
  message: string | null
}
```

不用结构化 `path`，避免维护复杂 path 解析逻辑。

### 8.2 extract_policy_impact()

职责：

- 读取 `policy_update`。
- 使用 policy update 正文、附件等上下文进行 LLM/规则处理。
- 写入黑盒自己的 policy impact 表。
- 返回 bool 表示成功/失败。

不负责：

- 更新 `radar_policy_updates.policy_extract_status`。
- 暴露 policy impact schema 给 Radar。
- 存储 user actions。

### 8.3 calculate_user_actions()

返回候选 Action Card 业务形状，不返回数据库字段名，也不负责生成 ID：

```ts
type UserActionCandidates = {
  affected_products: Array<{
    product_uid: string
    product_name: string
    hts_code: string | null
    applicable_action_types: Array<"reclassify_product" | "recalculate_tariff">
  }>

  suggested_actions: Array<{
    action_type: "reclassify_product" | "recalculate_tariff"
    note: string
    effective_date: string | null // YYYY-MM-DD
  }>
}
```

约束：

- `affected_products` 和 `suggested_actions` 任一为空，就视为这个用户没有 Action Card，不入库。
- `applicable_action_types` 必须是 `suggested_actions.action_type` 的子集。
- `effective_date` 可以为空；API 层可 fallback 到 `policy_update.effective_date`。
- 不返回 completion/status/email 相关字段。
- 不返回 deep link。
- 不返回 raw LLM/debug 信息。
- `product_uid` 必须能对应 `classification-backend` 里的产品标识。

## 9. 人工审核

### 9.1 审核原则

人工审核是 policy impact 的审核，而不是 Recent Update briefing 的审核。

引入人工审核的原因：

- `extract_policy_impact()` 基于 LLM，项目上线初期不完全信任。
- 如果 policy update 一出现就计算 Action 并发邮件，可能底层 Classification / Calculator 数据源尚未同步，导致用户收到通知后无法实际执行，系统不自洽。

人工审核要求：

- reviewer 必须能查看 policy impact。
- reviewer 必须能编辑 policy impact。
- reviewer approve 后才触发 user actions 计算。
- reviewer reject 后不触发 user actions 计算。

### 9.2 审核 API 边界

审核 API 由 `classification-backend` 承载 HTTP，黑盒承载 policy impact 数据读写和校验。

建议接口：

```http
GET  /api/compliance-radar/review/policy-impacts
GET  /api/compliance-radar/review/policy-impacts/{policy_update_id}
PUT  /api/compliance-radar/review/policy-impacts/{policy_update_id}
POST /api/compliance-radar/review/policy-impacts/{policy_update_id}/approve
POST /api/compliance-radar/review/policy-impacts/{policy_update_id}/reject
```

鉴权：

```http
Authorization: Bearer <REVIEW_TOKEN>
```

`REVIEW_TOKEN` 来自环境变量。

review API 返回 policy update 基础信息、状态字段和黑盒 policy impact object。`policy_impact` 是黑盒定义的结构，`classification-backend` 不理解、不拆字段、不入侵 schema，只做透传。

### 9.3 审核状态推进

review 状态枚举：

```text
policy_review_status = pending / approved / rejected
```

规则：

- 创建 policy update 时默认 `pending`。
- `pending` 表示“尚未审核过”，不表示“已经可审核”。
- 是否可审核由 `policy_extract_status = succeeded` 决定。
- approve/reject 只允许在：

```text
policy_extract_status = succeeded
AND policy_review_status = pending
```

- `policy_extract_status = failed` 时，不允许 approve/reject。
- approved 后不可编辑、不可回退、不可 reject。
- rejected 后不支持恢复。

## 10. 状态机

### 10.1 Raw Item 状态

字段：

```text
radar_raw_source_items.policy_update_status
```

枚举：

```text
pending / ingested / discarded / failed
```

语义：

- `pending`：已抓取，尚未处理。
- `ingested`：已通过过滤并创建 policy update。
- `discarded`：已处理，但判断不应进入 Recent Updates。
- `failed`：处理失败，若 attempt_count 未达上限，后续周期任务可重试。

### 10.2 Policy Update 状态

字段：

```text
radar_policy_updates.policy_extract_status
radar_policy_updates.policy_review_status
radar_policy_updates.action_calculate_status
```

枚举：

```text
policy_extract_status = pending / succeeded / failed
policy_review_status = pending / approved / rejected
action_calculate_status = pending / succeeded / failed
```

创建 policy update 时：

```text
policy_extract_status = pending
policy_review_status = pending
action_calculate_status = pending
```

extract 成功只更新：

```text
policy_extract_status = succeeded
```

extract 失败只更新：

```text
policy_extract_status = failed
```

### 10.3 Action 状态

Action Card 和 Action Item 都使用：

```text
action_needed / completed
```

Action Item 完成状态可逆：

- 用户可以 complete。
- 用户可以 uncomplete。

Action Card 状态由 items 同步维护：

- 如果所有 items 都 completed，则 card 为 `completed`，设置 `completed_at/completed_by`。
- 如果任一 item 为 `action_needed`，则 card 为 `action_needed`，清空 card 的 `completed_at/completed_by`。

### 10.4 Email Delivery 状态

```text
pending / sent / failed
```

语义：

- `pending`：尚未发送。
- `sent`：发送成功。
- `failed`：发送失败；如果 attempt_count < 3，后续可重试。

无 `skipped`，无 `bounced`。

## 11. Durable Retry 与内部重试

需要区分两层重试。

### 11.1 RPC 级内部重试

这是单次业务操作内部的短重试，不进入数据库 attempt count。

适用：

- LLM 调用失败。
- PDF 下载失败。
- 外部网页请求失败。
- 邮件服务短暂失败。
- 可安全重试的数据库 transient error。

规则：

```text
单次操作内部 retry 3 次，带短退避。
如果 3 次都失败，这次业务操作才算失败。
```

### 11.2 跨周期 durable 重试

这是数据库记录级别的重试，用 attempt_count 控制最多 3 次，避免坏数据无限拖垮系统。

需要字段：

```text
radar_raw_source_items.policy_update_attempt_count
radar_policy_updates.policy_extract_attempt_count
radar_policy_updates.action_calculate_attempt_count
radar_email_deliveries.attempt_count
```

扫描条件：

```text
raw item:
  policy_update_status in ('pending', 'failed')
  and policy_update_attempt_count < 3

extract:
  policy_extract_status in ('pending', 'failed')
  and policy_extract_attempt_count < 3

action calculate:
  policy_review_status = 'approved'
  and action_calculate_status in ('pending', 'failed')
  and action_calculate_attempt_count < 3

email:
  status in ('pending', 'failed')
  and attempt_count < 3
```

`attempt_count` 应在开始昂贵操作前先加 1。这样即使进程在中途崩溃，这次尝试也会被计入，避免无限重跑。

source fetch 本身不做 durable attempt_count，因为 source fetch 不是某条稳定记录的状态处理。一个 source 今天失败 3 次，不代表明天不该继续抓。fetch 阶段只做 RPC 内部重试，失败后下一轮自然重跑。

达到 3 次失败后：

- 自动流程停止推进。
- 不提供 reset API / UI。
- 需要恢复时，运维或开发直接改数据库。
- attempt_count 是纯后端控制信号，不暴露给普通用户 API，也不暴露给 review API / review 页面。

## 12. Action 计算

### 12.1 Target Users

Action 计算目标用户由 `radar-backend` 环境变量白名单控制：

- 未配置或配置为 `*`：所有用户。
- 配置为 user_ids 列表：只处理这些用户。

不基于用户是否有产品、是否有 recipient 提前过滤。

### 12.2 计算流程

对某个 approved policy update：

1. 读取 target users。
2. loop 每个 user。
3. 分别调用 `calculate_user_actions(policy_update_id, user_id)`。
4. 只保留有 actions 的返回。
5. 在内存中累积。
6. 如果任一用户计算失败，则本次 policy_update action 计算失败，不提交任何 actions。
7. 如果全部成功，则在一个事务内写入：
   - `radar_user_actions`
   - `radar_user_action_products`
   - `radar_user_action_items`
   - `radar_email_deliveries`
   - 更新 `action_calculate_status = succeeded`
8. 事务提交后再发送邮件，不能在事务内发送邮件。

如果返回空：

- 不为该用户写 Action Card。
- 不写“no action”记录。

### 12.3 竞态控制

Action 计算采用“允许重复计算 + 提交时数据库兜底”的方案。

不采用进程内 `Set<policy_update_id>` 作为最终一致性边界。进程内 Set 只在同进程有效，不能覆盖多进程、误部署、多入口、人工脚本等场景。

最终方案：

```text
1. 计算前读状态；如果 action_calculate_status = succeeded，直接返回。
2. 在事务外计算所有用户 candidates，累积在内存中。
3. 开启事务。
4. SELECT action_calculate_status
   FROM radar_policy_updates
   WHERE id = ?
   FOR UPDATE;
5. 如果已经 succeeded，说明其它 pipeline 赢了，rollback/exit，不写入、不发邮件。
6. 否则写入 actions/products/items/email_deliveries。
7. 更新 action_calculate_status = succeeded。
8. commit。
9. commit 后发送邮件。
```

如果计算或提交失败：

```text
UPDATE radar_policy_updates
SET action_calculate_status = 'failed'
WHERE id = ?
  AND action_calculate_status <> 'succeeded'
```

唯一键仍作为最终防线：

```text
unique(user_id, policy_update_id)
```

该方案的取舍：

- 可能有重复计算浪费。
- 但不会有 long-running lock 或 processing 状态卡死。
- 数据最终一致性由事务、唯一键和提交前状态检查保证。

## 13. Action 存储设计

不考虑黑盒自己的 policy impact 表，Radar 侧 Action 建议使用 4 张表。

### 13.1 radar_user_actions

一行表示一个用户对一个 policy update 的 Action Card。

核心字段：

```text
id
user_id
policy_update_id
status                         -- action_needed / completed
completed_at
completed_by
created_at
updated_at
```

约束：

```text
unique(user_id, policy_update_id)
```

冗余 `status` 是有意设计：

- SQL 更简单。
- 查询更快。
- 与 UI status tab 过滤匹配。
- item completion 时顺手同步 card status。

### 13.2 radar_user_action_products

一行表示 Action Card 中一个 affected product 快照。

核心字段：

```text
id
user_action_id
product_uid
product_name
hts_code
applicable_action_types text[]
created_at
```

`applicable_action_types` 表示这个 product 适用于哪些 action type，例如：

```text
["reclassify_product"]
["recalculate_tariff"]
["reclassify_product", "recalculate_tariff"]
```

这样前端点击某个 action type 的 Go 时，可以筛选出对应 products，并构造 classification/sandbox 的跳转参数。

关于 `match_dimension` / `match_reason`：

- 字段命名和是否需要暂不冻结。
- 它不是主流程阻塞项。
- 如果后续需要说明匹配原因，可再补充。

### 13.3 radar_user_action_items

一行表示一个 suggested action。

核心字段：

```text
id
user_action_id
action_type                    -- reclassify_product / recalculate_tariff
note
effective_date
status                         -- action_needed / completed
completed_at
completed_by
created_at
updated_at
```

`effective_date` 可为空。API 层可 fallback 到 `policy_update.effective_date`。

后端不做 `effective_date + 5 days` gating。前端是否允许点击，由前端自己判断。

### 13.4 radar_email_deliveries

一行表示一封待发或已发邮件。

核心字段：

```text
id
user_action_id
recipient_id
recipient_email
status                         -- pending / sent / failed
attempt_count
last_attempt_at
sent_at
created_at
updated_at
```

约束：

```text
unique(user_action_id, recipient_id)
```

`recipient_email` 是发送时快照。

不存 `last_error`，错误靠日志排查。

## 14. Action API

Action API 由 `classification-backend` 提供。

建议用户接口：

```http
GET /api/compliance-radar/actions
GET /api/compliance-radar/actions/{user_action_id}
PATCH /api/compliance-radar/action-items/{item_id}/completion
```

列表参数：

```ts
{
  status?: "action_needed" | "completed"
  policy_update_id?: string
  page?: number
  page_size?: number
}
```

返回 Action Card 数据应包含：

- policy update 摘要。
- card status。
- affected products。
- suggested actions。
- completion 状态。

完成/取消完成请求：

```ts
{
  completed: boolean
}
```

响应返回更新后的 Action Card，方便前端刷新当前卡片。

关键约束：

- 用户只能操作当前登录用户自己的 action item。
- card status 由后端同步维护。
- completion/uncompletion 不触发邮件。
- 后端不返回 deep link。
- 前端根据 `policy_update_id + action_type + affected_products` 构造跳转参数。

## 15. Action 执行入口

Radar 侧只提供足够上下文，不负责执行 reclassify/recalculate。

前端点击 `reclassify_product`：

- 跳转 classification 产品。
- 带上相关 product_uid 列表。
- 相关 products 来自 `affected_products[].applicable_action_types` 包含 `reclassify_product` 的产品。

前端点击 `recalculate_tariff`：

- 跳转 sandbox / calculator 产品。
- 带上相关 product_uid 列表。
- 相关 products 来自 `affected_products[].applicable_action_types` 包含 `recalculate_tariff` 的产品。

Radar 不判断用户是否已经在 classification/sandbox 中完成真实 reclassify/recalculate。

## 16. Email 设计

### 16.1 生成与发送

当 actions 落库时：

1. 读取当前 active recipients。
2. 为每个 recipient 创建 `radar_email_deliveries`。
3. actions/products/items/deliveries 与 `action_calculate_status = succeeded` 在同一事务提交。
4. 事务提交后实时触发一次邮件发送。
5. 周期任务后续回补 pending/failed 邮件。

如果用户当时没有 active recipient：

- 创建 Action Card。
- 不创建 delivery。
- 用户之后新增邮箱，不补发历史 Action 邮件。

如果 recipient 后续 delete/unsubscribe：

- 未来不再创建 delivery。
- 已有但未发送 delivery 在发送前检查 recipient 状态；如果不 active，则删除或丢弃该 delivery。
- 已 sent 的历史 delivery 保留记录。

### 16.2 邮件竞态控制

邮件是外部副作用，数据库回滚不了，因此与 Action 计算分开处理。

1.0 不新增 `sending` 状态，不加 lease 字段，使用 Postgres 行锁控制同一封 delivery 的单飞发送：

```sql
SELECT ...
FROM radar_email_deliveries
WHERE status IN ('pending', 'failed')
  AND attempt_count < 3
ORDER BY created_at
LIMIT 1
FOR UPDATE SKIP LOCKED;
```

在同一个事务中：

1. 锁住一条 delivery。
2. 重新检查 recipient 是否仍然 `active`。
3. 如果 recipient 已经 `unsubscribed/deleted`，删除这条未发送 delivery，提交事务。
4. 如果仍 active，调用邮件服务发送。
5. 成功则更新为 `sent`。
6. 失败则 `attempt_count += 1`，更新为 `failed`。
7. 提交事务。

取舍：

- 不需要 `sending/claimed_at/claim_token`。
- 周期任务和 approve 后实时触发即便撞上，也只有一个拿到同一条 delivery。
- 发送邮件时事务会保持打开，因此必须设置严格发送 timeout，且一次事务只处理一封邮件。
- 无法完全避免 SMTP 已接受邮件、但进程在写回 `sent` 前崩溃导致的重复邮件。1.0 接受 best-effort 语义。

### 16.3 邮件内容

邮件具体文案、版式、字段应在实现阶段回看 PRD 并尊重 PRD。

当前只冻结语义边界：

- 只发 Impact Action 通知。
- 一封邮件对应一个 `user_action_id + recipient`。
- 邮件应包含 policy headline、source/reference、summary、affected products、suggested actions、View actions 链接、unsubscribe 链接。
- 不附带 PDF。
- 不放完整 original text。
- 不发送没有 Action 的 Recent Policy Update 邮件。
- unsubscribe 走前端页面。
- 前端地址使用环境变量。

## 17. Notification Recipients API

由 `classification-backend` 提供。

建议接口：

```http
GET /api/compliance-radar/notification-recipients
POST /api/compliance-radar/notification-recipients
DELETE /api/compliance-radar/notification-recipients/{recipient_id}
GET /api/compliance-radar/unsubscribe/{token}
```

用户侧 Recipient：

```ts
type Recipient = {
  id: string
  email: string
  status: "active" | "unsubscribed" | "deleted"
  created_at: string
}
```

新增请求：

```ts
{
  email: string
}
```

新增规则：

- 只做 email 格式校验。
- 同一用户最多 5 个 active recipient。
- 已存在 `active`：409 duplicate。
- 已存在 `unsubscribed`：409 unsubscribed，不允许普通添加恢复。
- 已存在 `deleted`：可以重新激活为 `active`。倾向复用原 row 并刷新 token。

删除规则：

- `active` 或 `unsubscribed` 都可删除成 `deleted`。
- `deleted` 再删除保持幂等，返回成功。
- 删除为软删除。

unsubscribe：

- 不需要登录。
- token 命中 active 或 unsubscribed：设为 `unsubscribed`，返回成功。
- token 命中 deleted：返回已处理。
- token 不存在：404。

## 18. Recent Policy Updates API

由 `classification-backend` 提供。

建议接口：

```http
GET /api/compliance-radar/policy-updates
GET /api/compliance-radar/policy-updates/{policy_update_id}
```

列表参数：

```ts
{
  source_key?: string
  page?: number
  page_size?: number
}
```

列表返回核心字段：

```ts
{
  items: Array<{
    id: string
    source_key: string
    source_label: string
    reference_number: string | null
    headline: string
    summary: string
    source_url: string
    published_at: string | null
    effective_date: string | null
    created_at: string
  }>
  total: number
  page: number
  page_size: number
}
```

详情返回核心字段：

```ts
{
  id: string
  source_key: string
  source_label: string
  reference_number: string | null
  headline: string
  summary: string
  briefing_markdown: string
  original_text: string
  source_url: string
  pdf_urls: string[]
  published_at: string | null
  effective_date: string | null
  created_at: string
}
```

约束：

- 不暴露 `policy_extract_status / policy_review_status / action_calculate_status` 给普通用户。
- 不暴露黑盒 policy impact。
- 列表不返回 `briefing_markdown/original_text/pdf_urls`。
- source filter 只按 `source_key`。
- 默认排序：`published_at desc nulls last, created_at desc`。

具体字段可在编码时与前端和 `classification-backend` 现有 response style 对齐。

## 19. Review API

Review API 具体字段可在实现阶段敲定，但资源边界和状态语义已定。

详情返回应包含：

- policy update 基础信息。
- `policy_extract_status`
- `policy_review_status`
- `action_calculate_status`
- `policy_impact` object

不返回：

- attempt_count
- LLM request/response
- 后端错误详情

approve：

- token 鉴权。
- 要求 `policy_extract_status = succeeded` 且 `policy_review_status = pending`。
- approve 前调用黑盒 `validate_policy_impact()`。
- 成功后更新 `policy_review_status = approved`。
- API 尽快返回。
- 返回后触发 best-effort `run_after_approve(policy_update_id)`。

reject：

- token 鉴权。
- 要求 `policy_extract_status = succeeded` 且 `policy_review_status = pending`。
- 更新 `policy_review_status = rejected`。
- 不触发 action calculate。

## 20. 数据表清单

不包括黑盒自己的 policy impact 表，Radar 自有表包括：

```text
radar_raw_source_items
radar_policy_updates
radar_user_actions
radar_user_action_products
radar_user_action_items
radar_notification_recipients
radar_email_deliveries
```

### 20.1 radar_raw_source_items

建议字段：

```text
id
source_key
source_label
source_item_key
source_url
title
published_at
raw_content
raw_metadata
pdf_urls
policy_update_status
policy_update_attempt_count
created_at
updated_at
```

约束：

```text
unique(source_key, source_item_key)
```

### 20.2 radar_policy_updates

建议字段：

```text
id
raw_source_item_id
source_key
source_label
reference_number
headline
summary
briefing_markdown
original_text
source_url
pdf_urls
published_at
effective_date
policy_extract_status
policy_extract_attempt_count
policy_review_status
action_calculate_status
action_calculate_attempt_count
created_at
updated_at
```

约束：

```text
unique(raw_source_item_id)
```

### 20.3 radar_user_actions

见第 13.1 节。

### 20.4 radar_user_action_products

见第 13.2 节。

### 20.5 radar_user_action_items

见第 13.3 节。

### 20.6 radar_notification_recipients

建议字段：

```text
id
user_id
email
status                         -- active / unsubscribed / deleted
unsubscribe_token
created_at
updated_at
```

应支持同一用户邮箱状态判断和 active 数量限制。

### 20.7 radar_email_deliveries

见第 13.4 节。

## 21. 用户可见性边界

普通用户侧：

- 看不到 raw item。
- 看不到 policy extract/review/action calculate 状态。
- 看不到 attempt_count。
- 看不到系统失败状态。
- raw item 失败时用户无感，因为不会生成 policy update。
- policy update 已生成但 extract/action 失败，不影响 Recent Updates 展示。
- action calculate 失败时，用户只是不出现该 Action Card。
- email 失败不在普通用户页面展示。

Review 页面：

- 可看到 `policy_extract_status / policy_review_status / action_calculate_status`。
- 不显示 attempt_count。
- 不显示 LLM request/response。
- 不显示后端错误详情。

## 22. 日志与可观测性

该问题暂时搁置，不在需求澄清阶段冻结。

已经确认的边界：

- 数据库不存错误详情。
- 数据库不存 LLM request/response。
- raw item 处理失败错误信息不入库。
- extract/action 错误信息不入库。
- 具体日志字段、脱敏、run_id、dashboard、告警等后续再设计。

实现阶段至少需要有足够排障的应用日志。

## 23. Open Questions / 后续专题

主流程需求层面已经基本澄清完毕。以下内容后续仍需单独讨论或在实现阶段敲定：

1. **具体数据源清单**
   - 哪些 source。
   - 各自抓取方式。
   - limit/lookback 策略。
   - `source_item_key` 生成规则。
   - 附件解析规则。

2. **raw item -> policy_update prompt 细则**
   - 主契约已定。
   - “什么样的文章应该进入 Recent Updates”要结合真实 source 和 BLB 场景打磨。

3. **邮件内容**
   - 发送语义已定。
   - subject/body/版式/字段要回看 PRD 并尊重 PRD。

4. **黑盒 policy impact schema / review UI**
   - 边界已定。
   - impact object 结构、review 页面编辑方式由黑盒 owner 主导。

5. **API 字段级精确契约**
   - endpoint、权限、动作语义已定。
   - 最终字段在编码时结合前端和 `classification-backend` 现有风格敲定。

6. **日志与可观测性**
   - 不入库的边界已定。
   - 应用日志标准暂未冻结。

7. **任务频率、timeout、backoff 参数**
   - 原则已定。
   - 具体数值属于实现/部署设计。

8. **Action 执行入口与 classification/sandbox 对接**
   - Radar 提供跳转上下文。
   - classification/sandbox 如何接收参数、如何展示目标 products，需要后续与前端和对应后端接口对齐。

## 24. 当前结论摘要

Compliance Radar BLB 1.0 的核心设计可以概括为：

> 独立单实例 `radar-backend` worker 负责抓取、解析、状态推进、action 计算和邮件；`classification-backend` 负责所有 HTTP/API/auth 和 migration；两者共享 Postgres。Recent Policy Updates 是全局政策流，User Impact Actions 是用户级 Action Card。policy impact 由黑盒模块抽取、存储和审核，review approved 后才计算用户 actions。所有重逻辑通过状态机、attempt_count、事务、唯一键和周期任务回补保证可恢复性。1.0 不做跨源语义去重、不做 Radar Action 重算、不做 bounce、不做复杂管理后台。
