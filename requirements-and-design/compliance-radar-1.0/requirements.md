# Compliance Radar BLB 1.0 需求与设计结论

最后更新：2026-05-14

本文档沉淀 Compliance Radar BLB 1.0 已确认的产品边界、系统边界、主流程、状态机、数据契约和实现约束。它不是完整 PRD，也不是最终 API 细则；字段级数据库设计见 `database-design.md`，主流程时序见 `sequence-diagrams.md`。

## 1. 项目边界

Compliance Radar 1.0 不依赖、不改造、不触碰 `gov-backend`。`gov-backend` 只作为历史参考，不作为实现基础。

系统由两个后端项目协作：

- `radar-backend`：独立单实例 worker，负责抓取、解析、入库、状态推进、Policy Impact 抽取调度、User Actions 计算调度、邮件发送、运营 webhook 发送和 Radar 数据库 migration。
- `classification-backend`：承载 HTTP/API/auth，包括用户侧 API、review API 和 unsubscribe API。

两者共享同一个 Postgres 数据库。1.0 不引入共享 package，集成契约是数据库 schema、状态机和函数边界。

`gov-frontend`、`tarriff-simulator` 和 mock 页面作为前端体验参考，不决定后端边界。

## 2. 1.0 范围

必须实现：

- Recent Policy Updates
- User Impact Actions
- Impact Action email notifications
- 用户点击 action 后进入 classification / sandbox / calculator 的执行入口上下文

明确不做：

- 趋势预警
- Quick Check
- Topic subscription
- Google Calendar
- 自动重新分类 / 自动重新计算关税
- Radar Action 重新计算入口
- 跨 source 的 policy/action 语义去重
- bounce 自动停发
- 管理后台 reset attempt
- 复杂 observability/dashboard

需要区分：

- **Radar Action 重新计算入口**：重新调用 `calculate_user_actions(policy_update_id, user_id)` 并重建 Radar 中的 action。1.0 不做。
- **Action 执行入口**：用户点击 Go，带着 Radar 提供的上下文进入 classification 或 sandbox 执行 reclassify / recalculate。1.0 需要支持，但执行主体不在 Radar。

## 3. 产品模型

### 3.1 Recent Policy Updates

Recent Policy Updates 是全局政策更新流：

- 所有登录用户看到同一份列表。
- 不按用户产品过滤。
- 不依赖用户是否有 actions。
- 与 User Impact Actions 是两条独立业务线。
- 可以包含未来才生效的政策。
- 详情页展示独立 AI briefing，不展示黑盒 Policy Impact。

文章过滤和 briefing 生成在同一次 LLM 调用中完成：

- 相关文章创建 `radar_policy_updates`。
- 不相关文章标记为 `discarded`。

Recent Updates 不向普通用户暴露：

- 黑盒 Policy Impact
- `policy_extract_status`
- `policy_review_status`
- `action_calculate_status`
- attempt count

### 3.2 Policy Impact

Policy Impact 是黑盒模块的业务域。这里的“黑盒”是工作边界，不是第三方服务；它由同事实现，并以本地函数方式被调用。

黑盒负责：

- 基于 policy update 正文、附件和相关上下文抽取结构化政策影响。
- 持久化自己的 policy impact 表。
- 提供 review 读取、保存、校验 helper。
- 基于已审核通过的 policy impact 和用户数据计算候选 actions。

Radar 不设计黑盒内部表结构，也不把黑盒 impact object 拆字段保存到 Radar 表。

### 3.3 User Impact Actions

User Impact Actions 只认 Recent Policy Updates 为唯一数据来源。

Action 是用户级别的：

- 以 `user_id` 为核心。
- 不引入 company/account/tenant。
- 不按 topic subscription 过滤。
- 不基于用户产品提前过滤 policy update。

一个用户对一个 policy update 最多一条 action：

```text
unique(user_id, policy_update_id)
```

1.0 只支持两个 action type：

- `reclassify_product`
- `recalculate_tariff`

前端 action card 由两部分组成：

- `affected_products`：受影响产品快照。
- `action_items`：建议执行的 action items。

`affected_products[]` 必须能表达每个产品适用哪些 action type：

```json
{
  "product_uid": "string",
  "product_name": "string",
  "hts_code": "string | null",
  "suggested_actions": ["reclassify_product", "recalculate_tariff"]
}
```

