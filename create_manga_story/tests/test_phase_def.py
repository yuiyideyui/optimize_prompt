import json
import tempfile
import unittest
from pathlib import Path

from create_manga_story.core.chapter_summary import rebuild_chapter_summary
from create_manga_story.core.context_manager import build_page_context
from create_manga_story.core.database import ComicDatabase
from create_manga_story.core.entity_matching import resolve_analysis_entities
from create_manga_story.core.knowledge_updater import update_knowledge_base
from create_manga_story.core.rag import query_knowledge, rebuild_project_index
from create_manga_story.core.schemas import validate_analysis


def _analysis(page_id, chapter_id="chapter-1", characters=None):
    return {
        "schema_version": "1.0",
        "project_id": "project-1",
        "chapter_id": chapter_id,
        "page_id": page_id,
        "summary": "A student finds a lost key.",
        "panels": [
            {
                "panel_index": 1,
                "reading_order": 1,
                "description": "Aki finds a key at the rainy station.",
                "confidence": 0.93,
            }
        ],
        "characters": characters
        if characters is not None
        else [
            {
                "name": "Aki",
                "identity_candidate": "short black hair and a small scar on the left cheek",
                "appearance": "short black hair and a small scar on the left cheek",
                "clothing": "school uniform",
                "expression": "surprised",
                "action": "picks up a key",
                "emotion": "surprised",
                "confidence": 0.95,
                "panel_index": 1,
            },
            {
                "name": "Mina",
                "identity_candidate": "long wavy brown hair",
                "appearance": "long wavy brown hair",
                "clothing": "red coat",
                "expression": "worried",
                "action": "looks for the key",
                "emotion": "worried",
                "confidence": 0.92,
                "panel_index": 1,
            },
        ],
        "locations": [
            {
                "name": "Riverside station",
                "description": "a small brick station beside a river",
                "visual_state": "night with rain",
                "confidence": 0.9,
                "panel_index": 1,
            }
        ],
        "dialogues": [
            {
                "speaker": "Aki",
                "text": "Is this your key?",
                "kind": "speech",
                "panel_index": 1,
                "confidence": 0.88,
            }
        ],
        "story_events": [
            {
                "description": "Aki finds a lost key.",
                "participants": ["Aki"],
                "panel_index": 1,
                "confidence": 0.9,
            }
        ],
        "character_relationships": [
            {
                "source": "Aki",
                "target": "Mina",
                "relationship": "classmates",
                "confidence": 0.75,
                "panel_index": 1,
            }
        ],
        "continuity": [],
        "uncertainties": ["The key owner is unknown."],
    }


