"""SQLite persistence for imported pages and Gemini analysis runs."""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Generator, Iterable, Optional

from .asset_schema import initialize_asset_schema
from .workflow_schema import initialize_workflow_schema


class ComicDatabase:
    def __init__(self, database_path: Path):
        self.database_path = Path(database_path)

    @contextmanager
    def connect(self) -> Generator[sqlite3.Connection, None, None]:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.database_path), timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    project_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS chapters (
                    project_id TEXT NOT NULL,
                    chapter_id TEXT NOT NULL,
                    chapter_index INTEGER,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (project_id, chapter_id),
                    FOREIGN KEY (project_id) REFERENCES projects(project_id)
                );
                CREATE TABLE IF NOT EXISTS pages (
                    page_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    chapter_id TEXT NOT NULL,
                    page_index INTEGER NOT NULL,
                    source_path TEXT NOT NULL,
                    image_path TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    width INTEGER NOT NULL,
                    height INTEGER NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (project_id, chapter_id)
                        REFERENCES chapters(project_id, chapter_id)
                );
                CREATE TABLE IF NOT EXISTS character_voice_profiles (
                    project_id TEXT NOT NULL,
                    character_id TEXT NOT NULL,
                    voice_id TEXT NOT NULL,
                    voice_instruction TEXT NOT NULL,
                    language TEXT NOT NULL,
                    model_name TEXT NOT NULL,
                    sample_text TEXT NOT NULL,
                    audio_path TEXT NOT NULL,
                    sample_rate INTEGER NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (project_id, character_id),
                    UNIQUE (voice_id),
                    FOREIGN KEY (project_id) REFERENCES projects(project_id),
                    FOREIGN KEY (character_id) REFERENCES characters(character_id)
                );
                CREATE INDEX IF NOT EXISTS idx_pages_chapter_order
                    ON pages(project_id, chapter_id, page_index);
                CREATE TABLE IF NOT EXISTS analysis_runs (
                    analysis_id TEXT PRIMARY KEY,
                    page_id TEXT NOT NULL,
                    model_name TEXT NOT NULL,
                    prompt_version TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    result_path TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (page_id) REFERENCES pages(page_id)
                );
                CREATE INDEX IF NOT EXISTS idx_analysis_page_created
                    ON analysis_runs(page_id, created_at);
                CREATE TABLE IF NOT EXISTS characters (
                    character_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    canonical_name TEXT NOT NULL DEFAULT '',
                    stable_description TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'candidate',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (project_id) REFERENCES projects(project_id)
                );
                CREATE INDEX IF NOT EXISTS idx_characters_project
                    ON characters(project_id, status);
                CREATE TABLE IF NOT EXISTS character_aliases (
                    alias_id TEXT PRIMARY KEY,
                    character_id TEXT NOT NULL,
                    alias TEXT NOT NULL,
                    normalized_alias TEXT NOT NULL,
                    source_page_id TEXT,
                    UNIQUE(character_id, normalized_alias),
                    FOREIGN KEY (character_id) REFERENCES characters(character_id),
                    FOREIGN KEY (source_page_id) REFERENCES pages(page_id)
                );
                CREATE INDEX IF NOT EXISTS idx_character_alias_normalized
                    ON character_aliases(normalized_alias);
                CREATE TABLE IF NOT EXISTS character_appearances (
                    appearance_id TEXT PRIMARY KEY,
                    character_id TEXT NOT NULL,
                    page_id TEXT NOT NULL,
                    panel_index INTEGER NOT NULL,
                    appearance_description TEXT NOT NULL DEFAULT '',
                    clothing TEXT NOT NULL DEFAULT '',
                    expression TEXT NOT NULL DEFAULT '',
                    action TEXT NOT NULL DEFAULT '',
                    emotion TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(character_id, page_id, panel_index),
                    FOREIGN KEY (character_id) REFERENCES characters(character_id),
                    FOREIGN KEY (page_id) REFERENCES pages(page_id)
                );
                CREATE TABLE IF NOT EXISTS character_appearance_evidence (
                    evidence_id TEXT PRIMARY KEY,
                    appearance_id TEXT NOT NULL,
                    page_id TEXT NOT NULL,
                    panel_index INTEGER NOT NULL,
                    evidence_json TEXT NOT NULL,
                    UNIQUE(appearance_id, page_id, panel_index),
                    FOREIGN KEY (appearance_id)
                        REFERENCES character_appearances(appearance_id),
                    FOREIGN KEY (page_id) REFERENCES pages(page_id)
                );
                CREATE TABLE IF NOT EXISTS locations (
                    location_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    canonical_name TEXT NOT NULL DEFAULT '',
                    stable_description TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'candidate',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (project_id) REFERENCES projects(project_id)
                );
                CREATE INDEX IF NOT EXISTS idx_locations_project
                    ON locations(project_id, status);
                CREATE TABLE IF NOT EXISTS location_states (
                    state_id TEXT PRIMARY KEY,
                    location_id TEXT NOT NULL,
                    page_id TEXT NOT NULL,
                    panel_index INTEGER NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    visual_state TEXT NOT NULL DEFAULT '',
                    UNIQUE(location_id, page_id, panel_index),
                    FOREIGN KEY (location_id) REFERENCES locations(location_id),
                    FOREIGN KEY (page_id) REFERENCES pages(page_id)
                );
                CREATE TABLE IF NOT EXISTS story_events (
                    event_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    chapter_id TEXT NOT NULL,
                    page_id TEXT NOT NULL,
                    panel_index INTEGER NOT NULL,
                    description TEXT NOT NULL,
                    participants_json TEXT NOT NULL,
                    confidence REAL NOT NULL DEFAULT 0,
                    UNIQUE(page_id, panel_index, description),
                    FOREIGN KEY (project_id, chapter_id)
                        REFERENCES chapters(project_id, chapter_id),
                    FOREIGN KEY (page_id) REFERENCES pages(page_id)
                );
                CREATE TABLE IF NOT EXISTS character_relationships (
                    relationship_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    source_character_id TEXT NOT NULL,
                    target_character_id TEXT NOT NULL,
                    relationship TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'candidate',
                    source_page_id TEXT NOT NULL,
                    panel_index INTEGER NOT NULL,
                    confidence REAL NOT NULL DEFAULT 0,
                    UNIQUE(source_character_id, target_character_id, relationship,
                        source_page_id, panel_index),
                    FOREIGN KEY (source_character_id)
                        REFERENCES characters(character_id),
                    FOREIGN KEY (target_character_id)
                        REFERENCES characters(character_id),
                    FOREIGN KEY (source_page_id) REFERENCES pages(page_id)
                );
                CREATE TABLE IF NOT EXISTS panels (
                    panel_id TEXT PRIMARY KEY,
                    page_id TEXT NOT NULL,
                    panel_index INTEGER NOT NULL,
                    reading_order INTEGER NOT NULL,
                    description TEXT NOT NULL,
                    confidence REAL NOT NULL DEFAULT 0,
                    UNIQUE(page_id, panel_index),
                    FOREIGN KEY (page_id) REFERENCES pages(page_id)
                );
                CREATE TABLE IF NOT EXISTS dialogues (
                    dialogue_id TEXT PRIMARY KEY,
                    page_id TEXT NOT NULL,
                    panel_index INTEGER NOT NULL,
                    speaker TEXT NOT NULL DEFAULT '',
                    text TEXT NOT NULL,
                    kind TEXT NOT NULL DEFAULT '',
                    confidence REAL NOT NULL DEFAULT 0,
                    FOREIGN KEY (page_id) REFERENCES pages(page_id)
                );
                CREATE INDEX IF NOT EXISTS idx_dialogues_page_panel
                    ON dialogues(page_id, panel_index);
                CREATE TABLE IF NOT EXISTS entity_matches (
                    match_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    candidate_entity_id TEXT NOT NULL,
                    matched_entity_id TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending_review',
                    confidence REAL NOT NULL DEFAULT 0,
                    evidence_json TEXT NOT NULL,
                    source_page_id TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(entity_type, candidate_entity_id, matched_entity_id,
                        source_page_id),
                    FOREIGN KEY (source_page_id) REFERENCES pages(page_id)
                );
                CREATE INDEX IF NOT EXISTS idx_entity_matches_pending
                    ON entity_matches(project_id, status, entity_type);
                CREATE TABLE IF NOT EXISTS rag_index_records (
                    record_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    record_type TEXT NOT NULL,
                    text_content TEXT NOT NULL,
                    source_page_id TEXT,
                    source_panel_index INTEGER,
                    UNIQUE(entity_type, entity_id, record_type, source_page_id,
                        source_panel_index),
                    FOREIGN KEY (source_page_id) REFERENCES pages(page_id)
                );
                CREATE INDEX IF NOT EXISTS idx_rag_project_type
                    ON rag_index_records(project_id, entity_type);
                CREATE TABLE IF NOT EXISTS story_event_participants (
                    event_id TEXT NOT NULL,
                    character_id TEXT NOT NULL,
                    participant_name TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(event_id, character_id),
                    FOREIGN KEY (event_id) REFERENCES story_events(event_id),
                    FOREIGN KEY (character_id) REFERENCES characters(character_id)
                );
                CREATE TABLE IF NOT EXISTS chapter_summaries (
                    project_id TEXT NOT NULL,
                    chapter_id TEXT NOT NULL,
                    summary_json TEXT NOT NULL,
                    source_fingerprint TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(project_id, chapter_id),
                    FOREIGN KEY (project_id, chapter_id)
                        REFERENCES chapters(project_id, chapter_id)
                );
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS story_state_facts (
                    fact_id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
                    chapter_id TEXT NOT NULL, page_id TEXT NOT NULL,
                    subject TEXT NOT NULL, attribute TEXT NOT NULL, value TEXT NOT NULL,
                    status TEXT NOT NULL, certainty TEXT NOT NULL,
                    confidence REAL NOT NULL DEFAULT 0, panel_index INTEGER NOT NULL,
                    valid_to_page_id TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (project_id, chapter_id) REFERENCES chapters(project_id, chapter_id),
                    FOREIGN KEY (page_id) REFERENCES pages(page_id),
                    FOREIGN KEY (valid_to_page_id) REFERENCES pages(page_id)
                );
                CREATE INDEX IF NOT EXISTS idx_story_state_active
                    ON story_state_facts(project_id, status, subject, attribute);
                CREATE TABLE IF NOT EXISTS open_threads (
                    thread_id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
                    description TEXT NOT NULL, thread_type TEXT NOT NULL,
                    status TEXT NOT NULL, source_page_id TEXT NOT NULL,
                    source_panel_index INTEGER NOT NULL, confidence REAL NOT NULL DEFAULT 0,
                    resolved_page_id TEXT, resolved_panel_index INTEGER,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (project_id) REFERENCES projects(project_id),
                    FOREIGN KEY (source_page_id) REFERENCES pages(page_id),
                    FOREIGN KEY (resolved_page_id) REFERENCES pages(page_id),
                    UNIQUE(project_id, description, source_page_id)
                );
                CREATE INDEX IF NOT EXISTS idx_open_threads_project_status
                    ON open_threads(project_id, status);
                """
            )
            initialize_asset_schema(connection)
            initialize_workflow_schema(connection)
            chapter_columns = {row["name"] for row in connection.execute("PRAGMA table_info(chapters)")}
            if "chapter_index" not in chapter_columns:
                connection.execute("ALTER TABLE chapters ADD COLUMN chapter_index INTEGER")
            connection.execute("INSERT OR IGNORE INTO schema_migrations(version) VALUES (1)")
            # Backfill legacy databases deterministically, preserving their creation order.
            projects = connection.execute("SELECT DISTINCT project_id FROM chapters").fetchall()
            for project in projects:
                rows = connection.execute(
                    "SELECT chapter_id FROM chapters WHERE project_id = ? AND chapter_index IS NULL ORDER BY created_at, chapter_id",
                    (project["project_id"],),
                ).fetchall()
                next_index = connection.execute(
                    "SELECT COALESCE(MAX(chapter_index), 0) + 1 FROM chapters WHERE project_id = ?",
                    (project["project_id"],),
                ).fetchone()[0]
                for row in rows:
                    connection.execute(
                        "UPDATE chapters SET chapter_index = ? WHERE project_id = ? AND chapter_id = ?",
                        (next_index, project["project_id"], row["chapter_id"]),
                    )
                    next_index += 1

    def save_pages(self, pages: Iterable[Dict[str, Any]]) -> None:
        with self.connect() as connection:
            for page in pages:
                connection.execute(
                    "INSERT INTO projects(project_id) VALUES (?) "
                    "ON CONFLICT(project_id) DO NOTHING",
                    (page["project_id"],),
                )
                connection.execute(
                    "INSERT INTO chapters(project_id, chapter_id, chapter_index) "
                    "VALUES (?, ?, (SELECT COALESCE(MAX(chapter_index), 0) + 1 FROM chapters WHERE project_id = ?)) "
                    "ON CONFLICT(project_id, chapter_id) DO NOTHING",
                    (page["project_id"], page["chapter_id"], page["project_id"]),
                )
                connection.execute(
                    """INSERT INTO pages(page_id,project_id,chapter_id,page_index,source_path,
                       image_path,sha256,width,height) VALUES(?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(page_id) DO UPDATE SET page_index=excluded.page_index,
                       source_path=excluded.source_path,image_path=excluded.image_path,
                       sha256=excluded.sha256,width=excluded.width,height=excluded.height,
                       updated_at=CURRENT_TIMESTAMP""",
                    (page["page_id"], page["project_id"], page["chapter_id"], page["page_index"],
                     page["source_path"], page["image_path"], page["sha256"], page["width"], page["height"]),
                )

    def reorder_chapters(self, project_id: str, chapter_ids: Iterable[str]) -> None:
        """Set the complete chapter order for a project using 1-based indexes."""
        ordered = list(chapter_ids)
        if len(ordered) != len(set(ordered)):
            raise ValueError("Chapter order contains duplicate chapter IDs.")
        with self.connect() as connection:
            existing = [row[0] for row in connection.execute(
                "SELECT chapter_id FROM chapters WHERE project_id = ?", (project_id,)
            )]
            if set(existing) != set(ordered):
                raise ValueError("Chapter order must include every project chapter exactly once.")
            # Temporary negative values avoid uniqueness collisions if a unique index is added.
            for index, chapter_id in enumerate(ordered, 1):
                connection.execute(
                    "UPDATE chapters SET chapter_index = ? WHERE project_id = ? AND chapter_id = ?",
                    (-index, project_id, chapter_id),
                )
            for index, chapter_id in enumerate(ordered, 1):
                connection.execute(
                    "UPDATE chapters SET chapter_index = ? WHERE project_id = ? AND chapter_id = ?",
                    (index, project_id, chapter_id),
                )

    def rebuild_story_memory(self, project_id: str) -> Dict[str, int]:
        """Rebuild derived continuity records from latest saved analyses."""
        from .entity_matching import resolve_analysis_entities
        from .knowledge_updater import update_knowledge_base

        with self.connect() as connection:
            # These are derived projections. Clear them first so edits/removals in
            # saved analyses cannot leave stale state or resurrect resolved threads.
            connection.execute(
                "DELETE FROM story_state_facts WHERE project_id = ?", (project_id,)
            )
            connection.execute(
                "DELETE FROM open_threads WHERE project_id = ?", (project_id,)
            )
            rows = connection.execute(
                """SELECT a.result_json FROM analysis_runs a JOIN pages p ON p.page_id=a.page_id
                   WHERE p.project_id=? AND a.analysis_id=(SELECT latest.analysis_id
                     FROM analysis_runs latest WHERE latest.page_id=p.page_id
                     ORDER BY latest.created_at DESC, latest.rowid DESC LIMIT 1)
                   ORDER BY (SELECT chapter_index FROM chapters c WHERE c.project_id=p.project_id
                     AND c.chapter_id=p.chapter_id), p.page_index""",
                (project_id,),
            ).fetchall()
        rebuilt = 0
        for row in rows:
            analysis = json.loads(row["result_json"])
            analysis.setdefault("state_changes", [])
            analysis.setdefault("new_open_threads", [])
            analysis.setdefault("resolved_threads", [])
            resolution = resolve_analysis_entities(self, analysis)
            update_knowledge_base(self, analysis, resolution)
            rebuilt += 1
        return {"project_id": project_id, "analyses_rebuilt": rebuilt}

    def save_analysis(
        self,
        analysis_id: str,
        page_id: str,
        model_name: str,
        prompt_version: str,
        result: Dict[str, Any],
        result_path: Path,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO analysis_runs(
                    analysis_id, page_id, model_name, prompt_version,
                    result_json, result_path
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    analysis_id,
                    page_id,
                    model_name,
                    prompt_version,
                    json.dumps(result, ensure_ascii=False, separators=(",", ":")),
                    str(result_path),
                ),
            )

    def find_analysis_by_sha256(
        self,
        project_id: str,
        chapter_id: str,
        sha256: str,
        prompt_version: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """查找已有相同hash的分析结果。返回最新的分析运行记录或None。"""
        with self.connect() as connection:
            query = """
                SELECT ar.analysis_id, ar.result_json, ar.result_path
                FROM analysis_runs ar
                JOIN pages p ON ar.page_id = p.page_id
                WHERE p.project_id = ? AND p.chapter_id = ? AND p.sha256 = ?
            """
            params = [project_id, chapter_id, sha256]
            if prompt_version:
                query += " AND ar.prompt_version = ?"
                params.append(prompt_version)
            query += " ORDER BY ar.created_at DESC LIMIT 1"
            row = connection.execute(query, params).fetchone()
            if row is None:
                return None
            try:
                result = json.loads(row["result_json"])
                return {
                    "analysis_id": row["analysis_id"],
                    "result": result,
                    "result_path": row["result_path"],
                    "from_cache": True,
                }
            except (json.JSONDecodeError, ValueError):
                return None
