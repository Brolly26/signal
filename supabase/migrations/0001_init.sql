-- 0001_init.sql — Signal initial schema
--
-- Design notes (full rationale in docs/decisions.md):
--   * Relationships between requests are relational, never JSON, so that every
--     AI-suggested reference is protected by a foreign key.
--   * review_decisions is an append-only audit log; request_links holds the
--     current human-approved state. Approving a relationship never deletes or
--     merges the original requests.
--   * ai_analyses.classification is DERIVED by the backend from the validated
--     candidate set, not emitted by the model.

-- ---------------------------------------------------------------- requests ---
CREATE TABLE feature_requests (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    title           text        NOT NULL,
    description     text        NOT NULL,
    -- Demo identity only. This is NOT authentication; see README.
    requester_label text        NOT NULL DEFAULT 'anonymous',
    status          text        NOT NULL DEFAULT 'open',
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    search_vector   tsvector GENERATED ALWAYS AS (
        to_tsvector('english', coalesce(title, '') || ' ' || coalesce(description, ''))
    ) STORED,

    CONSTRAINT feature_requests_title_len
        CHECK (char_length(btrim(title)) BETWEEN 5 AND 140),
    CONSTRAINT feature_requests_description_len
        CHECK (char_length(btrim(description)) BETWEEN 20 AND 4000),
    CONSTRAINT feature_requests_requester_label_len
        CHECK (char_length(btrim(requester_label)) BETWEEN 1 AND 60),
    CONSTRAINT feature_requests_status_valid
        CHECK (status IN ('open', 'triaged', 'planned', 'declined'))
);

CREATE INDEX feature_requests_search_idx  ON feature_requests USING GIN (search_vector);
CREATE INDEX feature_requests_created_idx ON feature_requests (created_at DESC);

-- ------------------------------------------------------------------- votes ---
CREATE TABLE request_votes (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    request_id uuid        NOT NULL REFERENCES feature_requests (id) ON DELETE CASCADE,
    -- Browser-scoped demo identity. Prevents accidental double voting,
    -- NOT a defence against a determined actor.
    voter_key  text        NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT request_votes_voter_key_len CHECK (char_length(voter_key) BETWEEN 8 AND 64),
    CONSTRAINT request_votes_one_per_voter UNIQUE (request_id, voter_key)
);

CREATE INDEX request_votes_request_idx ON request_votes (request_id);

-- ---------------------------------------------------------------- analyses ---
CREATE TABLE ai_analyses (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    request_id            uuid NOT NULL REFERENCES feature_requests (id) ON DELETE CASCADE,
    status                text NOT NULL DEFAULT 'pending',

    -- Derived by the backend from the validated candidate set.
    classification        text,
    recommendation        text,
    underlying_need       text,
    problem_summary       text,
    suggested_theme       text,
    reasoning             text,

    -- Observability. No prompts and no user content are stored here.
    model_name            text,
    retrieval_mode        text,
    tool_calls_count      integer,
    fabricated_refs_count integer NOT NULL DEFAULT 0,
    latency_ms            integer,
    error_code            text,

    created_at            timestamptz NOT NULL DEFAULT now(),
    started_at            timestamptz,
    completed_at          timestamptz,

    CONSTRAINT ai_analyses_status_valid
        CHECK (status IN ('pending', 'completed', 'failed')),
    CONSTRAINT ai_analyses_classification_valid
        CHECK (classification IS NULL OR classification IN
               ('DUPLICATE', 'RELATED', 'DISTINCT', 'NEEDS_CLARIFICATION')),
    CONSTRAINT ai_analyses_recommendation_valid
        CHECK (recommendation IS NULL OR recommendation IN
               ('MERGE_CANDIDATE', 'LINK_AS_RELATED', 'KEEP_SEPARATE', 'REQUEST_CLARIFICATION')),
    CONSTRAINT ai_analyses_retrieval_mode_valid
        CHECK (retrieval_mode IS NULL OR retrieval_mode IN ('agentic', 'backend_fallback')),
    CONSTRAINT ai_analyses_error_code_valid
        CHECK (error_code IS NULL OR error_code IN
               ('TIMEOUT', 'PROVIDER_ERROR', 'INVALID_OUTPUT', 'REFUSAL',
                'INTERRUPTED', 'FABRICATED_REFERENCES', 'UNEXPECTED')),
    -- A completed analysis must actually carry a verdict.
    CONSTRAINT ai_analyses_completed_has_verdict
        CHECK (status <> 'completed'
               OR (classification IS NOT NULL AND completed_at IS NOT NULL)),
    -- A failed analysis must say why. This is what makes retry actionable.
    CONSTRAINT ai_analyses_failed_has_error
        CHECK (status <> 'failed' OR error_code IS NOT NULL),
    CONSTRAINT ai_analyses_counts_non_negative
        CHECK (coalesce(tool_calls_count, 0) >= 0
               AND fabricated_refs_count >= 0
               AND coalesce(latency_ms, 0) >= 0)
);

