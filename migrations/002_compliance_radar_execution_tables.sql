BEGIN;

CREATE TABLE IF NOT EXISTS radar_user_action_targets (
  id bigserial PRIMARY KEY,
  user_action_id bigint NOT NULL,
  policy_update_id bigint NOT NULL,
  user_id bigint NOT NULL,
  product_uid text NOT NULL,
  action_type text NOT NULL,
  status text NOT NULL DEFAULT 'action_needed',
  started_at timestamptz,
  completed_at timestamptz,
  completed_by bigint,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),

  CONSTRAINT uq_radar_user_action_targets_action_product
    UNIQUE (user_action_id, product_uid, action_type),
  CONSTRAINT chk_radar_user_action_targets_action_type
    CHECK (action_type IN ('reclassify_product', 'recalculate_tariff')),
  CONSTRAINT chk_radar_user_action_targets_status
    CHECK (status IN ('action_needed', 'in_progress', 'completed'))
);

DROP TRIGGER IF EXISTS trg_radar_user_action_targets_updated_at ON radar_user_action_targets;
CREATE TRIGGER trg_radar_user_action_targets_updated_at
BEFORE UPDATE ON radar_user_action_targets
FOR EACH ROW
EXECUTE FUNCTION radar_set_updated_at();

CREATE INDEX IF NOT EXISTS idx_radar_user_action_targets_user_action_status_product
ON radar_user_action_targets (user_id, action_type, status, product_uid);

CREATE INDEX IF NOT EXISTS idx_radar_user_action_targets_user_action_id
ON radar_user_action_targets (user_action_id);

CREATE INDEX IF NOT EXISTS idx_radar_user_action_targets_product_uid
ON radar_user_action_targets (product_uid);

CREATE TABLE IF NOT EXISTS t_sandbox_selection_history (
  id bigserial PRIMARY KEY,
  product_uid text NOT NULL,
  user_id bigint NOT NULL,
  hts_code text,
  hts_code_normalized text,
  country_code text,
  config_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  result_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  saved_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),

  CONSTRAINT chk_sandbox_selection_history_config_object
    CHECK (jsonb_typeof(config_json) = 'object'),
  CONSTRAINT chk_sandbox_selection_history_result_object
    CHECK (jsonb_typeof(result_json) = 'object')
);

DROP TRIGGER IF EXISTS trg_sandbox_selection_history_updated_at ON t_sandbox_selection_history;
CREATE TRIGGER trg_sandbox_selection_history_updated_at
BEFORE UPDATE ON t_sandbox_selection_history
FOR EACH ROW
EXECUTE FUNCTION radar_set_updated_at();

CREATE INDEX IF NOT EXISTS idx_sandbox_selection_history_user_product_created
ON t_sandbox_selection_history (user_id, product_uid, created_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS t_product_classification_history (
  id bigserial PRIMARY KEY,
  product_uid text NOT NULL,
  user_id bigint NOT NULL,
  case_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
  product_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
  qa_snapshot jsonb NOT NULL DEFAULT '[]'::jsonb,
  facts_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
  file_snapshot jsonb NOT NULL DEFAULT '[]'::jsonb,
  hts_candidate_snapshot jsonb NOT NULL DEFAULT '[]'::jsonb,
  snapshotted_at timestamptz NOT NULL DEFAULT now(),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),

  CONSTRAINT chk_product_classification_history_case_object
    CHECK (jsonb_typeof(case_snapshot) = 'object'),
  CONSTRAINT chk_product_classification_history_product_object
    CHECK (jsonb_typeof(product_snapshot) = 'object'),
  CONSTRAINT chk_product_classification_history_qa_array
    CHECK (jsonb_typeof(qa_snapshot) = 'array'),
  CONSTRAINT chk_product_classification_history_facts_object
    CHECK (jsonb_typeof(facts_snapshot) = 'object'),
  CONSTRAINT chk_product_classification_history_file_array
    CHECK (jsonb_typeof(file_snapshot) = 'array'),
  CONSTRAINT chk_product_classification_history_candidate_array
    CHECK (jsonb_typeof(hts_candidate_snapshot) = 'array')
);

DROP TRIGGER IF EXISTS trg_product_classification_history_updated_at ON t_product_classification_history;
CREATE TRIGGER trg_product_classification_history_updated_at
BEFORE UPDATE ON t_product_classification_history
FOR EACH ROW
EXECUTE FUNCTION radar_set_updated_at();

CREATE INDEX IF NOT EXISTS idx_product_classification_history_user_product_created
ON t_product_classification_history (user_id, product_uid, created_at DESC, id DESC);

COMMIT;
