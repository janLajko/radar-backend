# Compliance Radar BLB 1.0 时序图

最后更新：2026-05-08

本文档使用 https://sequencediagram.org/ 支持的文本格式编写。每个代码块是一张独立时序图，可以单独复制到 sequencediagram.org 中渲染。

## 1. 周期任务总览

这张图表达 Radar 主流程的外层编排，以及 policy impact 人工审核这个必经 gate。它只说明每一步的目的；各 stage 的实现细节在后续图中展开。

```text
title 1. Periodic Cycle Overview

participant Scheduler
participant RadarWorker
participant Reviewer

Scheduler->RadarWorker: run_periodic_cycle()
note right of RadarWorker: Single-instance worker\nThe outer cycle orchestrates all stages explicitly:\n- A stage never drives the next stage by itself\n- A stage starts only after the previous stage finishes

RadarWorker->RadarWorker: Stage 1. Collect source items
note right of RadarWorker: Source adapters may run concurrently inside this stage\nAll fetched raw items are inserted or skipped before stage 2 starts

RadarWorker->RadarWorker: Stage 2. Create Recent Policy Updates
note right of RadarWorker: Each raw item is evaluated for the Radar feed\nRelevant items become policy updates; irrelevant items are discarded

RadarWorker->RadarWorker: Stage 3. Prepare policy impacts for review
note right of RadarWorker: Each policy update is turned into structured impact data\nso a reviewer can validate what the policy affects

RadarWorker-->Reviewer: policy impact ready for review
note right of Reviewer: Human approval is required\nbefore user actions can be created
Reviewer-->RadarWorker: policy impact approved or rejected

RadarWorker->RadarWorker: Stage 4. Create user actions
note right of RadarWorker: Only approved policy impacts move forward\nActions are calculated separately for each target user

RadarWorker->RadarWorker: Stage 5. Send action notification emails
note right of RadarWorker: New user actions create email deliveries\nEmail sending happens after actions are committed

note right of RadarWorker: Cycle summary:\n- Every cycle is state-driven\n- Each stage picks up newly eligible records\nand unfinished or failed records within retry limits

RadarWorker-->Scheduler: cycle finished
```

## 2. Stage 1: Collect source items

这张图描述 worker 如何按配置收集外部 source items，并把它们写成 raw source items。

```text
title 2. Stage 1 - Collect Source Items

participant Scheduler
participant RadarWorker
participant SourceConfig
participant SourceAdapter
participant ExternalSource
participant SharedDB

Scheduler->RadarWorker: run_periodic_cycle()
RadarWorker->RadarWorker: Stage 1. Collect source items
note right of RadarWorker: collect_source_items() ends only after all enabled sources finish\nExisting raw items are skipped, not updated

RadarWorker->SourceConfig: load enabled sources
SourceConfig-->RadarWorker: source_key, source_label, adapter, fetch config

loop each enabled source
  RadarWorker->SourceAdapter: fetch(config)
  note right of SourceAdapter: Adapter owns source-specific lookback / limit\nAdapter produces stable source_item_key

  SourceAdapter->ExternalSource: fetch source data
  alt transient request failure
    SourceAdapter->ExternalSource: retry with backoff\nup to 3 RPC attempts
  end
  ExternalSource-->SourceAdapter: source items and attachment URLs
  SourceAdapter-->RadarWorker: RawSourceItemCandidate[]

  loop each candidate
    RadarWorker->SharedDB: insert radar_raw_source_items\nunique(source_key, source_item_key)
    alt row inserted
      SharedDB-->RadarWorker: inserted
    else row already exists
      SharedDB-->RadarWorker: skipped
      note right of SharedDB: Existing raw item is not updated\nNo upsert in 1.0
    end
  end
end

RadarWorker->RadarWorker: Stage 1 completed
```

## 3. Stage 2: Create Recent Policy Updates

这张图描述 raw item 如何变成 Recent Policy Update，或者被标记为 discarded / failed。

