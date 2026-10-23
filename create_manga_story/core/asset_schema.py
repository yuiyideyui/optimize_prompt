"""Additive SQLite schema for the generated-asset subsystem."""

import sqlite3


_ASSET_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS assets (
        asset_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        asset_type TEXT NOT NULL,
        display_name TEXT NOT NULL DEFAULT '',
        relative_path TEXT NOT NULL UNIQUE,
        file_hash TEXT NOT NULL,
        mime_type TEXT NOT NULL,
        width INTEGER NOT NULL CHECK(width > 0),
        height INTEGER NOT NULL CHECK(height > 0),
        file_size INTEGER NOT NULL CHECK(file_size >= 0),
        status TEXT NOT NULL DEFAULT 'candidate'
            CHECK(status IN ('candidate', 'approved', 'rejected',
                'needs_revision', 'archived')),
        metadata_json TEXT NOT NULL DEFAULT '{}',
        supersedes_asset_id TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(project_id, asset_id),
        FOREIGN KEY (project_id) REFERENCES projects(project_id),
        FOREIGN KEY (supersedes_asset_id) REFERENCES assets(asset_id)
    )""",
    """CREATE INDEX IF NOT EXISTS idx_assets_project_type_status
        ON assets(project_id, asset_type, status, created_at)""",
    """CREATE INDEX IF NOT EXISTS idx_assets_hash
        ON assets(project_id, file_hash)""",
    """CREATE TABLE IF NOT EXISTS asset_relations (
        relation_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        asset_id TEXT NOT NULL,
        entity_type TEXT NOT NULL,
        entity_id TEXT NOT NULL,
        relation_type TEXT NOT NULL,
        is_primary INTEGER NOT NULL DEFAULT 0 CHECK(is_primary IN (0, 1)),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(asset_id, entity_type, entity_id, relation_type),
        FOREIGN KEY (project_id, asset_id)
            REFERENCES assets(project_id, asset_id) ON DELETE CASCADE
    )""",
    """CREATE INDEX IF NOT EXISTS idx_asset_relations_entity
        ON asset_relations(project_id, entity_type, entity_id, relation_type, is_primary)""",
    """CREATE UNIQUE INDEX IF NOT EXISTS idx_asset_relations_one_primary
        ON asset_relations(project_id, entity_type, entity_id, relation_type)
        WHERE is_primary = 1""",
    """CREATE TABLE IF NOT EXISTS asset_generation_runs (
        run_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        status TEXT NOT NULL
            CHECK(status IN ('pending', 'running', 'succeeded', 'failed')),
        asset_type TEXT NOT NULL,
        prompt TEXT NOT NULL DEFAULT '',
        prompt_version TEXT NOT NULL DEFAULT '',
        model_name TEXT NOT NULL DEFAULT '',
        model_version TEXT,
        seed TEXT,
        parameters_json TEXT NOT NULL DEFAULT '{}',
        reference_asset_ids_json TEXT NOT NULL DEFAULT '[]',
        workflow_snapshot_json TEXT,
        error_message TEXT,
        started_at TEXT,
        finished_at TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (project_id) REFERENCES projects(project_id)
    )""",
    """CREATE INDEX IF NOT EXISTS idx_asset_runs_project_created
        ON asset_generation_runs(project_id, created_at)""",
    """CREATE TABLE IF NOT EXISTS asset_generation_outputs (
        run_id TEXT NOT NULL,
        asset_id TEXT NOT NULL UNIQUE,
        output_index INTEGER NOT NULL CHECK(output_index >= 0),
        PRIMARY KEY(run_id, output_index),
        FOREIGN KEY (run_id)
            REFERENCES asset_generation_runs(run_id) ON DELETE CASCADE,
        FOREIGN KEY (asset_id) REFERENCES assets(asset_id)
    )""",
    """CREATE TABLE IF NOT EXISTS asset_reviews (
        review_id TEXT PRIMARY KEY,
        asset_id TEXT NOT NULL,
        review_status TEXT NOT NULL
            CHECK(review_status IN ('approved', 'rejected', 'needs_revision')),
        review_notes TEXT NOT NULL DEFAULT '',
        reviewed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (asset_id) REFERENCES assets(asset_id)
    )""",
    """CREATE INDEX IF NOT EXISTS idx_asset_reviews_asset
        ON asset_reviews(asset_id, reviewed_at)""",
)


def initialize_asset_schema(connection: sqlite3.Connection) -> None:
    """Create only new asset tables; existing knowledge data is not rewritten."""
    for statement in _ASSET_SCHEMA:
        connection.execute(statement)
    connection.execute("INSERT OR IGNORE INTO schema_migrations(version) VALUES (2)")