-- At most one in-flight analysis per request, enforced by the database.
-- The retry endpoint relies on this instead of an application-level lock.
CREATE UNIQUE INDEX ai_analyses_one_pending_per_request
    ON ai_analyses (request_id) WHERE status = 'pending';

CREATE INDEX ai_analyses_request_idx ON ai_analyses (request_id, created_at DESC);
CREATE INDEX ai_analyses_review_queue_idx
    ON ai_analyses (created_at DESC) WHERE status = 'completed';

-- ------------------------------------------------- AI-suggested candidates ---
-- One row per candidate relationship, each with its own evidence.
CREATE TABLE analysis_related_requests (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    analysis_id        uuid NOT NULL REFERENCES ai_analyses (id) ON DELETE CASCADE,
    related_request_id uuid NOT NULL REFERENCES feature_requests (id) ON DELETE CASCADE,
    relationship_type  text NOT NULL,
    explanation        text NOT NULL,
    created_at         timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT analysis_related_type_valid
        CHECK (relationship_type IN ('DUPLICATE', 'RELATED')),
    CONSTRAINT analysis_related_explanation_len
        CHECK (char_length(btrim(explanation)) BETWEEN 10 AND 2000),
    CONSTRAINT analysis_related_unique UNIQUE (analysis_id, related_request_id)
);

CREATE INDEX analysis_related_analysis_idx ON analysis_related_requests (analysis_id);
CREATE INDEX analysis_related_request_idx  ON analysis_related_requests (related_request_id);

-- ------------------------------------------------- human decisions (audit) ---
-- Append-only. Rows are never updated or deleted; the effective state of a
-- pair is its most recent row.
CREATE TABLE review_decisions (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    analysis_id        uuid NOT NULL REFERENCES ai_analyses (id) ON DELETE CASCADE,
    -- NULL means a decision about the analysis as a whole (e.g. dismiss).
    related_request_id uuid REFERENCES feature_requests (id) ON DELETE CASCADE,
    decision           text NOT NULL,
    reviewer_label     text NOT NULL DEFAULT 'demo-pm',
    reason             text,
    created_at         timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT review_decisions_decision_valid
        CHECK (decision IN ('APPROVED', 'REJECTED', 'DISMISSED')),
    CONSTRAINT review_decisions_reviewer_len
        CHECK (char_length(btrim(reviewer_label)) BETWEEN 1 AND 60),
    CONSTRAINT review_decisions_reason_len
        CHECK (reason IS NULL OR char_length(btrim(reason)) <= 2000)
);

CREATE INDEX review_decisions_analysis_idx
    ON review_decisions (analysis_id, created_at DESC);
CREATE INDEX review_decisions_pair_idx
    ON review_decisions (analysis_id, related_request_id, created_at DESC);

-- -------------------------------------------- approved links between pairs ---
-- Current state only. Original requests are never deleted or merged.
CREATE TABLE request_links (
    id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    request_a_id             uuid NOT NULL REFERENCES feature_requests (id) ON DELETE CASCADE,
    request_b_id             uuid NOT NULL REFERENCES feature_requests (id) ON DELETE CASCADE,
    link_type                text NOT NULL,
    created_from_analysis_id uuid REFERENCES ai_analyses (id) ON DELETE SET NULL,
    created_at               timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT request_links_type_valid CHECK (link_type IN ('DUPLICATE', 'RELATED')),
    -- Normalised pair: forbids self-links and the (B,A) duplicate of (A,B).
    CONSTRAINT request_links_normalised_pair CHECK (request_a_id < request_b_id),
    CONSTRAINT request_links_unique_pair UNIQUE (request_a_id, request_b_id)
);

CREATE INDEX request_links_a_idx ON request_links (request_a_id);
CREATE INDEX request_links_b_idx ON request_links (request_b_id);