`action_items[]` 最多包含两个 item，按 `action_type` 唯一：

```json
{
  "action_type": "reclassify_product",
  "effective_date": "YYYY-MM-DD | null",
  "status": "action_needed"
}
```

1.0 不在 action item 上保存 note，也不保存 item 级 `completed_at/completed_by`。

### 3.4 Notification Recipients

1.0 只发送 Impact Action 通知，不发送 Recent Policy Update digest。

用户可以配置多个通知邮箱：

- 新增邮箱只做格式校验，不做邮箱验证。
- 每个用户最多 5 个 active recipients。
- 每封邮件必须带 unsubscribe 链接。
- unsubscribe 走前端页面，前端 URL 通过环境变量配置。

recipient 状态：

```text
active / unsubscribed / deleted
```

1.0 不做 `bounced` 状态，也不做 bounce 自动停发。

## 4. 系统职责

`radar-backend`：

- 读取 source 配置。
- 抓取外部 source items。
- 写入 raw source items；已存在则跳过。
- 处理 raw items，按需下载 PDF，调用 LLM 过滤并生成 briefing。
- 创建 Recent Policy Updates 或 discard raw items。
- 调用黑盒 `extract_policy_impact()`。
- 写入运营 webhook outbox。
- 在 review approved 后调用黑盒 `calculate_user_actions()`。
- 写入 `radar_user_actions` 和 `radar_email_deliveries`。
- 发送 action notification emails。
- 发送 Lark Team webhook。
- 管理 Radar migration。

`classification-backend`：

- 提供 Recent Policy Updates API。
- 提供 Actions API。
- 提供 Notification Recipients API。
- 提供 Unsubscribe API。
- 提供 Policy Impact Review API。
- 使用现有登录/auth 体系保护普通用户 API。

Review API 使用 `classification-backend` 现有 admin/internal auth，不引入单独审核 token。严格审核审计由独立 audit log 承载，不复用 `radar_policy_updates`。

## 5. 周期任务编排

`radar-backend` 按单实例 worker 部署。主流程由单一周期任务驱动：

```text
run_periodic_cycle():
  1. collect_source_items()
  2. create_policy_updates()
  3. create_policy_impacts()
  4. create_user_actions()
  5. send_action_notifications()
  6. dispatch_operational_webhooks()
```

编排约束：

- 外层容器显式编排 stage。
- stage 之间串行执行。
- 一个 stage 完成后才进入下一个 stage。
- stage 函数只处理自身职责，不在函数尾部驱动下一 stage。
- 外层编排容器捕获单个 stage 的整体异常，记录日志后继续执行后续 stage。
- worker loop 外层保留兜底异常捕获，避免非 stage 层错误导致进程退出。
- 定时任务内部不再启动异步线程。
- 唯一允许并发的是 Stage 1 内部的 source adapter 并发抓取。
- 所有 source 抓取完成并完成 raw item 写入/跳过后，才进入 Stage 2。
- 任何外部调用都不在数据库事务内，包括 source fetch、PDF 下载、LLM、黑盒函数、CMS、Email Provider 和 Lark webhook。
- 数据库事务只包本地状态推进和原子写入。

骨架实现约束：

- `WorkerContext` 只承载本轮 `run_id`，不放 settings、db、repository、service 或 logger。
- stage 技术依赖随用随取；数据库通过 `radar_backend.db` 模块级 gateway 获取连接，repository 是模块级函数，service 是普通工具类。
- repository 函数必须显式接收 connection；跨 repository 事务由调用方使用 `acquire_connection_with_transaction()` 包住。
- `StageResult` 保留 `run(context) -> StageResult` 契约；编排容器不依赖其字段。

每个周期都是 state-driven：

- 处理本轮新产生的 eligible records。
- 回补之前未完成或失败、且 attempt count 未达上限的 records。

Approve 只推进 review 状态，不实时触发 action 计算和邮件发送；周期任务按状态自然拾取。

## 6. 数据源与 Raw Items

### 6.1 Source 配置

source 只由配置文件控制，不使用环境变量配置 source 行为。

配置中至少包含：

- `source_key`
- `source_label`
- `adapter`
- `enabled`
- adapter-specific `fetch` 配置

worker 启动时校验：

