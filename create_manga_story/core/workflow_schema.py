"""Additive schema for asset-review and comic-to-shot workflows."""

import sqlite3


_WORKFLOW_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS story_summaries (
        summary_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        scope_type TEXT NOT NULL CHECK(scope_type IN ('page', 'chapter', 'arc', 'work')),
        scope_id TEXT NOT NULL,
        version INTEGER NOT NULL CHECK(version > 0),
        summary_json TEXT NOT NULL,
        source_refs_json TEXT NOT NULL DEFAULT '[]',
        source_fingerprint TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'current'
            CHECK(status IN ('current', 'stale', 'superseded')),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(project_id, scope_type, scope_id, version),
        FOREIGN KEY (project_id) REFERENCES projects(project_id)
    )""",
    """CREATE INDEX IF NOT EXISTS idx_story_summaries_scope
        ON story_summaries(project_id, scope_type, scope_id, status)""",
    """CREATE TABLE IF NOT EXISTS story_state_intervals (
        state_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        entity_type TEXT NOT NULL,
        entity_id TEXT NOT NULL,
        attribute TEXT NOT NULL,
        value_json TEXT NOT NULL,
        valid_from_event_id TEXT,
        valid_to_event_id TEXT,
        source_refs_json TEXT NOT NULL DEFAULT '[]',
        certainty TEXT NOT NULL DEFAULT 'uncertain'
            CHECK(certainty IN ('confirmed', 'uncertain')),
        confidence REAL NOT NULL DEFAULT 0 CHECK(confidence >= 0 AND confidence <= 1),
        status TEXT NOT NULL DEFAULT 'active'
            CHECK(status IN ('active', 'ended', 'needs_review')),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (project_id) REFERENCES projects(project_id),
        FOREIGN KEY (valid_from_event_id) REFERENCES story_events(event_id),
        FOREIGN KEY (valid_to_event_id) REFERENCES story_events(event_id)
    )""",
    """CREATE INDEX IF NOT EXISTS idx_story_state_intervals_entity
        ON story_state_intervals(project_id, entity_type, entity_id, status, attribute)""",
    """CREATE TABLE IF NOT EXISTS adaptation_plans (
        plan_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        version INTEGER NOT NULL CHECK(version > 0),
        scope_json TEXT NOT NULL,
        plan_json TEXT NOT NULL,
        source_refs_json TEXT NOT NULL DEFAULT '[]',
        source_fingerprint TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'draft'
            CHECK(status IN ('draft', 'needs_review', 'approved', 'stale', 'archived')),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(project_id, version),
        FOREIGN KEY (project_id) REFERENCES projects(project_id)
    )""",
    """CREATE TABLE IF NOT EXISTS story_beats (
        beat_id TEXT PRIMARY KEY,
        plan_id TEXT NOT NULL,
        sequence_index INTEGER NOT NULL CHECK(sequence_index >= 0),
        scene_id TEXT,
        beat_json TEXT NOT NULL,
        source_refs_json TEXT NOT NULL DEFAULT '[]',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(plan_id, sequence_index),
        FOREIGN KEY (plan_id) REFERENCES adaptation_plans(plan_id) ON DELETE CASCADE
    )""",
    """CREATE TABLE IF NOT EXISTS shots (
        shot_id TEXT PRIMARY KEY,
        plan_id TEXT NOT NULL,
        sequence_index INTEGER NOT NULL CHECK(sequence_index >= 0),
        scene_id TEXT,
        shot_json TEXT NOT NULL,
        beat_ids_json TEXT NOT NULL DEFAULT '[]',
        source_refs_json TEXT NOT NULL DEFAULT '[]',
        start_snapshot_json TEXT NOT NULL DEFAULT '{}',
        end_snapshot_json TEXT NOT NULL DEFAULT '{}',
        status TEXT NOT NULL DEFAULT 'draft'
            CHECK(status IN ('draft', 'needs_review', 'approved', 'generating',
                'qc_failed', 'complete', 'stale')),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(plan_id, sequence_index),
        FOREIGN KEY (plan_id) REFERENCES adaptation_plans(plan_id) ON DELETE CASCADE
    )""",
    """CREATE INDEX IF NOT EXISTS idx_shots_plan_sequence
        ON shots(plan_id, sequence_index, status)""",
    """CREATE TABLE IF NOT EXISTS shot_dependencies (
        shot_id TEXT NOT NULL,
        depends_on_shot_id TEXT NOT NULL,
        dependency_type TEXT NOT NULL
            CHECK(dependency_type IN ('tail_frame', 'state', 'plan', 'reference')),
        reason TEXT NOT NULL DEFAULT '',
        source_version TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'current'
            CHECK(status IN ('current', 'needs_check', 'stale')),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(shot_id, depends_on_shot_id, dependency_type),
        CHECK(shot_id != depends_on_shot_id),
        FOREIGN KEY (shot_id) REFERENCES shots(shot_id) ON DELETE CASCADE,
        FOREIGN KEY (depends_on_shot_id) REFERENCES shots(shot_id) ON DELETE CASCADE
    )""",
    """CREATE TABLE IF NOT EXISTS video_generation_runs (
        run_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        shot_id TEXT NOT NULL,
        plan_version INTEGER NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('pending', 'running', 'succeeded', 'failed')),
        input_package_json TEXT NOT NULL,
        model_name TEXT NOT NULL DEFAULT '',
        parameters_json TEXT NOT NULL DEFAULT '{}',
        output_path TEXT,
        output_hash TEXT,
        error_message TEXT,
        started_at TEXT,
        finished_at TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (project_id) REFERENCES projects(project_id),
        FOREIGN KEY (shot_id) REFERENCES shots(shot_id)
    )""",
    """CREATE INDEX IF NOT EXISTS idx_video_runs_shot_created
        ON video_generation_runs(shot_id, created_at)""",
    """CREATE TABLE IF NOT EXISTS video_qc_results (
        qc_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        decision TEXT NOT NULL
            CHECK(decision IN ('pass', 'warning', 'fail', 'needs_human_review', 'error')),
        report_json TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (run_id) REFERENCES video_generation_runs(run_id) ON DELETE CASCADE
    )""",
    """CREATE TABLE IF NOT EXISTS asset_workflow_requests (
        request_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        task_json TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'requested'
            CHECK(status IN ('requested', 'references_ready', 'prompt_ready',
                'generating', 'qc_pending', 'pass_candidate', 'retry_pending',
                'needs_human_review', 'error')),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (project_id) REFERENCES projects(project_id)
    )""",
    """CREATE TABLE IF NOT EXISTS asset_workflow_iterations (
        iteration_id TEXT PRIMARY KEY,
        request_id TEXT NOT NULL,
        attempt_index INTEGER NOT NULL CHECK(attempt_index >= 0),
        generation_run_id TEXT,
        prompt_json TEXT NOT NULL DEFAULT '{}',
        reference_manifest_json TEXT NOT NULL DEFAULT '[]',
        qc_report_json TEXT,
        status TEXT NOT NULL DEFAULT 'prompt_ready'
            CHECK(status IN ('prompt_ready', 'generated', 'pass_candidate',
                'retry_pending', 'needs_human_review', 'error')),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(request_id, attempt_index),
        FOREIGN KEY (request_id) REFERENCES asset_workflow_requests(request_id),
        FOREIGN KEY (generation_run_id) REFERENCES asset_generation_runs(run_id)
    )""",
    """CREATE INDEX IF NOT EXISTS idx_asset_workflow_iterations_request
        ON asset_workflow_iterations(request_id, attempt_index)""",
    """CREATE TABLE IF NOT EXISTS chapter_asset_groups (
        group_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        chapter_id TEXT NOT NULL,
        entity_type TEXT NOT NULL CHECK(entity_type IN ('character', 'location')),
        entity_id TEXT NOT NULL DEFAULT '',
        variant_key TEXT NOT NULL,
        variant_type TEXT NOT NULL,
        variant_description TEXT NOT NULL DEFAULT '',
        selected_source_asset_id TEXT,
        generated_asset_id TEXT,
        source_fingerprint TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'planned'
            CHECK(status IN ('planned', 'selecting', 'generating', 'approved', 'reused', 'retry', 'error')),
        decision_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(project_id, chapter_id, entity_type, entity_id, variant_key),
        FOREIGN KEY(project_id) REFERENCES projects(project_id),
        FOREIGN KEY(project_id, chapter_id) REFERENCES chapters(project_id, chapter_id)
    )""",
    """CREATE INDEX IF NOT EXISTS idx_chapter_asset_groups_scope
        ON chapter_asset_groups(project_id, chapter_id, entity_type, status)""",
    """CREATE TABLE IF NOT EXISTS chapter_asset_group_sources (
        group_id TEXT NOT NULL,
        source_crop_asset_id TEXT NOT NULL,
        source_page_id TEXT NOT NULL,
        panel_index INTEGER NOT NULL DEFAULT 0,
        reference_quality REAL NOT NULL CHECK(reference_quality >= 0 AND reference_quality <= 1),
        is_selected INTEGER NOT NULL DEFAULT 0 CHECK(is_selected IN (0, 1)),
        evidence_json TEXT NOT NULL DEFAULT '{}',
        PRIMARY KEY(group_id, source_crop_asset_id),
        FOREIGN KEY(group_id) REFERENCES chapter_asset_groups(group_id) ON DELETE CASCADE,
        FOREIGN KEY(source_crop_asset_id) REFERENCES assets(asset_id),
        FOREIGN KEY(source_page_id) REFERENCES pages(page_id)
    )""",
    """CREATE INDEX IF NOT EXISTS idx_chapter_asset_group_sources_quality
        ON chapter_asset_group_sources(group_id, is_selected, reference_quality DESC)""",
    """CREATE TABLE IF NOT EXISTS chapter_asset_page_bindings (
        binding_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        chapter_id TEXT NOT NULL,
        page_id TEXT NOT NULL,
        entity_type TEXT NOT NULL CHECK(entity_type IN ('character', 'location')),
        entity_id TEXT NOT NULL DEFAULT '',
        group_id TEXT NOT NULL,
        asset_id TEXT NOT NULL,
        variant_key TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(project_id, chapter_id, page_id, entity_type, entity_id, group_id),
        FOREIGN KEY(project_id) REFERENCES projects(project_id),
        FOREIGN KEY(project_id, chapter_id) REFERENCES chapters(project_id, chapter_id),
        FOREIGN KEY(page_id) REFERENCES pages(page_id),
        FOREIGN KEY(group_id) REFERENCES chapter_asset_groups(group_id) ON DELETE CASCADE,
        FOREIGN KEY(asset_id) REFERENCES assets(asset_id)
    )""",
    """CREATE INDEX IF NOT EXISTS idx_chapter_asset_page_bindings_lookup
        ON chapter_asset_page_bindings(project_id, chapter_id, page_id, entity_type, entity_id)""",
)


def initialize_workflow_schema(connection: sqlite3.Connection) -> None:
    """Create workflow tables without rewriting existing comic or asset data."""
    for statement in _WORKFLOW_SCHEMA:
        connection.execute(statement)
    connection.execute("INSERT OR IGNORE INTO schema_migrations(version) VALUES (3)")