```text
title 3. Stage 2 - Create Recent Policy Updates

participant Scheduler
participant RadarWorker
participant SharedDB
participant PDFDownloader
participant LLM

Scheduler->RadarWorker: run_periodic_cycle()
RadarWorker->RadarWorker: Stage 2. Create Recent Policy Updates
note right of RadarWorker: create_policy_updates() decides what belongs in the Radar feed\nEach raw item produces at most one policy update

RadarWorker->SharedDB: select raw items\npolicy_update_status in pending/failed\npolicy_update_attempt_count < 3

loop each selected raw item
  RadarWorker->SharedDB: increment policy_update_attempt_count

  opt pdf_urls not empty
    RadarWorker->PDFDownloader: download and parse PDFs
    alt transient PDF failure
      PDFDownloader->PDFDownloader: retry with backoff\nup to 3 RPC attempts
    end

    alt PDF still failed
      PDFDownloader-->RadarWorker: failure
      RadarWorker->SharedDB: set policy_update_status = failed
      RadarWorker->RadarWorker: stop processing this raw item
    else PDF parsed
      PDFDownloader-->RadarWorker: parsed attachment context
    end
  end

  RadarWorker->LLM: filter + generate briefing\nraw_content + attachment context
  alt transient LLM failure
    LLM->LLM: retry with backoff\nup to 3 RPC attempts
  end
  LLM-->RadarWorker: ingest decision, briefing, and policy update fields

  alt invalid output or processing failed
    RadarWorker->SharedDB: set policy_update_status = failed
    RadarWorker->RadarWorker: stop processing this raw item
  else should_ingest = false
    RadarWorker->SharedDB: set policy_update_status = discarded
    note right of RadarWorker: discard_reason is logged only\nnot persisted in DB
  else should_ingest = true
    RadarWorker->SharedDB: begin transaction
    RadarWorker->SharedDB: insert radar_policy_updates\ncopy source fields and original_text from raw item\npolicy_extract_status = pending\npolicy_review_status = pending\naction_calculate_status = pending
    RadarWorker->SharedDB: set policy_update_status = ingested
    RadarWorker->SharedDB: commit
  end
end

RadarWorker->RadarWorker: Stage 2 completed
```

## 4. Stage 3: Prepare policy impacts for review

这张图描述 policy update 如何触发黑盒抽取 policy impact。

```text
title 4. Stage 3 - Prepare Policy Impacts for Review

participant Scheduler
participant RadarWorker
participant SharedDB
participant PolicyImpactBlackBox

Scheduler->RadarWorker: run_periodic_cycle()
RadarWorker->RadarWorker: Stage 3. Prepare policy impacts for review
note right of RadarWorker: prepare_policy_impacts() evaluates and persists structured impact data\nIt does not approve the result

RadarWorker->SharedDB: select policy updates\npolicy_extract_status in pending/failed\npolicy_extract_attempt_count < 3

loop each selected policy update
  RadarWorker->SharedDB: increment policy_extract_attempt_count
  RadarWorker->PolicyImpactBlackBox: extract_policy_impact(policy_update_id)
  PolicyImpactBlackBox->SharedDB: read radar_policy_updates
  PolicyImpactBlackBox->PolicyImpactBlackBox: evaluate policy scope, affected HTS, and tariff implications
  PolicyImpactBlackBox->SharedDB: persist policy impact in black-box tables
  PolicyImpactBlackBox-->RadarWorker: true / false

  alt extract succeeded
    RadarWorker->SharedDB: set policy_extract_status = succeeded
  else extract failed
    RadarWorker->SharedDB: set policy_extract_status = failed
  end
end

RadarWorker->RadarWorker: Stage 3 completed
```

## 5. Policy Impact Review Flow

这张图描述 reviewer 如何查看、编辑、保存、approve 或 reject policy impact。