- `source_key` 唯一。
- 必填字段完整。
- adapter 名称存在。
- enabled source 的 fetch 配置通过 adapter 自己的校验。

`source_key` 和 `source_label` 都会作为快照写入 raw item 和 policy update。

### 6.2 Source Adapter

adapter 负责抓取和标准化，不负责判断是否进入 Recent Updates。

标准化候选项至少包含：

```ts
type RawSourceCandidate = {
  source_item_key: string
  source_url: string
  source_title: string
  published_at: string | null
  source_content: string
  source_metadata: object
  pdf_urls: string[]
  reference_number: string | null
}
```

约束：

- `source_item_key` 必须稳定。
- `source_content` 是 LLM 判断和 briefing 的主要文本来源。
- `source_metadata` 是 source 域只读补充快照；本文不定义内部 key。
- `pdf_urls` 只保存 URL，不保存 PDF 正文。
- adapter 内部网络请求做 RPC 级短重试。

### 6.3 去重

数据库使用以下唯一键去重：

```text
unique(source_key, source_item_key)
```

抓取入库语义是：

```text
exists -> skip
not exists -> insert
```

不是 upsert 覆盖。

`source_item_key` 生成优先级：

- 官方 ID。
- 稳定 canonical URL。
- feed entry id/link。
- 标题 + 发布时间 + URL 的组合 hash。

不建议用正文 hash 作为主去重键。

### 6.4 PDF 附件

附件可能是判断是否进入 Recent Updates、抽取 Policy Impact、计算 Actions 的关键材料。

规则：

- 解析并保存 `pdf_urls`。
- 不保存 PDF 正文到数据库。
- 在重要处理阶段按需下载/解析。
- PDF 下载/解析失败时做 RPC 级短重试；仍失败则本次记录处理失败，由 durable retry 回补。

## 7. Raw Item 到 Policy Update

1.0 按一对一边界设计：

```text
one radar_raw_source_item -> zero or one radar_policy_update
one radar_policy_update -> exactly one radar_raw_source_item
```

不做：

- 多个 raw items 合并成一个 policy update。
- 一个 raw item 拆成多个 policy updates。
- 跨 source 语义去重。

Stage 2 生成的 policy update draft：

```ts
type PolicyUpdateDraft = {
  should_ingest: boolean
  source_title: string
  reference_number: string | null
  headline: string
  summary: string
  briefing: string
  effective_date: string | null
  source_metadata_patch: object
}
```

语义：

- `should_ingest = false`：raw item 标记 `discarded`。
- `should_ingest = true`：创建 `radar_policy_updates`。
- `headline/summary/briefing` 必须非空。
- `source_key/source_label/source_url/source_content/pdf_urls/published_at/raw_source_item_id` 从 raw item 拷贝，不由 LLM 决定。
- `source_title/reference_number` 以 raw item 字段为基础，Stage 2 可用 LLM 提取结果补充或修正。
- `source_metadata` 以 raw item `source_metadata` 为基础，Stage 2 可按需补充 source attribution 字段，如 `document_type/agency`。
- LLM 不输出 topic、impact level、user action、policy impact。

`effective_date` 优先从 source metadata 的日期候选中解析；不足时由 LLM 从正文提取，仍不能确定则为空。

## 8. Policy Impact 黑盒

黑盒至少提供以下本地函数：

```ts
extract_policy_impact(policy_update_id: string) -> boolean

get_policy_impact(policy_update_id: string) -> object | null

validate_policy_impact(
  policy_update_id: string,
  policy_impact?: object
) -> { success: boolean, message: string | null }

save_policy_impact(
  policy_update_id: string,
  policy_impact: object
) -> void

calculate_user_actions(
  policy_update_id: string,
  user_id: string
) -> UserActionCandidates
```

`extract_policy_impact()`：

- 读取 policy update 和必要附件上下文。
- 计算政策作用范围、受影响 HTS、关税影响等结构化信息。
- 写入黑盒自己的 policy impact 表。
- 返回 boolean 表示成功/失败。
- 不更新 `radar_policy_updates` 状态。
- 必须按 `policy_update_id` 幂等；重复调用必须安全复用或覆盖同一个 policy impact 结果，不得产生多份不确定版本。
- 即使 Radar 还没来得及把 `policy_extract_status` 更新为 `succeeded`，`get_policy_impact(policy_update_id)` 也应能返回已经成功写入的 policy impact。

