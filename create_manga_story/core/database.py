"""SQLite persistence for imported pages and Gemini analysis runs."""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Generator, Iterable


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
                """
            )

    def save_pages(self, pages: Iterable[Dict[str, Any]]) -> None:
        with self.connect() as connection:
            for page in pages:
                connection.execute(
                    "INSERT INTO projects(project_id) VALUES (?) "
                    "ON CONFLICT(project_id) DO NOTHING",
                    (page["project_id"],),
                )
                connection.execute(
                    "INSERT INTO chapters(project_id, chapter_id) VALUES (?, ?) "
                    "ON CONFLICT(project_id, chapter_id) DO NOTHING",
                    (page["project_id"], page["chapter_id"]),
                )
                connection.execute(
                    """
                    INSERT INTO pages(
                        page_id, project_id, chapter_id, page_index, source_path,
                        image_path, sha256, width, height
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(page_id) DO UPDATE SET
                        page_index=excluded.page_index,
                        source_path=excluded.source_path,
                        image_path=excluded.image_path,
                        sha256=excluded.sha256,
                        width=excluded.width,
                        height=excluded.height,
                        updated_at=CURRENT_TIMESTAMP
                    """,
                    (
                        page["page_id"],
                        page["project_id"],
                        page["chapter_id"],
                        page["page_index"],
                        page["source_path"],
                        page["image_path"],
                        page["sha256"],
                        page["width"],
                        page["height"],
                    ),
                )

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