```text
title 5. Policy Impact Review Flow

actor Reviewer
participant ReviewUI
participant ClassificationBackend
participant SharedDB
participant PolicyImpactBlackBox

Reviewer->ReviewUI: open review page
ReviewUI->ClassificationBackend: GET /review/policy-impacts/{policy_update_id}\nAuthorization: Bearer REVIEW_TOKEN
ClassificationBackend->SharedDB: load policy update
SharedDB-->ClassificationBackend: policy update
ClassificationBackend->PolicyImpactBlackBox: get_policy_impact(policy_update_id)
PolicyImpactBlackBox-->ClassificationBackend: policy_impact object
ClassificationBackend-->ReviewUI: policy update + policy_impact

opt reviewer edits policy impact
  Reviewer->ReviewUI: edit and save policy impact
  ReviewUI->ClassificationBackend: PUT /review/policy-impacts/{policy_update_id}\nAuthorization: Bearer REVIEW_TOKEN
  ClassificationBackend->SharedDB: verify policy_extract_status = succeeded\nand policy_review_status = pending
  ClassificationBackend->PolicyImpactBlackBox: validate_policy_impact(policy_update_id, policy_impact)
  PolicyImpactBlackBox-->ClassificationBackend: {success, message}

  alt validation success
    ClassificationBackend->PolicyImpactBlackBox: save_policy_impact(policy_update_id, policy_impact)
    ClassificationBackend-->ReviewUI: saved
  else validation failed
    ClassificationBackend-->ReviewUI: 422 message
  end
end

alt reviewer approves
  ReviewUI->ClassificationBackend: POST /review/policy-impacts/{policy_update_id}/approve\nAuthorization: Bearer REVIEW_TOKEN
  ClassificationBackend->SharedDB: verify policy_extract_status = succeeded\nand policy_review_status = pending
  ClassificationBackend->PolicyImpactBlackBox: validate_policy_impact(policy_update_id)
  PolicyImpactBlackBox-->ClassificationBackend: {success, message}

  alt validation success
    ClassificationBackend->SharedDB: set policy_review_status = approved
    ClassificationBackend-->ReviewUI: approved
    ClassificationBackend->ClassificationBackend: try to create user actions and notifications immediately
    note right of ClassificationBackend: run_after_approve() calls create_user_actions()\nthen send_action_notifications()
  else validation failed
    ClassificationBackend-->ReviewUI: 422 message
  end
else reviewer rejects
  ReviewUI->ClassificationBackend: POST /review/policy-impacts/{policy_update_id}/reject\nAuthorization: Bearer REVIEW_TOKEN
  ClassificationBackend->SharedDB: verify policy_extract_status = succeeded\nand policy_review_status = pending
  ClassificationBackend->SharedDB: set policy_review_status = rejected
  ClassificationBackend-->ReviewUI: rejected
end
```

## 6. Stage 4: Create user actions

这张图描述 approved policy impact 如何为目标用户生成 user actions。按 target user 显式循环；只有所有目标用户都计算成功后，才会统一提交 actions 和 email deliveries。