`calculate_user_actions()` 返回候选 action 业务形状：

```ts
type ActionType = "reclassify_product" | "recalculate_tariff"

type UserActionCandidates = {
  affected_products: Array<{
    product_uid: string
    product_name: string
    hts_code: string | null
    suggested_actions: ActionType[]
  }>
  action_items: Array<{
    action_type: ActionType
    effective_date: string | null
  }>
}
```

Radar 负责把候选结果转换成 `radar_user_actions` JSONB，并补充 item status。黑盒不负责存储 Radar user actions，不负责 completion，不负责邮件。

如果 action item 缺少 `effective_date`，API/应用层可以用 `policy_update.effective_date` 兜底。后端不做“生效日期 + 5 天”点击 gating；前端自行决定 action 是否可点击。

## 9. 人工审核

人工审核审核的是 Policy Impact，不是 Recent Update briefing。

引入人工审核的原因：

- `extract_policy_impact()` 可能包含 LLM 判断，上线初期不能直接信任。
- Classification / Calculator 底层数据源可能尚未同步；若 actions 先于底层数据就绪出现，用户会看到无法执行的 action。

Review API 由 `classification-backend` 承载 HTTP；黑盒负责 policy impact object 的读取、保存和校验。

建议接口：

```http
GET  /api/compliance-radar/review/policy-impacts
GET  /api/compliance-radar/review/policy-impacts/{policy_update_id}
PUT  /api/compliance-radar/review/policy-impacts/{policy_update_id}
POST /api/compliance-radar/review/policy-impacts/{policy_update_id}/approve
```

1.0 不提供驳回动作：

- 如果不准备推进，就保持 `policy_review_status = confirm_needed`。
- 如果数据不正确，reviewer 修改正确后再 approve。

Approve 规则：

```text
policy_extract_status = succeeded
AND policy_review_status = confirm_needed
```

Approve 前调用黑盒 `validate_policy_impact(policy_update_id)`。成功后只更新：

```text
policy_review_status = approved
```

Approve 不实时触发 action 计算；周期任务 Stage 4 按状态处理。

## 10. 状态机

### 10.1 Raw Item

字段：

```text
radar_raw_source_items.policy_update_status
```

枚举：

```text
pending / ingested / discarded / failed
```

### 10.2 Policy Update

字段：

```text
radar_policy_updates.policy_extract_status
radar_policy_updates.policy_review_status
radar_policy_updates.action_calculate_status
```

枚举：

```text
policy_extract_status = pending / succeeded / failed
policy_review_status = confirm_needed / approved
action_calculate_status = pending / succeeded / failed
```

创建 policy update 时，`policy_extract_status` 和 `action_calculate_status` 默认是 `pending`，`policy_review_status` 默认是 `confirm_needed`。

### 10.3 User Action

顶层 action 状态：

```text
radar_user_actions.status = action_needed / completed
```

JSON action item 状态：

```text
radar_user_actions.action_items[].status = action_needed / completed
```

Action item completion 可逆：

- 用户可以 complete。
- 用户可以 uncomplete。

顶层 `status/completed_at/completed_by` 由 `action_items` 同步维护：

- 所有 items 都 completed，顶层为 `completed`。
- 任一 item 是 action_needed，顶层为 `action_needed`，清空 `completed_at/completed_by`。

### 10.4 Email Delivery

```text
pending / sent / failed / skipped
```

`skipped` 表示发送前发现 recipient 已不是 active，因此系统主动跳过发送。`skipped` 不属于发送失败，不递增 `attempt_count`。无 `bounced`。

### 10.5 Webhook Event

event type：

```text
policy_impact_ready_for_review / attempt_exhausted
```

status：

```text
pending / sent / failed
```

Webhook event 是内部运营 outbox，不是用户通知。

## 11. Durable Retry 与 Operational Webhooks

需要区分两层重试：

- RPC 级短重试：一次业务操作内部对网络、LLM、PDF、邮件等临时失败重试 3 次，并带退避。
- Durable retry：数据库记录级跨周期重试，用 `attempt_count` 控制最多 3 次。

Durable attempt 字段：

