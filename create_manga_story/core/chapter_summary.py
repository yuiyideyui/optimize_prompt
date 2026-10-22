"""Rebuild chapter summaries using persisted page analyses and entity evidence."""

import hashlib
import json
from typing import Any, Dict, List

from .database import ComicDatabase


def rebuild_chapter_summary(
    database: ComicDatabase, project_id: str, chapter_id: str
) -> Dict[str, Any]:
    with database.connect() as connection:
        chapter_exists = connection.execute(
            "SELECT 1 FROM chapters WHERE project_id = ? AND chapter_id = ?",
            (project_id, chapter_id),
        ).fetchone()
        if chapter_exists is None:
            raise ValueError("Chapter is not present in the imported-page database.")
        rows = connection.execute(
            """SELECT p.page_id, p.page_index, a.result_json
               FROM pages p
               LEFT JOIN analysis_runs a ON a.analysis_id = (
                   SELECT latest.analysis_id FROM analysis_runs latest
                   WHERE latest.page_id = p.page_id
                   ORDER BY latest.created_at DESC, latest.rowid DESC LIMIT 1
               )
               WHERE p.project_id = ? AND p.chapter_id = ?
               ORDER BY p.page_index""",
            (project_id, chapter_id),
        ).fetchall()
        page_analyses = []
        page_ids = []
        for row in rows:
            page_ids.append(row["page_id"])
            analysis = json.loads(row["result_json"]) if row["result_json"] else None
            if analysis is not None:
                page_analyses.append(
                    {
                        "page_id": row["page_id"],
                        "page_index": row["page_index"],
                        "analysis": analysis,
                    }
                )
        character_rows = connection.execute(
            """SELECT DISTINCT c.character_id, c.canonical_name, c.status,
                      a.page_id
               FROM characters c
               JOIN character_appearances a ON a.character_id = c.character_id
               JOIN pages p ON p.page_id = a.page_id
               WHERE c.project_id = ? AND p.chapter_id = ?
                 AND c.status != 'rejected'
               ORDER BY c.canonical_name, c.character_id""",
            (project_id, chapter_id),
        ).fetchall()
        location_rows = connection.execute(
            """SELECT DISTINCT l.location_id, l.canonical_name, l.status, s.page_id
               FROM locations l
               JOIN location_states s ON s.location_id = l.location_id
               JOIN pages p ON p.page_id = s.page_id
               WHERE l.project_id = ? AND p.chapter_id = ?
                 AND l.status != 'rejected'
               ORDER BY l.canonical_name, l.location_id""",
            (project_id, chapter_id),
        ).fetchall()
        event_rows = connection.execute(
            """SELECT e.event_id, e.description, e.participants_json,
                      e.confidence, e.page_id, e.panel_index, p.page_index
               FROM story_events e JOIN pages p ON p.page_id = e.page_id
               WHERE e.project_id = ? AND e.chapter_id = ?
               ORDER BY p.page_index, e.panel_index, e.event_id""",
            (project_id, chapter_id),
        ).fetchall()
        relationship_rows = connection.execute(
            """SELECT r.relationship_id, r.source_character_id,
                      source.canonical_name AS source_name,
                      r.target_character_id, target.canonical_name AS target_name,
                      r.relationship, r.status, r.confidence, r.source_page_id,
                      r.panel_index
               FROM character_relationships r
               JOIN characters source
                 ON source.character_id = r.source_character_id
               JOIN characters target
                 ON target.character_id = r.target_character_id
               JOIN pages p ON p.page_id = r.source_page_id
               WHERE r.project_id = ? AND p.chapter_id = ?
               ORDER BY p.page_index, r.panel_index, r.relationship_id""",
            (project_id, chapter_id),
        ).fetchall()
        pending_rows = connection.execute(
            """SELECT entity_type, candidate_entity_id, matched_entity_id,
                      confidence, source_page_id
               FROM entity_matches
               WHERE project_id = ? AND status = 'pending_review'
                 AND source_page_id IN (
                     SELECT page_id FROM pages WHERE project_id = ? AND chapter_id = ?
                 )
               ORDER BY created_at, match_id""",
            (project_id, project_id, chapter_id),
        ).fetchall()
    ordered_summaries = [
        {
            "page_id": item["page_id"],
            "page_index": item["page_index"],
            "summary": item["analysis"].get("summary", ""),
        }
        for item in page_analyses
        if item["analysis"].get("summary")
    ]
    unresolved: List[Dict[str, Any]] = []
    for item in page_analyses:
        for uncertainty in item["analysis"].get("uncertainties", []):
            unresolved.append(
                {
                    "question": uncertainty,
                    "source_page_id": item["page_id"],
                    "kind": "analysis_uncertainty",
                }
            )
    unresolved.extend(
        {
            "question": "Review whether candidate {} is the same {} as {}.".format(
                row["candidate_entity_id"],
                row["entity_type"],
                row["matched_entity_id"],
            ),
            "source_page_id": row["source_page_id"],
            "kind": "entity_match",
            "confidence": row["confidence"],
        }
        for row in pending_rows
    )
    characters: Dict[str, Dict[str, Any]] = {}
    for row in character_rows:
        entity = characters.setdefault(
            row["character_id"],
            {
                "character_id": row["character_id"],
                "name": row["canonical_name"],
                "status": row["status"],
                "source_page_ids": [],
            },
        )
        if row["page_id"] not in entity["source_page_ids"]:
            entity["source_page_ids"].append(row["page_id"])
    locations: Dict[str, Dict[str, Any]] = {}
    for row in location_rows:
        entity = locations.setdefault(
            row["location_id"],
            {
                "location_id": row["location_id"],
                "name": row["canonical_name"],
                "status": row["status"],
                "source_page_ids": [],
            },
        )
        if row["page_id"] not in entity["source_page_ids"]:
            entity["source_page_ids"].append(row["page_id"])
    stored_events = {
        (row["page_id"], row["panel_index"], row["description"]): row
        for row in event_rows
    }
    event_list = []
    for page in page_analyses:
        for event in page["analysis"].get("story_events", []):
            panel_index = int(event.get("panel_index", -1))
            description = str(event.get("description", ""))
            stored = stored_events.get((page["page_id"], panel_index, description))
            event_list.append(
                {
                    "event_id": stored["event_id"] if stored else "",
                    "description": description,
                    "participants": event.get("participants", []),
                    "confidence": event.get(
                        "confidence", stored["confidence"] if stored else 0
                    ),
                    "source_page_id": page["page_id"],
                    "page_index": page["page_index"],
                    "panel_index": panel_index,
                }
            )
    event_list.sort(key=lambda item: (item["page_index"], item["panel_index"]))
    relationship_list = [
        {
            "relationship_id": row["relationship_id"],
            "source_character_id": row["source_character_id"],
            "source_name": row["source_name"],
            "target_character_id": row["target_character_id"],
            "target_name": row["target_name"],
            "relationship": row["relationship"],
            "status": row["status"],
            "confidence": row["confidence"],
            "source_page_id": row["source_page_id"],
            "panel_index": row["panel_index"],
        }
        for row in relationship_rows
    ]
    explicit_summary = "\n".join(
        "第{}页：{}".format(item["page_index"], item["summary"])
        for item in ordered_summaries
    )
    fingerprint_content = {
        "page_analyses": [
            (item["page_id"], item["analysis"]) for item in page_analyses
        ],
        "characters": [dict(row) for row in character_rows],
        "locations": [dict(row) for row in location_rows],
        "events": [dict(row) for row in event_rows],
        "relationships": [dict(row) for row in relationship_rows],
        "pending_matches": [dict(row) for row in pending_rows],
    }
    source_fingerprint = hashlib.sha256(
        json.dumps(
            fingerprint_content,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    summary = {
        "schema_version": "1.0",
        "project_id": project_id,
        "chapter_id": chapter_id,
        "summary": explicit_summary,
        "page_summaries": ordered_summaries,
        "story_events": event_list,
        "character_relationships": relationship_list,
        "characters": list(characters.values()),
        "locations": list(locations.values()),
        "ending_state": (
            ordered_summaries[-1]["summary"] if ordered_summaries else ""
        ),
        "unresolved_questions": unresolved,
        "unanalyzed_page_ids": [
            page_id
            for page_id in page_ids
            if page_id not in {item["page_id"] for item in page_analyses}
        ],
        "source_page_ids": page_ids,
        "source_fingerprint": source_fingerprint,
    }
    with database.connect() as connection:
        connection.execute(
            """INSERT INTO chapter_summaries(
                project_id, chapter_id, summary_json, source_fingerprint
            ) VALUES (?, ?, ?, ?)
            ON CONFLICT(project_id, chapter_id) DO UPDATE SET
                summary_json=excluded.summary_json,
                source_fingerprint=excluded.source_fingerprint,
                updated_at=CURRENT_TIMESTAMP""",
            (
                project_id,
                chapter_id,
                json.dumps(summary, ensure_ascii=False, separators=(",", ":")),
                source_fingerprint,
            ),
        )
    return summary