```text
title 6. Stage 4 - Create User Actions

participant Trigger
participant RadarWorker
participant SharedDB
participant UserSelector
participant PolicyImpactBlackBox

Trigger->RadarWorker: create_user_actions(optional policy_update_id)
note right of Trigger: Trigger can be periodic cycle\nor best-effort run_after_approve(policy_update_id)
note right of RadarWorker: create_user_actions() runs only after approval\nAll target users must be calculated before committing

RadarWorker->SharedDB: select policy updates with approved policy impacts\naction_calculate_status in pending/failed\naction_calculate_attempt_count < 3

loop each selected policy update
  RadarWorker->SharedDB: increment action_calculate_attempt_count
  RadarWorker->UserSelector: load target users
  UserSelector-->RadarWorker: user_ids

  loop each target user
    RadarWorker->PolicyImpactBlackBox: calculate_user_actions(policy_update_id, user_id)
    PolicyImpactBlackBox->SharedDB: read approved policy impact\nand user product/tariff data
    PolicyImpactBlackBox->PolicyImpactBlackBox: match affected products and derive suggested actions

    alt calculation failed
      PolicyImpactBlackBox-->RadarWorker: failure
      RadarWorker->RadarWorker: stop looping target users
    else candidates empty
      PolicyImpactBlackBox-->RadarWorker: no actions for this user
      RadarWorker->RadarWorker: keep no record for this user
    else candidates non-empty
      PolicyImpactBlackBox-->RadarWorker: affected products and suggested actions
      RadarWorker->RadarWorker: accumulate candidates in memory
    end
  end

  alt any target user calculation failed
    RadarWorker->SharedDB: update action_calculate_status = failed\nonly if it is not already succeeded
    RadarWorker->RadarWorker: stop processing this policy update
  else all target users calculated successfully
    RadarWorker->SharedDB: begin transaction
    RadarWorker->SharedDB: SELECT radar_policy_updates row FOR UPDATE\nand re-check action_calculate_status

    alt another pipeline already set succeeded
      RadarWorker->SharedDB: close transaction and release lock
      RadarWorker->RadarWorker: skip writes and notifications
    else still not succeeded
      RadarWorker->SharedDB: insert radar_user_actions
      RadarWorker->SharedDB: insert radar_user_action_products
      RadarWorker->SharedDB: insert radar_user_action_items
      RadarWorker->SharedDB: insert radar_email_deliveries for active recipients
      RadarWorker->SharedDB: set action_calculate_status = succeeded
      RadarWorker->SharedDB: commit
    end
  end
end

RadarWorker->RadarWorker: Stage 4 completed
```

## 7. Stage 5: Send action notification emails

这张图描述 action notification email 的发送。邮件是外部副作用，因此同一封 delivery 必须单独控制并发。

```text
title 7. Stage 5 - Send Action Notification Emails

participant Trigger
participant RadarWorker
participant SharedDB
participant EmailProvider

Trigger->RadarWorker: send_action_notifications(optional policy_update_id)
note right of Trigger: Periodic cycle sends globally\nrun_after_approve sends only for one policy_update_id
note right of RadarWorker: send_action_notifications() sends one delivery at a time\nEach delivery is checked against the latest recipient status

loop select and send one delivery at a time
  RadarWorker->SharedDB: begin transaction
  RadarWorker->SharedDB: select one email delivery\nstatus in pending/failed\nattempt_count < 3\noptional policy_update_id filter\nFOR UPDATE SKIP LOCKED

  alt no delivery selected
    RadarWorker->SharedDB: commit
    RadarWorker->RadarWorker: stop email stage
  else delivery selected
    RadarWorker->SharedDB: load recipient

    alt recipient is not active
      RadarWorker->SharedDB: delete unsent delivery
      RadarWorker->SharedDB: commit
    else recipient is active
      RadarWorker->SharedDB: increment attempt_count\nset last_attempt_at
      RadarWorker->EmailProvider: send email with strict timeout

      alt provider accepted
        EmailProvider-->RadarWorker: accepted
        RadarWorker->SharedDB: set status = sent\nset sent_at
        RadarWorker->SharedDB: commit
      else provider failed or timed out
        EmailProvider-->RadarWorker: failure
        RadarWorker->SharedDB: set status = failed
        RadarWorker->SharedDB: commit
      end
    end
  end
end

note right of RadarWorker: If provider accepted but process crashes before marking sent,\na retry may send a duplicate. This is accepted in 1.0.
```

## 8. User action usage and execution entry

这张图描述用户查看 actions、完成或取消完成 action item，以及点击 Go 进入 classification/sandbox 执行动作。