```text
radar_raw_source_items.policy_update_attempt_count
radar_policy_updates.policy_extract_attempt_count
radar_policy_updates.action_calculate_attempt_count
radar_email_deliveries.attempt_count
radar_webhook_events.attempt_count
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

webhook:
  status in ('pending', 'failed')
  and attempt_count < 3
```

`attempt_count` 表示已经完成的 durable attempts。它不在重型操作前递增，而是在外部调用、LLM、黑盒计算、邮件发送或 webhook 发送返回后，和状态写回放在同一个短事务中递增。

`radar_email_deliveries` 和 `radar_webhook_events` 同时记录外部发送时间：

- `last_attempt_at`：最近一次外部发送调用返回后的写回时间，成功失败都设置。
- `sent_at`：外部系统 accepted 后的写回时间，仅 `status = sent` 时设置。

一旦某条记录进入重型操作，stage 实现应使用 `finally` 或等价的统一收口逻辑写回本次 durable attempt：递增对应 `attempt_count`、更新成功/失败状态，并在失败且达到上限时尝试创建 `attempt_exhausted` webhook event。

如果进程在重型操作中途崩溃，本次 attempt 不计数，周期任务会重新尝试。黑盒函数必须通过幂等契约承受重试；email/webhook 这类外部副作用采用 best-effort 语义。

当某个 durable attempt 达到上限后，worker 尝试创建一条 webhook event：

```text
event_type = attempt_exhausted
```

Policy Impact 抽取成功后，worker 尝试创建一条 webhook event：

```text
event_type = policy_impact_ready_for_review
```

Webhook event 写入规则：

- review-ready 事件带前端 review 链接。
- attempt-exhausted 事件带运营排查信息。
- event 默认 `status = pending`。
- 业务 stage 直接通过 repository 写入 `radar_webhook_events`。
- 唯一键保证同一事件按实体幂等。
- 业务 stage 只尝试创建 webhook event；同一事件已存在时不更新任何字段。
- Webhook event 是历史运营事件，不是实时业务状态投影；Stage 6 只按 outbox 状态发送事件。

Stage 6 `dispatch_operational_webhooks()` 按状态发送 Lark Team webhook：

1. 选择一批 `status in ('pending', 'failed') and attempt_count < 3` 的 webhook events。
2. 在事务外通过 `WebhookService` 调用 Lark webhook，内部 RPC retry 最多 3 次。
3. 用普通 `UPDATE` 在短事务中按发送结果维护 `status/attempt_count/last_attempt_at/sent_at`。

attempt count 是纯后端控制信号，不暴露给普通用户 API，也不暴露给 review API/UI。

## 12. User Actions 计算

目标用户从 CMS 获取。1.0 不使用环境变量控制 target users。

对一个 approved policy update：

1. 读取 target users。
2. 对每个 user 调用 `calculate_user_actions(policy_update_id, user_id)`。
3. 空 candidates 不写记录。
4. 非空 candidates 在内存中累积。
5. 如果任一 target user 计算失败，则该 policy update 本次 action 计算失败，不写 actions，不写 deliveries。
6. 如果所有 target users 都计算成功，则在一个事务内提交所有非空 actions、email deliveries，并更新 `action_calculate_status = succeeded`。

Action 计算以 policy update 为提交边界：任一目标用户失败则本次整体失败；全部目标用户计算完成后统一提交。

事务内写入：

```text
insert radar_user_actions with affected_products/action_items JSON
insert radar_email_deliveries with payload snapshots for active recipients
update radar_policy_updates.action_calculate_status = succeeded
```

`radar_user_actions` 上的唯一键是最终幂等防线：

```text
unique(user_id, policy_update_id)
```

如果所有用户都计算成功但没有任何用户有 actions，也应把 `action_calculate_status` 更新为 `succeeded`。

## 13. Action 展示与执行入口

Action API 由 `classification-backend` 提供。

建议接口：

```http
GET   /api/compliance-radar/actions
GET   /api/compliance-radar/actions/{action_id}
PATCH /api/compliance-radar/actions/{action_id}/items/{action_type}/completion
```

列表支持：

```text
status
policy_update_id
page
page_size
```

完成/取消完成：

- 只能操作登录用户自己的 action。
- 按 `action_type` 更新 `action_items` JSON 中对应 item 的状态。
- 同一事务内重算顶层 action `status/completed_at/completed_by`。
- completion/uncompletion 不触发邮件。

Go 跳转：

