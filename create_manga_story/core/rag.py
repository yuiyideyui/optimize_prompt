"""Rebuildable, source-traceable SQLite text retrieval."""

import re
import uuid
from typing import Any, Dict, List, Optional

from .database import ComicDatabase


_INDEX_NAMESPACE = uuid.UUID("85034d9b-a014-4ef2-a2fb-a879fdd32c03")


def rebuild_project_index(database: ComicDatabase, project_id: str) -> int:
    with database.connect() as connection:
        connection.execute(
            "DELETE FROM rag_index_records WHERE project_id = ?", (project_id,)
        )
        records: List[Dict[str, Any]] = []

        def add(
            entity_type: str,
            entity_id: str,
            record_type: str,
            text: str,
            page_id: Optional[str] = None,
            panel_index: Optional[int] = None,
        ) -> None:
            content = " ".join(str(text or "").split())
            if not content:
                return
            records.append(
                {
                    "project_id": project_id,
                    "entity_type": entity_type,
                    "entity_id": entity_id,
                    "record_type": record_type,
                    "text_content": content,
                    "source_page_id": page_id,
                    "source_panel_index": panel_index,
                }
            )

        for row in connection.execute(
            """SELECT c.character_id, c.canonical_name, c.stable_description,
                      (SELECT a.page_id FROM character_appearances a
                       WHERE a.character_id = c.character_id
                       ORDER BY a.page_id, a.panel_index LIMIT 1) AS source_page_id,
                      (SELECT a.panel_index FROM character_appearances a
                       WHERE a.character_id = c.character_id
                       ORDER BY a.page_id, a.panel_index LIMIT 1) AS source_panel_index
               FROM characters c WHERE c.project_id = ? AND c.status != 'rejected'
                 AND EXISTS (
                     SELECT 1 FROM character_appearances a
                     WHERE a.character_id = c.character_id
                 )""",
            (project_id,),
        ):
            add("character", row["character_id"], "identity",
                "{} {}".format(row["canonical_name"], row["stable_description"]),
                row["source_page_id"], row["source_panel_index"])
        for row in connection.execute(
            """SELECT a.character_id, a.alias, a.source_page_id
               FROM character_aliases a JOIN characters c
                 ON c.character_id = a.character_id
               WHERE c.project_id = ? AND c.status != 'rejected'
                 AND EXISTS (
                     SELECT 1 FROM character_appearances ca
                     WHERE ca.character_id = c.character_id
                 )""",
            (project_id,),
        ):
            add("character", row["character_id"], "alias", row["alias"], row["source_page_id"])
        for row in connection.execute(
            """SELECT a.character_id, a.page_id, a.panel_index,
                      a.appearance_description, a.clothing, a.action,
                      a.expression, a.emotion
               FROM character_appearances a JOIN characters c
                 ON c.character_id = a.character_id
               WHERE c.project_id = ? AND c.status != 'rejected'""",
            (project_id,),
        ):
            add(
                "character",
                row["character_id"],
                "appearance",
                " ".join(
                    row[key]
                    for key in (
                        "appearance_description",
                        "clothing",
                        "action",
                        "expression",
                        "emotion",
                    )
                ),
                row["page_id"],
                row["panel_index"],
            )
        for row in connection.execute(
            """SELECT l.location_id, l.canonical_name, l.stable_description,
                      (SELECT s.page_id FROM location_states s
                       WHERE s.location_id = l.location_id
                       ORDER BY s.page_id, s.panel_index LIMIT 1) AS source_page_id,
                      (SELECT s.panel_index FROM location_states s
                       WHERE s.location_id = l.location_id
                       ORDER BY s.page_id, s.panel_index LIMIT 1) AS source_panel_index
               FROM locations l WHERE l.project_id = ? AND l.status != 'rejected'
                 AND EXISTS (
                     SELECT 1 FROM location_states s
                     WHERE s.location_id = l.location_id
                 )""",
            (project_id,),
        ):
            add("location", row["location_id"], "identity",
                "{} {}".format(row["canonical_name"], row["stable_description"]),
                row["source_page_id"], row["source_panel_index"])
        for row in connection.execute(
            """SELECT s.location_id, s.page_id, s.panel_index,
                      s.description, s.visual_state
               FROM location_states s JOIN locations l
                 ON l.location_id = s.location_id
               WHERE l.project_id = ? AND l.status != 'rejected'
                 AND EXISTS (
                     SELECT 1 FROM location_states ls
                     WHERE ls.location_id = l.location_id
                 )""",
            (project_id,),
        ):
            add(
                "location",
                row["location_id"],
                "state",
                "{} {}".format(row["description"], row["visual_state"]),
                row["page_id"],
                row["panel_index"],
            )
        for row in connection.execute(
            """SELECT event_id, page_id, panel_index, description, participants_json
               FROM story_events WHERE project_id = ?""",
            (project_id,),
        ):
            add(
                "story_event",
                row["event_id"],
                "event",
                "{} {}".format(row["description"], row["participants_json"]),
                row["page_id"],
                row["panel_index"],
            )
        for row in connection.execute(
            """SELECT p.panel_id, p.page_id, p.panel_index,
                      p.reading_order, p.description
               FROM panels p JOIN pages page ON page.page_id = p.page_id
               WHERE page.project_id = ?""",
            (project_id,),
        ):
            add(
                "panel",
                row["panel_id"],
                "panel",
                row["description"],
                row["page_id"],
                row["panel_index"],
            )
        for row in connection.execute(
            """SELECT d.dialogue_id, d.page_id, d.panel_index, d.speaker,
                      d.text, d.kind
               FROM dialogues d JOIN pages page ON page.page_id = d.page_id
               WHERE page.project_id = ?""",
            (project_id,),
        ):
            add(
                "dialogue",
                row["dialogue_id"],
                "dialogue",
                "{} {} {}".format(row["speaker"], row["kind"], row["text"]),
                row["page_id"],
                row["panel_index"],
            )
        for row in connection.execute(
            """SELECT r.relationship_id, r.source_character_id,
                      source.canonical_name AS source_name,
                      r.target_character_id,
                      target.canonical_name AS target_name,
                      r.relationship, r.source_page_id, r.panel_index
               FROM character_relationships r
               JOIN characters source
                 ON source.character_id = r.source_character_id
               JOIN characters target
                 ON target.character_id = r.target_character_id
               WHERE r.project_id = ?""",
            (project_id,),
        ):
            add(
                "relationship",
                row["relationship_id"],
                "relationship",
                "{} {} {}".format(
                    row["source_name"], row["relationship"], row["target_name"]
                ),
                row["source_page_id"],
                row["panel_index"],
            )
        connection.executemany(
            """INSERT INTO rag_index_records(
                record_id, project_id, entity_type, entity_id, record_type,
                text_content, source_page_id, source_panel_index
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    str(
                        uuid.uuid5(
                            _INDEX_NAMESPACE,
                            "\0".join(
                                str(record[key] or "")
                                for key in (
                                    "project_id",
                                    "entity_type",
                                    "entity_id",
                                    "record_type",
                                    "source_page_id",
                                    "source_panel_index",
                                )
                            ),
                        )
                    ),
                    record["project_id"],
                    record["entity_type"],
                    record["entity_id"],
                    record["record_type"],
                    record["text_content"],
                    record["source_page_id"],
                    record["source_panel_index"],
                )
                for record in records
            ],
        )
    return len(records)


def query_knowledge(
    database: ComicDatabase,
    project_id: str,
    query: str = "",
    entity_type: str = "all",
    chapter_id: str = "",
    limit: int = 10,
    entity_id: str = "",
) -> Dict[str, Any]:
    if limit < 1 or limit > 100:
        raise ValueError("limit must be between 1 and 100.")
    allowed_types = {
        "all",
        "character",
        "location",
        "story_event",
        "panel",
        "dialogue",
        "relationship",
    }
    if entity_type not in allowed_types:
        raise ValueError("Unsupported knowledge query type.")
    terms = list(dict.fromkeys(re.findall(r"\w+", query.casefold(), flags=re.UNICODE)))
    with database.connect() as connection:
        sql = """SELECT r.record_id, r.entity_type, r.entity_id, r.record_type,
                        r.text_content, r.source_page_id, r.source_panel_index,
                        p.chapter_id, p.page_index, p.image_path
                 FROM rag_index_records r
                 LEFT JOIN pages p ON p.page_id = r.source_page_id
                 WHERE r.project_id = ?"""
        params: List[Any] = [project_id]
        if entity_type != "all":
            sql += " AND r.entity_type = ?"
            params.append(entity_type)
        if entity_id.strip():
            sql += " AND r.entity_id = ?"
            params.append(entity_id.strip())
        if chapter_id.strip():
            sql += " AND (p.chapter_id = ? OR r.source_page_id IS NULL)"
            params.append(chapter_id.strip())
        rows = connection.execute(sql, params).fetchall()
        ranked = []
        for row in rows:
            content = row["text_content"]
            folded = content.casefold()
            score = (
                sum(1 for term in terms if term in folded) / len(terms)
                if terms
                else 1.0
            )
            if not terms or score > 0:
                result = dict(row)
                result["score"] = round(score, 4)
                ranked.append(result)
        ranked.sort(
            key=lambda item: (
                -item["score"],
                item["page_index"] if item["page_index"] is not None else -1,
                item["entity_id"],
            )
        )
    return {
        "project_id": project_id,
        "query": query,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "results": ranked[:limit],
        "total_matches": len(ranked),
        "index_is_rebuildable": True,
    }