class PhaseDETests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.database = ComicDatabase(self.root / "comic.sqlite3")
        self.database.initialize()

    def add_page(self, page_id, chapter_id, page_index):
        self.database.save_pages(
            [
                {
                    "page_id": page_id,
                    "project_id": "project-1",
                    "chapter_id": chapter_id,
                    "page_index": page_index,
                    "source_path": page_id + ".png",
                    "image_path": page_id + ".png",
                    "sha256": page_id,
                    "width": 100,
                    "height": 100,
                }
            ]
        )

    def test_same_name_alone_stays_pending_and_clothing_is_not_identity_evidence(self):
        self.add_page("page-1", "chapter-1", 1)
        with self.database.connect() as connection:
            connection.execute(
                """INSERT INTO characters(
                    character_id, project_id, canonical_name, stable_description, status
                ) VALUES (?, ?, ?, ?, ?)""",
                (
                    "known-aki",
                    "project-1",
                    "Aki",
                    "short black hair and a small scar on the left cheek",
                    "confirmed",
                ),
            )
            connection.execute(
                """INSERT INTO character_aliases(
                    alias_id, character_id, alias, normalized_alias, source_page_id
                ) VALUES (?, ?, ?, ?, ?)""",
                ("alias-aki", "known-aki", "Aki", "aki", "page-1"),
            )
        same_name_only = _analysis(
            "page-1",
            characters=[
                {
                    "name": "Aki",
                    "identity_candidate": "",
                    "appearance": "",
                    "clothing": "school uniform",
                }
            ],
        )
        result = resolve_analysis_entities(self.database, same_name_only)
        self.assertEqual(result["characters"][0]["status"], "pending_review")
        strong_visual_match = _analysis(
            "page-1",
            characters=[
                {
                    "name": "Aki",
                    "identity_candidate": "short black hair and a small scar on the left cheek",
                    "appearance": "short black hair and a small scar on the left cheek",
                    "clothing": "battle armor",
                }
            ],
        )
        result = resolve_analysis_entities(self.database, strong_visual_match)
        self.assertEqual(result["characters"][0]["status"], "confirmed_match")
        self.assertEqual(result["characters"][0]["entity_id"], "known-aki")
        renamed = json.loads(json.dumps(strong_visual_match))
        renamed["characters"][0]["name"] = "Unknown"
        result = resolve_analysis_entities(self.database, renamed)
        self.assertEqual(result["characters"][0]["status"], "pending_review")

    def test_ambiguous_match_is_saved_for_review(self):
        self.add_page("page-1", "chapter-1", 1)
        with self.database.connect() as connection:
            connection.execute(
                """INSERT INTO characters(
                    character_id, project_id, canonical_name, stable_description, status
                ) VALUES (?, ?, ?, ?, ?)""",
                ("known-aki", "project-1", "Aki", "unrelated stable description", "confirmed"),
            )
            connection.execute(
                """INSERT INTO character_aliases(
                    alias_id, character_id, alias, normalized_alias, source_page_id
                ) VALUES (?, ?, ?, ?, ?)""",
                ("alias-aki", "known-aki", "Aki", "aki", "page-1"),
            )
        analysis = _analysis(
            "page-1",
            characters=[
                {
                    "name": "Aki",
                    "identity_candidate": "",
                    "appearance": "",
                    "clothing": "school uniform",
                }
            ],
        )
        resolution = resolve_analysis_entities(self.database, analysis)
        report = update_knowledge_base(self.database, analysis, resolution)
        self.assertEqual(resolution["characters"][0]["status"], "pending_review")
        self.assertEqual(len(report["pending_matches"]), 1)
        with self.database.connect() as connection:
            match = connection.execute(
                "SELECT status, matched_entity_id FROM entity_matches"
            ).fetchone()
            self.assertEqual(match["status"], "pending_review")
            self.assertEqual(match["matched_entity_id"], "known-aki")
        separated = resolve_analysis_entities(
            self.database,
            analysis,
            review_decisions={
                "characters": [{"source_index": 0, "decision": "separate"}]
            },
        )
        self.assertNotEqual(separated["characters"][0]["entity_id"], "known-aki")
        update_knowledge_base(self.database, analysis, separated)
        with self.database.connect() as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM entity_matches WHERE source_page_id = 'page-1'"
                ).fetchone()[0],
                0,
            )
        confirmed = resolve_analysis_entities(
            self.database,
            analysis,
            review_decisions={
                "characters": [
                    {
                        "source_index": 0,
                        "decision": "confirm",
                        "matched_entity_id": "known-aki",
                    }
                ]
            },
        )
        update_knowledge_base(self.database, analysis, confirmed)
        with self.database.connect() as connection:
            self.assertEqual(
                connection.execute(
                    """SELECT COUNT(*) FROM character_appearances
                       WHERE character_id = 'known-aki' AND page_id = 'page-1'"""
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute(
                    """SELECT COUNT(*) FROM entity_matches
                       WHERE source_page_id = 'page-1' AND status = 'pending_review'"""
                ).fetchone()[0],
                0,
            )

    def test_updater_is_idempotent_and_rag_results_keep_source_page_evidence(self):
        analysis = _analysis("page-1")
        self.add_page("page-1", "chapter-1", 1)
        self.database.save_analysis(
            "analysis-1",
            "page-1",
            "gemini-test",
            "prompt-test",
            analysis,
            self.root / "analysis-1.json",
        )
        resolution = resolve_analysis_entities(self.database, analysis)
        first_report = update_knowledge_base(self.database, analysis, resolution)
        rerun_resolution = resolve_analysis_entities(self.database, analysis)
        self.assertEqual(
            [item["entity_id"] for item in rerun_resolution["characters"]],
            [item["entity_id"] for item in resolution["characters"]],
        )
        counts_before_repeat = {}
        with self.database.connect() as connection:
            for table in (
                "characters",
                "character_appearances",
                "character_appearance_evidence",
                "locations",
                "location_states",
                "story_events",
                "story_event_participants",
                "character_relationships",
                "panels",
                "dialogues",
                "entity_matches",
            ):
                counts_before_repeat[table] = connection.execute(
                    "SELECT COUNT(*) FROM {}".format(table)
                ).fetchone()[0]
        second_report = update_knowledge_base(self.database, analysis, rerun_resolution)
        self.assertEqual(first_report["updated_characters"], second_report["updated_characters"])
        with self.database.connect() as connection:
            for table, count in counts_before_repeat.items():
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM {}".format(table)
                    ).fetchone()[0],
                    count,
                    table,
                )
        result = query_knowledge(
            self.database, "project-1", "small brick station", "location"
        )
        self.assertTrue(result["results"])
        self.assertEqual(result["results"][0]["source_page_id"], "page-1")
        self.assertEqual(result["results"][0]["image_path"], "page-1.png")
        record_count = rebuild_project_index(self.database, "project-1")
        rebuilt = query_knowledge(
            self.database, "project-1", "finds a lost key", "story_event"
        )
        self.assertEqual(record_count, first_report["index_records"])
        self.assertEqual(rebuilt["results"][0]["source_page_id"], "page-1")
        dialogue = query_knowledge(
            self.database, "project-1", "your key", "dialogue"
        )
        self.assertEqual(dialogue["results"][0]["source_panel_index"], 1)

        changed_analysis = json.loads(json.dumps(analysis))
        changed_analysis["story_events"] = []
        changed_analysis["character_relationships"] = []
        changed_resolution = resolve_analysis_entities(self.database, changed_analysis)
        update_knowledge_base(self.database, changed_analysis, changed_resolution)
        with self.database.connect() as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM story_events WHERE page_id = 'page-1'").fetchone()[0],
                0,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM character_relationships WHERE source_page_id = 'page-1'"
                ).fetchone()[0],
                0,
            )

    def test_context_and_summary_rebuild_from_saved_analyses(self):
        first = _analysis("page-1")
        self.assertEqual(
            validate_analysis(
                {key: value for key, value in first.items() if key not in {
                    "schema_version",
                    "project_id",
                    "chapter_id",
                    "page_id",
                }}
            )["summary"],
            first["summary"],
        )
        second = _analysis("page-2", "chapter-1")
        self.add_page("page-1", "chapter-1", 1)
        self.add_page("page-2", "chapter-1", 2)
        for index, analysis in enumerate((first, second), 1):
            self.database.save_analysis(
                "analysis-{}".format(index),
                analysis["page_id"],
                "gemini-test",
                "prompt-test",
                analysis,
                self.root / "analysis-{}.json".format(index),
            )
        previous, knowledge = build_page_context(
            self.database,
            "project-1",
            "chapter-1",
            page_index=2,
            query="student finds a lost key",
        )
        self.assertIn("Page 1:", previous)
        self.assertIn("A student finds a lost key.", previous)
        self.assertEqual(knowledge["results"], [])
        summary = rebuild_chapter_summary(self.database, "project-1", "chapter-1")
        self.assertEqual(len(summary["page_summaries"]), 2)
        self.assertEqual(len(summary["story_events"]), 2)
        self.assertEqual(summary["source_page_ids"], ["page-1", "page-2"])
        self.assertEqual(summary["unanalyzed_page_ids"], [])
        self.assertIn("key owner is unknown", summary["unresolved_questions"][0]["question"])
        with self.database.connect() as connection:
            stored = json.loads(
                connection.execute(
                    """SELECT summary_json FROM chapter_summaries
                       WHERE project_id = ? AND chapter_id = ?""",
                    ("project-1", "chapter-1"),
                ).fetchone()[0]
            )
        self.assertEqual(stored["source_fingerprint"], summary["source_fingerprint"])

    def test_story_memory_crosses_chapters_and_tracks_state_and_threads(self):
        self.add_page("memory-page-1", "chapter-1", 1)
        self.add_page("memory-page-2", "chapter-2", 1)
        first = _analysis("memory-page-1", chapter_id="chapter-1", characters=[])
        first["state_changes"] = [{
            "subject": "Aki", "attribute": "location", "value": "station",
            "certainty": "confirmed", "panel_index": 1, "confidence": 0.9,
        }]
        first["new_open_threads"] = [{
            "description": "Who owns the key?", "thread_type": "mystery",
            "panel_index": 1, "confidence": 0.8,
        }]
        self.database.save_analysis("memory-analysis-1", "memory-page-1", "test", "v1", first, self.root / "one.json")
        update_knowledge_base(self.database, first, resolve_analysis_entities(self.database, first))

        second = _analysis("memory-page-2", chapter_id="chapter-2", characters=[])
        second["state_changes"] = [{
            "subject": "Aki", "attribute": "location", "value": "school",
            "certainty": "uncertain", "panel_index": 1, "confidence": 0.4,
        }]
        second["resolved_threads"] = [{
            "description": "Who owns the key?", "panel_index": 1, "confidence": 0.95,
        }]
        self.database.save_analysis("memory-analysis-2", "memory-page-2", "test", "v1", second, self.root / "two.json")
        update_knowledge_base(self.database, second, resolve_analysis_entities(self.database, second))

        previous, _ = build_page_context(self.database, "project-1", "chapter-2", 2, "Aki", history_pages=3)
        memory = json.loads(previous)
        self.assertEqual(memory["current_state"][0]["value"], "station")
        self.assertEqual(memory["current_state"][0]["status"], "active")
        self.assertEqual(memory["recent_pages"][0]["chapter_id"], "chapter-1")
        with self.database.connect() as connection:
            states = connection.execute("SELECT value,status FROM story_state_facts ORDER BY value").fetchall()
            threads = connection.execute("SELECT status FROM open_threads").fetchall()
        self.assertEqual({(row["value"], row["status"]) for row in states}, {("station", "active"), ("school", "uncertain")})
        self.assertEqual(threads[0]["status"], "resolved")

    def test_strong_character_match_carries_across_chapters(self):
        first = _analysis("page-1", "chapter-1", characters=[_analysis("page-1")["characters"][0]])
        self.add_page("page-1", "chapter-1", 1)
        first_resolution = resolve_analysis_entities(self.database, first)
        update_knowledge_base(self.database, first, first_resolution)
        second = _analysis("page-2", "chapter-2", characters=[_analysis("page-1")["characters"][0]])
        self.add_page("page-2", "chapter-2", 1)
        second_resolution = resolve_analysis_entities(self.database, second)
        self.assertEqual(second_resolution["characters"][0]["status"], "confirmed_match")
        self.assertEqual(
            second_resolution["characters"][0]["entity_id"],
            first_resolution["characters"][0]["entity_id"],
        )


if __name__ == "__main__":
    unittest.main()