- `reclassify_product` 跳转 classification 产品。
- `recalculate_tariff` 跳转 sandbox/calculator 产品。
- 前端使用 `policy_update_id + action_type + affected_products[].product_uid` 构造跳转参数。
- 相关产品由 `affected_products[].suggested_actions` 过滤得到。
- 后端不返回 deep link。
- Radar 不判断用户是否已经在目标产品中完成真实 reclassify/recalculate。

## 14. Email Notifications

当 user actions 落库时：

1. 读取 active recipients。
2. 为每个 `user_action_id + recipient_id` 创建一条 `radar_email_deliveries`，并写入邮件发送 payload 快照。
3. actions 和 deliveries 在同一事务提交。
4. 邮件发送在事务提交后由 Stage 5 处理。

如果用户当时没有 active recipient：

- 创建 Action。
- 不创建 delivery。
- 用户之后新增邮箱，不补发历史 action 邮件。

如果 recipient 后续 `deleted/unsubscribed`：

- 不再为该 recipient 创建 delivery。
- 已存在但未发送的 delivery 在发送前重新检查 recipient 状态；如果不 active，则标记为 `skipped`。
- 已 sent delivery 保留为历史记录。

邮件发送按 delivery 粒度处理。每轮可以读取一批 eligible deliveries，但外部邮件调用不在数据库事务内：

```sql
SELECT ...
FROM radar_email_deliveries
WHERE status IN ('pending', 'failed')
  AND attempt_count < 3
ORDER BY created_at
LIMIT :limit
```

发送流程：

1. 选择一批 eligible deliveries。
2. 如果 recipient 已不是 active，短事务设置 `status = skipped`，不递增 `attempt_count`，不设置 `last_attempt_at/sent_at`。
3. 如果 recipient active，事务外通过 `EmailService` 使用 delivery payload 调用 Email Provider，内部 RPC retry 最多 3 次。
4. 短事务写 `status = sent` 或 `status = failed`，递增 `attempt_count`，并写 `last_attempt_at`；成功时同时写 `sent_at`。
5. 发送前会检查 recipient active；检查后立即 unsubscribe 的极小竞态可能仍会发送一次。
6. 如果失败后 `attempt_count` 已达到 3，尝试创建 `attempt_exhausted` webhook event。

邮件发送不引入 `sending/claimed_at/lease`。邮件是外部副作用，无法严格 exactly-once；如果 provider 已接受但进程在写回 `sent` 前崩溃，可能重复发送，发送语义为 best-effort。

邮件内容语义：

- 只发 Impact Action 通知。
- 一封邮件对应一个 `user_action_id + recipient`。
- delivery payload 固化发送所需内容，包括 policy headline、source/reference、summary、affected products、action items、View actions 链接、unsubscribe 链接。
- 不附带 PDF。
- 不放完整 original text。
- 不发送没有 Action 的 Recent Policy Update 邮件。

具体邮件文案和版式实现时回看 PRD。

## 15. Recipients 与 Unsubscribe API

建议接口：

```http
GET    /api/compliance-radar/notification-recipients
POST   /api/compliance-radar/notification-recipients
DELETE /api/compliance-radar/notification-recipients/{recipient_id}
GET    /api/compliance-radar/unsubscribe/{token}
```

新增规则：

- email 只做格式校验。
- 同一用户最多 5 个 active recipients。
- active recipient 数量限制需要由 API 层在同一事务内保证。
- 已存在 active：409 duplicate。
- 已存在 unsubscribed：409 unsubscribed，不允许普通添加恢复。
- 已存在 deleted：可以重新激活为 active，并刷新 token。

删除规则：

- 软删除，设置 `status = deleted`。
- `deleted` 再删除保持幂等。

unsubscribe：

- 不需要登录。
- token 命中 active 或 unsubscribed：设置或保持 `unsubscribed`，返回成功。
- token 命中 deleted：返回已处理。
- token 不存在：404。

## 16. Recent Policy Updates API

建议接口：

```http
GET /api/compliance-radar/policy-updates
GET /api/compliance-radar/policy-updates/{policy_update_id}
```

列表支持：

```text
source_key
page
page_size
```

排序：

```text
published_at desc nulls last, created_at desc
```

