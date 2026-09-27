CREATE TABLE IF NOT EXISTS lab_events (
  seq bigserial PRIMARY KEY, event_key text UNIQUE NOT NULL, kind text NOT NULL,
  project_id bigint NOT NULL, payload jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
  handled_at timestamptz, disposition text, attempts integer NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS lab_runs (
  id text PRIMARY KEY, project_id bigint NOT NULL, mr_iid bigint NOT NULL,
  source_sha text NOT NULL, trigger text NOT NULL, initial_state jsonb NOT NULL,
  status text NOT NULL DEFAULT 'queued', reason text, iteration integer NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
  lease_until timestamptz, owner text, attempts integer NOT NULL DEFAULT 0,
  model_calls integer NOT NULL DEFAULT 0, tokens_reserved bigint NOT NULL DEFAULT 0,
  tokens_used bigint NOT NULL DEFAULT 0, cost_reserved numeric NOT NULL DEFAULT 0,
  cost_used numeric NOT NULL DEFAULT 0, cancel_requested boolean NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS lab_queue ON lab_runs(status, lease_until, created_at);
ALTER TABLE lab_runs ADD COLUMN IF NOT EXISTS started_at timestamptz;
-- Preserve the conservative original deadline for runs attempted before this migration.
UPDATE lab_runs SET started_at=created_at WHERE started_at IS NULL AND attempts>0;
CREATE TABLE IF NOT EXISTS lab_objects (
  id bigserial PRIMARY KEY, run_id text NOT NULL REFERENCES lab_runs(id),
  object_id text NOT NULL CHECK (object_id ~ '^O[1-9]$'), version integer NOT NULL,
  slot text NOT NULL, iteration integer NOT NULL, project_id bigint NOT NULL, mr_iid bigint NOT NULL,
  source_sha text NOT NULL, test_sha text, producer text NOT NULL, content_hash text NOT NULL,
  payload jsonb NOT NULL, inputs jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (run_id,object_id,slot), UNIQUE (run_id,object_id,version)
);
CREATE TABLE IF NOT EXISTS lab_actions (
  action_key text PRIMARY KEY, run_id text NOT NULL REFERENCES lab_runs(id), kind text NOT NULL,
  status text NOT NULL DEFAULT 'pending', intent jsonb NOT NULL, result jsonb,
  attempts integer NOT NULL DEFAULT 0, created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS lab_links (
  project_id bigint NOT NULL, mr_iid bigint NOT NULL, author_id bigint NOT NULL,
  test_mr_iid bigint NOT NULL, source_branch text NOT NULL, last_run_id text NOT NULL,
  PRIMARY KEY (project_id,mr_iid), UNIQUE(project_id,test_mr_iid)
);
CREATE TABLE IF NOT EXISTS lab_feedback (
  project_id bigint NOT NULL, mr_iid bigint NOT NULL, event_key text NOT NULL,
  seq bigint NOT NULL REFERENCES lab_events(seq), payload jsonb NOT NULL,
  PRIMARY KEY(project_id,mr_iid,event_key)
);
CREATE TABLE IF NOT EXISTS lab_calls (
  id bigserial PRIMARY KEY, run_id text NOT NULL REFERENCES lab_runs(id), role text NOT NULL,
  iteration integer NOT NULL, endpoint text NOT NULL, model text NOT NULL,
  latency_ms integer NOT NULL, usage jsonb NOT NULL, paths jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
