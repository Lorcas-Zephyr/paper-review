-- Paper Review database schema
-- PostgreSQL 14+ with the pgvector extension installed.
-- This file is intentionally free of roles, passwords, and database names.

CREATE EXTENSION IF NOT EXISTS vector;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'taskstatus') THEN
        CREATE TYPE taskstatus AS ENUM ('PENDING', 'RUNNING', 'SUCCESS', 'FAILED', 'TIMEOUT');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'audit_status') THEN
        CREATE TYPE audit_status AS ENUM ('PENDING', 'RUNNING', 'SUCCESS', 'FAILED', 'TIMEOUT');
    END IF;
END
$$;

CREATE TABLE IF NOT EXISTS papers (
    paper_id UUID PRIMARY KEY,
    title TEXT,
    abstract TEXT,
    abstract_vector vector(768),
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS paper_sections (
    section_id BIGSERIAL PRIMARY KEY,
    paper_id UUID NOT NULL,
    section_order INTEGER NOT NULL DEFAULT 0,
    section_name TEXT,
    content TEXT,
    section_content TEXT,
    content_vector vector(768),
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_paper_sections_paper_id ON paper_sections (paper_id);
CREATE INDEX IF NOT EXISTS ix_paper_sections_paper_section ON paper_sections (paper_id, section_name);
CREATE INDEX IF NOT EXISTS ix_paper_sections_paper_order ON paper_sections (paper_id, section_order);

CREATE TABLE IF NOT EXISTS paper_paragraphs (
    paragraph_id BIGSERIAL PRIMARY KEY,
    paper_id UUID NOT NULL,
    paragraph_name TEXT,
    paragraph_content TEXT,
    content_vector vector(768),
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_paper_paragraphs_paper_id ON paper_paragraphs (paper_id);

CREATE TABLE IF NOT EXISTS reviews (
    review_id BIGSERIAL PRIMARY KEY,
    section_id BIGINT,
    paper_id UUID NOT NULL,
    review_content TEXT,
    review_vector vector(768),
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_reviews_paper_id ON reviews (paper_id);

CREATE TABLE IF NOT EXISTS agent_audits (
    id BIGSERIAL PRIMARY KEY,
    task_id UUID NOT NULL,
    paper_id UUID NOT NULL,
    chunk_id TEXT,
    agent_name TEXT,
    agent_version TEXT,
    status audit_status,
    score INTEGER,
    audit_level TEXT,
    result_json JSONB,
    error_msg TEXT,
    usage_tokens INTEGER,
    latency_ms INTEGER,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_agent_audits_task_id ON agent_audits (task_id);
CREATE INDEX IF NOT EXISTS ix_agent_audits_paper_id ON agent_audits (paper_id);
CREATE INDEX IF NOT EXISTS ix_agent_audits_paper_agent ON agent_audits (paper_id, agent_name);

CREATE TABLE IF NOT EXISTS review_tasks (
    id BIGSERIAL PRIMARY KEY,
    task_id TEXT NOT NULL,
    paper_id UUID NOT NULL,
    chunk_id TEXT NOT NULL DEFAULT 'full_paper',
    agent_name TEXT NOT NULL DEFAULT 'unknown',
    agent_version TEXT NOT NULL DEFAULT 'v1.0',
    status taskstatus NOT NULL DEFAULT 'PENDING',
    score INTEGER,
    audit_level TEXT,
    result_json JSONB,
    error_msg TEXT,
    usage_tokens INTEGER,
    latency_ms INTEGER,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_review_tasks_task_id UNIQUE (task_id)
);
CREATE INDEX IF NOT EXISTS ix_review_tasks_paper_id ON review_tasks (paper_id);
CREATE INDEX IF NOT EXISTS ix_review_tasks_paper_chunk ON review_tasks (paper_id, chunk_id);

CREATE TABLE IF NOT EXISTS agent_audit_result (
    id BIGSERIAL PRIMARY KEY,
    result_id TEXT,
    rule_id TEXT,
    request_id TEXT,
    task_id UUID,
    paper_id UUID,
    chunk_id TEXT,
    agent_code TEXT,
    agent_name TEXT,
    agent_version TEXT,
    status TEXT,
    score INTEGER,
    audit_level TEXT,
    point TEXT,
    description TEXT,
    evidence_quote TEXT,
    suggestion TEXT,
    location JSONB,
    error_msg TEXT,
    result_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_agent_audit_result_request_id ON agent_audit_result (request_id);
CREATE INDEX IF NOT EXISTS ix_agent_audit_result_paper_id ON agent_audit_result (paper_id);
CREATE INDEX IF NOT EXISTS ix_agent_audit_result_paper_chunk ON agent_audit_result (paper_id, chunk_id);
CREATE INDEX IF NOT EXISTS ix_agent_audit_result_agent_code ON agent_audit_result (agent_code);

CREATE TABLE IF NOT EXISTS expert_comments (
    comment_id BIGSERIAL PRIMARY KEY,
    rule_code TEXT,
    rule_category TEXT,
    rule_title TEXT NOT NULL DEFAULT 'Default rule title',
    rule_text TEXT,
    indicator_name TEXT NOT NULL DEFAULT 'default_indicator',
    operator TEXT,
    threshold_value DOUBLE PRECISION,
    threshold_secondary DOUBLE PRECISION,
    threshold_unit TEXT,
    severity TEXT,
    weight DOUBLE PRECISION CHECK (weight IS NULL OR (weight >= 0 AND weight <= 1)),
    is_hard_rule BOOLEAN NOT NULL DEFAULT FALSE,
    evidence_pattern TEXT,
    embedding vector(768),
    source TEXT,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    metric_id TEXT,
    text TEXT,
    CONSTRAINT uq_expert_comments_rule_code UNIQUE (rule_code)
);
CREATE INDEX IF NOT EXISTS ix_expert_comments_metric_id ON expert_comments (metric_id);
CREATE INDEX IF NOT EXISTS ix_expert_comments_active ON expert_comments (active);
CREATE INDEX IF NOT EXISTS ix_expert_comments_embedding ON expert_comments USING hnsw (embedding vector_cosine_ops);

CREATE TABLE IF NOT EXISTS agent_rules (
    id BIGSERIAL PRIMARY KEY,
    rule_id TEXT NOT NULL,
    content TEXT NOT NULL,
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_agent_rules_rule_id UNIQUE (rule_id)
);

CREATE TABLE IF NOT EXISTS ground_truth_issues (
    id BIGSERIAL PRIMARY KEY,
    sample_id TEXT,
    paper_id UUID,
    chunk_id TEXT,
    issue_type TEXT NOT NULL,
    severity TEXT,
    message TEXT,
    evidence TEXT,
    page_num INTEGER,
    bbox JSONB,
    source TEXT,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_ground_truth_issues_sample_id ON ground_truth_issues (sample_id);
CREATE INDEX IF NOT EXISTS ix_ground_truth_issues_paper_id ON ground_truth_issues (paper_id);
CREATE INDEX IF NOT EXISTS ix_ground_truth_issues_paper_chunk ON ground_truth_issues (paper_id, chunk_id);
CREATE INDEX IF NOT EXISTS ix_ground_truth_issues_issue_type ON ground_truth_issues (issue_type);

CREATE TABLE IF NOT EXISTS orchestrator_tasks (
    request_id TEXT PRIMARY KEY,
    paper_id TEXT NOT NULL,
    overall_status TEXT NOT NULL,
    progress JSONB NOT NULL DEFAULT '{}'::jsonb,
    aggregated_report JSONB,
    message TEXT,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    planner_status JSONB NOT NULL DEFAULT '{}'::jsonb,
    plan JSONB,
    round INTEGER NOT NULL DEFAULT 0,
    consultation_count INTEGER NOT NULL DEFAULT 0,
    scheduler_backend TEXT NOT NULL DEFAULT 'ai_planner_runtime'
);
CREATE INDEX IF NOT EXISTS ix_orchestrator_tasks_status ON orchestrator_tasks (overall_status);
CREATE INDEX IF NOT EXISTS ix_orchestrator_tasks_updated_at ON orchestrator_tasks (updated_at DESC);

CREATE TABLE IF NOT EXISTS main_rules (
    rule_id TEXT PRIMARY KEY,
    agent_code TEXT NOT NULL,
    agent_name_en TEXT,
    agent_name_cn TEXT,
    rule_name_en TEXT,
    rule_name_cn TEXT,
    rule_detail TEXT,
    full_score DOUBLE PRECISION,
    severity TEXT,
    rule_type TEXT,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_main_rules_agent_code ON main_rules (agent_code);

CREATE TABLE IF NOT EXISTS rule_judge (
    judge_id BIGSERIAL PRIMARY KEY,
    rule_id TEXT NOT NULL REFERENCES main_rules(rule_id) ON DELETE CASCADE,
    check_indicator TEXT,
    operator TEXT,
    threshold_val DOUBLE PRECISION,
    threshold_unit_en TEXT,
    threshold_unit_cn TEXT,
    is_core_rule BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_rule_judge_rule_id UNIQUE (rule_id)
);
CREATE INDEX IF NOT EXISTS ix_rule_judge_rule_id ON rule_judge (rule_id);

CREATE TABLE IF NOT EXISTS reflect_agent_verdict (
    verdict_id TEXT PRIMARY KEY,
    paper_id TEXT NOT NULL,
    paper_name TEXT,
    initial_score DOUBLE PRECISION,
    conflict_resolution TEXT,
    conflict_penalty DOUBLE PRECISION,
    final_score DOUBLE PRECISION,
    filtered_suggestions JSONB,
    prioritized_suggestions JSONB,
    final_verdict TEXT,
    verdict_tags TEXT,
    verdict_time TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_reflect_agent_verdict_paper_id UNIQUE (paper_id)
);
CREATE INDEX IF NOT EXISTS ix_reflect_agent_verdict_paper_id ON reflect_agent_verdict (paper_id);

CREATE TABLE IF NOT EXISTS reflection_results (
    id UUID PRIMARY KEY,
    paper_id TEXT NOT NULL,
    final_score DOUBLE PRECISION,
    needs_human_review BOOLEAN NOT NULL DEFAULT FALSE,
    review_reason TEXT,
    mentor_dialogue JSONB,
    dialogue_quality_score DOUBLE PRECISION,
    plugin_metadata JSONB,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_reflection_results_paper_id ON reflection_results (paper_id);

-- Additive compatibility migrations for databases created by older services.
ALTER TABLE paper_sections ADD COLUMN IF NOT EXISTS section_order INTEGER NOT NULL DEFAULT 0;
ALTER TABLE paper_sections ADD COLUMN IF NOT EXISTS content TEXT;
ALTER TABLE paper_sections ADD COLUMN IF NOT EXISTS section_content TEXT;
ALTER TABLE review_tasks ALTER COLUMN chunk_id SET DEFAULT 'full_paper';
ALTER TABLE review_tasks ALTER COLUMN agent_name SET DEFAULT 'unknown';
ALTER TABLE review_tasks ALTER COLUMN agent_version SET DEFAULT 'v1.0';