列表返回 policy update 展示所需字段；详情返回 `briefing`、`source_content`、`pdf_urls` 等详情字段。普通用户 API 不返回内部状态、attempt count 或黑盒 policy impact。

具体 response style 在编码时与 `classification-backend` 既有风格对齐。

## 17. 数据表边界

Radar 自有表：

```text
radar_raw_source_items
radar_policy_updates
radar_user_actions
radar_notification_recipients
radar_email_deliveries
radar_webhook_events
```

Action 产品和 action items 不设置独立表，统一保存在 `radar_user_actions` 的 JSONB 字段中。原因：

- affected products 是不可变快照，适合放在 `radar_user_actions.affected_products` JSONB。
- action items 最多两条，适合放在 `radar_user_actions.action_items` JSONB。
- item 级 completed fields 和 note 在 1.0 中不需要。

JSONB 使用边界：

- 只把同一业务域、只读或少量变更、不作为核心查询条件的字段抱团。
- 数据库只约束 JSON root type。
- 内部 shape 由应用层校验。
- 本文不定义 `source_metadata/payload` 的内部 keys，避免误导实现。

## 18. 用户可见性

普通用户看不到：

- raw item
- Policy Impact
- policy extract/review/action calculate 状态
- attempt count
- 后端错误详情
- LLM request/response

Review 页面可以看到 policy update 和 policy impact，并执行保存、approve。Review 页面不显示 attempt count。

系统失败对普通用户的表现：

- raw item 失败：用户无感，因为不会生成 policy update。
- policy extract/action calculate 失败：Recent Updates 仍可展示；Action 不出现或延后出现。
- email 失败：普通用户页面不展示失败状态。

运营侧通过 Lark Team webhook 感知 review-ready 和 attempt-exhausted。

## 19. 事务与并发原则

关键事务边界：

- 创建 policy update：`insert radar_policy_updates` 与 `update raw item status = ingested` 同事务。
- 写 user actions：`insert radar_user_actions`、`insert radar_email_deliveries`、`update action_calculate_status = succeeded` 同事务。
- action completion：验证用户归属、更新 `action_items` JSON、重算顶层 status 同事务。
- email send：事务外通过 `EmailService` 发送邮件，再用短事务写发送结果、递增 delivery attempt，并维护 `last_attempt_at/sent_at`。
- webhook dispatch：事务外发送 Lark，再用短事务写发送结果、递增 webhook event attempt，并维护 `last_attempt_at/sent_at`。

并发策略：

- 主 worker 单实例。
- 周期任务 stage 串行。
- Stage 1 source adapters 可以并发，但必须在 Stage 2 前全部完成。
- Approve 不启动实时 pipeline，避免与周期任务产生复杂竞态。
- 唯一键和事务是最终一致性防线。
- 串行模型不使用数据库抢锁或 lease。

运行日志：

- worker 同时输出控制台日志和 `logs/radar-worker.log`。
- 文件日志按服务器本地时区每日零点滚动，保留 7 天。
- 周期和 stage 日志必须包含 `run_id`，并记录耗时，耗时单位使用 seconds。

## 20. 实现阶段确认

以下内容在实现阶段落细：

1. 具体 source 清单、抓取方式、lookback/limit、`source_item_key` 规则。
2. raw item -> policy update prompt 细则。
3. 黑盒 Policy Impact schema 和 review UI 编辑方式。
4. 邮件 subject/body/版式。
5. API response 字段级精确契约。
6. 业务日志字段、脱敏和更细粒度可观测性。
7. 周期任务频率、timeout、backoff。
8. classification/sandbox 如何接收 action 跳转参数。

相关边界：

- Approve 后触发 simulator 数据库更新不是 Radar 主流程的一部分。

## 21. 结论摘要

Compliance Radar BLB 1.0 采用独立单实例 `radar-backend` worker + `classification-backend` API 的分工。系统以共享 Postgres 中的 `radar_` 表和状态机作为协作契约，通过周期任务串行推进 source 抓取、Recent Policy Updates 生成、Policy Impact 抽取、User Actions 计算、邮件发送和运营 webhook 发送。Policy Impact 由黑盒模块持久化并经人工 approve 后才进入 action 计算；approve 只推进 review 状态。Actions 用一张 `radar_user_actions` 表承载 card、affected products 和 action items；邮件通知与 Lark 告警都通过状态和 outbox 保持可恢复。