```text
title 8. User Action Usage and Execution Entry

actor User
participant Frontend
participant ClassificationBackend
participant SharedDB
participant ClassificationProduct
participant SandboxCalculator

User->Frontend: open Compliance Radar Actions
Frontend->ClassificationBackend: GET /api/compliance-radar/actions\nstatus, policy_update_id, page, page_size
ClassificationBackend->SharedDB: query current user's actions\nwith affected products and action items
SharedDB-->ClassificationBackend: user actions
ClassificationBackend-->Frontend: user actions
Frontend-->User: render actions

User->Frontend: complete or uncomplete an action item
Frontend->ClassificationBackend: PATCH /api/compliance-radar/action-items/{item_id}/completion\n{completed: true/false}
ClassificationBackend->SharedDB: begin transaction
ClassificationBackend->SharedDB: verify item belongs to current user
ClassificationBackend->SharedDB: update item status\ncompleted_at / completed_by
ClassificationBackend->SharedDB: recompute parent card status

alt all items completed
  ClassificationBackend->SharedDB: set card status = completed\nset completed_at / completed_by
else any item still action_needed
  ClassificationBackend->SharedDB: set card status = action_needed\nclear completed_at / completed_by
end

ClassificationBackend->SharedDB: commit
ClassificationBackend-->Frontend: updated action card
note right of ClassificationBackend: Completion changes do not send emails

User->Frontend: click Go on reclassify_product
Frontend->Frontend: filter affected products\nwhere applicable_action_types contains reclassify_product
Frontend->ClassificationProduct: navigate with action context\npolicy_update_id, action_item_id, product_uids
note right of ClassificationProduct: Actual reclassification is owned by classification product\nRadar does not execute it

User->Frontend: click Go on recalculate_tariff
Frontend->Frontend: filter affected products\nwhere applicable_action_types contains recalculate_tariff
Frontend->SandboxCalculator: navigate with action context\npolicy_update_id, action_item_id, product_uids
note right of SandboxCalculator: Actual recalculation is owned by sandbox/calculator\nRadar does not execute it
```

## 9. Notification Recipients and Unsubscribe

这张图描述用户管理通知邮箱、系统为新 actions 创建 email deliveries，以及收件人 unsubscribe。

```text
title 9. Notification Recipients and Unsubscribe

actor User
actor Recipient
participant Frontend
participant ClassificationBackend
participant SharedDB
participant RadarWorker
participant EmailProvider

User->Frontend: add notification email
Frontend->ClassificationBackend: POST /api/compliance-radar/notification-recipients\n{email}
ClassificationBackend->ClassificationBackend: validate email format
ClassificationBackend->SharedDB: check existing recipient\nand active recipient count

alt active count >= 5
  ClassificationBackend-->Frontend: 409 limit exceeded
else email already active
  ClassificationBackend-->Frontend: 409 duplicate
else email already unsubscribed
  ClassificationBackend-->Frontend: 409 unsubscribed
else email exists as deleted
  ClassificationBackend->SharedDB: reactivate as active\nrefresh unsubscribe_token
  ClassificationBackend-->Frontend: recipient
else new email
  ClassificationBackend->SharedDB: insert recipient as active\ncreate unsubscribe_token
  ClassificationBackend-->Frontend: recipient
end

User->Frontend: delete notification email
Frontend->ClassificationBackend: DELETE /api/compliance-radar/notification-recipients/{recipient_id}
ClassificationBackend->SharedDB: verify recipient belongs to current user
ClassificationBackend->SharedDB: set status = deleted
ClassificationBackend-->Frontend: success

RadarWorker->SharedDB: while creating user actions\nload active recipients
RadarWorker->SharedDB: insert email deliveries in the same transaction\none per user_action_id + recipient_id
RadarWorker->RadarWorker: after commit, call send_action_notifications()
RadarWorker->EmailProvider: send action notification with unsubscribe link
EmailProvider-->Recipient: impact action email

Recipient->Frontend: open unsubscribe link
note right of Frontend: /compliance-radar/unsubscribe?token=...
Frontend->ClassificationBackend: GET /api/compliance-radar/unsubscribe/{token}
ClassificationBackend->SharedDB: find recipient by token

alt token not found
  ClassificationBackend-->Frontend: 404
else token found
  ClassificationBackend->SharedDB: set status = unsubscribed
  ClassificationBackend-->Frontend: success or already handled
end

note right of RadarWorker: Existing sent deliveries remain historical records\nFuture deliveries only use active recipients\nUnsubscribed/deleted pending deliveries are not sent
```

## 10. Reclassify
TODO

## 11. Recalculate
TODO
